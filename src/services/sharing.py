"""Single-conversation public sharing with exact-token read-back."""

from __future__ import annotations

from typing import Any

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState, is_valid_remote_chat_id
from ..infrastructure.rpc_contracts import execute_contract
from .account import account_contract_result


async def list_public_links_result(client: Any) -> DomainResult[list[dict[str, Any]]]:
    response = await execute_contract(client, "sharing.public_links")
    observed = account_contract_result(response, "sharing.public_links")
    if not observed.ok or observed.data is None:
        return DomainResult.failure(
            observed.error.code if observed.error else DomainErrorCode.UPSTREAM_CHANGED,
            "The public link listing could not be verified.", verification_status=observed.meta.verification_status,
            details=observed.meta.details,
        )
    return DomainResult.success(observed.data["value"], verification_status="observed", details=observed.meta.details)


def _unverified(payload: dict[str, Any], status: str) -> DomainResult[dict[str, Any]]:
    return DomainResult.success(
        payload, operation_state=OperationState.ACCEPTED, verification_status=status,
        warnings=(DomainWarning("MUTATION_UNVERIFIED", "The sharing request was accepted but fresh read-back is not verified."),),
    )


async def create_public_link_result(
    client: Any, chat_id: str, response_id: str | None = None, *, title: str,
    full_conversation: bool = True, locale: str = "zh-CN",
) -> DomainResult[dict[str, Any]]:
    """Share exactly the supplied source through the UI's public-link destination.

    response_id is the observed draft ID, not a turn ID. Artifact-specific
    expansion/Drive export are not inputs to this plain conversation contract.
    """
    if (not isinstance(chat_id, str) or not is_valid_remote_chat_id(chat_id)
            or response_id is not None and (not isinstance(response_id, str) or not response_id.strip())
            or not isinstance(title, str) or not title.strip()
            or type(full_conversation) is not bool or not isinstance(locale, str) or not locale.strip()):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Exact source chat/draft IDs, a title and a boolean conversation selection are required.")
    if response_id is None:
        if not callable(getattr(client, "read_chat", None)):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "This client cannot resolve the source draft ID.")
        history = await client.read_chat(chat_id, limit=1)
        if getattr(history, "cid", None) != chat_id:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "The requested source chat could not be verified.")
        turns = getattr(history, "turns", None)
        output = next((getattr(turn, "model_output", None) for turn in turns
                       if getattr(turn, "role", None) == "model"), None) if isinstance(turns, list) else None
        metadata = getattr(output, "metadata", None)
        try:
            response_id = getattr(output, "rcid", None)
        except (IndexError, AttributeError):
            response_id = None
        if (not isinstance(response_id, str) or not response_id.strip()
                or not isinstance(metadata, list) or not metadata or metadata[0] != chat_id):
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "No observed draft ID was returned for the requested source chat.")
    response = await execute_contract(
        client, "sharing.create", chat_id=chat_id, response_id=response_id,
        title=title, full_conversation=full_conversation, locale=locale,
    )
    ack = account_contract_result(response, "sharing.create")
    if not ack.ok or ack.data is None:
        return ack
    created = ack.data["value"]
    if created is None:
        return _unverified({"chat_id": chat_id, "accepted": True, "verified": False}, "created_token_not_returned")
    payload = {**created, "chat_id": chat_id, "accepted": True, "verified": False}
    try:
        after = await list_public_links_result(client)
    except Exception:
        after = None
    if after is None or not after.ok or after.data is None:
        return _unverified(payload, "read_back_unavailable")
    item = next((item for item in after.data if item["id"] == created["id"]), None)
    if item is None:
        return _unverified(payload, "target_not_observed")
    if item["title"] != title or item["full_conversation"] != full_conversation:
        return _unverified({**payload, "item": item}, "read_back_mismatch")
    return DomainResult.success({**payload, "verified": True, "item": item}, verification_status="verified_created")


async def delete_public_link_result(client: Any, token: str) -> DomainResult[dict[str, Any]]:
    """Revoke exactly one observed token. No delete-all request is constructed."""
    if not isinstance(token, str) or not token.strip() or token != token.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "An exact public-link token is required.")
    before = await list_public_links_result(client)
    if not before.ok or before.data is None:
        return DomainResult.failure(
            before.error.code if before.error else DomainErrorCode.UPSTREAM_CHANGED,
            "The pre-delete public link listing could not be verified.", details=before.meta.details,
            verification_status=before.meta.verification_status,
        )
    if all(item["id"] != token for item in before.data):
        return DomainResult.success({"id": token, "deleted": False, "already_absent": True},
                                    verification_status="verified_absent")
    response = await execute_contract(client, "sharing.delete", token=token)
    ack = account_contract_result(response, "sharing.delete")
    if not ack.ok:
        return ack
    payload = {"id": token, "accepted": True, "deleted": False}
    try:
        after = await list_public_links_result(client)
    except Exception:
        after = None
    if after is None or not after.ok or after.data is None:
        return _unverified(payload, "read_back_unavailable")
    if any(item["id"] == token for item in after.data):
        return _unverified(payload, "read_back_mismatch")
    return DomainResult.success({**payload, "deleted": True}, verification_status="verified_deleted")
