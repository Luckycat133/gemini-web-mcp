"""
媒体生成 MCP 工具
"""

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Optional, cast

from ..adapters.mcp_sdk import MCPServer, TextContent

from gemini_webapi.types.video import GeneratedMedia

from ..adapters import append_artifact_block, attach_domain_result, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_media_request
from ..domain import Artifact, ArtifactKind, ArtifactResultData, ArtifactState, DomainErrorCode, DomainResult
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from ..services import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_result,
    classify_media_artifact_state,
    detect_image_mime_type,
    extract_response_artifacts,
    merge_artifacts,
    media_artifacts,
    media_operation_timeout,
    observed_backend_from_response,
    response_chat_id,
)
from ..thinking_client import client_request_timeout
from .annotations import MUTATES_REMOTE
from .utils import parse_response, validate_optional_image_path

logger = logging.getLogger(__name__)


def _safe_media_filename(prompt: str, media_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", prompt.strip())[:48].strip("._-")
    return stem or media_type


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


def _unused_media_filename(destination: Path, filename: str) -> str:
    """Avoid overwriting an existing named artifact on repeated requests."""
    candidate = Path(filename)
    if not (destination / candidate).exists():
        return filename
    suffix = candidate.suffix
    stem = candidate.stem if suffix else candidate.name
    index = 2
    while (destination / f"{stem}_{index}{suffix}").exists():
        index += 1
    return f"{stem}_{index}{suffix}"


def _normalize_saved_image_extension(path: str) -> str:
    """Keep a saved image's filename consistent with its actual bytes."""
    source = Path(path)
    mime_type = detect_image_mime_type(source)
    suffixes = {
        "image/jpeg": (".jpg", ".jpeg"),
        "image/png": (".png",),
        "image/gif": (".gif",),
        "image/webp": (".webp",),
        "image/bmp": (".bmp",),
        "image/tiff": (".tif", ".tiff"),
        "image/heic": (".heic",),
        "image/heif": (".heif",),
    }.get(mime_type or "")
    if suffixes is None or source.suffix.lower() in suffixes:
        return path
    candidate = source.with_suffix(suffixes[0])
    counter = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}_{counter}{suffixes[0]}")
        counter += 1
    try:
        source.rename(candidate)
    except OSError as error:
        logger.warning("Could not align saved image extension with MIME: %r", error)
        return path
    return str(candidate)


async def _save_generated_media(
    response,
    *,
    media_type: str,
    output_dir: Optional[str],
    filename: Optional[str],
    prompt: str,
    media_items: Optional[list] = None,
    requested_model: str,
    request_model: str,
    effective_backend: str,
    observed_backend: str | None,
    source_chat_id: str | None,
    artifact_sink: list[Artifact] | None = None,
) -> "_MediaSaveOutcome":
    media_items = media_items if media_items is not None else _response_media_items(response, media_type)
    if not media_items:
        return _MediaSaveOutcome()

    destination = Path(output_dir or "generated_media").expanduser()
    saved_lines: list[str] = []
    saved_artifacts: list[Artifact] = []
    failures: list[str] = []
    for index, media in enumerate(media_items, 1):
        base_name = filename or _safe_media_filename(prompt, media_type)
        if len(media_items) > 1:
            name_path = Path(base_name)
            base_name = (
                f"{name_path.stem}_{index}{name_path.suffix}"
                if name_path.suffix
                else f"{base_name}_{index}"
            )
        downloads: list[str | None] = [None]
        if media_type == "music":
            downloads = [
                kind for kind, attribute in (("audio", "mp3_url"), ("video", "url"))
                if getattr(media, attribute, None)
            ] or ["audio"]
        elif media_type == "video" and hasattr(media, "mp3_url"):
            downloads = ["video"]
        for download_type in downloads:
            # Upstream keeps an explicit suffix for both downloads. Separate
            # names prevent audio/video from racing on the same output path.
            output_name = (
                str(Path(base_name).with_suffix(".mp3" if download_type == "audio" else ".mp4"))
                if download_type else base_name
            )
            output_name = _unused_media_filename(destination, output_name)
            save_kwargs = {"path": str(destination), "filename": output_name, "verbose": False}
            if download_type:
                save_kwargs["download_type"] = download_type
            try:
                raw_saved = await media.save(**save_kwargs)
            except Exception as error:
                logger.error(
                    "artifact save failed media_type=%s index=%s error=%r", media_type, index, error, exc_info=True
                )
                failures.append(f"{type(error).__name__}:save")
                continue
            saved = _normalize_saved_paths(raw_saved, download_type or media_type)
            if download_type and saved is not None:
                # GeneratedMedia also returns image thumbnails; those are not
                # evidence that an audio/video stream was saved.
                saved = {kind: path for kind, path in saved.items() if kind == download_type}
            if saved is None:
                failures.append(f"item_{index}:unsupported_save_result")
                continue
            for kind, path in sorted(saved.items()):
                if kind.endswith("_thumbnail"):
                    continue
                if not path:
                    failures.append(f"{kind}:no_saved_path")
                    continue
                artifact_kind = _saved_artifact_kind(media_type, kind)
                if artifact_kind == ArtifactKind.IMAGE:
                    path = _normalize_saved_image_extension(path)
                uri = _saved_artifact_uri(media, artifact_kind)
                # Decoding/probing can block; moving it off the event loop also
                # lets the operation deadline bound verification.
                artifact = await asyncio.to_thread(
                    artifact_from_local_path,
                    artifact_kind,
                    path,
                    title=getattr(media, "title", None),
                    uri=uri,
                    source_chat_id=source_chat_id,
                    requested_backend=requested_model,
                    request_model=request_model,
                    effective_backend=effective_backend,
                    observed_backend=observed_backend,
                )
                saved_artifacts.append(artifact)
                if artifact_sink is not None:
                    artifact_sink.append(artifact)
                if artifact.state == ArtifactState.FAILED:
                    failures.append(f"{kind}:verification")
                else:
                    line = f"{kind}: {path}"
                    duration = artifact.duration_seconds
                    if duration is not None:
                        line += f" ({duration:.2f}s)"
                    saved_lines.append(line)
            if not saved:
                failures.append(f"item_{index}:no_saved_path")
    return _MediaSaveOutcome(
        lines=tuple(saved_lines),
        artifacts=tuple(saved_artifacts),
        failures=tuple(failures),
    )


