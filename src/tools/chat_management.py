"""Thin MCP adapter for explicit, verified chat metadata changes."""

import logging
from typing import Any, Literal

from ..adapters import domain_error_boundary, domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..domain.chat_management import ChatMetadata, ChatMutationRequest
from ..services.chat_management import ChatManagementDependencies, ChatManagementService
from ..services.chat_metadata import mutate_chat_metadata, read_chat_metadata
from .annotations import UPDATES_REMOTE_METADATA

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


def register_chat_management_tools(mcp: MCPServer) -> None:
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
        result = await _service().update(ChatMutationRequest(
            action=action, chat_id=chat_id, title=title, expected_title=expected_title,
        ))
        text = (f"{action}: verified." if result.ok else domain_failure_text(result))
        return domain_text(result, text, use_result_data=True)
