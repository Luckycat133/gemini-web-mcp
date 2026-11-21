"""Run explicit Omni API jobs using the shared operation and artifact stores."""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

from ..domain import ArtifactKind, ArtifactState, DomainErrorCode
from ..infrastructure.account_identity import (
    omni_video_api_key, omni_video_owner, operation_account_matches, uses_omni_video_api,
)
from ..infrastructure.omni_video_api import MAX_OMNI_IMAGE_BYTES, OmniVideoAPI, OmniVideoError, OmniVideoInteraction
from ..infrastructure.operation_store import OperationRecord, OperationStore
from .artifacts import detect_image_mime_type
from .media_generation import media_timeout
from .media_storage import OMNI_ARTIFACT_MODEL_VERIFIED, _save_generated_media, read_verified_media_artifacts


def _reference(path: str | None) -> tuple[str, bytes] | None:
    if path is None:
        return None
    image = Path(path)
    if not image.is_file():
        raise ValueError("Invalid Omni reference image.")
    mime_type = detect_image_mime_type(image)
    with image.open("rb") as stream:
        data = stream.read(MAX_OMNI_IMAGE_BYTES + 1)
    if (mime_type is None or mime_type not in {"image/jpeg", "image/png", "image/webp"}
            or not 0 < len(data) <= MAX_OMNI_IMAGE_BYTES):
        raise ValueError("Omni reference image must be a JPEG, PNG or WebP file up to 14 MB.")
    return mime_type, data


@dataclass(frozen=True)
class _InlineVideo:
    data: bytes

    async def save(self, *, path: str, filename: str, verbose: bool) -> str:
        """Write into the artifact saver's empty, atomically reserved path."""
        destination = Path(path) / filename
        fd = os.open(destination, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "wb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size or info.st_nlink != 1:
                raise OSError("Invalid video artifact reservation.")
            stream.write(self.data)
        return str(destination)


async def _save_video(
    store: OperationStore, record: OperationRecord, token: str, observation: OmniVideoInteraction,
) -> bool:
    if not observation.video:
        return False
    saved = await _save_generated_media(
        None, media_type="video", output_dir=record.output_dir, filename=record.filename, prompt="",
        media_items=[_InlineVideo(observation.video)], requested_model=record.requested_model or "",
        request_model=record.request_model or "", effective_backend=record.effective_backend or "",
        observed_backend=observation.observed_model, source_chat_id=None,
    )
    videos = tuple(item for item in saved.artifacts if item.kind == ArtifactKind.VIDEO and item.state == ArtifactState.LOCAL)
    return bool(videos and operation_account_matches(record) and store.save_media_artifacts(
        record.operation_id, videos, worker_token=token, artifact_state="local",
        verification_status=OMNI_ARTIFACT_MODEL_VERIFIED if observation.observed_model else "local_artifacts_verified",
    ))


async def _finish_cleanup(api: OmniVideoAPI, store: OperationStore, record: OperationRecord, token: str) -> None:
    if not record.upstream_operation_id or record.cleanup_state in {"completed", "retained"}:
        return
    if record.retain_chat:
        store.finish_omni_cleanup(record.operation_id, "retained", worker_token=token)
        return
    if record.state == "completed":
        try:
            if not read_verified_media_artifacts(store, record):
                return
        except (OSError, ValueError):
            return
    if not operation_account_matches(record) or not store.is_worker_owner(record.operation_id, token):
        return
    if not store.finish_omni_cleanup(record.operation_id, "pending", worker_token=token):
        return
    try:
        absent = await api.delete_and_verify(record.upstream_operation_id)
    except OmniVideoError:
        return  # Keep the exact interaction pending for the next observation worker.
    if absent:
        store.finish_omni_cleanup(record.operation_id, "completed", worker_token=token)


async def run_omni_video_job(
    store: OperationStore, operation_id: str, token: str, *,
    request: dict[str, Any] | None, observation_seconds: float, poll_seconds: float,
) -> None:
    """Start at most once. Recovery observes an exact API interaction, never a Web chat."""
    record = store.get(operation_id)
    if record is None or not uses_omni_video_api(record) or not operation_account_matches(record):
        return
    key = omni_video_api_key()
    if omni_video_owner(key) != record.account_key:
        return
    timeout = observation_seconds
    if request is not None:
        timeout = min(timeout, media_timeout("video", request.get("timeout_seconds")))

    def finish(state: str, phase: str, verification: str, code: DomainErrorCode | None = None) -> bool:
        return bool(operation_account_matches(record) and store.finish_worker(
            operation_id, worker_token=token, state=state, phase=phase,
            verification_status=verification, error_code=code.value if code is not None else None,
        ))

    try:
        async with asyncio.timeout(timeout), httpx2.AsyncClient(timeout=60, follow_redirects=False) as http:
            api = OmniVideoAPI(http, key)
            if request is not None:
                if not store.begin_omni_video(operation_id, worker_token=token):
                    return
                try:
                    reference = _reference(request.get("image_path"))
                    provider_id = await api.create(
                        request["prompt"], image=reference, duration_seconds=request.get("duration_seconds"),
                    )
                    del reference
                except (OSError, ValueError):
                    if finish("failed", "omni_input_rejected", "input_rejected", DomainErrorCode.INVALID_ARGUMENT):
                        store.release_cleanup(operation_id)
                    return
                except OmniVideoError as error:
                    if error.dispatch_rejected:
                        if finish("failed", "omni_start_rejected", "upstream_rejection_observed", error.code):
                            store.release_cleanup(operation_id)
                    else:
                        finish("partial", "omni_start_unverified", "dispatch_outcome_unverified", error.code)
                    return
                if not store.checkpoint_omni_interaction(operation_id, provider_id, worker_token=token):
                    return
                request = None

            while store.is_worker_owner(operation_id, token) and operation_account_matches(record):
                current = store.get(operation_id)
                if current is None or not current.upstream_operation_id:
                    return
                if current.state == "completed" or current.phase == "omni_rejected":
                    await _finish_cleanup(api, store, current, token)
                    return
                observed = await api.read(current.upstream_operation_id)
                if not operation_account_matches(record) or not store.is_worker_owner(operation_id, token):
                    return
                if observed.status == "in_progress":
                    finish("running", "omni_observing", "upstream_progress_observed")
                    await asyncio.sleep(poll_seconds)
                    continue
                if observed.status == "completed" and observed.video:
                    if not await _save_video(store, current, token, observed):
                        finish("partial", "omni_artifact_unverified", "artifact_not_verified", DomainErrorCode.ARTIFACT_SAVE_FAILED)
                        return
                    continue
                if observed.status in {"failed", "cancelled"}:
                    code = DomainErrorCode.CANCELLED if observed.status == "cancelled" else DomainErrorCode.UPSTREAM_REJECTED
                    if finish("failed", "omni_rejected", "upstream_rejection_observed", code):
                        await _finish_cleanup(api, store, current, token)
                    return
                code = (DomainErrorCode.ARTIFACT_NOT_RETURNED if observed.status == "completed"
                        else DomainErrorCode.CAPABILITY_UNAVAILABLE)
                finish("partial", "omni_artifact_unverified", "artifact_not_verified", code)
                return
    except TimeoutError:
        finish("partial", "omni_observation_timeout", "outcome_unverified", DomainErrorCode.TIMED_OUT)
    except OmniVideoError as error:
        finish("partial", "omni_observation_unverified", "outcome_unverified", error.code)
