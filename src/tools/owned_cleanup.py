"""Account-facing ownership-scoped cleanup adapter."""

from typing import Literal

from ..adapters import domain_text, domain_failure_text, domain_error_boundary
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..services.owned_cleanup import owned_cleanup_result
from .annotations import DESTRUCTIVE_REMOTE
import logging


def register_owned_cleanup_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("gemini_cleanup", logging.getLogger(__name__))
    async def gemini_cleanup(action: Literal["list", "retry", "recheck"] = "list", chat_id: str | None = None,
                             limit: int = 50, offset: int = 0, include_completed: bool = False) -> list[TextContent]:
        """Inspect or retry cleanup of MCP-owned chats in the currently authenticated account.

        list reads the local ownership ledger, not a fresh deletion proof. retry
        processes pending records and honors active operations and explicit retention.
        recheck requires one known chat_id and fresh remote metadata; it can repair
        an old completed record only after the chat is positively observed again.
        For known Tasks, completion requires both exact Task-goal absence and
        conversation-history absence. Associated schedules protect the thread.
        Unknown chats are never selected by title or deleted by this tool.
        """
        result = await owned_cleanup_result(action, chat_id=chat_id, limit=limit, offset=offset,
                                             include_completed=include_completed)
        return domain_text(result, "MCP-owned cleanup state inspected." if result.ok else domain_failure_text(result),
                           use_result_data=True)
