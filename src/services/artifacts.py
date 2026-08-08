"""Artifact extraction, identity, verification, and result helpers."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
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
        return Artifact(
            id=identity,
            kind=kind,
            state=ArtifactState.FAILED,
            title=title,
            uri=uri,
            local_path=resolved_path,
            mime_type=_guess_mime_type(resolved_path),
            source_chat_id=source_chat_id,
            requested_backend=requested_backend,
            request_model=request_model,
            effective_backend=effective_backend,
            observed_backend=observed_backend,
            verification=ArtifactVerification(
                ArtifactVerificationStatus.FAILED,
                methods=("file_missing",),
            ),
        )

    try:
        size_bytes = file_path.stat().st_size
    except OSError:
        return Artifact(
            id=identity,
            kind=kind,
            state=ArtifactState.FAILED,
            title=title,
            uri=uri,
            local_path=resolved_path,
            mime_type=_guess_mime_type(resolved_path),
            source_chat_id=source_chat_id,
            requested_backend=requested_backend,
            request_model=request_model,
            effective_backend=effective_backend,
            observed_backend=observed_backend,
            verification=ArtifactVerification(
                ArtifactVerificationStatus.FAILED,
                methods=("file_unreadable",),
            ),
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

    width = height = None
    if kind == ArtifactKind.IMAGE:
        probe = dimensions_probe or _probe_image_dimensions
        dimensions = probe(resolved_path)
        if dimensions is not None:
            width, height = dimensions
            methods.append("image_dimensions")

    duration_seconds = None
    if kind in {ArtifactKind.AUDIO, ArtifactKind.VIDEO} and duration_probe is not None:
        duration_seconds = _positive_float(duration_probe(resolved_path))
        if duration_seconds is not None:
            methods.append("duration_probe")

    return Artifact(
        id=identity,
        kind=kind,
        state=state,
        title=title,
        uri=uri,
        local_path=resolved_path,
        mime_type=_guess_mime_type(resolved_path),
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
        merged[artifact.id] = replace(
            prefer,
            title=prefer.title or other.title,
            uri=prefer.uri or other.uri,
            local_path=prefer.local_path or (other.local_path if other.state == ArtifactState.LOCAL else None),
            mime_type=prefer.mime_type or other.mime_type,
            size_bytes=prefer.size_bytes or other.size_bytes,
            width=prefer.width or other.width,
            height=prefer.height or other.height,
            duration_seconds=prefer.duration_seconds or other.duration_seconds,
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
            retryable=True,
            suggested_action="Retry with a clearer prompt or inspect the upstream chat later.",
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
    if any(artifact.state == ArtifactState.LOCAL for artifact in data.artifacts):
        verification_status = "artifact_saved_and_verified"
    elif any(artifact.state == ArtifactState.LOCAL for artifact in data.input_artifacts):
        verification_status = "input_artifact_verified"
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
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _probe_image_dimensions(path: str) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            width, height = image.size
            return int(width), int(height)
    except (OSError, ValueError):
        return None


def _state_rank(state: ArtifactState) -> int:
    return {
        ArtifactState.FAILED: 0,
        ArtifactState.EMPTY: 0,
        ArtifactState.QUEUED: 1,
        ArtifactState.REMOTE: 2,
        ArtifactState.LOCAL: 3,
    }[state]
