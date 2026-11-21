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
import uuid
from typing import Any

from ..constants import OMNI_VIDEO_API_BACKEND, resolve_media_request, resolve_thinking_level_id
from ..domain.operations import LongOperationData, operation_failure_code
from ..domain.results import DomainErrorCode, DomainResult, DomainWarning, OperationState, result_from_exception
from ..infrastructure.account_identity import (
    current_account_key, omni_video_api_key, operation_account_matches, uses_omni_video_api,
)
from ..infrastructure.operation_store import OperationRecord, OperationStore, RETENTION_SECONDS
from .media_generation import video_options_error
from .media_storage import _valid_media_filename

logger = logging.getLogger(__name__)


def worker_active(record: OperationRecord, *, now: float) -> bool:
    return bool(record.worker_token and record.heartbeat_at is not None and now - record.heartbeat_at < 60)


def _launch_detached_worker(payload: dict[str, Any], store: OperationStore) -> int:
    omni_job = payload["kind"] in {"omni_video", "omni_video_resume"}
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
        pid = await asyncio.to_thread(_launch_detached_worker, {
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
    )
    if invalid:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, invalid)
    settings = resolve_media_request(model, media_type, thinking_level)
    omni_job = settings["backend_label"] == OMNI_VIDEO_API_BACKEND
    if omni_job and not omni_video_api_key():
        return DomainResult.failure(
            DomainErrorCode.AUTH_REQUIRED, "The Gemini API video route requires GEMINI_API_KEY.",
            suggested_action="Configure GEMINI_API_KEY for a project with Gemini API billing enabled.",
            verification_status="credential_missing_before_dispatch",
        )
    if not omni_job and not current_account_key():
        from ..client_manager import ClientAuthenticationRequired
        from ..client_wrapper import ensure_web_cookie

        try:
            await asyncio.to_thread(ensure_web_cookie)
        except ClientAuthenticationRequired as error:
            return result_from_exception(error, logger=logger, operation=operation)
    store = OperationStore()
    try:
        directory = str(Path(request.get("output_dir") or store.path.resolve().parent / "artifacts" / handle).expanduser().resolve())
        record, created = store.create_or_get(
            handle, operation, retain_chat=request.get("retain_chat", False),
            delete_after_seconds=request.get("delete_after_seconds"), requested_model=model, thinking_level=thinking_level,
            media_type=media_type, output_dir=directory, filename=filename,
            request_model=settings["request_model"], effective_backend=settings["backend_label"],
        )
        if not created:
            if not operation_account_matches(record):
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                            verification_status="account_mismatch_or_unbound")
            if record.operation != operation or record.media_type != media_type or uses_omni_video_api(record) != omni_job:
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "This handle belongs to a different operation.")
            if store.clock() - record.created_at >= RETENTION_SECONDS and not record.cleanup_protected:
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The operation handle has expired.")
            return _accepted(record)
        payload = {**request, "output_dir": directory, "filename": filename}
        if payload.get("image_path"):
            payload["image_path"] = str(Path(payload["image_path"]).expanduser().resolve())
        return await _dispatch(store, record, "omni_video" if omni_job else "media", payload)
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


async def resume_media_worker(store: OperationStore, record: OperationRecord) -> DomainResult[LongOperationData]:
    """Resume observation only; no saved prompt exists and generation cannot be replayed."""
    if not operation_account_matches(record):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                    verification_status="account_mismatch_or_unbound")
    if record.cleanup_state in {"completed", "already_completed"} or worker_active(record, now=store.clock()):
        return _accepted(record)
    if uses_omni_video_api(record):
        if not record.upstream_operation_id or record.cleanup_state == "retained":
            return _accepted(record)
        return await _dispatch(store, record, "omni_video_resume")
    if not record.upstream_chat_id:
        return _accepted(record)
    if record.state != "completed" and (record.upstream_response_id is None or record.upstream_candidate_id is None):
        from dataclasses import replace
        result = _accepted(record)
        return replace(result, warnings=(*result.warnings, DomainWarning(
            code="generation_identity_incomplete", message="The original generation turn was not fully observed; it will not be repeated.",
        )))
    return await _dispatch(store, record, "media_resume")


async def maintain_background_operations() -> None:
    """Restart only read-back/finalization workers; persisted prompts do not exist."""
    store = OperationStore()
    for record in store.recoverable_media():
        await resume_media_worker(store, record)
    for record in store.recoverable_web_workflows():
        if operation_account_matches(record) and not worker_active(record, now=store.clock()):
            await _dispatch(store, record, "task_resume")
    for record in store.recoverable_research():
        if operation_account_matches(record) and not worker_active(record, now=store.clock()):
            await _dispatch(store, record, "research_observe")


def repair_operation_cleanup(store: OperationStore, get_observation: Any, schedule: Any) -> int:
    """Restore missing queue entries only for account-bound observed operations."""
    account = current_account_key()
    if not account:
        return 0
    repaired = 0
    for record in store.cleanup_repair_candidates(account):
        if not operation_account_matches(record) or not record.upstream_chat_id:
            continue
        try:
            if get_observation(record.upstream_chat_id) is not None:
                continue
            delay = record.delete_after_seconds
            if record.cleanup_protected and not record.retain_chat:
                delay = max(1800, delay or 0)
            observation = schedule(record.upstream_chat_id, retain_chat=record.retain_chat,
                                   delete_after_seconds=delay, source=f"{record.operation}:reconciled")
            from ..domain import CleanupObservation, CleanupState
            if isinstance(observation, CleanupObservation) and observation.state in {
                CleanupState.PENDING, CleanupState.RETAINED, CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED,
            }:
                repaired += 1
        except Exception:
            # Leave the operation protected and retry on the next maintenance pass.
            continue
    return repaired
