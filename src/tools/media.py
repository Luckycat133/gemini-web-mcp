"""
媒体生成 MCP 工具
"""

import asyncio
import logging
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Optional, cast

import orjson
from ..adapters.mcp_sdk import MCPServer, TextContent

from gemini_webapi.constants import GRPC
from gemini_webapi.types import RPCData
from gemini_webapi.types.video import GeneratedMedia
from gemini_webapi.utils import extract_json_from_response, get_nested_value

from ..adapters import append_artifact_block, attach_domain_result, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_media_request
from ..domain import Artifact, ArtifactKind, ArtifactResultData, ArtifactState, DomainErrorCode, DomainResult
from ..services import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_result,
    classify_artifact_state,
    extract_response_artifacts,
    merge_artifacts,
    observed_backend_from_response,
    response_chat_id,
)
from .annotations import MUTATES_REMOTE
from .utils import parse_response, validate_optional_image_path

logger = logging.getLogger(__name__)


def _safe_media_filename(prompt: str, media_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", prompt.strip())[:48].strip("._-")
    return stem or media_type


def _probe_duration(path: str) -> Optional[float]:
    if not shutil.which("ffprobe"):
        return None
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


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
            base_name = f"{base_name}_{index}"

        save_kwargs = {"path": str(destination), "filename": base_name, "verbose": False}
        if media_type == "music":
            save_kwargs["download_type"] = "both"
        try:
            raw_saved = await media.save(**save_kwargs)
        except Exception as error:
            logger.error(
                "artifact save failed media_type=%s index=%s error=%r",
                media_type,
                index,
                error,
                exc_info=True,
            )
            failures.append(f"{type(error).__name__}:save")
            continue
        saved = _normalize_saved_paths(raw_saved, media_type)
        if saved is None:
            failures.append(f"item_{index}:unsupported_save_result")
            continue
        for kind, path in sorted(saved.items()):
            if not path:
                failures.append(f"{kind}:no_saved_path")
                continue
            artifact_kind = _saved_artifact_kind(media_type, kind)
            uri = _saved_artifact_uri(media, artifact_kind)
            artifact = artifact_from_local_path(
                artifact_kind,
                path,
                title=getattr(media, "title", None),
                uri=uri,
                source_chat_id=source_chat_id,
                requested_backend=requested_model,
                request_model=request_model,
                effective_backend=effective_backend,
                observed_backend=observed_backend,
                duration_probe=_probe_duration,
            )
            saved_artifacts.append(artifact)
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
        return list(getattr(response, "images", None) or getattr(response, "media", None) or [])
    if media_type == "video":
        return list(getattr(response, "videos", None) or getattr(response, "media", None) or [])
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


def _media_from_music_card(card_data, *, client, cid: str, rid: str, rcid: str) -> Optional[GeneratedMedia]:
    title = str(get_nested_value(card_data, [1, 2], "")) or "[Media]"
    is_mp4 = title.endswith(".mp4")
    media_url = str(get_nested_value(card_data, [1, 7, 1], ""))
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
        rid=rid,
        rcid=rcid,
        client_ref=client,
        proxy=getattr(client, "proxy", None),
    )


async def _fetch_music_media_from_chat(client, cid: str) -> list[GeneratedMedia]:
    if not cid or not hasattr(client, "_batch_execute"):
        return []

    response = await client._batch_execute(
        [
            RPCData(
                rpcid=GRPC.READ_CHAT,
                payload=orjson.dumps([cid, 10, None, 1, [1], [4], None, 1]).decode("utf-8"),
            )
        ]
    )
    media_items: list[GeneratedMedia] = []
    for part in extract_json_from_response(response.text):
        part_body_str = get_nested_value(part, [2])
        if not part_body_str:
            continue
        part_body = orjson.loads(part_body_str)
        for conv_turn in get_nested_value(part_body, [0], []) or []:
            rid = get_nested_value(conv_turn, [0, 1], "")
            for candidate_data in get_nested_value(conv_turn, [3, 0], []) or []:
                rcid = get_nested_value(candidate_data, [0], "")
                music_cards = get_nested_value(candidate_data, [12, 0, "87"], []) or []
                for card_data in music_cards:
                    media = _media_from_music_card(
                        card_data,
                        client=client,
                        cid=cid,
                        rid=rid,
                        rcid=rcid,
                    )
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
    if timeout_seconds and timeout_seconds > 0:
        return timeout_seconds
    if media_type == "image":
        return 180
    return 600


def _set_client_timeouts(client, timeout_seconds: int) -> tuple[Optional[float], Optional[float]]:
    previous_timeout = getattr(client, "timeout", None)
    previous_watchdog_timeout = getattr(client, "watchdog_timeout", None)
    if previous_timeout is not None:
        client.timeout = max(float(previous_timeout), float(timeout_seconds))
    if previous_watchdog_timeout is not None:
        client.watchdog_timeout = min(
            max(float(previous_watchdog_timeout), 120.0),
            max(float(timeout_seconds), 120.0),
        )
    return previous_timeout, previous_watchdog_timeout


def _restore_client_timeouts(
    client,
    previous_timeout: Optional[float],
    previous_watchdog_timeout: Optional[float],
) -> None:
    if previous_timeout is not None:
        client.timeout = previous_timeout
    if previous_watchdog_timeout is not None:
        client.watchdog_timeout = previous_watchdog_timeout


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


def _media_failure_response(error: Exception, job: _MediaJob, message: str) -> list[TextContent]:
    failure_data = ArtifactResultData(
        state=ArtifactState.FAILED,
        requested_model=job.requested_model,
        request_model=job.request_model,
        effective_backend=job.backend_label,
        input_artifacts=job.input_artifacts,
        media_type=job.media_type,
    )
    result = artifact_exception_result(
        error,
        failure_data,
        logger=logger,
        operation=f"gemini_generate_media:{job.media_type}",
    )
    return attach_domain_result([TextContent(type="text", text=message)], result, use_result_data=True)


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
) -> _MediaOutcome:
    parsed = parse_response(response, job.effective_alias)
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
        remote_artifacts,
        recovered_artifacts,
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
    artifact_state = classify_artifact_state(response, artifacts)
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
    result = artifact_result(data, save_failures=save_outcome.failures)
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
        content[0].text = (
            f"{content[0].text}\n\n"
            f"{empty_message}"
            "请换更明确的生成提示词，或稍后重试。"
        )
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
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return _invalid_image_response(image_error)

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
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
        previous_timeout, previous_watchdog_timeout = _set_client_timeouts(
            client,
            effective_timeout,
        )
        try:
            response = await asyncio.wait_for(
                client.generate_content(
                    prompt=_generation_prompt(job),
                    files=job.files,
                    model=job.request_model,
                    thinking_level=thinking_level,
                    timeout=effective_timeout,
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
            )
        finally:
            _restore_client_timeouts(
                client,
                previous_timeout,
                previous_watchdog_timeout,
            )

        outcome = await _build_media_outcome(client, response, job, output_dir, filename)
        schedule_remote_chat_cleanup_from_response(
            response,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=f"gemini_generate_media:{job.media_type}",
        )
        content = _finalize_media_content(outcome.parsed, job, outcome.artifacts_data, response, outcome.save_outcome)
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
