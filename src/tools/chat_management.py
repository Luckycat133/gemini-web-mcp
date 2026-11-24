"""Thin MCP adapter for explicit, verified chat metadata changes."""

import logging
from typing import Any, Literal

from ..adapters import domain_error_boundary, domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..domain import DomainErrorCode, DomainResult, is_valid_remote_chat_id
from ..domain.chat_management import ChatAction, ChatMetadata, ChatMutationRequest
from ..infrastructure.account_identity import current_account_key
from ..services.chat_management import ChatManagementDependencies, ChatManagementService
from ..services.chat_metadata import mutate_chat_metadata, read_chat_metadata
from ..services.notebooks import move_chat_to_notebook
from .annotations import MUTATES_REMOTE, UPDATES_REMOTE_METADATA

logger = logging.getLogger(__name__)


async def _read_metadata(client: Any, chat_id: str) -> ChatMetadata | None:
    value = await read_chat_metadata(client, chat_id)
    return (ChatMetadata(chat_id=value["id"], title=value["title"], is_pinned=value["is_pinned"])
            if value is not None else None)


def _service() -> ChatManagementService:
    return ChatManagementService(ChatManagementDependencies(
        client_provider=get_gemini_client, client_initializer=initialize_client,
        read_metadata=_read_metadata, mutate=mutate_chat_metadata,
    ))


async def _update_chat_metadata(
    action: ChatAction, chat_id: str, title: str | None, expected_title: str | None,
) -> list[TextContent]:
    result = await _service().update(ChatMutationRequest(
        action=action, chat_id=chat_id, title=title, expected_title=expected_title,
    ))
    text = f"{action}: verified." if result.ok else domain_failure_text(result)
    return domain_text(result, text, use_result_data=True)


async def _move_to_notebook(
    chat_id: str, notebook_id: str | None, title: str | None, expected_title: str | None,
) -> DomainResult[dict[str, Any]]:
    if (not is_valid_remote_chat_id(chat_id) or chat_id != chat_id.strip()
            or notebook_id is None or not notebook_id.strip() or notebook_id != notebook_id.strip()):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Exact chat_id and notebook_id are required.")
    if title is not None:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "title is only valid for rename.")
    if expected_title is not None and not expected_title.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "expected_title must be nonblank when provided.")

    def account_changed() -> DomainResult[dict[str, Any]]:
        return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "Gemini account changed or is unavailable.",
                                    verification_status="account_mismatch")

    previous_owner = current_account_key()
    await initialize_client()
    owner = current_account_key()
    if not owner or (previous_owner and owner != previous_owner):
        return account_changed()
    client = get_gemini_client()
    if not hasattr(client, "_batch_execute"):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Notebook move is unavailable.")
    if expected_title is not None:
        metadata = await _read_metadata(client, chat_id)
        if current_account_key() != owner:
            return account_changed()
        if metadata is None or metadata.chat_id != chat_id or metadata.title != expected_title:
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED,
                                        "The expected chat title could not be verified before moving.",
                                        verification_status="expected_title_conflict")

    data: dict[str, Any] = {"action": "move_to_notebook", "chat_id": chat_id, "notebook_id": notebook_id,
                            "mutation_sent": None, "verified": False}
    try:
        outcome = await move_chat_to_notebook(client, chat_id=chat_id, notebook_id=notebook_id,
                                              expected_account=owner, account_key_provider=current_account_key)
    except PermissionError:
        return account_changed()
    except Exception:
        if current_account_key() != owner:
            return account_changed()
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The Notebook move is unverified.",
                                    data=data, verification_status="move_unverified",
                                    suggested_action="Check the destination Notebook before submitting another move.")
    if current_account_key() != owner:
        return account_changed()

    status = outcome["verification_status"]
    if status == "target_not_found":
        data["mutation_sent"] = False
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "The exact destination Notebook was not found.",
                                    data=data, verification_status=status)
    data.update(mutation_sent=True, accepted=outcome.get("accepted"), verified=outcome["ok"])
    if outcome["ok"]:
        return DomainResult.success(data, verification_status=status)
    if status == "verified":
        status = "move_unverified"
    return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The Notebook move is unverified.",
                                data=data, verification_status=status,
                                suggested_action="Check the destination Notebook before submitting another move.")


def _register_chat_management_with_moves(mcp: MCPServer) -> None:
    @mcp.tool(annotations=MUTATES_REMOTE)
    @domain_error_boundary("gemini_manage_chat", logger)
    async def gemini_manage_chat(
        action: Literal["rename", "pin", "unpin", "move_to_notebook"], chat_id: str,
        title: str | None = None, expected_title: str | None = None, notebook_id: str | None = None,
    ) -> list[TextContent]:
        """Rename, pin, unpin, or move one exact chat. Verify the change by read-back.

        Use chat_id from gemini_history. Rename requires title. expected_title
        guards a stale selection. For move_to_notebook, use notebook_id from
        gemini_notebooks(source="gemini"). Recheck an unverified move before retrying.
        """
        if action == "move_to_notebook":
            result = await _move_to_notebook(chat_id, notebook_id, title, expected_title)
            text = "Notebook membership verified." if result.ok else domain_failure_text(result)
        elif notebook_id is None:
            return await _update_chat_metadata(action, chat_id, title, expected_title)
        else:
            result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "notebook_id is only valid for move_to_notebook.")
            text = domain_failure_text(result)
        return domain_text(result, text, use_result_data=True)


def register_chat_management_tools(mcp: MCPServer, *, include_notebook_moves: bool = False) -> None:
    if include_notebook_moves:
        _register_chat_management_with_moves(mcp)
        return

    @mcp.tool(annotations=UPDATES_REMOTE_METADATA)
    @domain_error_boundary("gemini_manage_chat", logger)
    async def gemini_manage_chat(
        action: Literal["rename", "pin", "unpin"], chat_id: str,
        title: str | None = None, expected_title: str | None = None,
    ) -> list[TextContent]:
        """Rename, pin or unpin one explicitly selected chat, with fresh metadata read-back.

        Use the exact c_ ID from gemini_history, not a title match. Rename titles
        have a local 200-character limit. expected_title optionally guards stale
        selections. No conversation text is read. Missing read-back is unverified;
        a sent mutation is never automatically replayed. Deletion remains separate.
        """
        return await _update_chat_metadata(action, chat_id, title, expected_title)
