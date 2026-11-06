"""Explicit Gemini account management, separate from functional requests."""

from __future__ import annotations

import logging

from .. import __version__
from ..adapters import MCPServer
from ..client_wrapper import init_startup_infrastructure, server_lifespan
from ..tools.manage import (
    _register_gem_tools,
    register_account_tools,
    register_history_tools,
    register_notebook_tools,
    register_scheduled_tools,
)
from ..tools.owned_cleanup import register_owned_cleanup_tools
from ..tools.chat_management import register_chat_management_tools
from ..tools.web_tasks import register_web_task_tools
from ..tools.task_browser import register_task_browser_tools
from ..tools.preferences import register_preference_tools
from ..tools.managed_skills import register_managed_skill_tools
from ..tools.notebook_admin import register_notebook_admin_tools

logger = logging.getLogger(__name__)
mcp = MCPServer(name="Gemini account", version=__version__, lifespan=server_lifespan, instructions="""
Manage account data only at the user's request. Start with paginated history
metadata. Read conversation text only as needed and Skill instructions only on
explicit request. Mutate exact IDs and verify by fresh read-back. Treat uncertain
writes as unverified and never blindly replay. Chat deletion requires explicit
intent. gemini_cleanup is restricted to the MCP ownership ledger.
gemini_notebooks defaults to NotebookLM source metadata. source=gemini selects
the project catalog. Notebook deletion also removes its conversations.
Task schedules and Chat scheduled actions are separate. Saved browser and
app-action preferences are independent of native confirmation cards.
""")

register_history_tools(mcp, {"gemini_history", "gemini_delete_chat"}, compact=True)
register_notebook_tools(mcp, {"gemini_notebooks", "gemini_move_chat_to_notebook"}, default_source="notebooklm")
register_account_tools(mcp, {"gemini_account_inventory"})
register_scheduled_tools(mcp, {
    "gemini_list_scheduled_actions", "gemini_get_scheduled_action",
    "gemini_create_scheduled_action", "gemini_delete_scheduled_action",
})
_register_gem_tools(mcp, {"gemini_manage_gems"})
register_owned_cleanup_tools(mcp)
register_chat_management_tools(mcp)
register_web_task_tools(mcp)
register_task_browser_tools(mcp)
register_preference_tools(mcp)
register_managed_skill_tools(mcp)
register_notebook_admin_tools(mcp)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gemini account (v%s)", __version__)
    init_startup_infrastructure()
    mcp.run()


if __name__ == "__main__":
    main()
