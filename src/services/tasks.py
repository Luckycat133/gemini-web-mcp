"""Read an existing native Task by its exact upstream conversation ID."""

from __future__ import annotations

import logging
from typing import Any

from ..domain import DomainErrorCode, DomainResult, OperationState, is_valid_remote_chat_id, result_from_exception
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope

logger = logging.getLogger(__name__)

_TASK_STATES = {
    0: OperationState.PARTIAL,
    1: OperationState.QUEUED,
    2: OperationState.RUNNING,
    3: OperationState.COMPLETED,
    4: OperationState.FAILED,
    5: OperationState.PARTIAL,
    6: OperationState.RUNNING,
    7: OperationState.QUEUED,
    8: OperationState.CANCELLED,
}


def unknown_task_status(chat_id: str | None) -> dict[str, Any]:
    return {"chat_id": chat_id, "status": "unknown", "status_id": None, "terminal": False}


async def get_task_status_result(client: Any, chat_id: str) -> DomainResult[dict[str, Any]]:
    """Read back a matching goal; never generate, resume, or delete its source."""
    if not is_valid_remote_chat_id(chat_id):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "An exact upstream chat_id is required.")
    if not hasattr(client, "_batch_execute"):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Task status read-back is unavailable.",
                                    data=unknown_task_status(chat_id), verification_status="read_back_unavailable")
    contract = get_contract("tasks.get")
    try:
        response = await execute_contract(client, contract.key, chat_id=chat_id)
    except Exception as error:
        return result_from_exception(error, logger=logger, operation=contract.key)
    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), contract.rpc_id)
    diagnostic = {"contract_key": contract.key, "status_code": getattr(response, "status_code", None),
                  "reject_code": envelope.reject_code, "body_count": len(envelope.bodies)}
    if diagnostic["status_code"] == 200 and envelope.parsed and envelope.reject_code is None and envelope.single_body:
        parsed = parse_contract_body(contract, envelope.bodies[0], expected_id=chat_id)
        diagnostic["parser_status"] = parsed.status
        if parsed.status == "success" and not parsed.warnings:
            state = _TASK_STATES[parsed.value["status_id"]]
            if state in {OperationState.FAILED, OperationState.CANCELLED}:
                code = DomainErrorCode.CANCELLED if state is OperationState.CANCELLED else DomainErrorCode.VERIFICATION_FAILED
                return DomainResult.failure(code, "The native Task reported a terminal failure or cancellation.",
                                            data=parsed.value, operation_state=state,
                                            verification_status="task_goal_observed", details=diagnostic)
            return DomainResult.success(parsed.value, operation_state=_TASK_STATES[parsed.value["status_id"]],
                                        verification_status="task_goal_observed", details=diagnostic)
    code = DomainErrorCode.UPSTREAM_REJECTED if envelope.reject_code is not None else DomainErrorCode.UPSTREAM_CHANGED
    return DomainResult.failure(code, "The requested Task status could not be verified.",
                                data=unknown_task_status(chat_id), verification_status="read_back_unavailable",
                                details=diagnostic)
