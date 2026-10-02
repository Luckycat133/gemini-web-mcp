"""Shared construction and retention decisions for media requests."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, TypeVar

from gemini_webapi.types.video import GeneratedMedia, GeneratedVideo
from gemini_webapi.types.image import GeneratedImage, Image
from gemini_webapi.exceptions import APIError

from ..domain import (
    Artifact,
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    ArtifactVerificationStatus,
    CleanupObservation,
    DomainErrorCode,
    DomainResult,
    OperationState,
)
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from ..thinking_client import MediaRequestObservation, ThinkingLevelGeminiClient
from .artifacts import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_result,
    detect_image_mime_type,
    extract_response_artifacts,
    is_response_queued,
    media_artifacts,
    media_operation_timeout,
    merge_artifacts,
    observed_backend_from_response,
    response_chat_id,
)


logger = logging.getLogger(__name__)


_MEDIA_PROMPTS = {
    "image": "Generate an image. Prompt: {prompt}",
    "image_edit": "Edit this image: {prompt}",
    "video": "Generate a video using Gemini's video generation capability. Prompt: {prompt}",
    "music": "Create music/audio using Gemini's music generation capability. Prompt: {prompt}",
}


class MediaGenerationError(RuntimeError):
    """A public-safe SDK API failure, without an unsupported quota claim."""

    def __init__(self, code: DomainErrorCode, message: str, failure_kind: str) -> None:
        self.code = code
        self.failure_kind = failure_kind
        super().__init__(message)


def _safe_generation_error(error: APIError) -> MediaGenerationError:
    # These phrases are SDK-produced messages in both supported 2.0/2.1
    # versions. Never copy an arbitrary HTTP body/exception into the result.
    text = str(error).lower()
    if "failed to parse response body from google" in text:
        return MediaGenerationError(
            DomainErrorCode.UPSTREAM_CHANGED,
            "Gemini Web returned a media response the supported SDK could not parse.",
            "response_parse_failed",
        )
    if "silently aborted by google" in text:
        return MediaGenerationError(
            DomainErrorCode.UPSTREAM_REJECTED,
            "The media request ended without a complete result; the SDK reported a possible upstream interruption.",
            "request_interrupted",
        )
    return MediaGenerationError(
        DomainErrorCode.UPSTREAM_REJECTED,
        "Gemini Web failed to complete the media request.",
        "upstream_api_failed",
    )


def normalize_media_exception(error: Exception) -> Exception:
    """Keep SDK API failures safe across initialization and recovery phases."""
    return _safe_generation_error(error) if isinstance(error, APIError) else error


@dataclass
class MediaGenerationAttempt:
    """One shared media attempt, retaining only its observed new-chat CID."""

    observation: MediaRequestObservation = field(default_factory=MediaRequestObservation)

    async def generate(self, client: Any, **request: Any) -> Any:
        if request.get("chat") is not None:
            raise ValueError("Owned media generation cannot use an existing chat.")
        if isinstance(client, ThinkingLevelGeminiClient):
            request = {**request, "media_observation": self.observation}
        try:
            return await client.generate_content(**request)
        except APIError as error:
            raise _safe_generation_error(error) from None

    @property
    def recovery_response(self) -> Any | None:
        if self.observation.chat_id is None:
            return None
        # This is evidence of an allocated chat, not of a completed/empty
        # generation. Callers attach it only to failures and always retain it.
        return SimpleNamespace(
            metadata=[self.observation.chat_id], text="", images=[], videos=[], media=[],
        )


def media_exception_result(
    error: BaseException,
    data: ArtifactResultData,
    *,
    logger: logging.Logger,
    operation: str,
) -> DomainResult[ArtifactResultData]:
    """Use the stable domain vocabulary with specific safe API-failure evidence."""
    if isinstance(error, APIError):
        error = normalize_media_exception(error)
    result = artifact_exception_result(error, data, logger=logger, operation=operation)
    if not isinstance(error, MediaGenerationError) or result.error is None:
        return result
    return replace(
        result,
        error=replace(
            result.error, code=error.code, message=str(error), retryable=False,
            suggested_action=(
                "Inspect the retained chat and observed artifacts before deciding whether to make another request."
                if data.source_chat_id
                else "No chat ID was observed. Check Gemini Web request status before deciding whether to make another request."
            ),
        ),
        meta=replace(result.meta, details={
            **result.meta.details, "upstream_failure_kind": error.failure_kind,
            "request_chat_observed": data.source_chat_id is not None,
        }),
    )


def _is_created_image(item: Any) -> bool:
    """Use the SDK's provenance distinction while accepting adapter fixtures."""
    return not isinstance(item, Image) or isinstance(item, GeneratedImage)


