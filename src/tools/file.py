import asyncio
from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from ..adapters.mcp_sdk import MCPServer, TextContent

from ..adapters import append_artifact_block, attach_domain_result, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    finalize_remote_chat_cleanup_from_response,
    schedule_remote_chat_cleanup,
)
from ..constants import resolve_model_name
from ..domain import (
    Artifact,
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    CleanupObservation,
    CleanupState,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
)
from ..services import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_from_remote,
    artifact_result,
    classify_artifact_state,
    extract_response_artifacts,
    observed_backend_from_response,
    response_chat_id,
)
from .annotations import MUTATES_REMOTE
from .utils import validate_local_file_path
from ..infrastructure.account_identity import current_account_key
from ..services.cleanup_scope import observe_cleanup_frames

logger = logging.getLogger(__name__)


class _AnalysisCleanupObserver:
    """Register only request-owned SDK identities without ending the request."""

    def __init__(self, *, owner: str, retain_chat: bool, delay: int | None, source: str):
        self.owner = owner
        self.retain_chat = retain_chat
        self.delay = delay if retain_chat else max(1800, delay or 0)
        self.source = source
        self.registered: set[str] = set()
        self.failed: set[str] = set()

    def observe(self, value: Any) -> None:
        cid = response_chat_id(value)
        if not cid or cid in self.registered:
            return
        try:
            result = schedule_remote_chat_cleanup(
                cid, retain_chat=self.retain_chat, delete_after_seconds=self.delay,
                source=f"{self.source}:in_progress", _owner_account=self.owner,
            )
            if not isinstance(result, CleanupObservation) or result.state in {
                CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE,
            }:
                self.failed.add(cid)
                return
            self.failed.discard(cid)
            self.registered.add(cid)
        except Exception:
            self.failed.add(cid)


async def _generate_analysis(client: Any, prompt: str, *, observer: _AnalysisCleanupObserver,
                             **kwargs: Any) -> Any:
    """Observe early SDK identities and preserve any CID if metadata rolls back."""
    factory = getattr(type(client), "create_cleanup_chat", None)
    chat = factory(client) if callable(factory) else None
    if chat is not None:
        kwargs["chat"] = chat
    try:
        with observe_cleanup_frames(observer.observe):
            return await asyncio.wait_for(client.generate_content(prompt, **kwargs), timeout=60)
    finally:
        if chat is not None:
            observer.observe(chat)


def _validate_file_path(file_path: str) -> tuple[bool, str]:
    """验证文件路径安全性，防止路径遍历攻击。"""
    return validate_local_file_path(file_path)


def _validate_url(url: str) -> tuple[bool, str]:
    """验证 URL 格式是否正确。
    
    Args:
        url: 要验证的 URL
        
    Returns:
        (is_valid, message): 是否有效及原因
    """
    if not url:
        return False, "URL 不能为空"

    try:
        result = urlparse(url)
        if (result.scheme.lower() not in {"http", "https"} or not result.hostname
                or result.username is not None or result.password is not None):
            return False, "URL 格式无效"
        _ = result.port

        return True, url
    except ValueError:
        return False, "URL 格式无效"


def _analysis_state(response, outputs: tuple[Artifact, ...], source: Artifact) -> ArtifactState:
    state = classify_artifact_state(response, outputs)
    return source.state if state == ArtifactState.EMPTY else state


def _analysis_content(
    text: str,
    data: ArtifactResultData,
) -> list[TextContent]:
    content = [TextContent(type="text", text=text)]
    content = append_artifact_block(content, data.input_artifacts, heading="Input artifacts")
    return append_artifact_block(content, data.artifacts, heading="Output artifacts")


def _response_result_text(response: Any) -> str:
    """Assemble the shared text body: upstream text plus images and chat id."""
    result_text: str = response.text
    if response.images:
        result_text += "\n\n📷 Images in response:\n"
        for i, img in enumerate(response.images, 1):
            img_info = f"{i}. {img.title or 'Untitled image'}"
            if hasattr(img, "url"):
                img_info += f": {img.url}"
            result_text += f"\n{img_info}"
    remote_chat_id = response_chat_id(response)
    if remote_chat_id:
        result_text += f"\n\nRemote chat ID: {remote_chat_id}"
    return result_text


@dataclass(frozen=True)
class _AnalysisContext:
    """Shared identity fields for the upload/URL analysis tools."""

    requested_model: str
    request_model: str
    effective_backend: str
    media_type: str
    operation: str
    source_artifact: Artifact


def _analysis_failure_response(error: Exception, context: _AnalysisContext, message: str,
                               observer: _AnalysisCleanupObserver | None = None) -> list[TextContent]:
    data = ArtifactResultData(
        state=ArtifactState.FAILED,
        input_artifacts=(context.source_artifact,),
        requested_model=context.requested_model,
        request_model=context.request_model,
        effective_backend=context.effective_backend,
        media_type=context.media_type,
    )
    result = artifact_exception_result(
        error,
        data,
        logger=logger,
        operation=context.operation,
    )
    result = _with_cleanup_registration_warning(result, observer)
    return attach_domain_result(
        [TextContent(type="text", text=message)],
        result,
        use_result_data=True,
    )


