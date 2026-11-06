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
        """Inspect or retry cleanup of exact MCP-owned chats in the current account.

        list reads the local ledger. retry processes pending rows. recheck requires
        a known chat_id and fresh remote evidence, and may retry after positive
        presence. Task cleanup verifies both exact goal and chat absence. Active
        operations, retention and associated schedules protect chats. Never match titles.
        """
        result = await owned_cleanup_result(action, chat_id=chat_id, limit=limit, offset=offset,
                                             include_completed=include_completed)
        return domain_text(result, "MCP-owned cleanup state inspected." if result.ok else domain_failure_text(result),
                           use_result_data=True)
