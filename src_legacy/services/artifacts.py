"""Artifact extraction, identity, verification, and result helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import mimetypes
import shutil
import subprocess
import wave
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..domain import (
    Artifact,
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    ArtifactVerification,
    ArtifactVerificationStatus,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    result_from_exception,
)


def artifact_id(
    kind: ArtifactKind,
    *,
    uri: str | None = None,
    local_path: str | None = None,
    source_chat_id: str | None = None,
    title: str | None = None,
    ordinal: int = 0,
) -> str:
    """Build a deterministic public identity from the strongest known location."""
    if uri:
        identity = f"uri:{uri.strip()}"
    elif local_path:
        identity = f"file:{_normalized_local_path(local_path)}"
    else:
        identity = f"fallback:{source_chat_id or ''}:{title or ''}:{ordinal}"
    digest = hashlib.sha256(f"{kind.value}|{identity}".encode()).hexdigest()[:24]
    return f"artifact_{digest}"


def response_chat_id(response: Any) -> str | None:
    cid = getattr(response, "cid", None)
    if isinstance(cid, str) and cid.startswith("c_"):
        return cid
    metadata = getattr(response, "metadata", None)
    if isinstance(metadata, list) and metadata:
        cid = metadata[0]
        if isinstance(cid, str) and cid.startswith("c_"):
            return cid
    return None


def observed_backend_from_response(response: Any) -> str | None:
    for name in ("observed_backend", "backend", "generator"):
        value = getattr(response, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for collection_name in ("images", "videos", "media"):
        for item in getattr(response, collection_name, None) or ():
            for name in ("observed_backend", "backend", "generator"):
                value = getattr(item, name, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def artifact_from_remote(
    kind: ArtifactKind,
    uri: str,
    *,
    title: str | None = None,
    source_chat_id: str | None = None,
    requested_backend: str | None = None,
    request_model: str | None = None,
    effective_backend: str | None = None,
    observed_backend: str | None = None,
    size_bytes: int | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_seconds: float | None = None,
    verification_method: str = "response_uri_observed",
) -> Artifact:
    return Artifact(
        id=artifact_id(kind, uri=uri),
        kind=kind,
        state=ArtifactState.REMOTE,
        title=title,
        uri=uri,
        mime_type=_guess_mime_type(uri),
        size_bytes=_positive_int(size_bytes),
        width=_positive_int(width),
        height=_positive_int(height),
        duration_seconds=_positive_float(duration_seconds),
        source_chat_id=source_chat_id,
        requested_backend=requested_backend,
        request_model=request_model,
        effective_backend=effective_backend,
        observed_backend=observed_backend,
        verification=ArtifactVerification(
            ArtifactVerificationStatus.UNVERIFIED,
            methods=(verification_method,),
        ),
    )


def _unavailable_local_artifact(
    *,
    identity: str,
    kind: ArtifactKind,
    path: str,
    title: str | None,
    uri: str | None,
    source_chat_id: str | None,
    requested_backend: str | None,
    request_model: str | None,
    effective_backend: str | None,
    observed_backend: str | None,
    method: str,
) -> Artifact:
    return Artifact(
        id=identity,
        kind=kind,
        state=ArtifactState.FAILED,
        title=title,
        uri=uri,
        local_path=path,
        mime_type=_guess_mime_type(path),
        source_chat_id=source_chat_id,
        requested_backend=requested_backend,
        request_model=request_model,
        effective_backend=effective_backend,
        observed_backend=observed_backend,
        verification=ArtifactVerification(
            ArtifactVerificationStatus.FAILED,
            methods=(method,),
        ),
    )


def _verify_image(path: str, mime_type: str, methods: list[str]) -> tuple[ArtifactVerificationStatus, int | None, int | None]:
    try:
        from PIL import Image
    except ImportError:
        methods.append("image_decoder_unavailable")
        return ArtifactVerificationStatus.UNVERIFIED, None, None
    Image.init()
    if mime_type in {"image/heic", "image/heif"} and not ({"HEIF", "HEIC"} & Image.OPEN.keys()):
        methods.append("image_decoder_unavailable")
        return ArtifactVerificationStatus.UNVERIFIED, None, None
    try:
        with Image.open(path) as image:
            image.verify()
        # verify() checks the container; load() also checks the encoded pixels.
        with Image.open(path) as image:
            image.load()
            width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("empty image dimensions")
    except (OSError, ValueError, SyntaxError):
        methods.append("image_decode_failed")
        return ArtifactVerificationStatus.FAILED, None, None
    methods.extend(("image_decoded", "image_dimensions"))
    return ArtifactVerificationStatus.VERIFIED, int(width), int(height)


def _probe_av_metadata(path: str) -> dict[str, Any] | None:
    """Return parsed stream evidence, or None when no decoder is installed."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration,format_name",
             "-of", "json", path],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if probe.returncode != 0:
            return {}
        payload = json.loads(probe.stdout)
        return payload if isinstance(payload, dict) else {}
    except (OSError, subprocess.TimeoutExpired):
        return None
    except ValueError:
        return {}