def _with_cleanup_registration_warning(result: DomainResult[Any], observer: _AnalysisCleanupObserver | None,
                                       cleanup: Any = None) -> DomainResult[Any]:
    if observer is None:
        return result
    failed = set(observer.failed)
    if (isinstance(cleanup, CleanupObservation) and cleanup.upstream_chat_id
            and cleanup.state not in {CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE}):
        failed.discard(cleanup.upstream_chat_id)
    if not failed:
        return result
    return replace(result, warnings=(*result.warnings, DomainWarning(
        code="cleanup_registration_unverified", message="A request-owned chat could not be registered for cleanup.",
    )), meta=replace(result.meta, details={**result.meta.details, "cleanup_registration_failed_chat_ids": tuple(sorted(failed))}))


def _invalid_analysis_input_response(
    error_message: str,
    suggested_action: str,
    requested_model: str,
    media_type: str,
) -> list[TextContent]:
    data = ArtifactResultData(
        state=ArtifactState.FAILED,
        requested_model=requested_model,
        media_type=media_type,
    )
    return domain_text(
        DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            error_message,
            data=data,
            suggested_action=suggested_action,
            verification_status="input_rejected",
        ),
        f"❌ {error_message}",
        use_result_data=True,
    )


async def _init_analysis_client() -> Any:
    client = get_gemini_client()
    await initialize_client()
    await cleanup_due_remote_chats(client)
    return client


def _analysis_success_result(
    response: Any,
    source_artifact: Artifact,
    model: str,
    model_name: str,
    media_type: str,
) -> tuple[ArtifactResultData, Any]:
    observed_backend = observed_backend_from_response(response)
    outputs = extract_response_artifacts(
        response,
        requested_backend=model,
        request_model=model_name,
        effective_backend=model_name,
        observed_backend=observed_backend,
    )
    data = ArtifactResultData(
        state=_analysis_state(response, outputs, source_artifact),
        artifacts=outputs,
        input_artifacts=(source_artifact,),
        requested_model=model,
        request_model=model_name,
        effective_backend=model_name,
        observed_backend=observed_backend,
        source_chat_id=response_chat_id(response),
        media_type=media_type,
    )
    return data, artifact_result(data)


def _url_analysis_prompt(analysis_prompt: Optional[str], valid_url: str) -> str:
    if analysis_prompt:
        return (
            f"{analysis_prompt}\n\n"
            f"URL: {valid_url}\n"
            "Use the URL above as the content source for your answer."
        )
    return f"Please analyze the content at this URL: {valid_url}"


