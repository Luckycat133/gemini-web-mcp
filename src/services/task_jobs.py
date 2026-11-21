"""Gemini Task background execution and read-only, identity-bound recovery.

One generation is dispatched per operation through the observed Task transport.
Only opaque CID/RID/candidate identities and numeric status phases are
persisted; prompts, response text, notebook titles and credentials never enter
the store. Recovery and reads never replay generation: they fresh-read exact
Task metadata and answer only for the exact saved turn and candidate.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import inspect
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..constants import is_generation_failure_reply
from ..domain import (
    CleanupObservation,
    CleanupState,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    result_from_exception,
)
from ..domain.operations import OperationReadData, operation_failure_code
from ..domain.web_tasks import TASK_STATUS_FAILED, TASK_STATUS_INPUT_REQUIRED
from ..infrastructure.account_identity import operation_account_matches
from ..infrastructure.operation_store import RETENTION_SECONDS
from ..services.artifacts import response_chat_id
from ..services.cleanup_scope import observe_cleanup_frames
from ..services.deadlines import await_before_deadline
from ..services.client_timeouts import client_timeout_scope
from ..services.model_policy import ModelPolicyError, model_policy_scope
from ..services.notebooklm import resolve_notebook_context
from ..services.web_context_transport import fetch_task_metadata, fetch_workflow_turn, refresh_task_schedule_guard
from ..services.web_context_transport import fetch_task_file_cards
from .task_result_storage import TaskResultUnavailable, load_task_files, load_task_result, save_task_result
from .task_file_artifacts import save_task_file
from .task_inputs import input_handle as _input_id

logger = logging.getLogger(__name__)

TASK_INIT_TIMEOUT_SECONDS = 60.0
TASK_READBACK_TIMEOUT_SECONDS = 60.0
TASK_IN_PROGRESS_CLEANUP_SECONDS = 1800
TASK_ANSWER_MAX_CHARS = 4000
TASK_COMPLETED_STATUS = 3
TASK_DEFAULT_TIMEOUT_SECONDS = 600
# A native setup card was observed while this goal status was 8. The status
# itself remains unknown; only an exact, independently read card is classified.
_TASK_SETUP_OBSERVED_STATUS = 8
_TASK_TERMINAL_STATES = frozenset({
    OperationState.FAILED,
    OperationState.TIMED_OUT,
    OperationState.CANCELLED,
    OperationState.UNAVAILABLE,
})
_TASK_CLEANUP_REMOVED = "task_removed_after_cleanup"


class _LostTaskLease(RuntimeError):
    """The worker lost its lease or the account changed; stop without replaying generation."""


def _task_unknown(
    store: Any, operation_id: str, worker_token: str, *, code: str | None = None,
    phase: str = "task_outcome_unverified",
) -> None:
    """Record an unverified partial outcome while keeping cleanup protection."""
    store.finish_worker(
        operation_id, worker_token=worker_token, state="partial", phase=phase,
        verification_status="not_observed", error_code=code,
    )


def _task_input_rejected(store: Any, operation_id: str, worker_token: str) -> None:
    store.finish_worker(
        operation_id, worker_token=worker_token, state="failed", phase="input_rejected",
        verification_status="input_rejected", error_code="INVALID_ARGUMENT",
    )


def _task_response_identity(value: Any) -> tuple[str, str | None, str | None] | None:
    """Extract opaque (chat_id, response_id, candidate_id) from one upstream frame."""
    cid = response_chat_id(value)
    metadata = getattr(value, "metadata", None)
    rid = metadata[1] if isinstance(metadata, (list, tuple)) and len(metadata) > 1 else None
    try:
        rcid = getattr(value, "rcid", None)
    except (IndexError, AttributeError):
        rcid = None
    if not rcid and isinstance(metadata, (list, tuple)) and len(metadata) > 2:
        rcid = metadata[2]
    if not cid or not isinstance(cid, str):
        return None
    return cid, rid if isinstance(rid, str) else None, rcid if isinstance(rcid, str) else None


def _checkpoint_task(store: Any, operation_id: str, worker_token: str, response: Any,
                     previous_turn: tuple[str, str] | None = None) -> None:
    """Persist the first observed Task identity; never overwrite a conflicting turn."""
    record = store.get(operation_id) if hasattr(store, "get") else None
    cid = response_chat_id(response) or ""
    if record is not None and (not operation_account_matches(record) or not store.is_worker_owner(operation_id, worker_token)):
        if cid:
            store.attach_late_chat(operation_id, cid)
        raise _LostTaskLease()
    metadata = getattr(response, "metadata", ())
    rid = metadata[1] if isinstance(metadata, (list, tuple)) and len(metadata) > 1 else ""
    try:
        rcid = getattr(response, "rcid", "")
    except (IndexError, AttributeError):
        rcid = ""
    if not rcid and isinstance(metadata, (list, tuple)) and len(metadata) > 2:
        rcid = metadata[2]
    if previous_turn is not None:
        if rid == previous_turn[0]:
            rid, rcid = "", ""
        elif rcid == previous_turn[1]:
            # ChatSession keeps the previous candidate while receiving the new
            # RID. Never checkpoint that stale candidate as the new answer.
            rcid = ""
    if cid and not store.checkpoint_media(
        operation_id, worker_token=worker_token, chat_id=cid,
        response_id=rid or None, candidate_id=rcid or None,
    ):
        # A replacement worker may have taken the lease while the SDK request
        # was still receiving its first CID. Save only the opaque ID under the
        # existing protected operation; never schedule under a different account
        # or replace a conflicting identity.
        store.attach_late_chat(operation_id, cid)
        raise _LostTaskLease()
    if record is not None and not operation_account_matches(record):
        raise _LostTaskLease()


def _optional_scope(client: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    """Enter a client-scoped context when the SDK client offers it."""
    factory = getattr(client, name, None)
    if not callable(factory):
        if name == "notebook_scope" and (not args or not args[0]):
            return nullcontext()
        raise ModelPolicyError("The required Web workflow transport is unavailable.")
    return factory(*args, **kwargs)


def _task_account_mismatch() -> DomainResult[OperationReadData]:
    return DomainResult.failure(
        DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
        verification_status="account_mismatch_or_unbound",
    )


def _partial_saved_answer(record: Any, saved: Any, *, reason: str) -> DomainResult[OperationReadData]:
    """Keep a verified answer usable when its associated file set is uncertain."""
    notebook_chat = record.operation == "gemini_notebook_chat"
    # Notebook Chat has no associated Task files. A failure here means its
    # answer itself failed revalidation, so an earlier snapshot is stale.
    artifact, preview = saved if saved is not None and not notebook_chat else (None, None)
    data = OperationReadData(
        operation_id=record.operation_id, operation=record.operation, state=OperationState.PARTIAL,
        continuation_possible=bool(record.cleanup_protected), report_available=False,
        verification_status=reason, task_response=preview[:TASK_ANSWER_MAX_CHARS] if preview else None,
        execution_mode="chat" if notebook_chat else "task", upstream_chat_id=record.upstream_chat_id,
        artifacts=(artifact,) if artifact is not None else (),
        artifact_state=artifact.state if artifact is not None else None,
    )
    message = ("Notebook Chat answer artifact could not be reverified." if notebook_chat
               else "Task file evidence is incomplete; the saved answer remains available.")
    return _task_read_result(data, warnings=(DomainWarning(code=reason, message=message),))


def _setup_app_name(turn: Any, cid: str, rid: str, rcid: str) -> str | None:
    if (not isinstance(turn, dict) or turn.get("chat_id") != cid or turn.get("rid") != rid
            or turn.get("rcid") != rcid or turn.get("setup_required") is not True
            or turn.get("setup_kind") != "app_connection"):
        return None
    name = turn.get("setup_app_name")
    if (not isinstance(name, str) or not name.strip() or len(name) > 160
            or any(ord(character) < 32 for character in name)):
        return None
    return name


def _native_task_url(cid: str) -> str:
    return "https://gemini.google.com/app/" + quote(cid.removeprefix("c_"), safe="")


def _task_read_result(
    data: OperationReadData, *, warnings: tuple[DomainWarning, ...] = (),
) -> DomainResult[OperationReadData]:
    if data.state in _TASK_TERMINAL_STATES:
        workflow = "Notebook Chat" if data.execution_mode == "chat" else "Task"
        return DomainResult.failure(
            operation_failure_code(data.error_code), f"The {workflow} operation did not complete.",
            data=data, operation_state=data.state, verification_status=data.verification_status,
            warnings=warnings,
        )
    return DomainResult.success(
        data, operation_state=data.state, verification_status=data.verification_status, warnings=warnings,
    )


async def execute_task_job(request: dict, *, operation_id: str, store: Any, worker_token: str) -> None:
    """Generate one Task once, checkpoint its identity, then recover read-only.

    The request dict is never persisted. Max one generate_content call is made;
    recovery reads Task metadata only and never generates.
    """
    from ..client_wrapper import (
        get_gemini_client, initialize_client, schedule_remote_chat_cleanup_from_response,
    )

    prompt = request.get("prompt", "")
    model = request.get("model", "flash")
    thinking_level = request.get("thinking_level", "standard")
    notebook_ids = request.get("notebook_ids") or ()
    raw_context = request.get("chat_context")
    action_choice_id = request.get("task_action_choice_id")
    if action_choice_id is not None and (raw_context is None or not isinstance(action_choice_id, str)
            or not action_choice_id.strip() or len(action_choice_id) > 512
            or any(ord(char) < 32 for char in action_choice_id)):
        _task_input_rejected(store, operation_id, worker_token)
        return
    previous_turn = ((raw_context[1], raw_context[2])
                     if isinstance(raw_context, list) and len(raw_context) == 3 else None)
    timeout = request.get("timeout_seconds", TASK_DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        timeout = TASK_DEFAULT_TIMEOUT_SECONDS
    if not isinstance(notebook_ids, (list, tuple)):
        _task_input_rejected(store, operation_id, worker_token)
        return
    notebook_ids = tuple(notebook_ids)
    for notebook_id in notebook_ids:
        if not isinstance(notebook_id, str) or not notebook_id.strip():
            _task_input_rejected(store, operation_id, worker_token)
            return

    record = store.get(operation_id) if hasattr(store, "get") else None
    if not store.is_worker_owner(operation_id, worker_token) or (
        record is not None and not operation_account_matches(record)
    ):
        return
    if not isinstance(prompt, str) or not prompt.strip():
        _task_input_rejected(store, operation_id, worker_token)
        return
    notebook_chat = record is not None and record.operation == "gemini_notebook_chat"

    try:
        await await_before_deadline(initialize_client(), timeout=TASK_INIT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        if store.is_worker_owner(operation_id, worker_token) and (
            record is None or operation_account_matches(record)
        ):
            _task_unknown(store, operation_id, worker_token, phase="task_initialize_timeout", code="TIMED_OUT")
        return
    client = get_gemini_client()
    if not store.is_worker_owner(operation_id, worker_token) or (
        record is not None and not operation_account_matches(record)
    ):
        return

    resolved_notebooks: tuple[tuple[str, str], ...] = ()
    if notebook_ids:
        try:
            resolved_notebooks = await await_before_deadline(
                resolve_notebook_context(client, list(notebook_ids)), timeout=TASK_READBACK_TIMEOUT_SECONDS,
            )
        except ModelPolicyError:
            if store.is_worker_owner(operation_id, worker_token) and (
                record is None or operation_account_matches(record)
            ):
                store.finish_worker(
                    operation_id, worker_token=worker_token, state="failed",
                    phase="task_notebook_unresolved", verification_status="notebook_not_found",
                    error_code="CAPABILITY_UNAVAILABLE",
                )
            return
    if not store.is_worker_owner(operation_id, worker_token) or (
        record is not None and not operation_account_matches(record)
    ):
        return

    observed_cids: set[str] = set()

    def observe(value: Any) -> None:
        identity = _task_response_identity(value)
        if identity is None:
            return
        cid, _rid, _rcid = identity
        try:
            _checkpoint_task(store, operation_id, worker_token, value, previous_turn)
        except _LostTaskLease:
            raise
        if cid not in observed_cids:
            observed_cids.add(cid)
            # Register the cleanup ledger for the observed chat without starting
            # its deletion yet; the read path finalizes cleanup after a verified
            # answer, and the in-progress TTL keeps the chat alive meanwhile.
            try:
                retain = bool(getattr(record, "retain_chat", False))
                delete_after = getattr(record, "delete_after_seconds", None)
                delete_after = int(delete_after) if isinstance(delete_after, (int, float)) else None
                ttl = delete_after if retain else max(TASK_IN_PROGRESS_CLEANUP_SECONDS, delete_after or 0)
                schedule_remote_chat_cleanup_from_response(
                    value, retain_chat=retain, delete_after_seconds=ttl, source="task_job:in_progress",
                )
            except Exception:
                pass

    def on_late(future: asyncio.Future[Any]) -> None:
        # A reply that arrives after the deadline is still a lost reply for this
        # dispatch: preserve its opaque IDs without treating it as completed.
        def preserve_late(done: asyncio.Future[Any]) -> None:
            try:
                late_response = done.result()
            except BaseException:
                return
            try:
                _checkpoint_task(store, operation_id, worker_token, late_response, previous_turn)
            except Exception:
                pass

        future.add_done_callback(preserve_late)

    arguments: dict[str, Any] = {
        "prompt": prompt, "model": model, "thinking_level": thinking_level, "timeout": int(timeout),
    }
    chat_factory = getattr(client, "create_cleanup_chat", None)
    chat_context = request.get("chat_context")
    if chat_context is not None:
        if (not isinstance(chat_context, list) or len(chat_context) != 3
                or not all(isinstance(value, str) and value for value in chat_context)
                or record is None or chat_context[0] != record.upstream_chat_id or notebook_chat):
            _task_input_rejected(store, operation_id, worker_token)
            return
        chat = client.start_chat(metadata=chat_context)
    else:
        chat = chat_factory() if callable(chat_factory) else None
    if chat is not None:
        arguments["chat"] = chat
    try:
        parameters = inspect.signature(client.generate_content).parameters
        if "timeout" not in parameters and not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            arguments.pop("timeout", None)
    except (TypeError, ValueError):
        pass

    try:
        with (
            nullcontext() if notebook_chat else _optional_scope(
                client, "task_generation_scope", *([chat_context[0]] if chat_context is not None else []),
                **({"action_choice_id": action_choice_id} if action_choice_id is not None else {})),
            _optional_scope(client, "notebook_scope", resolved_notebooks),
            model_policy_scope("notebook_chat" if notebook_chat else "task", allow_fallback=False),
            observe_cleanup_frames(observe),
            client_timeout_scope(client, int(timeout)),
        ):
            response = await await_before_deadline(
                client.generate_content(**arguments), timeout=float(timeout), on_late=on_late,
            )
    except _LostTaskLease:
        return
    except asyncio.CancelledError:
        raise
    except ModelPolicyError:
        if store.is_worker_owner(operation_id, worker_token) and operation_account_matches(record):
            store.finish_worker(operation_id, worker_token=worker_token, state="failed", phase="workflow_unavailable",
                                verification_status="capability_unavailable", error_code="CAPABILITY_UNAVAILABLE")
            latest = store.get(operation_id)
            if latest is not None and not latest.upstream_chat_id:
                store.release_cleanup(operation_id)
        return
    except Exception as exc:
        if chat is not None:
            try:
                _checkpoint_task(store, operation_id, worker_token, chat, previous_turn)
            except Exception:
                pass
        _task_unknown(
            store, operation_id, worker_token,
            code="TIMED_OUT" if isinstance(exc, asyncio.TimeoutError) else None,
        )
        return

    if not store.is_worker_owner(operation_id, worker_token):
        return
    try:
        # Checkpoint the final output identity under the same protected operation.
        _checkpoint_task(store, operation_id, worker_token, response, previous_turn)
    except _LostTaskLease:
        return
    except Exception as exc:
        _task_unknown(store, operation_id, worker_token, phase="task_identity_conflict",
                      code="TIMED_OUT" if isinstance(exc, asyncio.TimeoutError) else None)
        return
    record = store.get(operation_id) if hasattr(store, "get") else None
    if record is None or not store.is_worker_owner(operation_id, worker_token):
        return
    # Independently recover the same saved task; no generation in recovery.
    await recover_task_job(record, store=store, worker_token=worker_token)


async def recover_task_job(record: Any, *, store: Any, worker_token: str) -> None:
    """Read exact Task metadata once; never send a generation request again."""
    if not operation_account_matches(record):
        return
    cid = getattr(record, "upstream_chat_id", None)
    rid = getattr(record, "upstream_response_id", None)
    rcid = getattr(record, "upstream_candidate_id", None)
    if not cid or not rid:
        _task_unknown(store, record.operation_id, worker_token, phase="task_identity_incomplete")
        return
    from ..client_wrapper import get_gemini_client, initialize_client

    try:
        await await_before_deadline(initialize_client(), timeout=TASK_INIT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        if operation_account_matches(record):
            _task_unknown(
                store, record.operation_id, worker_token, phase="task_initialize_timeout", code="TIMED_OUT",
            )
        return
    client = get_gemini_client()
    if not store.is_worker_owner(record.operation_id, worker_token) or not operation_account_matches(record):
        return
    if not rcid:
        # A timed-out stream may have delivered the original RID before its
        # candidate. Bind only a unique candidate in that exact saved turn.
        try:
            observed_turn = await fetch_workflow_turn(client, cid, rid, None)
        except Exception:
            observed_turn = None
        candidate = observed_turn.get("rcid") if isinstance(observed_turn, dict) else None
        if (not operation_account_matches(record) or not isinstance(candidate, str) or not candidate
                or not store.checkpoint_media(record.operation_id, worker_token=worker_token, chat_id=cid,
                                              response_id=rid, candidate_id=candidate)):
            _task_unknown(store, record.operation_id, worker_token, phase="task_identity_incomplete")
            return
        rcid = candidate
    if record.operation == "gemini_notebook_chat":
        try:
            turn = await await_before_deadline(
                fetch_workflow_turn(client, cid, rid, rcid), timeout=TASK_READBACK_TIMEOUT_SECONDS,
            )
        except Exception:
            turn = None
        if not store.is_worker_owner(record.operation_id, worker_token) or not operation_account_matches(record):
            return
        if turn is not None and turn.get("completed") is True:
            if is_generation_failure_reply(turn.get("text")):
                # Reuse the failed-answer recovery phase so cleanup can still
                # retry the same exact candidate after an interrupted read.
                store.finish_worker(record.operation_id, worker_token=worker_token, state="failed",
                                    phase="task_failed", verification_status="notebook_reply_rejected",
                                    error_code="UPSTREAM_REJECTED")
                return
            store.finish_worker(record.operation_id, worker_token=worker_token, state="completed",
                                phase="notebook_reply_completed", verification_status="exact_reply_completed")
        else:
            _task_unknown(store, record.operation_id, worker_token, phase="notebook_reply_unverified")
        return
    read_timed_out = False
    metadata: Any = None
    try:
        metadata = await await_before_deadline(
            fetch_task_metadata(client, cid), timeout=TASK_READBACK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        read_timed_out = True
    except Exception:
        metadata = None
    if not store.is_worker_owner(record.operation_id, worker_token) or not operation_account_matches(record):
        return
    status: int | None = None
    if isinstance(metadata, dict):
        raw_status = metadata.get("upstream_status")
        if type(raw_status) is int:
            status = raw_status
        raw_chat_id = metadata.get("chat_id")
        if raw_chat_id != cid:
            # Fresh metadata belongs to a different Task; treat as absence.
            metadata = None
            status = None
    if status == TASK_COMPLETED_STATUS:
        if store.is_worker_owner(record.operation_id, worker_token):
            store.finish_worker(
                record.operation_id, worker_token=worker_token, state="completed", phase="task_completed",
                verification_status="task_completed_verified",
            )
    elif status == TASK_STATUS_FAILED:
        store.finish_worker(record.operation_id, worker_token=worker_token, state="failed", phase="task_failed",
                            verification_status="task_failed_verified", error_code="UPSTREAM_REJECTED")
    elif status == TASK_STATUS_INPUT_REQUIRED:
        try:
            turn = await fetch_workflow_turn(client, cid, rid, rcid)
        except Exception:
            turn = None
        if (isinstance(turn, dict) and turn.get("input_required") is True
                and operation_account_matches(record) and store.is_worker_owner(record.operation_id, worker_token)):
            _task_unknown(store, record.operation_id, worker_token, phase="task_input_required")
        else:
            _task_unknown(store, record.operation_id, worker_token, phase="task_input_required")
    elif status == _TASK_SETUP_OBSERVED_STATUS:
        try:
            turn = await await_before_deadline(
                fetch_workflow_turn(client, cid, rid, rcid), timeout=TASK_READBACK_TIMEOUT_SECONDS,
            )
        except Exception:
            turn = None
        if not operation_account_matches(record) or not store.is_worker_owner(record.operation_id, worker_token):
            return
        setup = _setup_app_name(turn, cid, rid, rcid)
        _task_unknown(store, record.operation_id, worker_token,
                      phase="task_setup_required" if setup is not None else "task_outcome_unverified")
    else:
        _task_unknown(
            store, record.operation_id, worker_token, phase="task_outcome_unverified",
            code="TIMED_OUT" if read_timed_out else None,
        )


def _bound_answer(text: str) -> tuple[str, bool]:
    if len(text) > TASK_ANSWER_MAX_CHARS:
        return text[:TASK_ANSWER_MAX_CHARS], True
    return text, False


async def _read_exact_answer(client: Any, cid: str, rid: str, rcid: str, *, limit: int) -> str | None:
    """Require the original candidate's explicit completion, never SDK text alone."""
    try:
        turn = await await_before_deadline(
            fetch_workflow_turn(client, cid, rid, rcid, limit=limit), timeout=TASK_READBACK_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    text = turn.get("text") if isinstance(turn, dict) else None
    if isinstance(turn, dict) and turn.get("completed") is True and isinstance(text, str) and text.strip():
        return text
    return None


async def _finish_saved_task_cleanup(record: Any, store: Any, client: Any, finalize: Any,
                                      state: OperationState) -> tuple[CleanupObservation | None, tuple[DomainWarning, ...]]:
    """A saved answer permits cleanup only after checking for persistent schedules."""
    if not operation_account_matches(record):
        return None, ()
    try:
        if record.operation == 'gemini_run_task':
            schedules = await refresh_task_schedule_guard(client, record.upstream_chat_id, store=store)
            if not operation_account_matches(record):
                return None, ()
        else:
            schedules = []
        # Scheduling and initialization are awaits; recheck all local bytes
        # immediately before the atomic cleanup release.
        if load_task_result(store, record, include_text=False) is None:
            raise TaskResultUnavailable('Task answer unavailable.')
        if record.operation == 'gemini_run_task':
            load_task_files(store, record)
        terminal = 'failed' if state == OperationState.FAILED else 'completed'
        if not store.release_task_result_cleanup(record.operation_id, terminal_state=terminal):
            return None, ()
        if schedules:
            store.update_cleanup(record.operation_id, CleanupState.RETAINED.value)
            return CleanupObservation(state=CleanupState.RETAINED, upstream_chat_id=record.upstream_chat_id), (
                DomainWarning(code='task_schedule_retained',
                              message='The Task thread is retained because deleting it would also delete its schedules.'),)
        if finalize is None:
            raise RuntimeError('cleanup callback unavailable')
        observation = await finalize(record.upstream_chat_id, retain_chat=record.retain_chat,
                                     delete_after_seconds=record.delete_after_seconds,
                                     source=record.operation, client=client)
        if not operation_account_matches(record):
            return None, ()
        store.update_cleanup(record.operation_id, observation.state.value)
        return observation, ()
    except TaskResultUnavailable:
        raise
    except (OSError, UnicodeError) as error:
        raise TaskResultUnavailable('Task artifact could not be revalidated.') from error
    except Exception:
        message = ("Notebook Chat cleanup could not be verified." if record.operation == "gemini_notebook_chat"
                   else "Task cleanup or its schedule protection could not be verified.")
        return None, (DomainWarning(code="cleanup_unverified", message=message),)


async def read_task_operation(
    record: Any,
    *,
    store: Any,
    client_provider: Any,
    client_initializer: Any,
    finalize_chat_cleanup: Any,
    include_answer: bool,
) -> DomainResult[OperationReadData]:
    """Read one saved Task operation without replaying generation or reading text.

    include_answer=False never reads conversation text or deletes anything.
    include_answer=True may read bounded history for the exact saved CID/RID/
    candidate and a completed status3 Task only, then releases cleanup on a
    verified exact answer.
    """
    notebook_chat = getattr(record, "operation", "") == "gemini_notebook_chat"
    execution_mode = "chat" if notebook_chat else "task"
    workflow = "Notebook Chat" if notebook_chat else "Task"
    operation_id = getattr(record, "operation_id", "") or ""
    if not isinstance(operation_id, str) or not operation_id.strip():
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT, "operation_id must not be blank.",
            verification_status="input_rejected",
        )
    if not operation_account_matches(record):
        return _task_account_mismatch()

    now = store.clock()
    created_at = getattr(record, "created_at", now) or now
    expired = now - created_at >= RETENTION_SECONDS
    expired_protected = expired and bool(getattr(record, "cleanup_protected", False)) and bool(
        getattr(record, "upstream_chat_id", None),
    )
    if expired and not expired_protected:
        store.delete_expired(operation_id)
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT, "The operation handle has expired.",
            verification_status="operation_expired",
        )

    upstream_chat_id = getattr(record, "upstream_chat_id", None)
    try:
        saved_state = OperationState(getattr(record, "state", OperationState.PARTIAL.value))
    except ValueError:
        saved_state = OperationState.PARTIAL
    heartbeat = getattr(record, "heartbeat_at", None)
    worker_active = bool(
        getattr(record, "worker_token", None) and heartbeat is not None and now - heartbeat < 60,
    )

    if not upstream_chat_id:
        state = saved_state if (worker_active or saved_state in _TASK_TERMINAL_STATES) else OperationState.PARTIAL
        data = OperationReadData(
            operation_id=operation_id, operation=getattr(record, "operation", ""), state=state,
            continuation_possible=worker_active, report_available=False,
            verification_status="task_dispatched" if worker_active else "task_identity_incomplete",
            worker_active=worker_active, error_code=getattr(record, "error_code", None),
            execution_mode=execution_mode,
        )
        return _task_read_result(data)

    saved_cleanup_state = getattr(record, "cleanup_state", None) or "not_scheduled"
    try:
        saved_artifact = load_task_result(store, record, include_text=include_answer)
    except (TaskResultUnavailable, OSError, UnicodeError):
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED,
                                    f"The saved {workflow} result was removed or changed.",
                                    verification_status="task_artifact_verification_failed")
    if saved_artifact is not None and (notebook_chat or store.task_files_committed(operation_id)):
        artifact, preview = saved_artifact
        try:
            file_artifacts = () if notebook_chat else load_task_files(store, record)
        except (TaskResultUnavailable, OSError, UnicodeError):
            return _partial_saved_answer(record, saved_artifact, reason='task_artifact_verification_failed')
        cached_state = OperationState.FAILED if saved_state == OperationState.FAILED else OperationState.COMPLETED
        cached_warnings: tuple[DomainWarning, ...] = ()
        cleanup = (CleanupObservation(state=CleanupState(saved_cleanup_state), upstream_chat_id=upstream_chat_id)
                   if saved_cleanup_state in {state.value for state in CleanupState} else None)
        if include_answer and getattr(record, 'cleanup_protected', False):
            try:
                await await_before_deadline(client_initializer(), timeout=TASK_INIT_TIMEOUT_SECONDS)
                if not operation_account_matches(record):
                    return _task_account_mismatch()
                cleanup, cached_warnings = await _finish_saved_task_cleanup(
                    record, store, client_provider(), finalize_chat_cleanup, cached_state)
            except TaskResultUnavailable:
                return _partial_saved_answer(record, saved_artifact, reason='task_artifact_verification_failed')
            except Exception:
                cached_warnings = (DomainWarning(code="cleanup_unverified", message=f"{workflow} cleanup is still protected."),)
            if not operation_account_matches(record):
                return _task_account_mismatch()
        data = OperationReadData(
            operation_id=operation_id, operation=record.operation, state=cached_state,
            continuation_possible=False, report_available=False, verification_status="task_artifact_verified",
            task_response=preview[:TASK_ANSWER_MAX_CHARS] if preview is not None and not notebook_chat else None,
            answer=preview[:TASK_ANSWER_MAX_CHARS] if preview is not None and notebook_chat else None,
            execution_mode=execution_mode,
            upstream_chat_id=upstream_chat_id,
            upstream_status=None if notebook_chat else TASK_STATUS_FAILED if cached_state == OperationState.FAILED else TASK_COMPLETED_STATUS,
            cleanup=cleanup, error_code=record.error_code,
            artifacts=(artifact, *file_artifacts), artifact_state=artifact.state, worker_active=worker_active,
        )
        return _task_read_result(data, warnings=cached_warnings)
    if (saved_artifact is not None and not notebook_chat
            and saved_cleanup_state in {CleanupState.COMPLETED.value, CleanupState.ALREADY_COMPLETED.value}):
        return _partial_saved_answer(record, saved_artifact, reason='task_file_manifest_unavailable')
    if saved_cleanup_state in {CleanupState.COMPLETED.value, CleanupState.ALREADY_COMPLETED.value}:
        if include_answer:
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                        f"The remote {workflow} was deleted before its answer was saved.",
                                        verification_status=_TASK_CLEANUP_REMOVED)
        if saved_state != OperationState.COMPLETED:
            remote_chat = "Notebook Chat" if notebook_chat else "Task chat"
            return DomainResult.failure(
                DomainErrorCode.CAPABILITY_UNAVAILABLE,
                f"The remote {remote_chat} was deleted and cannot be read again.",
                verification_status=_TASK_CLEANUP_REMOVED,
            )
        cleanup = CleanupObservation(
            state=CleanupState(saved_cleanup_state), upstream_chat_id=upstream_chat_id,
        )
        data = OperationReadData(
            operation_id=operation_id, operation=getattr(record, "operation", ""),
            state=OperationState.COMPLETED, continuation_possible=False, report_available=False,
            verification_status=_TASK_CLEANUP_REMOVED, task_response=None, upstream_chat_id=upstream_chat_id,
            upstream_status=None, cleanup=cleanup, error_code=getattr(record, "error_code", None),
            execution_mode=execution_mode,
        )
        return DomainResult.success(
            data, operation_state=OperationState.COMPLETED, verification_status=_TASK_CLEANUP_REMOVED,
        )

    try:
        # Provider is synchronous and runs after initialization.
        await await_before_deadline(client_initializer(), timeout=TASK_INIT_TIMEOUT_SECONDS)
        client = client_provider()
    except Exception as error:
        return result_from_exception(error, logger=logger, operation="task_operation_read")
    if not operation_account_matches(record):
        return _task_account_mismatch()

    upstream_status: int | None = None
    metadata = None
    if not notebook_chat:
        try:
            metadata = await await_before_deadline(
                fetch_task_metadata(client, upstream_chat_id), timeout=TASK_READBACK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            metadata = None
        except Exception:
            logger.exception("Task metadata read failed")
    if not operation_account_matches(record):
        return _task_account_mismatch()
    if isinstance(metadata, dict):
        if metadata.get("chat_id") != upstream_chat_id:
            metadata = None
    if isinstance(metadata, dict):
        raw_status = metadata.get("upstream_status")
        if type(raw_status) is int:
            upstream_status = raw_status

    if notebook_chat:
        state = saved_state if saved_state in _TASK_TERMINAL_STATES or saved_state == OperationState.COMPLETED else OperationState.PARTIAL
        if state == OperationState.COMPLETED:
            verification_status = "exact_reply_completed"
        elif state == OperationState.FAILED and getattr(record, "verification_status", None) == "notebook_reply_rejected":
            verification_status = "notebook_reply_rejected"
        else:
            verification_status = "notebook_reply_unverified"
    elif upstream_status == TASK_COMPLETED_STATUS:
        state = OperationState.COMPLETED
        verification_status = "task_completed_verified"
    elif upstream_status == TASK_STATUS_FAILED:
        state = OperationState.FAILED
        verification_status = "task_failed_verified"
    elif upstream_status == TASK_STATUS_INPUT_REQUIRED:
        state = OperationState.PARTIAL
        verification_status = "task_input_required"
    elif upstream_status is not None:
        state = saved_state if saved_state in _TASK_TERMINAL_STATES else OperationState.PARTIAL
        verification_status = "task_status_not_completed"
    else:
        state = saved_state if saved_state in _TASK_TERMINAL_STATES else OperationState.PARTIAL
        verification_status = "task_readback_unverified" if upstream_chat_id else "task_identity_incomplete"

    warnings: list[DomainWarning] = []
    answer: str | None = None
    input_required = not notebook_chat and upstream_status == TASK_STATUS_INPUT_REQUIRED
    rid = getattr(record, "upstream_response_id", None)
    rcid = getattr(record, "upstream_candidate_id", None)
    # Upstream may keep status5 and the selected old card while the single
    # continuation dispatch starts. Only that consumed turn (or the interval
    # before a new exact turn arrives) is suppressed, never a fresh card.
    awaiting_continuation = False
    if not notebook_chat:
        awaiting_continuation = (store.task_input_dispatched(operation_id, _input_id(record)) if rid and rcid
                                 else store.has_task_input(operation_id))
    if input_required and awaiting_continuation:
        input_required = False
        verification_status = "task_awaiting_continuation"
    input_prompt: str | None = None
    input_options: tuple[dict[str, str], ...] = ()
    # Status-only reads do not fetch conversation content. A worker may already
    # have verified this same saved turn's setup card; app labels stay transient.
    needs_attention = (upstream_status == _TASK_SETUP_OBSERVED_STATUS
                       and getattr(record, "phase", None) == "task_setup_required")
    setup_required = False
    setup_app_name: str | None = None
    if needs_attention:
        verification_status = "task_setup_previously_observed"
    read_limit = 0
    if include_answer and (not awaiting_continuation or upstream_status == _TASK_SETUP_OBSERVED_STATUS):
        if not notebook_chat and upstream_status == _TASK_SETUP_OBSERVED_STATUS:
            # A fresh result read must revalidate the card, even if an earlier
            # worker observed setup. Never use a stale label or cached approval.
            setup_required = False
            needs_attention = False
            verification_status = "task_status_not_completed"
            if rid and rcid:
                try:
                    turn = await await_before_deadline(
                        fetch_workflow_turn(client, upstream_chat_id, rid, rcid),
                        timeout=TASK_READBACK_TIMEOUT_SECONDS,
                    )
                except Exception:
                    turn = None
                if not operation_account_matches(record):
                    return _task_account_mismatch()
                setup_app_name = _setup_app_name(turn, upstream_chat_id, rid, rcid)
                setup_required = setup_app_name is not None
                needs_attention = setup_required
                if setup_required:
                    verification_status = "task_setup_required"
        elif not notebook_chat and upstream_status == TASK_STATUS_INPUT_REQUIRED and not awaiting_continuation and rid and rcid:
            try:
                turn = await fetch_workflow_turn(client, upstream_chat_id, rid, rcid)
            except Exception:
                turn = None
            if not operation_account_matches(record):
                return _task_account_mismatch()
            if isinstance(turn, dict) and turn.get("input_required") is True:
                input_required = True
                raw_prompt = turn.get("input_prompt")
                input_prompt = raw_prompt[:1000] if isinstance(raw_prompt, str) else None
                input_options = tuple({key: option[key] for key in ("label", "prompt", "input_option_id", "choice_label")
                                       if isinstance(option.get(key), str)}
                                      for option in (turn.get("input_options") or ()) if isinstance(option, dict))
                verification_status = "task_input_required"
        elif notebook_chat and isinstance(rid, str) and rid and isinstance(rcid, str) and rcid:
            read_limit = 100
            try:
                turn = await await_before_deadline(
                    fetch_workflow_turn(client, upstream_chat_id, rid, rcid), timeout=TASK_READBACK_TIMEOUT_SECONDS,
                )
            except Exception:
                turn = None
            if not operation_account_matches(record):
                return _task_account_mismatch()
            if isinstance(turn, dict) and turn.get("completed") is True and isinstance(turn.get("text"), str) and turn["text"].strip():
                answer = turn["text"]
                state = OperationState.FAILED if is_generation_failure_reply(answer) else OperationState.COMPLETED
                verification_status = "notebook_reply_rejected" if state == OperationState.FAILED else "exact_reply_completed"
            else:
                verification_status = "notebook_reply_unverified"
        elif (
            upstream_status in {TASK_COMPLETED_STATUS, TASK_STATUS_FAILED} and upstream_chat_id and isinstance(rid, str) and rid
            and isinstance(rcid, str) and rcid
        ):
            read_limit = 10
            answer = await _read_exact_answer(client, upstream_chat_id, rid, rcid, limit=10)
            if not operation_account_matches(record):
                return _task_account_mismatch()
            if answer is None:
                read_limit = 100
                answer = await _read_exact_answer(client, upstream_chat_id, rid, rcid, limit=100)
                if not operation_account_matches(record):
                    return _task_account_mismatch()
            if answer is None:
                verification_status = "task_answer_not_observed"
                warnings.append(DomainWarning(
                    code="task_answer_not_in_read_window",
                    message="No ready answer was found in the bounded recent-history window.",
                ))
        elif upstream_status is not None and not awaiting_continuation:
            verification_status = "task_status_not_completed"
        elif not (isinstance(rid, str) and rid and isinstance(rcid, str) and rcid):
            verification_status = "task_identity_incomplete"
        # An unreadable status keeps task_readback_unverified; no answer read is attempted.

    if needs_attention:
        input_required = False
        input_prompt = None
        input_options = ()
        warnings.append(DomainWarning(
            code="task_setup_requires_native_ui",
            message="Connect or decline the app in the original Gemini Task. A text reply cannot complete app setup or OAuth.",
        ))

    finalized: CleanupObservation | None = None
    answer_artifact = None
    file_artifacts = ()
    if answer is not None:
        try:
            answer_artifact = save_task_result(store, record, answer)
            if not notebook_chat:
                if not isinstance(rid, str) or not rid or not isinstance(rcid, str) or not rcid:
                    raise ValueError('Task result identity unavailable')
                if not answer_artifact.local_path:
                    raise ValueError('Task answer location unavailable')
                cards = await fetch_task_file_cards(client, upstream_chat_id, rid, rcid)
                if not operation_account_matches(record):
                    return _task_account_mismatch()
                downloaded = []
                for card in cards:
                    downloaded.append(await save_task_file(
                        client.client, card, output_dir=Path(answer_artifact.local_path).parent,
                        chat_id=upstream_chat_id, rid=rid, rcid=rcid,
                        account_matches=lambda: operation_account_matches(record)))
                    if not operation_account_matches(record):
                        return _task_account_mismatch()
                file_artifacts = tuple(downloaded)
                store.commit_task_files(operation_id, response_id=rid, candidate_id=rcid, artifacts=file_artifacts)
            if not store.checkpoint_task_result_state(
                    operation_id, terminal_state='failed' if state == OperationState.FAILED else 'completed'):
                return _task_account_mismatch()
        except Exception:
            if not operation_account_matches(record):
                return _task_account_mismatch()
            answer = None
            answer_artifact = None
            file_artifacts = ()
            state = OperationState.PARTIAL
            verification_status = 'task_files_unverified'
            message = ("The Notebook Chat answer could not be verified; its thread remains protected." if notebook_chat
                       else "The Task result files could not be verified; its thread remains protected.")
            warnings.append(DomainWarning(code='task_files_unverified',
                message=message))
    if answer is not None:
        answer, truncated = _bound_answer(answer)
        if truncated:
            warnings.append(DomainWarning(
                code="task_answer_truncated",
                message=f"The preview is limited to {TASK_ANSWER_MAX_CHARS} characters; the complete answer is in its artifact.",
            ))
        verification_status = "task_answer_observed"
        if not operation_account_matches(record):
            return _task_account_mismatch()
        try:
            finalized, cleanup_warnings = await _finish_saved_task_cleanup(
                record, store, client, finalize_chat_cleanup, state)
        except TaskResultUnavailable:
            try:
                saved = load_task_result(store, record, include_text=True)
            except (TaskResultUnavailable, OSError, UnicodeError):
                saved = None
            return _partial_saved_answer(record, saved, reason='task_artifact_verification_failed')
        warnings.extend(cleanup_warnings)
        if not operation_account_matches(record):
            return _task_account_mismatch()

    cleanup_observation: CleanupObservation | None = None
    if finalized is not None:
        cleanup_observation = finalized
    else:
        cleanup_state_value = saved_cleanup_state if saved_cleanup_state in {
            state.value for state in CleanupState
        } else "not_scheduled"
        if cleanup_state_value != "not_scheduled" and upstream_chat_id:
            try:
                cleanup_observation = CleanupObservation(
                    state=CleanupState(cleanup_state_value), upstream_chat_id=upstream_chat_id,
                )
            except ValueError:
                cleanup_observation = None

    continuation_possible = (
        state not in _TASK_TERMINAL_STATES
        and not needs_attention
        and not (finalized is not None and finalized.state in {CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED})
    )
    data = OperationReadData(
        operation_id=operation_id, operation=getattr(record, "operation", ""), state=state,
        continuation_possible=continuation_possible, report_available=False,
        verification_status=verification_status, task_response=None if notebook_chat else answer,
        answer=answer if notebook_chat else None, execution_mode=execution_mode,
        input_required=input_required, input_prompt=input_prompt,
        input_url=f"https://gemini.google.com/app/{upstream_chat_id.removeprefix('c_')}" if input_required else None,
        input_id=_input_id(record) if input_required and input_options else None,
        input_options=input_options,
        needs_attention=needs_attention, setup_required=setup_required,
        setup_kind="app_connection" if needs_attention else None,
        setup_app_name=setup_app_name,
        setup_url=_native_task_url(upstream_chat_id) if needs_attention else None,
        upstream_chat_id=upstream_chat_id, upstream_status=upstream_status,
        cleanup=cleanup_observation, worker_active=worker_active,
        error_code="UPSTREAM_REJECTED" if state == OperationState.FAILED else getattr(record, "error_code", None),
        read_window_limit=read_limit,
        artifacts=(answer_artifact, *file_artifacts) if answer_artifact is not None else (),
        artifact_state=answer_artifact.state if answer_artifact is not None else None,
    )
    return _task_read_result(data, warnings=tuple(warnings))
