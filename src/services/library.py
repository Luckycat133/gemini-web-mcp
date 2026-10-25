"""Native Library asset pages and exact, independently verified removal."""

from __future__ import annotations

from typing import Any

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState
from ..infrastructure.rpc_contracts import execute_contract
from .account import account_contract_result


_SECTIONS = {"all", "documents", "media", "purchases"}
# Current React LibraryOverviewSurface Ot explicitly rejects document/Canvas
# types. The older generic category0 mapping is not evidence they can be deleted.
_DELETABLE_TYPES = frozenset({0, 1, 2, 3, 7, 8, 11})


async def list_library_assets_result(
    client: Any, *, section: str = "all", page_size: int = 30, next_page_token: str | None = None,
) -> DomainResult[dict[str, Any]]:
    """Read one native page, retaining its continuation instead of implying completeness."""
    if (section not in _SECTIONS or type(page_size) is not int or not 1 <= page_size <= 100
            or next_page_token is not None and (not isinstance(next_page_token, str) or not next_page_token.strip())):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "A valid Library section, page size and continuation are required.")
    response = await execute_contract(client, "library.page", section=section, page_size=page_size,
                                      next_page_token=next_page_token)
    result = account_contract_result(response, "library.page")
    if not result.ok or result.data is None:
        return result
    value = result.data["value"]
    return DomainResult.success(
        {**value, "section": section, "complete": value["next_page_token"] is None},
        verification_status="observed", details=result.meta.details,
    )


async def _scan_library(client: Any, *, section: str, max_pages: int) -> DomainResult[dict[str, Any]]:
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "max_pages must be between 1 and 100.")
    items: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    seen_ids: set[str] = set()
    token = None
    for page in range(max_pages):
        result = await list_library_assets_result(client, section=section, page_size=100, next_page_token=token)
        if not result.ok or result.data is None:
            return result
        for item in result.data["items"]:
            if item["id"] in seen_ids:
                return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "The Library pagination repeated an asset identity.")
            seen_ids.add(item["id"])
            items.append(item)
        token = result.data["next_page_token"]
        if not token:
            return DomainResult.success({"items": items, "complete": True, "pages": page + 1},
                                        verification_status="observed")
        if token in seen_tokens:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "The Library pagination repeated a continuation.")
        seen_tokens.add(token)
    return DomainResult.success(
        {"items": items, "complete": False, "next_page_token": token, "pages": max_pages},
        operation_state=OperationState.PARTIAL, verification_status="bounded_read",
        warnings=(DomainWarning("LIBRARY_INCOMPLETE", "More Library pages remain; absence has not been established."),),
    )


async def get_library_asset_result(
    client: Any, asset_id: str, *, section: str = "all", max_pages: int = 20,
) -> DomainResult[dict[str, Any]]:
    """Resolve only an exact identity previously returned by the native listing."""
    if not isinstance(asset_id, str) or not asset_id.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "An exact Library asset_id is required.")
    result = await _scan_library(client, section=section, max_pages=max_pages)
    if not result.ok or result.data is None:
        return result
    item = next((item for item in result.data["items"] if item["id"] == asset_id), None)
    return DomainResult.success(
        {"item": item, "complete": result.data["complete"], "found": item is not None, "section": section},
        operation_state=result.meta.operation_state, warnings=result.warnings,
        verification_status="observed" if item is not None or result.data["complete"] else "target_not_observed",
    )


def _delete_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    kind = {1: 1, 2: 2, 3: 3, 8: 4, 11: 6, 7: 5}.get(item["type_id"], 0)
    return item["chat_id"], item["turn_id"], kind


async def delete_library_asset_result(
    client: Any, asset_id: str, *, section: str = "all", max_pages: int = 20,
) -> DomainResult[dict[str, Any]]:
    """Delete one observed native target; bounded or invalid reads never prove absence."""
    if not isinstance(asset_id, str) or not asset_id.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "An exact Library asset_id is required.")
    before = await _scan_library(client, section=section, max_pages=max_pages)
    if not before.ok or before.data is None:
        return before
    if not before.data["complete"]:
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The pre-delete Library listing is incomplete.",
                                    verification_status="bounded_read")
    item = next((item for item in before.data["items"] if item["id"] == asset_id), None)
    if item is None:
        # "all" is the UI's ordinary filter and excludes purchases. A miss in
        # any selected section cannot establish account-wide asset absence.
        return DomainResult.failure(DomainErrorCode.OPERATION_NOT_FOUND, "The asset was not observed in the selected Library section.",
                                    data={"asset_id": asset_id, "section": section, "deleted": False},
                                    verification_status="target_not_observed")
    if item["type_id"] not in _DELETABLE_TYPES:
        return DomainResult.failure(
            DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "Gemini does not support separate Library deletion of this artifact type.",
            data={"asset_id": asset_id, "section": section, "type_id": item["type_id"], "deleted": False},
            verification_status="unsupported_artifact_type",
        )
    chat_id, turn_id, kind = _delete_identity(item)
    if not chat_id or not turn_id:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "This asset lacks the source identity required for removal.")
    if not item["native_id"] and sum(_delete_identity(other) == (chat_id, turn_id, kind)
                                     for other in before.data["items"]) > 1:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "This legacy target groups multiple assets and cannot remove only the selected item.")
    target = [[chat_id, turn_id], kind]
    if item["native_id"]:
        target.extend([None, None, item["native_id"]])
    response = await execute_contract(client, "library.delete", target=target)
    ack = account_contract_result(response, "library.delete")
    if not ack.ok or ack.data is None:
        return ack
    if ack.data["value"]["failures"]:
        return DomainResult.failure(DomainErrorCode.UPSTREAM_REJECTED, "The Library removal returned failed targets.",
                                    verification_status="mutation_rejected")
    payload = {"asset_id": asset_id, "section": section, "accepted": True, "deleted": False}
    try:
        after = await _scan_library(client, section=section, max_pages=max_pages)
    except Exception:
        after = None
    if after is not None and after.ok and after.data is not None and after.data["complete"]:
        remaining = (any(other["native_id"] == item["native_id"] for other in after.data["items"])
                     if item["native_id"] else any(_delete_identity(other) == (chat_id, turn_id, kind)
                                                   for other in after.data["items"]))
        if not remaining:
            return DomainResult.success({**payload, "deleted": True}, verification_status="verified_deleted")
        status = "read_back_mismatch"
    else:
        status = "read_back_unavailable"
    return DomainResult.success(
        payload, operation_state=OperationState.ACCEPTED, verification_status=status,
        warnings=(DomainWarning("MUTATION_UNVERIFIED", "The asset removal was accepted but fresh absence is not verified."),),
    )