def _av_signature(path: str) -> str | None:
    try:
        with Path(path).open("rb") as media_file:
            header = media_file.read(16)
    except OSError:
        return None
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio/wav"
    if header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return "video/x-msvideo"
    if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if header[4:8] == b"ftyp":
        return "video/mp4"
    if header.startswith(b"OggS"):
        return "application/ogg"
    if header.startswith(b"fLaC"):
        return "audio/flac"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return None


def _verify_av(
    kind: ArtifactKind, path: str, methods: list[str],
) -> tuple[ArtifactVerificationStatus, str | None, int | None, int | None, float | None]:
    image_mime = detect_image_mime_type(path)
    if image_mime is not None:
        methods.append("media_kind_mismatch")
        return ArtifactVerificationStatus.FAILED, image_mime, None, None, None
    mime = _av_signature(path)
    if mime == "audio/wav":
        try:
            with wave.open(path, "rb") as audio:
                frame_count = audio.getnframes()
                frame_size = audio.getnchannels() * audio.getsampwidth()
                rate = audio.getframerate()
                actual_bytes = 0
                while chunk := audio.readframes(65536):
                    actual_bytes += len(chunk)
                if kind != ArtifactKind.AUDIO or rate <= 0 or frame_count <= 0 or actual_bytes != frame_count * frame_size:
                    raise ValueError("invalid WAV stream")
                wav_duration = frame_count / rate
            methods.extend(("audio_stream_decoded", "duration_probe"))
            return ArtifactVerificationStatus.VERIFIED, mime, None, None, wav_duration
        except (OSError, EOFError, wave.Error, ValueError):
            methods.append("media_decode_failed")
            return ArtifactVerificationStatus.FAILED, mime, None, None, None

    payload = _probe_av_metadata(path)
    if payload is None:
        # A recognizable container is not proof of a playable media stream.
        if kind == ArtifactKind.VIDEO and mime is not None and mime.startswith("audio/"):
            methods.append("media_kind_mismatch")
            return ArtifactVerificationStatus.FAILED, mime, None, None, None
        methods.append("media_decoder_unavailable" if mime else "media_format_invalid")
        status = ArtifactVerificationStatus.UNVERIFIED if mime else ArtifactVerificationStatus.FAILED
        return status, mime, None, None, None
    streams = payload.get("streams", [])
    expected = "audio" if kind == ArtifactKind.AUDIO else "video"
    matching = [
        stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == expected
    ] if isinstance(streams, list) else []
    if not matching:
        methods.append("media_stream_missing")
        return ArtifactVerificationStatus.FAILED, mime, None, None, None
    stream = matching[0]
    format_data = payload.get("format", {})
    duration: float | None = None
    if isinstance(format_data, dict):
        try:
            duration = _positive_float(float(format_data.get("duration", 0)))
        except (TypeError, ValueError):
            pass
    width, height = _positive_int(stream.get("width")), _positive_int(stream.get("height"))
    if expected == "video" and (width is None or height is None):
        methods.append("media_dimensions_invalid")
        return ArtifactVerificationStatus.FAILED, mime, None, None, duration
    methods.append(f"{expected}_stream_probed")
    if duration is not None:
        methods.append("duration_probe")
    return ArtifactVerificationStatus.VERIFIED, mime or _guess_mime_type(path), width, height, duration


