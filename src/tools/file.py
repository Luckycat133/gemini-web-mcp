import asyncio
from dataclasses import dataclass
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
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_model_name
from ..domain import (
    Artifact,
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    DomainErrorCode,
    DomainResult,
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

logger = logging.getLogger(__name__)


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
        if not result.scheme or not result.netloc:
            return False, "URL 格式无效"

        return True, url
    except Exception as e:
        return False, f"URL 验证失败: {str(e)}"


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


def _analysis_failure_response(error: Exception, context: _AnalysisContext, message: str) -> list[TextContent]:
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
    return attach_domain_result(
        [TextContent(type="text", text=message)],
        result,
        use_result_data=True,
    )


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

        client = await _init_analysis_client()
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

        try:
            prompt = analysis_prompt or "Please analyze this file and tell me what you see."

            response = await asyncio.wait_for(
                client.generate_content(
                    prompt,
                    files=[safe_file_path],
                    model=model_name,
                    thinking_level=thinking_level,
                    timeout=60,
                ),
                timeout=60,
            )

            result_text = _response_result_text(response)
            schedule_remote_chat_cleanup_from_response(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source="gemini_upload_file",
            )

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
            return attach_domain_result(content, result, use_result_data=True)
        except asyncio.TimeoutError as error:
            return _analysis_failure_response(error, context, "❌ Error: 文件分析超时，请检查认证状态或稍后重试。")
        except Exception as e:
            return _analysis_failure_response(e, context, f"❌ Error: {str(e)}")

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

        client = await _init_analysis_client()
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

        logger.info(f"分析 URL: {valid_url}")

        context = _AnalysisContext(
            requested_model=model,
            request_model=model_name,
            effective_backend=model_name,
            media_type="url_analysis",
            operation="gemini_analyze_url",
            source_artifact=source_artifact,
        )

        try:
            response = await asyncio.wait_for(
                client.generate_content(
                    prompt,
                    model=model_name,
                    thinking_level=thinking_level,
                    timeout=60,
                ),
                timeout=60,
            )

            result_text = _response_result_text(response)
            schedule_remote_chat_cleanup_from_response(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source="gemini_analyze_url",
            )

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
            return attach_domain_result(
                _analysis_content(result_text, data),
                result,
                use_result_data=True,
            )
        except asyncio.TimeoutError as error:
            return _analysis_failure_response(error, context, "❌ Error: URL 分析超时，请稍后重试。")
        except Exception as e:
            return _analysis_failure_response(e, context, f"❌ Error: {str(e)}")
