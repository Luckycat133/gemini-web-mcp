"""
媒体生成 MCP 工具
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, cast

from ..adapters.mcp_sdk import MCPServer, TextContent

from ..adapters import append_artifact_block, attach_domain_result, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    finalize_generated_chat_cleanup,
    get_gemini_client,
    initialize_client,
)
from ..constants import resolve_media_request
from ..domain import Artifact, ArtifactKind, ArtifactResultData, ArtifactState, CleanupObservation, DomainErrorCode, DomainResult
from ..services import (
    artifact_exception_result,
    artifact_from_local_path,
    classify_media_artifact_state,
    extract_response_artifacts,
    merge_artifacts,
    media_artifacts,
    media_operation_timeout,
    observed_backend_from_response,
    response_chat_id,
)
from ..thinking_client import client_request_timeout
from ..services.media_generation import (
    MediaSaveOutcome as _MediaSaveOutcome,
    _media_from_music_card as _media_from_music_card,
    fetch_music_media_from_chat as _fetch_music_media_from_chat,
    materialize_generated_media,
    media_artifact_result,
    media_creation_response,
    media_generation_kwargs,
    media_requires_recovery,
    normalize_saved_image_extension as _normalize_saved_image_extension,  # noqa: F401 - private compatibility helper
    safe_media_filename as _safe_media_filename,  # noqa: F401 - private compatibility helper
    save_generated_media as _save_generated_media,  # noqa: F401 - private compatibility helper
    with_media_cleanup,
)
from .annotations import MUTATES_REMOTE
from .utils import parse_response, validate_optional_image_path

logger = logging.getLogger(__name__)


def _valid_media_filename(filename: str | None) -> bool:
    """A caller may name a file, but cannot escape the requested directory."""
    if filename is None:
        return True
    return bool(
        filename.strip()
        and filename not in {".", ".."}
        and Path(filename).name == filename
        and not any(character in filename for character in ("/", "\\", "\x00"))
        and not any(ord(character) < 32 for character in filename)
    )


def _prepend_backend_note(parsed: list[TextContent], note_lines: list[str]) -> list[TextContent]:
    if not parsed or not note_lines:
        return parsed
    first = parsed[0]
    prefix = "\n".join(note_lines).strip()
    if not prefix:
        return parsed
    return [TextContent(type="text", text=f"{prefix}\n\n{first.text}".strip()), *parsed[1:]]


def _media_timeout(media_type: str, timeout_seconds: Optional[int]) -> int:
    return media_operation_timeout(media_type, timeout_seconds)


@dataclass(frozen=True)
class _MediaJob:
    """Immutable request context shared across media generation phases."""

    prompt: str
    media_type: str
    requested_model: str
    request_model: str | None
    backend_label: str
    effective_alias: str
    note: str
    files: list[str] | None
    safe_image_path: str | None
    input_artifacts: tuple[Artifact, ...]
    timeout_seconds: int


def _media_failure_response(
    error: Exception, job: _MediaJob, message: str, *,
    response: object | None = None, observed_artifacts: tuple[Artifact, ...] = (),
    cleanup: CleanupObservation | None = None,
) -> list[TextContent]:
    remote_artifacts = () if observed_artifacts else extract_response_artifacts(
            media_creation_response(response),
            requested_backend=job.requested_model,
            request_model=job.request_model,
            effective_backend=job.backend_label,
        )
    artifacts = media_artifacts(merge_artifacts(
        remote_artifacts,
        observed_artifacts,
    ), job.media_type)
    failure_data = ArtifactResultData(
        state=classify_media_artifact_state(response, artifacts, job.media_type) if artifacts else ArtifactState.FAILED,
        artifacts=artifacts,
        requested_model=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        input_artifacts=job.input_artifacts,
        observed_backend=observed_backend_from_response(response),
        source_chat_id=response_chat_id(response),
        media_type=job.media_type,
    )
    result = artifact_exception_result(
        error,
        failure_data,
        logger=logger,
        operation=f"gemini_generate_media:{job.media_type}",
    )
    if cleanup is not None:
        result = with_media_cleanup(result, cleanup)
    content = append_artifact_block([TextContent(type="text", text=message)], artifacts)
    return attach_domain_result(content, result, use_result_data=True)


@dataclass(frozen=True)
class _MediaOutcome:
    parsed: list[TextContent]
    artifacts_data: ArtifactResultData
    result: DomainResult
    save_outcome: "_MediaSaveOutcome"


async def _build_media_outcome(
    client: object,
    response: object,
    job: _MediaJob,
    output_dir: Optional[str],
    filename: Optional[str],
    artifact_sink: list[Artifact] | None = None,
) -> _MediaOutcome:
    parsed = parse_response(media_creation_response(response), job.effective_alias)
    remote_chat_id = response_chat_id(response)
    observed_backend = observed_backend_from_response(response)
    materialized = await materialize_generated_media(
        client,
        response,
        media_type=job.media_type,
        output_dir=output_dir,
        filename=filename,
        prompt=job.prompt,
        requested_model=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        artifact_sink=artifact_sink,
        music_fetcher=_fetch_music_media_from_chat,
    )
    save_outcome = materialized.saved
    if save_outcome.lines:
        parsed[0].text = f"{parsed[0].text}\n\nSaved files:\n" + "\n".join(save_outcome.lines)
    artifacts = materialized.artifacts
    input_artifacts = job.input_artifacts
    if job.safe_image_path:
        input_artifacts = (
            await asyncio.to_thread(artifact_from_local_path,
                ArtifactKind.IMAGE,
                job.safe_image_path,
                title=Path(job.safe_image_path).name,
                requested_backend=job.requested_model,
                request_model=job.request_model,
                effective_backend=job.backend_label,
                observed_backend=observed_backend,
                source_chat_id=remote_chat_id,
            ),
        )
    artifact_state = classify_media_artifact_state(response, artifacts, job.media_type)
    if artifact_state == ArtifactState.EMPTY and save_outcome.failures:
        artifact_state = ArtifactState.FAILED
    data = ArtifactResultData(
        state=artifact_state,
        artifacts=artifacts,
        input_artifacts=input_artifacts,
        requested_model=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        observed_backend=observed_backend,
        source_chat_id=remote_chat_id,
        media_type=job.media_type,
    )
    video_empty_action = (
        "Gemini returned no usable video artifact. "
        "Gemini Omni video mode is available at https://gemini.google.com/videos; "
        "a generic chat prompt does not prove that mode was selected. "
        "Use that dedicated entry in an authorized browser."
        if job.media_type == "video" and artifact_state == ArtifactState.EMPTY
        else "Check Gemini's media capability and quota before another attempt."
    )
    result = media_artifact_result(
        data,
        response=response,
        save_failures=save_outcome.failures,
        empty_suggested_action=video_empty_action,
        empty_retryable=job.media_type != "video",
    )
    return _MediaOutcome(parsed=parsed, artifacts_data=data, result=result, save_outcome=save_outcome)


def _finalize_media_content(
    parsed: list[TextContent],
    job: _MediaJob,
    data: ArtifactResultData,
    save_outcome: "_MediaSaveOutcome",
    *,
    upstream_queued: bool = False,
) -> list[TextContent]:
    if save_outcome.failures and data.state != ArtifactState.FAILED:
        parsed[0].text = (
            f"{parsed[0].text}\n\n"
            "⚠️ Local artifact save was incomplete; use the remote URI or retry with another output directory."
        )
    note_lines = [f"后端: {job.backend_label}"]
    if job.note:
        note_lines.append(job.note)
    if job.media_type == "image":
        note_lines.append("说明: Pro redo 属于网页生成后的二次操作，不是独立首轮生成模型。")
    content = _prepend_backend_note(parsed, note_lines)
    if data.state == ArtifactState.FAILED:
        content[0].text = (
            f"{content[0].text}\n\n"
            f"❌ {job.media_type} 产物未能保存或通过本地验证。请检查输出目录后重试。"
        )
        return append_artifact_block(content, data.artifacts)
    if data.state == ArtifactState.EMPTY:
        empty_message = f"⚠️ {job.media_type} 请求已返回，但没有返回可用的 {job.media_type} 产物。"
        next_step = (
            "当前通用聊天请求未证实进入 Gemini Omni 视频模式。"
            + "如需生成视频，可使用网页专用视频入口 https://gemini.google.com/videos。"
            if job.media_type == "video"
            else "请检查 Gemini 当前媒体能力和配额，再决定是否重试。"
        )
        content[0].text = f"{content[0].text}\n\n{empty_message}{next_step}"
        return content
    if data.state == ArtifactState.QUEUED:
        content[0].text = (
            f"{content[0].text}\n\n"
            f"⏳ {job.media_type} 请求已进入上游队列，尚未返回可验证产物。"
        )
        return content
    if upstream_queued:
        content[0].text += "\n\n⏳ 已返回的产物可用，但上游仍在排队或处理中；会话已保留。"
    return append_artifact_block(content, data.artifacts)


def _invalid_image_response(image_error: str | None) -> list[TextContent]:
    return domain_text(
        DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            image_error or "Invalid image path.",
            suggested_action="Correct the image path and retry.",
            verification_status="input_rejected",
        ),
        f"❌ {image_error}",
    )


def _invalid_media_argument(message: str) -> list[TextContent]:
    return domain_text(
        DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            message,
            suggested_action="Correct the media request and retry.",
            verification_status="input_rejected",
        ),
        f"❌ {message}",
    )


def _initial_input_artifacts(
    safe_image_path: str | None,
    requested_model: str,
    request_model: str | None,
    effective_backend: str,
) -> tuple[Artifact, ...]:
    if not safe_image_path:
        return ()
    return (
        artifact_from_local_path(
            ArtifactKind.IMAGE,
            safe_image_path,
            title=Path(safe_image_path).name,
            requested_backend=requested_model,
            request_model=request_model,
            effective_backend=effective_backend,
        ),
    )


def register_media_tools(mcp: MCPServer):

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_generate_media(
        prompt: str,
        media_type: Literal["image", "video", "music"],
        model: str = "flash",
        thinking_level: str = "standard",
        image_path: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
        output_dir: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> list[TextContent]:
        """通过 Gemini Web 原生图片/音乐模式请求媒体，默认保存到 generated_media 并验证。

        视频原生入口尚未证实。仅清理本次新建且所有输出已验证保存、或明确无产物
        的会话；远端资源、排队和保存不完整时保留。retain_chat / delete_after_seconds
        控制保留策略，meta.details.cleanup 返回独立的清理观测。
        """
        if not prompt.strip():
            return _invalid_media_argument("prompt must not be blank.")
        if not _valid_media_filename(filename):
            return _invalid_media_argument("filename must be a single non-empty file name.")
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return _invalid_image_response(image_error)

        media_request = resolve_media_request(model, media_type, thinking_level)
        effective_timeout = _media_timeout(media_type, timeout_seconds)
        job = _MediaJob(
            prompt=prompt,
            media_type=media_type,
            requested_model=model,
            request_model=media_request["request_model"],
            backend_label=media_request["backend_label"],
            effective_alias=media_request["effective_alias"],
            note=media_request["note"],
            files=[safe_image_path] if safe_image_path else None,
            safe_image_path=safe_image_path,
            input_artifacts=_initial_input_artifacts(
                safe_image_path,
                model,
                media_request["request_model"],
                media_request["backend_label"],
            ),
            timeout_seconds=effective_timeout,
        )
        logger.info(
            "正在生成 %s，requested_model=%s effective_model=%s backend=%s",
            media_type,
            model,
            media_request["effective_alias"],
            media_request["backend_label"],
        )
        response = None
        client = None
        observed_artifacts: list[Artifact] = []
        try:
            # One budget covers generation, chat recovery, downloads (including
            # upstream HTTP 206 polling), and local verification.
            async with asyncio.timeout(effective_timeout):
                client = get_gemini_client()
                await initialize_client()
                await cleanup_due_remote_chats(client)
                with client_request_timeout(client, effective_timeout):
                    response = await client.generate_content(**media_generation_kwargs(
                        job.prompt,
                        job.media_type,
                        files=job.files,
                        model=job.request_model,
                        thinking_level=thinking_level,
                        timeout_seconds=effective_timeout,
                    ))
                    outcome = await _build_media_outcome(client, response, job, output_dir, filename, observed_artifacts)
        except asyncio.TimeoutError as error:
            cleanup = None
            if response is not None:
                cleanup = await finalize_generated_chat_cleanup(
                    response,
                    owns_chat=True,
                    retain_chat=retain_chat,
                    preserve_for_recovery=True,
                    source=f"gemini_generate_media:{job.media_type}",
                    client=client,
                )
            return _media_failure_response(
                error,
                job,
                (
                    f"后端: {job.backend_label}\n"
                    f"❌ {job.media_type} 生成超时: {job.timeout_seconds}s 内没有收到完整结果。"
                    "视频/音乐通常需要更长时间或会被 Gemini Web 上游排队；"
                    "可增大 timeout_seconds；先检查保留的 chat 和已观察到的资源，确认状态后再重试。"
                ),
                response=response,
                observed_artifacts=tuple(observed_artifacts),
                cleanup=cleanup,
            )
        except Exception as e:
            cleanup = None
            if response is not None:
                cleanup = await finalize_generated_chat_cleanup(
                    response,
                    owns_chat=True,
                    retain_chat=retain_chat,
                    preserve_for_recovery=True,
                    source=f"gemini_generate_media:{job.media_type}",
                    client=client,
                )
            return _media_failure_response(
                e,
                job,
                (
                    f"后端: {job.backend_label}\n"
                    f"❌ {job.media_type} 生成失败: {str(e)}\n"
                    + (
                        "图片/音乐请求已显式选择 Gemini Web 原生工具模式；上游仍可能拒绝或排队。"
                        if job.media_type in {"image", "music"}
                        else "当前通用 generate_content 尚未证实进入原生视频模式。"
                    )
                ),
                response=response,
                observed_artifacts=tuple(observed_artifacts),
                cleanup=cleanup,
            )
        cleanup = await finalize_generated_chat_cleanup(
            response,
            owns_chat=True,
            retain_chat=retain_chat,
            preserve_for_recovery=media_requires_recovery(
                outcome.artifacts_data, response=response,
                save_failed=bool(outcome.save_outcome.failures),
            ),
            delete_after_seconds=delete_after_seconds,
            source=f"gemini_generate_media:{job.media_type}",
            client=client,
        )
        content = _finalize_media_content(
            outcome.parsed, job, outcome.artifacts_data, outcome.save_outcome,
            upstream_queued=bool(outcome.result.meta.details.get("upstream_queued")),
        )
        return attach_domain_result(content, with_media_cleanup(outcome.result, cleanup), use_result_data=True)

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_generate_music(
        prompt: str,
        model: str = "flash",
        thinking_level: str = "extended",
        timeout_seconds: Optional[int] = None,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
        output_dir: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> list[TextContent]:
        """通过原生音乐模式生成，默认本地保存并验证；未保存或排队时保留会话。

        retain_chat / delete_after_seconds 控制本次新建会话的清理策略，
        meta.details.cleanup 独立报告清理结果。
        """
        result = await gemini_generate_media(
            prompt=prompt,
            media_type="music",
            model=model,
            thinking_level=thinking_level,
            timeout_seconds=timeout_seconds,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            output_dir=output_dir,
            filename=filename,
        )
        return cast(list[TextContent], result)