class _CreationResponse:
    """Filter search reference images without mutating the original output."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.images = [item for item in getattr(response, "images", None) or [] if _is_created_image(item)]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


def media_creation_response(response: Any) -> Any:
    """Provide a creation-only view; chat and understanding keep WebImages."""
    return response if isinstance(response, _CreationResponse) else _CreationResponse(response)


def media_generation_kwargs(
    prompt: str,
    media_type: str,
    *,
    model: str | None,
    thinking_level: str,
    files: Sequence[str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Build the same supported Web request for every media surface.

    Only parameters understood by our client belong here. Upstream forwards
    unknown keywords to its HTTP transport; they cannot select a native tool.
    """
    if media_type not in _MEDIA_PROMPTS:
        raise ValueError(f"Unsupported media type: {media_type}")
    request = {
        "prompt": _MEDIA_PROMPTS[media_type].format(prompt=prompt),
        "files": list(files) if files else None,
        "model": model,
        "thinking_level": thinking_level,
        "timeout": media_operation_timeout(media_type, timeout_seconds),
    }
    if media_type in {"image", "image_edit"}:
        request["media_mode"] = "image"
    elif media_type == "music":
        request["media_mode"] = "music"
    return request


def media_requires_recovery(
    data: ArtifactResultData,
    *,
    response: Any = None,
    save_failed: bool = False,
    operation_failed: bool = False,
) -> bool:
    """Keep a generated chat until its relevant outputs are verified locally."""
    if save_failed or operation_failed or is_response_queued(response):
        return True
    outputs = media_artifacts(data.artifacts, data.media_type or "")
    if data.state == ArtifactState.EMPTY:
        # An absent required stream can coexist with an unsaved auxiliary
        # stream (for example a music visualization without audio).
        return bool(outputs)
    if data.state != ArtifactState.LOCAL:
        return True
    return not outputs or any(
        artifact.state != ArtifactState.LOCAL
        or not artifact.local_path
        or artifact.verification.status != ArtifactVerificationStatus.VERIFIED
        for artifact in outputs
    )


def media_artifact_result(
    data: ArtifactResultData,
    *,
    response: Any,
    save_failures: Sequence[str] = (),
    empty_suggested_action: str | None = None,
    empty_retryable: bool = True,
) -> DomainResult[ArtifactResultData]:
    """Keep ready artifacts usable without declaring a queued request complete."""
    result = artifact_result(
        data, save_failures=save_failures,
        empty_suggested_action=empty_suggested_action, empty_retryable=empty_retryable,
    )
    if result.ok and is_response_queued(response):
        return replace(result, meta=replace(
            result.meta, operation_state=OperationState.QUEUED,
            details={**result.meta.details, "upstream_queued": True},
        ))
    return result


class MusicRecoveryError(RuntimeError):
    """Unavailable music read-back is different from a verified empty result."""

    def __init__(self, code: DomainErrorCode, message: str) -> None:
        self.code = code.value
        super().__init__(message)


def _safe_recovery_error(error: Exception) -> MusicRecoveryError:
    # Do not copy exception text: HTTP/RPC exceptions may contain account data.
    if isinstance(error, TimeoutError):
        return MusicRecoveryError(DomainErrorCode.TIMED_OUT, "Music read-back timed out.")
    if isinstance(error, ConnectionError):
        return MusicRecoveryError(DomainErrorCode.NETWORK_ERROR, "Music read-back network operation failed.")
    return MusicRecoveryError(DomainErrorCode.INTERNAL_ERROR, "Music read-back recovery failed.")


_T = TypeVar("_T")


def with_media_cleanup(
    result: DomainResult[_T], observation: CleanupObservation,
) -> DomainResult[_T]:
    """Attach public cleanup evidence without changing media availability.

    Domain serialization excludes the observation's private source and timing
    fields, so the adapters need no independent serialization rules.
    """
    return replace(result, meta=replace(result.meta, details={
        **result.meta.details,
        "cleanup": observation,
    }))