def _response_media_items(response, media_type: str) -> list:
    if media_type == "image":
        return list(getattr(response, "images", None) or [])
    if media_type == "video":
        videos = getattr(response, "videos", None)
        if videos:
            return list(videos)
        return [item for item in getattr(response, "media", None) or [] if getattr(item, "url", None)]
    return list(getattr(response, "media", None) or [])


def _normalize_saved_paths(saved, media_type: str) -> dict[str, str | None] | None:
    if isinstance(saved, (str, Path)):
        return {media_type: str(saved)}
    if isinstance(saved, Mapping):
        normalized: dict[str, str | None] = {}
        for kind, path in saved.items():
            if path is None:
                normalized[str(kind)] = None
            elif isinstance(path, (str, Path)):
                normalized[str(kind)] = str(path)
            else:
                return None
        return normalized
    return None


@dataclass(frozen=True)
class _MediaSaveOutcome:
    lines: tuple[str, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    failures: tuple[str, ...] = ()


def _saved_artifact_kind(media_type: str, saved_kind: str) -> ArtifactKind:
    normalized = saved_kind.lower()
    if normalized in {"video", "mp4", "webm"}:
        return ArtifactKind.VIDEO
    if normalized in {"audio", "mp3", "music", "wav", "m4a"}:
        return ArtifactKind.AUDIO
    if normalized in {"image", "png", "jpg", "jpeg", "webp"}:
        return ArtifactKind.IMAGE
    if media_type == "music":
        return ArtifactKind.AUDIO
    if media_type == "image":
        return ArtifactKind.IMAGE
    return ArtifactKind.VIDEO


def _saved_artifact_uri(media, kind: ArtifactKind) -> str | None:
    if kind == ArtifactKind.AUDIO:
        return getattr(media, "mp3_url", None) or getattr(media, "url", None)
    return getattr(media, "url", None)


def _media_from_music_card(card_data: Mapping[str, str], *, client, cid: str) -> Optional[GeneratedMedia]:
    title = card_data.get("title") or "[Media]"
    is_mp4 = title.endswith(".mp4")
    media_url = card_data.get("url", "")
    mp3_url = "" if is_mp4 else media_url
    mp4_url = media_url if is_mp4 else ""
    if not (mp3_url or mp4_url):
        return None
    return GeneratedMedia(
        url=mp4_url,
        thumbnail="",
        mp3_url=mp3_url,
        mp3_thumbnail="",
        title=title,
        cid=cid,
        rid=card_data.get("rid", ""),
        rcid=card_data.get("rcid", ""),
        client_ref=client,
        proxy=getattr(client, "proxy", None),
    )


async def _fetch_music_media_from_chat(client, cid: str) -> list[GeneratedMedia]:
    if not cid or not hasattr(client, "_batch_execute"):
        return []

    contract = get_contract("media.music_chat")
    response = await execute_contract(client, contract.key, chat_id=cid)
    envelope = parse_rpc_envelope(response.text, contract.rpc_id)
    media_items: list[GeneratedMedia] = []
    for body in envelope.bodies:
        parsed = parse_contract_body(contract, body, reject_code=envelope.reject_code)
        if parsed.status not in {"success", "empty"}:
            logger.warning("music read-back shape=%s for contract=%s", parsed.status, contract.key)
            continue
        for card_data in parsed.value or ():
            media = _media_from_music_card(card_data, client=client, cid=cid)
            if media:
                media_items.append(media)
    return media_items


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


_GENERATION_PROMPTS = {
    "image": "Generate an image. Prompt: {prompt}",
    "video": "Generate a video using Gemini's video generation capability. Prompt: {prompt}",
    "music": "Create music/audio using Gemini's music generation capability. Prompt: {prompt}",
}


def _generation_prompt(job: _MediaJob) -> str:
    return _GENERATION_PROMPTS[job.media_type].format(prompt=job.prompt)


def _media_failure_response(
    error: Exception, job: _MediaJob, message: str, *,
    response: object | None = None, observed_artifacts: tuple[Artifact, ...] = (),
) -> list[TextContent]:
    artifacts = media_artifacts(merge_artifacts(
        extract_response_artifacts(
            response,
            requested_backend=job.requested_model,
            request_model=job.request_model,
            effective_backend=job.backend_label,
        ),
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
    content = append_artifact_block([TextContent(type="text", text=message)], artifacts)
    return attach_domain_result(content, result, use_result_data=True)


async def _recover_music_media(client: object, response: object, job: _MediaJob) -> tuple[list, tuple[Artifact, ...]]:
    if job.media_type != "music" or (getattr(response, "media", None) or []):
        return [], ()
    remote_chat_id = response_chat_id(response)
    try:
        recovered_media = await _fetch_music_media_from_chat(client, remote_chat_id or "")
    except Exception as e:
        logger.warning("无法从远端 chat 恢复音乐媒体 URL: %s", e)
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
    artifact_sink: list[Artifact] | None = None,
) -> _MediaOutcome:
    parsed = parse_response(response, job.effective_alias)
    remote_chat_id = response_chat_id(response)
    observed_backend = observed_backend_from_response(response)
    remote_artifacts = media_artifacts(extract_response_artifacts(
        response,
        media_type=job.media_type,
        requested_backend=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        observed_backend=observed_backend,
    ), job.media_type)
    if artifact_sink is not None:
        artifact_sink.extend(remote_artifacts)
    recovered_media, recovered_artifacts = await _recover_music_media(client, response, job)
    if artifact_sink is not None:
        artifact_sink.extend(recovered_artifacts)
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
        artifact_sink=artifact_sink,
    )
    if save_outcome.lines:
        parsed[0].text = f"{parsed[0].text}\n\nSaved files:\n" + "\n".join(save_outcome.lines)
    artifacts = media_artifacts(merge_artifacts(
        remote_artifacts,
        recovered_artifacts,
        save_outcome.artifacts,
    ), job.media_type)
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
        ("Inspect the retained upstream chat. " if remote_chat_id else "No upstream chat ID was observed. ")
        + "Gemini Omni video mode is available at https://gemini.google.com/videos; "
        "a generic chat prompt does not prove that mode was selected. "
        "Do not duplicate the request until its state is known."
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
        empty_message = f"⚠️ {job.media_type} 请求已返回，但没有返回可用的 {job.media_type} 产物。"
        next_step = (
            "当前通用聊天请求未证实进入 Gemini Omni 视频模式。"
            + ("请先检查已保留的上游聊天；" if data.source_chat_id else "未取得上游聊天 ID；")
            + "如需生成视频，可使用网页专用视频入口 https://gemini.google.com/videos。"
            if job.media_type == "video"
            else "请检查上游聊天状态，再决定是否重试。"
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
    ) -> list[TextContent]:
        """媒体生成"""
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
        observed_artifacts: list[Artifact] = []
        try:
            # One budget covers generation, chat recovery, downloads (including
            # upstream HTTP 206 polling), and local verification.
            async with asyncio.timeout(effective_timeout):
                client = get_gemini_client()
                await initialize_client()
                await cleanup_due_remote_chats(client)
                with client_request_timeout(client, effective_timeout):
                    response = await client.generate_content(
                        prompt=_generation_prompt(job),
                        files=job.files,
                        model=job.request_model,
                        thinking_level=thinking_level,
                        timeout=effective_timeout,
                    )
                    outcome = await _build_media_outcome(client, response, job, output_dir, filename, observed_artifacts)
        except asyncio.TimeoutError as error:
            if response is not None:
                schedule_remote_chat_cleanup_from_response(response, retain_chat=True, delete_after_seconds=None,
                                                          source=f"gemini_generate_media:{job.media_type}")
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
            )
        except Exception as e:
            return _media_failure_response(
                e,
                job,
                (
                    f"后端: {job.backend_label}\n"
                    f"❌ {job.media_type} 生成失败: {str(e)}\n"
                    "说明: 当前封装通过 Gemini Web 的通用 generate_content 触发媒体能力，"
                    "视频/音乐可能被上游静默中止或长时间排队。"
                ),
                response=response,
                observed_artifacts=tuple(observed_artifacts),
            )
        schedule_remote_chat_cleanup_from_response(
            response,
            retain_chat=retain_chat or outcome.artifacts_data.state in {ArtifactState.QUEUED, ArtifactState.EMPTY},
            delete_after_seconds=delete_after_seconds,
            source=f"gemini_generate_media:{job.media_type}",
        )
        content = _finalize_media_content(outcome.parsed, job, outcome.artifacts_data, outcome.save_outcome)
        return attach_domain_result(content, outcome.result, use_result_data=True)

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
        """音乐生成"""
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
