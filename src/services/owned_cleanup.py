"""Explicit account maintenance confined to MCP's current-account ownership ledger."""

from __future__ import annotations

from typing import Any

from ..domain import CleanupState, DomainErrorCode, DomainResult, OperationState


async def owned_cleanup_result(action: str, *, chat_id: str | None = None, limit: int = 50,
                               offset: int = 0, include_completed: bool = False,
                               manager: Any = None, client_provider: Any = None, initializer: Any = None) -> DomainResult[dict]:
    from ..client_wrapper import _cleanup_manager, get_gemini_client, initialize_client

    manager = manager or _cleanup_manager
    client_provider = client_provider or get_gemini_client
    initializer = initializer or initialize_client
    if action not in {"list", "retry", "recheck"} or limit < 1 or limit > 100 or offset < 0:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid cleanup action or pagination.")
    account = manager._account()
    if not account:
        return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "A Gemini authentication context is required.")
    records = manager._store.all_records(account)
    if chat_id is not None:
        records = [row for row in records if row["chat_id"] == chat_id]
        if not records:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The chat is not in this account's MCP ownership ledger.")
    if action == "recheck" and not chat_id:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "A known chat_id is required for recheck.")
    if action == "list":
        rows = [row for row in records if include_completed or row["state"] != "completed"]
        page = rows[offset:offset + limit]
        return DomainResult.success({
            "items": [{"chat_id": row["chat_id"], "cleanup_state": row["state"],
                       "verification": "ledger_only", "attempts": row["attempts"],
                       "cleanup_protected": bool(manager._cleanup_guard(row["chat_id"]))} for row in page],
            "total_count": len(rows), "offset": offset, "limit": limit,
            "has_more": offset + len(page) < len(rows),
        }, verification_status="ownership_ledger_observed")
    rows = [row for row in records if row["state"] == "pending" or action == "recheck"]
    rows = rows[offset:offset + limit]
    if not rows:
        return DomainResult.success({"items": [], "count": 0, "verified_absent_count": 0, "unverified_count": 0},
                                    verification_status="no_pending_owned_cleanup")
    await initializer()
    client = client_provider()
    observations = []
    for row in rows:
        if manager._account_key_provider() != account:
            break
        observation = (await manager.recheck_cleanup_result(row["chat_id"], client=client)
                       if action == "recheck" else
                       await manager.delete_chat_result(row["chat_id"], client=client, _expected_account=account))
        observations.append(observation)
    verified = sum(item.state in {CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED} for item in observations)
    unprocessed = len(rows) - len(observations)
    return DomainResult.success({"items": observations, "count": len(observations), "verified_absent_count": verified,
                                 "target_count": len(rows), "unprocessed_count": unprocessed,
                                 "unverified_count": len(rows) - verified},
                                operation_state=OperationState.COMPLETED if verified == len(rows) else OperationState.PARTIAL,
                                verification_status=("account_changed_cleanup_unverified" if unprocessed
                                                     else "cleanup_readback_observed"))