def artifact_from_local_path(
    kind: ArtifactKind,
    path: str,
    *,
    title: str | None = None,
    uri: str | None = None,
    source_chat_id: str | None = None,
    requested_backend: str | None = None,
    request_model: str | None = None,
    effective_backend: str | None = None,
    observed_backend: str | None = None,
    duration_probe: Callable[[str], float | None] | None = None,
    dimensions_probe: Callable[[str], tuple[int, int] | None] | None = None,
) -> Artifact:
    resolved_path = _normalized_local_path(path)
    identity = artifact_id(kind, uri=uri, local_path=resolved_path)
    file_path = Path(resolved_path)
    if not file_path.is_file():
        return _unavailable_local_artifact(
            identity=identity,
            kind=kind,
            path=resolved_path,
            title=title,
            uri=uri,
            source_chat_id=source_chat_id,
            requested_backend=requested_backend,
            request_model=request_model,
            effective_backend=effective_backend,
            observed_backend=observed_backend,
            method="file_missing",
        )

    try:
        size_bytes = file_path.stat().st_size
    except OSError:
        return _unavailable_local_artifact(
            identity=identity,
            kind=kind,
            path=resolved_path,
            title=title,
            uri=uri,
            source_chat_id=source_chat_id,
            requested_backend=requested_backend,
            request_model=request_model,
            effective_backend=effective_backend,
            observed_backend=observed_backend,
            method="file_unreadable",
        )

    methods = ["file_exists", "size_checked"]
    status = ArtifactVerificationStatus.VERIFIED
    state = ArtifactState.LOCAL
    if size_bytes <= 0:
        status = ArtifactVerificationStatus.FAILED
        state = ArtifactState.FAILED
        methods.append("size_empty")
    else:
        methods.append("size_nonzero")

    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    mime_type = _guess_mime_type(resolved_path)
    if size_bytes > 0 and kind == ArtifactKind.IMAGE:
        mime_type = detect_image_mime_type(file_path)
        if mime_type is None:
            status = ArtifactVerificationStatus.FAILED
            methods.append("image_format_invalid")
        else:
            methods.append("image_mime_signature")
            status, width, height = _verify_image(resolved_path, mime_type, methods)
            if status == ArtifactVerificationStatus.VERIFIED and dimensions_probe is not None:
                dimensions = dimensions_probe(resolved_path)
                if dimensions is not None:
                    width, height = dimensions
    elif size_bytes > 0 and kind in {ArtifactKind.AUDIO, ArtifactKind.VIDEO}:
        status, mime_type, width, height, duration_seconds = _verify_av(kind, resolved_path, methods)
        if status == ArtifactVerificationStatus.VERIFIED and duration_probe is not None:
            probed_duration = _positive_float(duration_probe(resolved_path))
            if probed_duration is not None:
                duration_seconds = probed_duration
                if "duration_probe" not in methods:
                    methods.append("duration_probe")
    if status == ArtifactVerificationStatus.FAILED:
        state = ArtifactState.FAILED

    return Artifact(
        id=identity,
        kind=kind,
        state=state,
        title=title,
        uri=uri,
        local_path=resolved_path,
        mime_type=mime_type,
        size_bytes=size_bytes,
        width=width,
        height=height,
        duration_seconds=duration_seconds,
        source_chat_id=source_chat_id,
        requested_backend=requested_backend,
        request_model=request_model,
        effective_backend=effective_backend,
        observed_backend=observed_backend,
        verification=ArtifactVerification(status, methods=tuple(methods)),
    )


def extract_response_artifacts(
    response: Any,
    *,
    media_type: str | None = None,
    requested_backend: str | None = None,
    request_model: str | None = None,
    effective_backend: str | None = None,
    observed_backend: str | None = None,
) -> tuple[Artifact, ...]:
    source_chat_id = response_chat_id(response)
    observed_backend = observed_backend or observed_backend_from_response(response)
    artifacts: list[Artifact] = []

    for item in getattr(response, "images", None) or ():
        uri = _string_attr(item, "url")
        if uri:
            artifacts.append(
                _remote_from_item(
                    ArtifactKind.IMAGE,
                    item,
                    uri,
                    source_chat_id=source_chat_id,
                    requested_backend=requested_backend,
                    request_model=request_model,
                    effective_backend=effective_backend,
                    observed_backend=observed_backend,
                )
            )
    for item in getattr(response, "videos", None) or ():
        uri = _string_attr(item, "url")
        if uri:
            artifacts.append(
                _remote_from_item(
                    ArtifactKind.VIDEO,
                    item,
                    uri,
                    source_chat_id=source_chat_id,
                    requested_backend=requested_backend,
                    request_model=request_model,
                    effective_backend=effective_backend,
                    observed_backend=observed_backend,
                )
            )
    for item in getattr(response, "media", None) or ():
        for attribute in ("mp3_url", "url"):
            uri = _string_attr(item, attribute)
            if uri:
                kind = ArtifactKind.AUDIO if attribute == "mp3_url" else ArtifactKind.VIDEO
                artifacts.append(
                    _remote_from_item(
                        kind,
                        item,
                        uri,
                        source_chat_id=source_chat_id,
                        requested_backend=requested_backend,
                        request_model=request_model,
                        effective_backend=effective_backend,
                        observed_backend=observed_backend,
                    )
                )
    for attribute, kind in (
        ("image_url", ArtifactKind.IMAGE),
        ("video_url", ArtifactKind.VIDEO),
        ("audio_url", ArtifactKind.AUDIO),
    ):
        uri = _string_attr(response, attribute)
        if uri:
            artifacts.append(
                artifact_from_remote(
                    kind,
                    uri,
                    source_chat_id=source_chat_id,
                    requested_backend=requested_backend,
                    request_model=request_model,
                    effective_backend=effective_backend,
                    observed_backend=observed_backend,
                )
            )
    return merge_artifacts(artifacts)


