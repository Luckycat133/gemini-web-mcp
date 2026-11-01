"""
媒体生成 MCP 工具
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Optional, cast

from ..adapters.mcp_sdk import MCPServer, TextContent

from ..adapters import append_artifact_block, attach_domain_result, domain_failure_text, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    finalize_remote_chat_cleanup_from_response,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_media_request
from ..domain import Artifact, ArtifactKind, ArtifactResultData, ArtifactState, DomainErrorCode, DomainResult
from ..services.music_recovery import (
    fetch_music_media_from_chat as _fetch_music_media_from_chat,
    media_from_music_card as _media_from_music_card,  # noqa: F401 - compatibility import
    music_response_identity,
)
from ..services.media_generation import (
    MediaGenerationProgress, add_media_cleanup_evidence, compose_media_prompt, generate_media_response, media_timeout_scope,
    media_timeout as _media_timeout,
)
from ..services import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_result,
    probe_playable_av_tracks,
    extract_response_artifacts,
    is_response_queued,
    merge_artifacts,
    observed_backend_from_response,
    response_chat_id,
    response_image_items,
)
from .annotations import MUTATES_REMOTE
from .utils import parse_response, validate_optional_image_path
from ..services.media_storage import (
    _MediaSaveOutcome,
    _normalize_saved_image_extension as _normalize_saved_image_extension,
    _normalize_saved_paths as _normalize_saved_paths,
    _probe_duration,
    _response_media_items as _response_media_items,
    _safe_media_filename as _safe_media_filename,
    _saved_artifact_kind as _saved_artifact_kind,
    _saved_artifact_uri as _saved_artifact_uri,
    _unused_media_filename as _unused_media_filename,
    _valid_media_filename,
    _save_generated_media as _shared_save_generated_media,
    reconcile_remote_media_types,
    classify_generated_media,
)

logger = logging.getLogger(__name__)


async def _save_generated_media(*args, **kwargs):
    return await _shared_save_generated_media(
        *args, **kwargs, duration_probe=_probe_duration, track_probe=probe_playable_av_tracks,
    )


def _prepend_backend_note(parsed: list[TextContent], note_lines: list[str]) -> list[TextContent]:
    if not parsed or not note_lines:
        return parsed
    first = parsed[0]
    prefix = "\n".join(note_lines).strip()
    if not prefix:
        return parsed
    return [TextContent(type="text", text=f"{prefix}\n\n{first.text}".strip()), *parsed[1:]]


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


def _generation_prompt(job: _MediaJob) -> str:
    return compose_media_prompt(job.media_type, job.prompt)


def _media_failure_response(
    error: Exception, job: _MediaJob, message: str, progress: MediaGenerationProgress | None = None,
) -> list[TextContent]:
    failure_data = ArtifactResultData(
        state=ArtifactState.FAILED,
        requested_model=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        input_artifacts=job.input_artifacts,
        media_type=job.media_type,
        source_chat_id=progress.last_chat_id if progress else None,
    )
    result = artifact_exception_result(
        error,
        failure_data,
        logger=logger,
        operation=f"gemini_generate_media:{job.media_type}",
    )
    if progress is not None:
        result = add_media_cleanup_evidence(result, progress)
    return attach_domain_result([TextContent(type="text", text=message)], result, use_result_data=True)


async def _recover_music_media(client: object, response: object, job: _MediaJob) -> tuple[list, tuple[Artifact, ...]]:
    if job.media_type != "music" or (getattr(response, "media", None) or []):
        return [], ()
    remote_chat_id, rid, rcid = music_response_identity(response)
    if not rid:
        return [], ()
    try:
        recovered_media = await _fetch_music_media_from_chat(client, remote_chat_id, rid=rid, rcid=rcid)
    except Exception as e:
        logger.warning("无法从远端 chat 恢复音乐媒体 error_type=%s", type(e).__name__)
        return [], ()
    recovered_artifacts = extract_response_artifacts(
        SimpleNamespace(
            images=[],
            videos=[],
            media=recovered_media,
            metadata=[remote_chat_id] if remote_chat_id else [],
        ),
        media_type=job.media_type,
        requested_backend=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        observed_backend=observed_backend_from_response(response),
    )
    return recovered_media, recovered_artifacts


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
) -> _MediaOutcome:
    parsed = parse_response(
        response,
        job.effective_alias,
        image_items=response_image_items(response, generated_only=True) if job.media_type == "image" else None,
    )
    remote_chat_id = response_chat_id(response)
    observed_backend = observed_backend_from_response(response)
    remote_artifacts = extract_response_artifacts(
        response,
        media_type=job.media_type,
        requested_backend=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        observed_backend=observed_backend,
    )
    recovered_media, recovered_artifacts = await _recover_music_media(client, response, job)
    save_outcome = await _save_generated_media(
        response,
        media_type=job.media_type,
        output_dir=output_dir,
        filename=filename,
        prompt=job.prompt,
        media_items=recovered_media or None,
        requested_model=job.requested_model,
        request_model=cast("str", job.request_model),
        effective_backend=job.backend_label,
        observed_backend=observed_backend,
        source_chat_id=remote_chat_id,
    )
    if save_outcome.lines:
        parsed[0].text = f"{parsed[0].text}\n\nSaved files:\n" + "\n".join(save_outcome.lines)
    artifacts = merge_artifacts(
        reconcile_remote_media_types(remote_artifacts, save_outcome.artifacts),
        reconcile_remote_media_types(recovered_artifacts, save_outcome.artifacts),
        save_outcome.artifacts,
    )
    input_artifacts = job.input_artifacts
    if job.safe_image_path:
        input_artifacts = (
            artifact_from_local_path(
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
    artifact_state = classify_generated_media(response, artifacts, job.media_type, save_outcome.failures)
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
        ("Review the cleanup status before retrying. " if remote_chat_id else "No upstream chat ID was observed. ")
        + "No usable video was returned. Do not duplicate the request until its state is known."
        if job.media_type == "video" and artifact_state == ArtifactState.EMPTY
        else None
    )
    result = artifact_result(
        data,
        save_failures=save_outcome.failures,
        empty_suggested_action=video_empty_action,
        empty_retryable=job.media_type != "video",
    )
    return _MediaOutcome(parsed=parsed, artifacts_data=data, result=result, save_outcome=save_outcome)


def _finalize_media_content(
    parsed: list[TextContent],
    job: _MediaJob,
    data: ArtifactResultData,
    response: object,
    save_outcome: "_MediaSaveOutcome",
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
        empty_message = (
            f"⚠️ {job.media_type} 请求已完成，但没有返回可验证的媒体产物。"
            if (getattr(response, "text", "") or "").strip()
            else f"⚠️ {job.media_type} 请求已完成，但没有返回文本、图片、视频或音乐资源。"
        )
        next_step = (
            "尚未取得可验证的视频产物。"
            + ("请先检查清理状态；" if data.source_chat_id else "未取得上游聊天 ID；")
            + "确认任务状态后再决定是否重试。"
            if job.media_type == "video"
            else "请检查清理状态，再决定是否重试。"
        )
        content[0].text = f"{content[0].text}\n\n{empty_message}{next_step}"
        return content
    if data.state == ArtifactState.QUEUED:
        content[0].text = (
            f"{content[0].text}\n\n"
            f"⏳ {job.media_type} 请求已进入上游队列，尚未返回可验证产物。"
        )
        return content
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
        background: bool = True,
        operation_id: Optional[str] = None,
    ) -> list[TextContent]:
        """生成媒体；默认后台运行，以 operation_id 查询任务状态和结果。

        使用 gemini_get_operation_status / gemini_get_operation_result 查询；
        background=false 显式选择同步模式，不接受 operation_id。
        """
        if not background and operation_id is not None:
            return _invalid_media_argument("operation_id requires background=true.")
        if not prompt.strip():
            return _invalid_media_argument("prompt must not be blank.")
        if not _valid_media_filename(filename):
            return _invalid_media_argument("filename must be a single non-empty file name.")
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return _invalid_image_response(image_error)

        if background:
            from ..services.background import submit_media
            result = await submit_media({
                "prompt": prompt, "media_type": media_type, "model": model, "thinking_level": thinking_level,
                "image_path": safe_image_path, "output_dir": output_dir, "filename": filename,
                "timeout_seconds": timeout_seconds, "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds, "edit": False,
            }, operation="gemini_generate_media", operation_id=operation_id)
            return domain_text(result, "媒体任务已提交，可用任务句柄查询状态和结果。" if result.ok
                               else domain_failure_text(result), use_result_data=True)

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
        progress = MediaGenerationProgress()
        try:
            client = get_gemini_client()
            await initialize_client()
            await cleanup_due_remote_chats(client)
            with media_timeout_scope(client, effective_timeout):
                response = await asyncio.wait_for(
                    generate_media_response(
                        client,
                        arguments=dict(prompt=_generation_prompt(job), files=job.files, model=job.request_model,
                                       thinking_level=thinking_level, timeout=effective_timeout),
                        schedule_cleanup=schedule_remote_chat_cleanup_from_response,
                        retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                        source=f"gemini_generate_media:{job.media_type}",
                        media_type=job.media_type,
                        progress=progress,
                    ),
                    timeout=effective_timeout,
                )
        except asyncio.TimeoutError as error:
            return _media_failure_response(
                error,
                job,
                (
                    f"后端: {job.backend_label}\n"
                    f"❌ {job.media_type} 生成超时: {job.timeout_seconds}s 内没有收到完整结果。"
                    "视频/音乐通常需要更长时间或会被 Gemini Web 上游排队；"
                    "可增大 timeout_seconds 后重试。"
                ),
                progress,
            )
        except Exception as e:
            return _media_failure_response(
                e,
                job,
                (
                    f"后端: {job.backend_label}\n"
                    f"❌ {job.media_type} 生成失败，请查看结构化错误详情。\n"
                    "说明: 上游可能中止请求或长时间排队；请检查清理状态再决定是否重试。"
                ),
                progress,
            )
        cleanup_finalized = False
        try:
            outcome = await _build_media_outcome(client, response, job, output_dir, filename)
            content = _finalize_media_content(outcome.parsed, job, outcome.artifacts_data, response, outcome.save_outcome)
            deferred = outcome.artifacts_data.state == ArtifactState.QUEUED and not retain_chat
            cleanup = await finalize_remote_chat_cleanup_from_response(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=(1800 if deferred and delete_after_seconds is None else delete_after_seconds),
                source=f"gemini_generate_media:{job.media_type}" + (":deferred" if deferred else ""),
            )
            result = replace(outcome.result, meta=replace(outcome.result.meta, details={**outcome.result.meta.details, "cleanup": cleanup}))
            result = add_media_cleanup_evidence(result, progress, cleanup)
            finalized = attach_domain_result(content, result, use_result_data=True)
            cleanup_finalized = True
            return finalized
        finally:
            if not cleanup_finalized:
                try:
                    deferred = is_response_queued(response) and not retain_chat
                    schedule_remote_chat_cleanup_from_response(
                        response, retain_chat=retain_chat,
                        delete_after_seconds=(1800 if deferred and delete_after_seconds is None else delete_after_seconds),
                        source=f"gemini_generate_media:{job.media_type}:interrupted" + (":deferred" if deferred else ""),
                    )
                except Exception as cleanup_error:
                    logger.warning("Could not persist interrupted media cleanup error_type=%s", type(cleanup_error).__name__)

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_generate_music(
        prompt: str,
        model: str = "flash",
        thinking_level: str = "standard",
        timeout_seconds: Optional[int] = None,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
        output_dir: Optional[str] = None,
        filename: Optional[str] = None,
        background: bool = True,
        operation_id: Optional[str] = None,
    ) -> list[TextContent]:
        """生成音乐；默认后台运行，以操作句柄查询状态和结果。

        使用 gemini_get_operation_status / gemini_get_operation_result 查询；
        background=false 显式选择同步模式，不接受 operation_id。
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
            background=background,
            operation_id=operation_id,
        )
        return cast(list[TextContent], result)
