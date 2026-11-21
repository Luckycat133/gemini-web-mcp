"""
媒体生成 MCP 工具
"""

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
from ..domain import Artifact, ArtifactKind, ArtifactResultData, ArtifactState, DomainErrorCode, DomainResult
from ..services import (
    artifact_from_local_path,
    media_operation_timeout,
)
from ..services.media_generation import (
    MediaSaveOutcome as _MediaSaveOutcome,
    _media_from_music_card as _media_from_music_card,
    fetch_music_media_from_chat as _fetch_music_media_from_chat,
    normalize_saved_image_extension as _normalize_saved_image_extension,  # noqa: F401 - private compatibility helper
    safe_media_filename as _safe_media_filename,  # noqa: F401 - private compatibility helper
    save_generated_media as _save_generated_media,  # noqa: F401 - private compatibility helper
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
            "请求已选择网页视频模式，但这次没有观察到视频产物。"
            + "可检查网页视频入口 https://gemini.google.com/videos 的状态。"
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
        """通过 Gemini Web 原生图片/视频/音乐模式请求媒体，默认本地保存并验证。

        仅清理本次新建且所有输出已验证保存、或明确无产物
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
        from ..services.creation import CreationRequest, CreationService

        responses: list[object] = []
        service = CreationService(
            client_provider=get_gemini_client, initializer=initialize_client,
            cleanup_due=cleanup_due_remote_chats, finalizer=finalize_generated_chat_cleanup,
            music_fetcher=_fetch_music_media_from_chat, timeout_provider=_media_timeout,
            source="gemini_generate_media",
        )
        result = await service.generate(CreationRequest(
            prompt=prompt, media_type=media_type, model=model, thinking_level=thinking_level,
            image_path=safe_image_path, timeout_seconds=timeout_seconds,
            retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
            output_dir=output_dir, filename=filename,
        ), response_sink=responses)
        if result.data is None:
            return domain_text(result, result.error.message if result.error else "Media result unavailable.")
        data = result.data
        if result.error is not None and result.error.code == DomainErrorCode.TIMED_OUT:
            parsed = [TextContent(type="text", text=(
                f"❌ {media_type} 生成超时: {effective_timeout}s 内没有收到完整结果。"
                "可增大 timeout_seconds；先检查保留的 chat 和已观察到的资源，确认状态后再重试。"
            ))]
        elif result.error is not None and data.state == ArtifactState.FAILED:
            parsed = [TextContent(type="text", text=(
                f"❌ {media_type} 生成失败: {result.error.message}\n"
                "请求已显式选择 Gemini Web 原生工具模式；请根据错误和保留资源确认状态。"
            ))]
        elif responses:
            parsed = parse_response(responses[-1], job.effective_alias)
        else:
            parsed = [TextContent(type="text", text="Media result observed.")]
        saved = _MediaSaveOutcome(
            lines=tuple(f"{artifact.kind.value}: {artifact.local_path}" for artifact in data.artifacts if artifact.local_path),
            artifacts=data.artifacts,
            failures=("Local save incomplete.",) if result.meta.details.get("save_failure_count") else (),
        )
        if saved.lines:
            parsed[0].text += "\n\nSaved files:\n" + "\n".join(saved.lines)
        content = _finalize_media_content(
            parsed, job, data, saved, upstream_queued=bool(result.meta.details.get("upstream_queued")),
        )
        return attach_domain_result(content, result, use_result_data=True)

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