def merge_artifacts(*groups: Iterable[Artifact]) -> tuple[Artifact, ...]:
    """Merge remote and local observations without changing stable identities."""
    merged: dict[str, Artifact] = {}
    order: list[str] = []
    for artifact in (item for group in groups for item in group):
        current = merged.get(artifact.id)
        if current is None:
            merged[artifact.id] = artifact
            order.append(artifact.id)
            continue
        prefer = artifact if _state_rank(artifact.state) >= _state_rank(current.state) else current
        other = current if prefer is artifact else artifact
        usable_other = other.state != ArtifactState.FAILED
        merged[artifact.id] = replace(
            prefer,
            title=prefer.title or other.title,
            uri=prefer.uri or other.uri,
            local_path=prefer.local_path or (other.local_path if other.state == ArtifactState.LOCAL else None),
            mime_type=prefer.mime_type or (other.mime_type if usable_other else None),
            size_bytes=prefer.size_bytes or (other.size_bytes if usable_other else None),
            width=prefer.width or (other.width if usable_other else None),
            height=prefer.height or (other.height if usable_other else None),
            duration_seconds=prefer.duration_seconds or (other.duration_seconds if usable_other else None),
            source_chat_id=prefer.source_chat_id or other.source_chat_id,
            requested_backend=prefer.requested_backend or other.requested_backend,
            request_model=prefer.request_model or other.request_model,
            effective_backend=prefer.effective_backend or other.effective_backend,
            observed_backend=prefer.observed_backend or other.observed_backend,
        )
    return tuple(merged[item_id] for item_id in order)


def classify_artifact_state(
    response: Any,
    artifacts: Sequence[Artifact],
) -> ArtifactState:
    if any(artifact.state == ArtifactState.LOCAL for artifact in artifacts):
        return ArtifactState.LOCAL
    if any(artifact.state == ArtifactState.REMOTE for artifact in artifacts):
        return ArtifactState.REMOTE
    if is_response_queued(response):
        return ArtifactState.QUEUED
    if artifacts and all(artifact.state == ArtifactState.FAILED for artifact in artifacts):
        return ArtifactState.FAILED
    return ArtifactState.EMPTY


def media_artifacts(artifacts: Iterable[Artifact], media_type: str) -> tuple[Artifact, ...]:
    """Select the requested creation outputs without narrowing understanding."""
    kinds = {
        "image": {ArtifactKind.IMAGE},
        "image_edit": {ArtifactKind.IMAGE},
        "video": {ArtifactKind.VIDEO},
        "music": {ArtifactKind.AUDIO, ArtifactKind.VIDEO},
    }.get(media_type, set())
    return tuple(artifact for artifact in artifacts if artifact.kind in kinds)


def media_operation_timeout(media_type: str, timeout_seconds: int | None = None) -> int:
    if timeout_seconds is not None and timeout_seconds > 0:
        return timeout_seconds
    return 180 if media_type in {"image", "image_edit"} else 600