def safe_media_filename(prompt: str, media_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", prompt.strip())[:48].strip("._-")
    return stem or media_type



@dataclass(frozen=True)
class _FilenameReservation:
    path: Path
    identity: tuple[int, int]

    def release(self, *, keep_output: bool) -> None:
        """Remove only this reservation or an unreturned partial download."""
        try:
            status = self.path.stat()
            if (status.st_dev, status.st_ino) == self.identity and (not keep_output or status.st_size == 0):
                self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.warning("Could not release media filename reservation: %r", error)


def _reserve_media_filename(destination: Path, filename: str) -> _FilenameReservation:
    """Claim a path atomically across tasks, event loops, and processes."""
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    name = Path(filename)
    index = 1
    while True:
        candidate = destination / (filename if index == 1 else f"{name.stem}_{index}{name.suffix}")
        try:
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            index += 1
            continue
        try:
            status = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        return _FilenameReservation(candidate, (status.st_dev, status.st_ino))


async def _save_reserved_media(media: Any, destination: Path, filename: str, kwargs: dict[str, Any]) -> Any:
    reservation = _reserve_media_filename(destination, filename)
    keep_output = False
    try:
        # The SDK writes unrequested thumbnails to additional, unreserved
        # names. Suppress those downloads on a request-local model copy; never
        # mutate a returned object shared with another surface or task.
        if isinstance(media, GeneratedVideo):
            updates = {"thumbnail": ""}
            if isinstance(media, GeneratedMedia):
                updates["mp3_thumbnail"] = ""
            media = media.model_copy(update=updates)
        saved = await media.save(**{**kwargs, "filename": reservation.path.name})
        paths = _normalize_saved_paths(saved, "media")
        keep_output = bool(paths and any(
            path and Path(path).expanduser().resolve() == reservation.path for path in paths.values()
        ))
        return saved
    finally:
        reservation.release(keep_output=keep_output)



def normalize_saved_image_extension(path: str) -> str:
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
    try:
        reservation = _reserve_media_filename(source.parent, source.with_suffix(suffixes[0]).name)
    except OSError as error:
        logger.warning("Could not align saved image extension with MIME: %r", error)
        return path
    try:
        source.rename(reservation.path)
    except OSError as error:
        reservation.release(keep_output=False)
        logger.warning("Could not align saved image extension with MIME: %r", error)
        return path
    reservation.release(keep_output=True)
    return str(reservation.path)



async def save_generated_media(
    response,
    *,
    media_type: str,
    output_dir: Optional[str],
    filename: Optional[str],
    prompt: str,
    media_items: Optional[list] = None,
    requested_model: str,
    request_model: str | None,
    effective_backend: str | None,
    observed_backend: str | None,
    source_chat_id: str | None,
    artifact_sink: list[Artifact] | None = None,
) -> "MediaSaveOutcome":
    media_items = media_items if media_items is not None else response_media_items(response, media_type)
    if not media_items:
        return MediaSaveOutcome()

    destination = Path(output_dir or "generated_media").expanduser()
    saved_lines: list[str] = []
    saved_artifacts: list[Artifact] = []
    failures: list[str] = []
    for index, media in enumerate(media_items, 1):
        # GeneratedImage.save mutates its URL when requesting full-size bytes.
        # Retain the URI observed before downloading as the stable identity
        # used by the remote observation and its verified local counterpart.
        source_uris = {kind: _saved_artifact_uri(media, kind) for kind in (
            ArtifactKind.IMAGE, ArtifactKind.VIDEO, ArtifactKind.AUDIO,
        )}
        base_name = filename or safe_media_filename(prompt, media_type)
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
            if not Path(output_name).suffix:
                output_name += ".png" if media_type in {"image", "image_edit"} else ".mp4"
            save_kwargs = {"path": str(destination), "filename": output_name, "verbose": False}
            if download_type:
                save_kwargs["download_type"] = download_type
            try:
                raw_saved = await _save_reserved_media(media, destination, output_name, save_kwargs)
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
                    path = normalize_saved_image_extension(path)
                uri = source_uris[artifact_kind]
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
    return MediaSaveOutcome(
        lines=tuple(saved_lines),
        artifacts=tuple(saved_artifacts),
        failures=tuple(failures),
    )



def response_media_items(response, media_type: str) -> list:
    if media_type in {"image", "image_edit"}:
        return list(media_creation_response(response).images)
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
class MediaSaveOutcome:
    lines: tuple[str, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class MediaMaterialization:
    artifacts: tuple[Artifact, ...]
    saved: MediaSaveOutcome


async def materialize_generated_media(
    client: Any,
    response: Any,
    *,
    media_type: str,
    prompt: str,
    requested_model: str,
    request_model: str | None,
    effective_backend: str | None,
    output_dir: str | None = None,
    filename: str | None = None,
    artifact_sink: list[Artifact] | None = None,
    music_fetcher: Callable[..., Awaitable[list]] | None = None,
) -> MediaMaterialization:
    """Recover returned music cards, then save and verify the requested outputs.

    The caller owns the total deadline. The sink retains all observed URIs and
    verified paths if that deadline expires during a later download or probe.
    """
    response = media_creation_response(response)
    cid = response_chat_id(response)
    backend = observed_backend_from_response(response)
    remote = media_artifacts(extract_response_artifacts(
        response,
        media_type=media_type,
        requested_backend=requested_model,
        request_model=request_model,
        effective_backend=effective_backend,
        observed_backend=backend,
    ), media_type)
    if artifact_sink is not None:
        artifact_sink.extend(remote)
    recovered: tuple[Artifact, ...] = ()
    recovered_media: list = []
    if (
        media_type == "music"
        and not (getattr(response, "media", None) or [])
        and not any(artifact.kind == ArtifactKind.AUDIO and artifact.uri for artifact in remote)
    ):
        try:
            recovered_media = await (music_fetcher or fetch_music_media_from_chat)(client, cid or "")
        except MusicRecoveryError:
            raise
        except Exception as error:
            raise _safe_recovery_error(error) from None
        recovered = media_artifacts(extract_response_artifacts(
            SimpleNamespace(images=[], videos=[], media=recovered_media, metadata=[cid] if cid else []),
            media_type=media_type,
            requested_backend=requested_model,
            request_model=request_model,
            effective_backend=effective_backend,
            observed_backend=backend,
        ), media_type)
        if artifact_sink is not None:
            artifact_sink.extend(recovered)
    saved = await save_generated_media(
        response,
        media_type=media_type,
        prompt=prompt,
        requested_model=requested_model,
        request_model=request_model,
        effective_backend=effective_backend,
        observed_backend=backend,
        source_chat_id=cid,
        output_dir=output_dir,
        filename=filename,
        media_items=recovered_media or None,
        artifact_sink=artifact_sink,
    )
    return MediaMaterialization(
        media_artifacts(merge_artifacts(remote, recovered, saved.artifacts), media_type), saved,
    )



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
    if media_type in {"image", "image_edit"}:
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



async def fetch_music_media_from_chat(client, cid: str) -> list[GeneratedMedia]:
    if not cid or not hasattr(client, "_batch_execute"):
        raise MusicRecoveryError(DomainErrorCode.UPSTREAM_CHANGED, "Music read-back transport is unavailable.")

    contract = get_contract("media.music_chat")
    response = await execute_contract(client, contract.key, chat_id=cid)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        raise MusicRecoveryError(DomainErrorCode.UPSTREAM_CHANGED, "Music read-back HTTP response shape changed.")
    if status != 200:
        if status == 429:
            raise MusicRecoveryError(DomainErrorCode.RATE_LIMITED, "Music read-back reached the upstream rate limit.")
        if isinstance(status, int) and status >= 500:
            raise MusicRecoveryError(DomainErrorCode.NETWORK_ERROR, "Music read-back network operation failed.")
        raise MusicRecoveryError(DomainErrorCode.UPSTREAM_REJECTED, "Music read-back upstream rejected the operation.")
    envelope = parse_rpc_envelope(response.text, contract.rpc_id)
    if envelope.reject_code is not None:
        raise MusicRecoveryError(DomainErrorCode.UPSTREAM_REJECTED, "Music read-back upstream rejected the operation.")
    if not envelope.parsed or not envelope.bodies:
        raise MusicRecoveryError(DomainErrorCode.UPSTREAM_CHANGED, "Music read-back response shape changed.")
    media_items: list[GeneratedMedia] = []
    for body in envelope.bodies:
        parsed = parse_contract_body(contract, body, reject_code=envelope.reject_code)
        if parsed.status not in {"success", "empty"}:
            raise MusicRecoveryError(DomainErrorCode.UPSTREAM_CHANGED, "Music read-back response shape changed.")
        for card_data in parsed.value or ():
            media = _media_from_music_card(card_data, client=client, cid=cid)
            if media:
                media_items.append(media)
    return media_items
