"""Explicit, idempotent replies to an observed Task confirmation card."""

import asyncio
from dataclasses import replace
import hashlib
import re
import uuid
from typing import Any

from ..domain import DomainErrorCode, DomainResult, DomainWarning, LongOperationData
from ..infrastructure.account_identity import operation_account_matches
from .deadlines import await_before_deadline

TASK_INPUT_INITIALIZE_SECONDS = 60.0
TASK_INPUT_READ_SECONDS = 60.0


def input_handle(record: Any) -> str:
    identity = "\0".join((record.operation_id, record.upstream_chat_id or "",
                           record.upstream_response_id or "", record.upstream_candidate_id or ""))
    return "input_" + hashlib.sha256(identity.encode()).hexdigest()[:32]


async def submit_task_input(request: dict[str, Any], *, operation_id: str) -> DomainResult[LongOperationData]:
    """Send one new explicit instruction; a duplicate card token never sends it twice."""
    from ..client_wrapper import get_gemini_client, initialize_client
    from ..infrastructure.operation_store import OperationStore
    from .background import _accepted, _launch_detached_worker
    from .web_context_transport import fetch_task_metadata, fetch_workflow_turn

    token = request.get("input_id")
    if not isinstance(token, str) or not re.fullmatch(r"input_[0-9a-f]{32}", token):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Use the input_id returned for this Task.")
    option_id = request.get("input_option_id")
    if option_id is not None and (not isinstance(option_id, str) or not re.fullmatch(r"option_[0-9a-f]{32}", option_id)):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Use an input_option_id returned by this card.")
    store = OperationStore()
    record = store.get(operation_id)
    if record is None or record.operation != "gemini_run_task":
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The original Task handle was not found.")
    try:
        await await_before_deadline(initialize_client(), timeout=TASK_INPUT_INITIALIZE_SECONDS)
    except Exception:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "Task input initialization was not verified; no reply was sent.",
                                    verification_status="task_input_unverified")
    if not operation_account_matches(record):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.")
    if store.task_input_dispatched(operation_id, token):
        result = _accepted(store.get(operation_id) or record)
        return replace(result, warnings=(*result.warnings, DomainWarning(code="task_input_consumed",
            message="This card was already answered; this call sent no reply and did not change the original choice.")))
    if (token != input_handle(record) or not record.upstream_chat_id
            or not record.upstream_response_id or not record.upstream_candidate_id):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The input token does not identify the saved Task turn.")
    client = get_gemini_client()
    try:
        goal = await await_before_deadline(fetch_task_metadata(client, record.upstream_chat_id), timeout=TASK_INPUT_READ_SECONDS)
        if not operation_account_matches(record):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.")
        if (not goal or goal.get("chat_id") != record.upstream_chat_id
                or type(goal.get("upstream_status")) is not int or goal["upstream_status"] != 5):
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The Task input request is no longer verified.",
                                        verification_status="task_input_unverified")
        turn = await await_before_deadline(fetch_workflow_turn(client, record.upstream_chat_id,
            record.upstream_response_id, record.upstream_candidate_id), timeout=TASK_INPUT_READ_SECONDS)
    except Exception:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "The Task input card could not be read; no reply was sent.",
                                    verification_status="task_input_unverified")
    if (not operation_account_matches(record) or not goal or goal.get("chat_id") != record.upstream_chat_id
            or goal.get("upstream_status") != 5 or not turn or turn.get("input_required") is not True):
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The exact Task input request is no longer verified.",
                                    verification_status="task_input_unverified")
    choices = turn.get("input_options") or ()
    card = turn.get("input_card")
    selected: dict[str, Any] | None = None
    if isinstance(card, dict) and card.get("unsupported") is not True:
        replies = card.get("replies")
        if isinstance(replies, list):
            matched = [reply for reply in replies if isinstance(reply, dict) and
                       (reply.get("input_option_id") == option_id if option_id is not None else
                        card.get("requires_option_id") is False and reply.get("prompt") == request.get("prompt"))]
            if len(matched) == 1:
                selected = matched[0]
    elif card is None and option_id is None:
        # Compatibility for already supported cards without native choices.
        matched = [choice for choice in choices if isinstance(choice, dict) and choice.get("prompt") == request.get("prompt")]
        if len(matched) == 1:
            selected = {"prompt": matched[0]["prompt"], "action_choice_id": None, "subagent_conversation_id": None}
    if (selected is None or selected.get("prompt") != request.get("prompt")
            or selected.get("subagent_conversation_id") is not None
            or not any(isinstance(choice, dict) and choice.get("prompt") == selected.get("prompt")
                       and (option_id is None or choice.get("input_option_id") == option_id) for choice in choices)):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
                                    "Select a current input_option_id and its exact prompt; unsupported cards require the native UI.",
                                    verification_status="task_input_choice_required")
    choice_id = selected.get("action_choice_id")
    if choice_id is not None and (not isinstance(choice_id, str) or not choice_id.strip() or len(choice_id) > 512
                                 or any(ord(char) < 32 for char in choice_id)):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The native confirmation choice is unsupported.")
    worker_token = uuid.uuid4().hex
    if not store.begin_task_input(operation_id, token, response_id=record.upstream_response_id,
                                  candidate_id=record.upstream_candidate_id, worker_token=worker_token):
        if store.task_input_dispatched(operation_id, token):
            return _accepted(store.get(operation_id) or record)
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The Task changed before input was claimed.")
    payload: dict[str, Any] = {
        "operation_id": operation_id, "worker_token": worker_token, "kind": "task_input",
        "request": {"prompt": request["prompt"], "model": record.requested_model or "flash",
                    "thinking_level": record.thinking_level or "standard",
                    "timeout_seconds": request.get("timeout_seconds", 600),
                    "chat_context": [record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id]},
    }
    if choice_id is not None:
        payload["request"]["task_action_choice_id"] = choice_id
    try:
        pid = await asyncio.to_thread(_launch_detached_worker, payload, store)
        if not operation_account_matches(record):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.")
        store.bind_worker(operation_id, worker_token, pid)
    except Exception:
        store.finish_worker(operation_id, worker_token=worker_token, state="partial", phase="task_input_dispatch_unverified",
                            verification_status="input_not_replayed", error_code="WORKER_START_FAILED")
        store.retire_worker(operation_id, worker_token)
        return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "Input dispatch was not verified; it will not be replayed.")
    return _accepted(store.get(operation_id) or record)