def classify_media_artifact_state(
    response: Any, artifacts: Sequence[Artifact], media_type: str,
) -> ArtifactState:
    # A music visualization is auxiliary; music completion requires audio.
    required_kind = {
        "image": ArtifactKind.IMAGE,
        "image_edit": ArtifactKind.IMAGE,
        "video": ArtifactKind.VIDEO,
        "music": ArtifactKind.AUDIO,
    }.get(media_type)
    matching = tuple(artifact for artifact in artifacts if artifact.kind == required_kind)
    return classify_artifact_state(response, matching)


def is_response_queued(response: Any) -> bool:
    if getattr(response, "queued", False) is True:
        return True
    for name in ("operation_state", "status", "state"):
        value = getattr(response, name, None)
        if isinstance(value, str) and value.strip().lower() in {
            "accepted",
            "pending",
            "processing",
            "queued",
            "running",
        }:
            return True
    return False


def artifact_result(
    data: ArtifactResultData,
    *,
    save_failures: Sequence[str] = (),
    empty_suggested_action: str | None = None,
    empty_retryable: bool = True,
) -> DomainResult[ArtifactResultData]:
    details = {
        "artifact_state": data.state.value,
        "artifact_count": len(data.artifacts),
        "input_artifact_count": len(data.input_artifacts),
        "save_failure_count": len(save_failures),
        "request_model": data.request_model,
        "observed_backend": data.observed_backend,
    }
    if data.state == ArtifactState.EMPTY:
        return DomainResult.failure(
            DomainErrorCode.ARTIFACT_NOT_RETURNED,
            "The upstream response did not include a usable artifact.",
            data=data,
            retryable=empty_retryable,
            suggested_action=empty_suggested_action or "Retry with a clearer prompt or inspect the upstream chat later.",
            requested_backend=data.requested_model,
            effective_backend=data.effective_backend,
            verification_status="artifact_absent",
            details=details,
        )
    if data.state == ArtifactState.FAILED:
        code = DomainErrorCode.ARTIFACT_SAVE_FAILED if save_failures else DomainErrorCode.VERIFICATION_FAILED
        return DomainResult.failure(
            code,
            "The artifact could not be saved or verified.",
            data=data,
            retryable=True,
            suggested_action="Retry with another output directory and inspect server diagnostics.",
            requested_backend=data.requested_model,
            effective_backend=data.effective_backend,
            verification_status="artifact_verification_failed",
            details=details,
        )
    if data.state == ArtifactState.QUEUED:
        return DomainResult.success(
            data,
            operation_state=OperationState.QUEUED,
            requested_backend=data.requested_model,
            effective_backend=data.effective_backend,
            verification_status="upstream_queued",
            details=details,
        )

    failed_count = sum(artifact.state == ArtifactState.FAILED for artifact in (*data.input_artifacts, *data.artifacts))
    warnings: tuple[DomainWarning, ...] = ()
    operation_state = OperationState.COMPLETED
    if failed_count or save_failures:
        operation_state = OperationState.PARTIAL
        warnings = (
            DomainWarning(
                code="ARTIFACT_SAVE_PARTIAL",
                message="At least one artifact location could not be saved or verified.",
                suggested_action="Use a verified remote URI or retry the local save.",
            ),
        )
    unverified_local = any(
        artifact.state == ArtifactState.LOCAL and artifact.verification.status == ArtifactVerificationStatus.UNVERIFIED
        for artifact in (*data.input_artifacts, *data.artifacts)
    )
    if unverified_local:
        operation_state = OperationState.PARTIAL
        warnings += (
            DomainWarning(
                code="ARTIFACT_VERIFICATION_UNAVAILABLE",
                message="A local media file exists, but its media content could not be decoded or probed.",
                suggested_action="Install the image decoder or ffprobe and verify the saved file before using it.",
            ),
        )
    if any(
        artifact.state == ArtifactState.LOCAL and artifact.verification.status == ArtifactVerificationStatus.UNVERIFIED
        for artifact in data.artifacts
    ):
        verification_status = "artifact_saved_unverified"
    elif any(
        artifact.state == ArtifactState.LOCAL and artifact.verification.status == ArtifactVerificationStatus.VERIFIED
        for artifact in data.artifacts
    ):
        verification_status = "artifact_saved_and_verified"
    elif any(artifact.state == ArtifactState.LOCAL for artifact in data.artifacts):
        verification_status = "artifact_saved_unverified"
    elif any(
        artifact.state == ArtifactState.LOCAL and artifact.verification.status == ArtifactVerificationStatus.VERIFIED
        for artifact in data.input_artifacts
    ):
        verification_status = "input_artifact_verified"
    elif any(artifact.state == ArtifactState.LOCAL for artifact in data.input_artifacts):
        verification_status = "input_artifact_unverified"
    else:
        verification_status = "remote_uri_observed_unverified"
    return DomainResult.success(
        data,
        operation_state=operation_state,
        warnings=warnings,
        requested_backend=data.requested_model,
        effective_backend=data.effective_backend,
        verification_status=verification_status,
        details=details,
    )


