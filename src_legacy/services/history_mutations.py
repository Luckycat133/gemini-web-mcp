"""Targeted history metadata changes with independent, bounded read-back."""

from __future__ import annotations

from typing import Any

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState, is_valid_remote_chat_id
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .history import fetch_history_metadata


async def update_chat_metadata_result(
    client: Any, chat_id: str, *, title: str | None = None, pinned: bool | None = None,
) -> DomainResult[dict[str, Any]]:
    """Rename/pin/unpin exactly one chat, preserving every omitted field."""
    if (not isinstance(chat_id, str) or not is_valid_remote_chat_id(chat_id.strip())
            or (title is None and pinned is None)
            or (title is not None and (not isinstance(title, str) or not title.strip()))
            or (pinned is not None and not isinstance(pinned, bool))):
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT, "An exact chat_id and a nonblank title or boolean pinned value are required.",
        )
    if not hasattr(client, "_batch_execute"):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "History metadata updates are unavailable in this client.")
    chat_id = chat_id.strip()
    title = title.strip() if title is not None else None
    contract = get_contract("history.update")
    response = await execute_contract(client, contract.key, chat_id=chat_id, title=title, pinned=pinned)
    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), contract.rpc_id)
    diagnostic: dict[str, Any] = {"contract_key": contract.key,
                                  "status_code": getattr(response, "status_code", None),
                                  "reject_code": envelope.reject_code}
    if diagnostic["status_code"] != 200 or not envelope.parsed or envelope.reject_code is not None or not envelope.single_body:
        code = DomainErrorCode.UPSTREAM_REJECTED if envelope.reject_code is not None else DomainErrorCode.UPSTREAM_CHANGED
        return DomainResult.failure(code, "The history update acknowledgement could not be verified.", details=diagnostic)
    parsed = parse_contract_body(contract, envelope.bodies[0], expected_id=chat_id)
    diagnostic["parser_status"] = parsed.status
    if parsed.status != "success" or parsed.warnings:
        return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED,
                                    "The history update acknowledgement changed shape.", details=diagnostic)
    requested = {key: value for key, value in (("title", title), ("is_pinned", pinned)) if value is not None}
    payload: dict[str, Any] = {"chat_id": chat_id, "requested": requested, "accepted": True, "verified": False}
    # Read both buckets: pin/unpin moves the target between them. A bounded miss
    # cannot establish absence, and the mutation's echo cannot establish success.
    try:
        read_back = await fetch_history_metadata(client, target_count=200)
    except Exception as error:
        read_back = None
        diagnostic["read_back_error"] = type(error).__name__
    if read_back is not None and read_back.ok and read_back.data is not None:
        item = next((item for item in read_back.data["items"] if item.get("id") == chat_id), None)
        diagnostic["read_back"] = read_back.data["diagnostic"]
        if item is not None:
            payload["item"] = item
            mismatched = [key for key, value in requested.items() if item.get(key) != value]
            if not mismatched:
                payload["verified"] = True
                return DomainResult.success(payload, verification_status="verified_updated", details=diagnostic)
            payload["mismatched_fields"] = mismatched
            status = "read_back_mismatch"
        else:
            status = "target_not_observed"
    else:
        status = "read_back_unavailable"
        if read_back is not None:
            diagnostic["read_back_error"] = read_back.error_code
            diagnostic["read_back"] = dict(read_back.meta.details)
    return DomainResult.success(
        payload, operation_state=OperationState.ACCEPTED, verification_status=status, details=diagnostic,
        warnings=(DomainWarning("MUTATION_UNVERIFIED", "The update was accepted, but the requested metadata is not verified."),),
    )
