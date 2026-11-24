"""Dispatch long work to detached workers using ephemeral, anonymous stdin pipes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

from ..constants import OMNI_VIDEO_API_BACKEND, resolve_media_request, resolve_thinking_level_id
from ..domain import CleanupState
from ..domain.operations import LongOperationData, operation_failure_code
from ..domain.results import DomainErrorCode, DomainResult, DomainWarning, OperationState, result_from_exception
from ..infrastructure.account_identity import (
    current_account_key, omni_video_api_key, operation_account_matches, uses_omni_video_api,
)
from ..infrastructure.operation_store import OperationRecord, OperationStore, RETENTION_SECONDS
from ..tools.utils import validate_image_paths
from .media_cleanup import reconcile_owned_media_cleanup
from .media_generation import music_options_error, video_options_error
from .media_inputs import MediaSourceUnavailable, image_workflow_options_error, resolve_saved_image, verify_source_image
from .media_storage import _valid_media_filename

_MEDIA_RATE_LIMIT_BACKOFF_SECONDS = 5 * 60
_MEDIA_RATE_LIMIT_MAX_BACKOFF_SECONDS = 30 * 60
MEDIA_STALE_CLEANUP_RELEASE_SECONDS = 300

# Bound concurrent detached worker spawns so a burst of submissions cannot
# trigger a simultaneous subprocess/cookie spawn storm.
_DISPATCH_SEMAPHORE = threading.BoundedSemaphore(2)

logger = logging.getLogger(__name__)


def worker_active(record: OperationRecord, *, now: float) -> bool:
    return bool(record.worker_token and record.heartbeat_at is not None and now - record.heartbeat_at < 60)


def _run_launch_detached_worker(payload: dict[str, Any], store: OperationStore) -> int:
    """Acquire the dispatch semaphore then launch one detached worker.

    The semaphore is thread-safe so it can be acquired inside the worker thread
    without binding to a single asyncio event loop across pytest runs.
    """
    with _DISPATCH_SEMAPHORE:
        return _launch_detached_worker(payload, store)


def _launch_detached_worker(
    payload: dict[str, Any], store: OperationStore
) -> int:
    omni_job = payload["kind"] in {"omni_video", "omni_video_resume"}
    # Each worker thread snapshots process-global os.environ itself; nothing is
    # passed in from the dispatcher, so concurrent threads never race on the
    # process environment (e.g. GEMINI_COOKIE_PATH).
    environment = os.environ.copy()
    if omni_job:
        for key in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
            environment.pop(key, None)
    else:
        environment.pop("GEMINI_API_KEY", None)
    environment["GEMINI_OPERATION_DB_PATH"] = str(store.path.resolve())
    for key in ("GEMINI_CLEANUP_DB_PATH", "XDG_STATE_HOME", "GEMINI_COOKIE_PATH"):
        if environment.get(key):
            environment[key] = str(Path(environment[key]).expanduser().resolve())
    package_root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = package_root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    # Prompts, attachments and credentials never appear in argv or disk spools.
    process = subprocess.Popen(
        [sys.executable, "-m", "src.operation_worker"], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment,
        start_new_session=os.name == "posix", close_fds=True, cwd=store.path.resolve().parent,
    )
    assert process.stdin is not None
    try:
        # The same anonymous pipe also preserves the parent's complete cookie
        # context. Neither authentication nor request content enters the ledger.
        envelope = payload
        if not omni_job:
            from ..client_manager import get_extra_cookies
            from ..cookie_manager import get_cookie_manager

            cookie_data = get_cookie_manager().get_cookie()
            source = "browser_worker" if cookie_data and cookie_data.source.startswith("browser_") else "worker_inherited"
            envelope = {**payload, "auth_cookies": get_extra_cookies(), "auth_source": source}
        process.stdin.write(json.dumps(envelope).encode("utf-8"))
        process.stdin.close()
    except Exception:
        process.stdin.close()
        raise
    threading.Thread(target=process.wait, daemon=True, name="gemini-worker-reaper").start()
    return process.pid


def _handle_valid(operation_id: str) -> bool:
    return re.fullmatch(r"op_[0-9a-f]{32}", operation_id) is not None


def _accepted(record: OperationRecord) -> DomainResult[LongOperationData]:
    continuation = record.state not in {"failed", "unavailable", "cancelled"}
    if uses_omni_video_api(record) and record.state == "partial" and not record.upstream_operation_id:
        continuation = False
    data = LongOperationData(
        operation=record.operation, state=OperationState(record.state), operation_id=record.operation_id,
        upstream_chat_id=record.upstream_chat_id, upstream_operation_id=record.upstream_operation_id,
        continuation_possible=continuation, media_type=record.media_type,
        # Only a verified result read may claim current local availability.
        artifacts_available=False,
        error_code=record.error_code,
        execution_mode=("task" if record.operation == "gemini_run_task" else
                        "chat" if record.operation == "gemini_notebook_chat" else None),
        source_operation_id=record.source_operation_id, source_artifact_id=record.source_artifact_id,
    )
    if data.state in {OperationState.FAILED, OperationState.UNAVAILABLE, OperationState.TIMED_OUT, OperationState.CANCELLED}:
        return DomainResult.failure(operation_failure_code(record.error_code), "The saved background operation did not complete.",
                                    data=data, operation_state=data.state, verification_status=record.verification_status,
                                    requested_backend=record.requested_model, effective_backend=record.effective_backend)
    return DomainResult.success(data, operation_state=data.state, verification_status=record.verification_status,
                                requested_backend=record.requested_model, effective_backend=record.effective_backend)


async def _dispatch(store: OperationStore, record: OperationRecord, kind: str,
                    request: dict[str, Any] | None = None) -> DomainResult[LongOperationData]:
    may_bind_new = (record.account_binding_pending and record.phase == "created" and not record.upstream_chat_id
                    and kind in {"media", "research", "task", "notebook_chat"})
    if not operation_account_matches(record) and not may_bind_new:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                    verification_status="account_mismatch_or_unbound")
    token = uuid.uuid4().hex
    if not store.claim_worker(record.operation_id, token):
        return _accepted(store.get(record.operation_id) or record)
    try:
        pid = await asyncio.to_thread(_run_launch_detached_worker, {
            "operation_id": record.operation_id, "worker_token": token, "kind": kind, "request": request or {},
        }, store)
        store.bind_worker(record.operation_id, token, pid)
    except Exception:
        api_recovery = kind == "omni_video_resume"
        store.finish_worker(record.operation_id, worker_token=token, state="partial" if api_recovery else "failed",
                            phase="omni_observer_unavailable" if api_recovery else "dispatch_failed",
                            verification_status="worker_not_dispatched", error_code="WORKER_START_FAILED")
        if not (record.upstream_chat_id or record.upstream_operation_id) and operation_account_matches(record):
            store.release_cleanup(record.operation_id)
        store.retire_worker(record.operation_id, token)
        return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "The background worker could not be started.",
                                    verification_status="worker_not_dispatched")
    return _accepted(store.get(record.operation_id) or record)


async def submit_media(request: dict[str, Any], *, operation: str,
                       operation_id: str | None = None) -> DomainResult[LongOperationData]:
    """Accept a generation without initializing Gemini or waiting for its response."""
    handle = operation_id or "op_" + uuid.uuid4().hex
    if not _handle_valid(handle) or not isinstance(request.get("prompt"), str) or not request["prompt"].strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "A valid handle and nonblank prompt are required.")
    media_type = request.get("media_type")
    if media_type not in {"image", "video", "music"}:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Unsupported media type.")
    filename = request.get("filename") or handle
    if not isinstance(filename, str) or not _valid_media_filename(filename):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "filename must be a single file name.")
    model, thinking_level = request.get("model", "flash"), request.get("thinking_level", "standard")
    invalid = video_options_error(
        media_type=media_type, model=model, thinking_level=thinking_level,
        duration_seconds=request.get("duration_seconds"), delete_after_seconds=request.get("delete_after_seconds"),
        aspect_ratio=request.get("aspect_ratio"),
    )
    if invalid:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, invalid)
    invalid = music_options_error(
        media_type=media_type,
        music_duration=request.get("music_duration"),
        music_vocals=request.get("music_vocals"),
        music_genre=request.get("music_genre"),
    )
    if invalid:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, invalid)
    if ((request.get("music_duration") is not None or request.get("music_vocals") is not None
            or request.get("music_genre") is not None)
            and request.get("web_media_transport", os.environ.get("GEMINI_WEB_MEDIA_TRANSPORT")) == "sdk"):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "Explicit music options require the native Gemini Web composer.")
    image_paths = request.get("image_paths")
    source_operation_id = request.get("source_operation_id")
    source_artifact_id = request.get("source_artifact_id")
    invalid = image_workflow_options_error(
        media_type=media_type, image_path=request.get("image_path"), image_paths=image_paths,
        source_operation_id=source_operation_id, source_artifact_id=source_artifact_id,
    )
    if invalid:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, invalid)
    settings = resolve_media_request(model, media_type, thinking_level)
    if settings["backend_label"] == OMNI_VIDEO_API_BACKEND:
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            "Paid Gemini API (gemini-omni-1.2-flash / GEMINI_API_KEY) is disabled; use Gemini Web.",
        )
    if settings["effective_alias"] == "flash-lite" and (source_operation_id or len(image_paths or ()) > 1):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "Use Flash for multiple image references or editing a previous result.")
    omni_job = False
    if omni_job and not omni_video_api_key():
        return DomainResult.failure(
            DomainErrorCode.AUTH_REQUIRED, "The Gemini API video route requires GEMINI_API_KEY.",
            suggested_action="Configure GEMINI_API_KEY for a project with Gemini API billing enabled.",
            verification_status="credential_missing_before_dispatch",
        )
    if not omni_job and not current_account_key():
        from ..client_manager import ClientAuthenticationRequired, ClientRateLimited
        from ..client_wrapper import ensure_web_cookie

        try:
            await asyncio.to_thread(ensure_web_cookie)
        except (ClientAuthenticationRequired, ClientRateLimited) as error:
            return result_from_exception(error, logger=logger, operation=operation)
    store = OperationStore()

    def existing(record: OperationRecord) -> DomainResult[LongOperationData]:
        if not operation_account_matches(record):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                        verification_status="account_mismatch_or_unbound")
        if (record.operation != operation or record.media_type != media_type or uses_omni_video_api(record) != omni_job
                or record.source_operation_id != source_operation_id
                or (source_artifact_id is not None and record.source_artifact_id != source_artifact_id)):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "This handle belongs to a different operation.")
        if store.clock() - record.created_at >= RETENTION_SECONDS and not record.cleanup_protected:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The operation handle has expired.")
        return _accepted(record)

    try:
        if operation_id is not None and (source_operation_id is not None or image_paths is not None):
            previous = store.get(handle)
            if previous is not None:
                return existing(previous)
        valid, normalized_paths, message = validate_image_paths(image_paths)
        if not valid:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, message)
        resolved_artifact_id = None
        if source_operation_id is not None:
            source = resolve_saved_image(store, source_operation_id, source_artifact_id)
            await asyncio.to_thread(verify_source_image, source, Path(source["local_path"]))
            resolved_artifact_id = source["artifact_id"]
        directory = str(Path(request.get("output_dir") or store.path.resolve().parent / "artifacts" / handle).expanduser().resolve())
        record, created = store.create_or_get(
            handle, operation, retain_chat=request.get("retain_chat", False),
            delete_after_seconds=request.get("delete_after_seconds"), requested_model=model, thinking_level=thinking_level,
            media_type=media_type, output_dir=directory, filename=filename,
            request_model=settings["request_model"], effective_backend=settings["backend_label"],
            source_operation_id=source_operation_id, source_artifact_id=resolved_artifact_id,
        )
        if not created:
            return existing(record)
        payload = {**request, "output_dir": directory, "filename": filename}
        if normalized_paths:
            payload["image_paths"] = normalized_paths
        if payload.get("image_path"):
            payload["image_path"] = str(Path(payload["image_path"]).expanduser().resolve())
        # The worker resolves lineage from its account-bound record.
        payload.pop("source_operation_id", None)
        payload.pop("source_artifact_id", None)
        return await _dispatch(store, record, "omni_video" if omni_job else "media", payload)
    except MediaSourceUnavailable as error:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, str(error),
                                    verification_status="source_image_unavailable")
    except PermissionError:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "The media operation or its source image is unavailable.")
    except Exception:
        return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "Unable to persist the media operation; no retry was sent.",
                                    verification_status="operation_not_persisted")


async def submit_task(request: dict[str, Any], *, operation_id: str | None = None,
                       notebook_chat: bool = False) -> DomainResult[LongOperationData]:
    """Accept one Task with an explicit persistent handle and no prompt spool."""
    handle = operation_id or "op_" + uuid.uuid4().hex
    prompt = request.get("prompt")
    identifiers = request.get("notebook_ids")
    if identifiers is None:
        identifiers = []
    timeout = request.get("timeout_seconds", 600)
    level = request.get("thinking_level", "standard")
    operation = "gemini_notebook_chat" if notebook_chat else "gemini_run_task"
    if (not isinstance(handle, str) or not _handle_valid(handle) or not isinstance(prompt, str) or not prompt.strip()
            or not isinstance(level, str) or resolve_thinking_level_id(level) is None
            or not isinstance(request.get("model", "flash"), str) or not request.get("model", "flash").strip()
            or type(timeout) is not int or not 30 <= timeout <= 3600
            or not isinstance(identifiers, (list, tuple)) or len(identifiers) > 10
            or any(not isinstance(value, str) or not value.strip() or len(value) > 256 for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid Task prompt, model, context or timeout.")
    if notebook_chat and not identifiers:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Notebook context IDs are required.")
    if request.get("input_option_id") is not None and request.get("input_id") is None:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "A card option requires its original input_id.")
    if request.get("input_id") is not None:
        if notebook_chat or operation_id is None:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Task input requires its original operation_id.")
        from .task_inputs import submit_task_input
        return await submit_task_input(request, operation_id=operation_id)
    store = OperationStore()
    try:
        record, created = store.create_or_get(
            handle, operation, retain_chat=request.get("retain_chat", False),
            delete_after_seconds=request.get("delete_after_seconds"), requested_model=request.get("model", "flash"),
            thinking_level=request.get("thinking_level", "standard"),
        )
        if not created:
            if not operation_account_matches(record):
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                            verification_status="account_mismatch_or_unbound")
            if record.operation != operation:
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "This handle belongs to another operation.")
            if store.clock() - record.created_at >= RETENTION_SECONDS and not record.cleanup_protected:
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The operation handle has expired.")
            return _accepted(record)
        return await _dispatch(store, record, "notebook_chat" if notebook_chat else "task", request)
    except Exception:
        return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "Unable to persist the Task operation.",
                                    verification_status="operation_not_persisted")


async def submit_notebook_chat(request: dict[str, Any], *, operation_id: str | None = None) -> DomainResult[LongOperationData]:
    return await submit_task(request, operation_id=operation_id, notebook_chat=True)


async def submit_research(request: Any) -> DomainResult[LongOperationData]:
    """Return before even the upstream research planning phase has completed."""
    from dataclasses import asdict

    handle = request.operation_id or "op_" + uuid.uuid4().hex
    if not _handle_valid(handle) or not request.query.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "A valid handle and nonblank query are required.")
    store = OperationStore()
    try:
        record, created = store.create_or_get(
            handle, request.operation, retain_chat=request.retain_chat, delete_after_seconds=request.delete_after_seconds,
            requested_model=request.model, thinking_level=request.thinking_level,
        )
        if not created:
            if not operation_account_matches(record):
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                            verification_status="account_mismatch_or_unbound")
            if record.operation != request.operation:
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "This handle belongs to a different operation.")
            if store.clock() - record.created_at >= RETENTION_SECONDS and not record.cleanup_protected:
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The operation handle has expired.")
            if (record.phase == "plan_outcome_unverified" and record.upstream_chat_id and record.cleanup_protected
                    and record.requested_model and record.thinking_level and not worker_active(record, now=store.clock())):
                return await _dispatch(store, record, "research_resume")
            return _accepted(record)
        payload = asdict(request)
        payload["operation_id"] = handle
        return await _dispatch(store, record, "research", payload)
    except Exception:
        return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "Unable to persist the research operation.",
                                    verification_status="operation_not_persisted")


async def _resume_media_worker_step(store: OperationStore, record: OperationRecord) -> tuple[DomainResult[LongOperationData], bool]:
    """Resume observation only; return (result, dispatched_worker)."""
    cleaned = reconcile_owned_media_cleanup(store, record)
    if cleaned:
        record = store.get(record.operation_id) or record
    if not operation_account_matches(record):
        return (DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                     verification_status="account_mismatch_or_unbound"), False)
    web_cleanup_complete = (not uses_omni_video_api(record) and not record.cleanup_protected and not record.retain_chat
                            and record.cleanup_state in {"completed", "already_completed"})
    if cleaned or web_cleanup_complete or worker_active(record, now=store.clock()):
        return _accepted(record), False
    if uses_omni_video_api(record):
        if not record.upstream_operation_id or record.cleanup_state in {"retained", "completed", "already_completed"}:
            return _accepted(record), False
        return (await _dispatch(store, record, "omni_video_resume")), True
    if not record.upstream_chat_id:
        return _accepted(record), False
    native_pending = (record.phase == "native_media_submitted" and record.media_type in {"music", "video"}
                      and record.upstream_response_id is None and record.upstream_candidate_id is None)
    if (record.state != "completed" and not native_pending
            and (record.upstream_response_id is None or record.upstream_candidate_id is None)):
        from dataclasses import replace
        result = _accepted(record)
        return (replace(result, warnings=(*result.warnings, DomainWarning(
            code="generation_identity_incomplete", message="The original generation turn was not fully observed; it will not be repeated.",
        ))), False)
    return (await _dispatch(store, record, "media_resume")), True


async def resume_media_worker(store: OperationStore, record: OperationRecord) -> DomainResult[LongOperationData]:
    """Resume observation only; no saved prompt exists and generation cannot be replayed."""
    result, _ = await _resume_media_worker_step(store, record)
    return result


async def maintain_background_operations() -> None:
    """Restart at most one due read-back/finalization worker when not rate-limited."""
    from ..client_wrapper import _client_manager

    if getattr(_client_manager, "is_rate_limited", lambda: False)():
        return
    store = OperationStore()
    media_rows = store.recoverable_media()
    workflow_rows = store.recoverable_web_workflows()
    research_rows = store.recoverable_research()
    now = store.clock()
    if getattr(store, "has_active_worker", lambda **_kwargs: False)(now=now) or any(
        worker_active(record, now=now) for record in (*media_rows, *workflow_rows, *research_rows)
    ):
        return
    for record in media_rows:
        _, dispatched = await _resume_media_worker_step(store, record)
        if dispatched or worker_active(store.get(record.operation_id) or record, now=store.clock()):
            return
    for record in workflow_rows:
        if operation_account_matches(record) and not worker_active(record, now=store.clock()):
            await _dispatch(store, record, "task_resume")
            return
    for record in research_rows:
        if operation_account_matches(record) and not worker_active(record, now=store.clock()):
            await _dispatch(store, record, "research_observe")
            return


def repair_operation_cleanup(store: OperationStore, get_observation: Any, schedule: Any) -> int:
    """Restore missing queue entries only for account-bound observed operations.

    Heals stuck cleanup rows that would otherwise leak forever: protected media
    operations past the recovery TTL are released and re-scheduled for immediate
    cleanup, and already-unprotected rows whose durable queue entry is still
    pending (or missing) are made immediately drainable.
    """
    account = current_account_key()
    if not account:
        return 0
    repaired = 0
    now = time.time()
    for record in store.cleanup_repair_candidates(account):
        if not operation_account_matches(record) or not record.upstream_chat_id:
            continue
        cid = record.upstream_chat_id
        try:
            obs = get_observation(cid)
        except Exception:
            # Leave the operation protected and retry on the next maintenance pass.
            continue
        # Protected media rows stuck past the recovery TTL are released so their
        # chat can be cleaned up instead of leaking forever.
        if (record.media_type
                and not uses_omni_video_api(record)
                and record.cleanup_protected
                and not record.retain_chat
                and not worker_active(record, now=now)):
            stuck = (
                (record.state in {"failed", "unavailable", "cancelled"}
                 and record.error_code != "RATE_LIMITED")
                or (record.state == "partial"
                    and (record.phase == "media_identity_incomplete"
                         or (now - record.updated_at) >= MEDIA_STALE_CLEANUP_RELEASE_SECONDS))
                or (record.state == "running"
                    and (now - max(record.updated_at, record.heartbeat_at or 0.0))
                    >= MEDIA_STALE_CLEANUP_RELEASE_SECONDS)
            )
            if stuck:
                store.release_cleanup(record.operation_id)
                res = schedule(cid, retain_chat=False, delete_after_seconds=0, reschedule_pending=True,
                               source=f"{record.operation}:repaired")
                if res is not None and hasattr(res, "state"):
                    store.update_cleanup(record.operation_id, res.state.value)
                repaired += 1
                continue
        # Missing queue entry: restore it with the original delay logic for ALL
        # records (including retained chats and running research ops). Protected
        # media ops that are not stuck are left alone; they may still recover.
        if obs is None and (not record.media_type or not record.cleanup_protected):
            delay = max(1800, record.delete_after_seconds or 0) if record.cleanup_protected else record.delete_after_seconds
            schedule(cid, retain_chat=record.retain_chat, delete_after_seconds=delay,
                     reschedule_pending=True, source=f"{record.operation}:repaired")
            repaired += 1
            continue
        # Already-unprotected rows: overdue or still-pending entries become
        # immediately eligible for the due-cleanup drain.
        if (not record.cleanup_protected
                and obs.state == CleanupState.PENDING
                and record.state in {"completed", "failed", "partial", "unavailable", "cancelled"}):
            schedule(cid, retain_chat=False, delete_after_seconds=0, reschedule_pending=True,
                     source=f"{record.operation}:repaired")
            repaired += 1
    return repaired