def artifact_exception_result(
    error: BaseException,
    data: ArtifactResultData,
    *,
    logger: logging.Logger,
    operation: str,
) -> DomainResult[ArtifactResultData]:
    classified = result_from_exception(error, logger=logger, operation=operation)
    return DomainResult(
        ok=False,
        data=data,
        error=classified.error,
        warnings=classified.warnings,
        meta=replace(
            classified.meta,
            requested_backend=data.requested_model,
            effective_backend=data.effective_backend,
            details={
                **classified.meta.details,
                "artifact_state": data.state.value,
                "artifact_count": len(data.artifacts),
                "input_artifact_count": len(data.input_artifacts),
                "request_model": data.request_model,
                "observed_backend": data.observed_backend,
            },
        ),
    )


def artifact_save_failure_result(
    error: BaseException,
    data: ArtifactResultData,
    *,
    logger: logging.Logger,
    operation: str,
) -> DomainResult[ArtifactResultData]:
    """Log a write exception while exposing a stable artifact-specific error."""
    classified = result_from_exception(error, logger=logger, operation=operation)
    diagnostic_id = classified.meta.diagnostic_id
    return DomainResult.failure(
        DomainErrorCode.ARTIFACT_SAVE_FAILED,
        "The artifact could not be written to local storage.",
        data=data,
        retryable=True,
        suggested_action="Check the output directory permissions and available space, then retry.",
        request_id=classified.meta.request_id,
        diagnostic_id=diagnostic_id,
        requested_backend=data.requested_model,
        effective_backend=data.effective_backend,
        verification_status="artifact_write_failed",
        details={
            **classified.meta.details,
            "artifact_state": data.state.value,
            "artifact_count": len(data.artifacts),
            "input_artifact_count": len(data.input_artifacts),
            "save_failure_count": 1,
            "request_model": data.request_model,
            "observed_backend": data.observed_backend,
        },
    )


def _remote_from_item(
    kind: ArtifactKind,
    item: Any,
    uri: str,
    **evidence: Any,
) -> Artifact:
    return artifact_from_remote(
        kind,
        uri,
        title=_string_attr(item, "title") or None,
        size_bytes=getattr(item, "size_bytes", None) or getattr(item, "size", None),
        width=getattr(item, "width", None),
        height=getattr(item, "height", None),
        duration_seconds=getattr(item, "duration_seconds", None) or getattr(item, "duration", None),
        **evidence,
    )


def _string_attr(value: Any, name: str) -> str:
    candidate = getattr(value, name, "")
    return candidate.strip() if isinstance(candidate, str) else ""


def _guess_mime_type(location: str) -> str | None:
    path = urlparse(location).path if "://" in location else location
    mime_type, _encoding = mimetypes.guess_type(path)
    return mime_type


def detect_image_mime_type(path: str | Path) -> str | None:
    """Identify common image formats from bytes, independent of file suffix."""
    try:
        with Path(path).open("rb") as image_file:
            header = image_file.read(16)
    except OSError:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header[4:8] == b"ftyp":
        if header[8:12] in {b"heic", b"heix", b"hevc", b"hevx"}:
            return "image/heic"
        if header[8:12] in {b"mif1", b"msf1"}:
            return "image/heif"
    return None


def _normalized_local_path(path: str) -> str:
    candidate = Path(path)
    try:
        candidate = candidate.expanduser()
    except RuntimeError:
        pass
    try:
        return str(candidate.resolve(strict=False))
    except (OSError, RuntimeError):
        return str(candidate.absolute())


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _positive_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 and math.isfinite(value):
        return float(value)
    return None


def _state_rank(state: ArtifactState) -> int:
    return {
        ArtifactState.FAILED: 0,
        ArtifactState.EMPTY: 0,
        ArtifactState.QUEUED: 1,
        ArtifactState.REMOTE: 2,
        ArtifactState.LOCAL: 3,
    }[state]