def register_file_tools(mcp: MCPServer) -> None:
    """Register all file and URL related MCP tools.

    Args:
        mcp: MCPServer instance
    """

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_upload_file(
        file_path: str,
        analysis_prompt: Optional[str] = None,
        model: str = "flash",
        thinking_level: str = "standard",
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """上传文件供 Gemini 分析。

        支持: 图片、PDF、文档等。

        Args:
            file_path: 文件路径
            analysis_prompt: 可选分析提示词
            model: 模型选择 (flash-lite/flash/pro; fast/thinking 为兼容别名)
        """
        is_safe, safe_path_or_error = _validate_file_path(file_path)
        if not is_safe:
            return _invalid_analysis_input_response(
                safe_path_or_error,
                "Provide an existing file inside an allowed directory.",
                requested_model=model,
                media_type="file_analysis",
            )
        safe_file_path = safe_path_or_error

        model_name = resolve_model_name(model)
        source_artifact = artifact_from_local_path(
            ArtifactKind.FILE,
            safe_file_path,
            title=Path(safe_file_path).name,
            requested_backend=model,
            request_model=model_name,
            effective_backend=model_name,
        )
        context = _AnalysisContext(
            requested_model=model,
            request_model=model_name,
            effective_backend=model_name,
            media_type="file_analysis",
            operation="gemini_upload_file",
            source_artifact=source_artifact,
        )

        logger.info(f"上传文件: {safe_file_path}")

        response = None
        cleanup_finalized = False
        observer: _AnalysisCleanupObserver | None = None
        try:
            client = await _init_analysis_client()
            owner = current_account_key()
            if not owner:
                raise PermissionError("Conversation account could not be verified")
            observer = _AnalysisCleanupObserver(owner=owner, retain_chat=retain_chat, delay=delete_after_seconds,
                                                source="gemini_upload_file")
            prompt = analysis_prompt or "Please analyze this file and tell me what you see."

            response = await _generate_analysis(client, prompt, observer=observer, files=[safe_file_path],
                                                model=model_name, thinking_level=thinking_level, timeout=60)
            observer.observe(response)
            if current_account_key() != owner:
                raise PermissionError("Conversation account changed")

            result_text = _response_result_text(response)
            source_artifact = artifact_from_local_path(
                ArtifactKind.FILE,
                safe_file_path,
                title=Path(safe_file_path).name,
                source_chat_id=response_chat_id(response),
                requested_backend=model,
                request_model=model_name,
                effective_backend=model_name,
                observed_backend=observed_backend_from_response(response),
            )
            data, result = _analysis_success_result(response, source_artifact, model, model_name, "file_analysis")
            content = _analysis_content(
                f"✅ Successfully analyzed {Path(safe_file_path).name}\n\n{result_text}",
                data,
            )
            if current_account_key() != owner:
                raise PermissionError("Conversation account changed")
            cleanup = await finalize_remote_chat_cleanup_from_response(
                response, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                source="gemini_upload_file",
            )
            if current_account_key() != owner:
                raise PermissionError("Conversation account changed")
            result = replace(result, meta=replace(result.meta, details={**result.meta.details, "cleanup": cleanup}))
            result = _with_cleanup_registration_warning(result, observer, cleanup)
            finalized = attach_domain_result(content, result, use_result_data=True)
            cleanup_finalized = True
            return finalized
        except asyncio.TimeoutError as error:
            return _analysis_failure_response(error, context, "❌ Error: 文件分析超时，请检查认证状态或稍后重试。", observer)
        except Exception as e:
            return _analysis_failure_response(e, context, "❌ Error: 文件分析失败，请查看结构化错误详情。", observer)
        finally:
            if response is not None and not cleanup_finalized:
                try:
                    if observer is not None:
                        observer.observe(response)
                except Exception as cleanup_error:
                    logger.warning("Could not persist interrupted file cleanup error_type=%s", type(cleanup_error).__name__)

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_analyze_url(
        url: str,
        analysis_prompt: Optional[str] = None,
        model: str = "flash",
        thinking_level: str = "standard",
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """分析 URL 内容。

        支持: YouTube 视频、网页等。

        Args:
            url: 网址
            analysis_prompt: 可选分析提示词
            model: 模型选择
        """
        is_valid, valid_url_or_error = _validate_url(url)
        if not is_valid:
            return _invalid_analysis_input_response(
                valid_url_or_error,
                "Provide an absolute URL with a scheme and host.",
                requested_model=model,
                media_type="url_analysis",
            )
        valid_url = valid_url_or_error

        model_name = resolve_model_name(model)
        source_artifact = artifact_from_remote(
            ArtifactKind.WEBPAGE,
            valid_url,
            title=urlparse(valid_url).netloc,
            requested_backend=model,
            request_model=model_name,
            effective_backend=model_name,
            verification_method="input_uri_provided",
        )
        context = _AnalysisContext(
            requested_model=model,
            request_model=model_name,
            effective_backend=model_name,
            media_type="url_analysis",
            operation="gemini_analyze_url",
            source_artifact=source_artifact,
        )

        prompt = _url_analysis_prompt(analysis_prompt, valid_url)

        logger.info("开始 URL 分析")

        context = _AnalysisContext(
            requested_model=model,
            request_model=model_name,
            effective_backend=model_name,
            media_type="url_analysis",
            operation="gemini_analyze_url",
            source_artifact=source_artifact,
        )

        response = None
        cleanup_finalized = False
        observer: _AnalysisCleanupObserver | None = None
        try:
            client = await _init_analysis_client()
            owner = current_account_key()
            if not owner:
                raise PermissionError("Conversation account could not be verified")
            observer = _AnalysisCleanupObserver(owner=owner, retain_chat=retain_chat, delay=delete_after_seconds,
                                                source="gemini_analyze_url")
            response = await _generate_analysis(client, prompt, observer=observer, model=model_name,
                                                thinking_level=thinking_level, timeout=60)
            observer.observe(response)
            if current_account_key() != owner:
                raise PermissionError("Conversation account changed")

            result_text = _response_result_text(response)
            source_artifact = artifact_from_remote(
                ArtifactKind.WEBPAGE,
                valid_url,
                title=urlparse(valid_url).netloc,
                source_chat_id=response_chat_id(response),
                requested_backend=model,
                request_model=model_name,
                effective_backend=model_name,
                observed_backend=observed_backend_from_response(response),
                verification_method="input_uri_provided",
            )
            data, result = _analysis_success_result(response, source_artifact, model, model_name, "url_analysis")
            content = _analysis_content(result_text, data)
            if current_account_key() != owner:
                raise PermissionError("Conversation account changed")
            cleanup = await finalize_remote_chat_cleanup_from_response(
                response, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                source="gemini_analyze_url",
            )
            if current_account_key() != owner:
                raise PermissionError("Conversation account changed")
            result = replace(result, meta=replace(result.meta, details={**result.meta.details, "cleanup": cleanup}))
            result = _with_cleanup_registration_warning(result, observer, cleanup)
            finalized = attach_domain_result(content, result, use_result_data=True)
            cleanup_finalized = True
            return finalized
        except asyncio.TimeoutError as error:
            return _analysis_failure_response(error, context, "❌ Error: URL 分析超时，请稍后重试。", observer)
        except Exception as e:
            return _analysis_failure_response(e, context, "❌ Error: URL 分析失败，请查看结构化错误详情。", observer)
        finally:
            if response is not None and not cleanup_finalized:
                try:
                    if observer is not None:
                        observer.observe(response)
                except Exception as cleanup_error:
                    logger.warning("Could not persist interrupted URL cleanup error_type=%s", type(cleanup_error).__name__)
