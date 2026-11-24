"""Explicit Gemini account management, separate from functional requests."""

from __future__ import annotations

import logging

from .. import __version__
from ..adapters import MCPServer
from ..client_wrapper import init_startup_infrastructure, server_lifespan
from ..tools.chat_management import register_chat_management_tools
from ..tools.manage import register_history_tools, register_notebook_tools
from ..tools.notebook_admin import register_notebook_admin_tools
from ..tools.web_tasks import register_web_task_reads

logger = logging.getLogger(__name__)
_INSTRUCTIONS = """
Manage Gemini conversations only at the user's request. Start with paginated
history metadata. Read exact conversations when needed. Use exact IDs and fresh
read-back for changes. Keep chat deletion explicit. Use gemini_notebooks to find
NotebookLM sources or select source=gemini for a chat's move destination.
Notebook deletion also removes its conversations. gemini_tasks reads existing
Task metadata and progress.
"""
_FULL_INSTRUCTIONS = """
Manage account data only at the user's request. Start with paginated history
metadata. Read conversation text only as needed and Skill instructions only on
explicit request. Mutate exact IDs and verify by fresh read-back. Treat uncertain
writes as unverified and never blindly replay. Chat deletion requires explicit
intent. gemini_cleanup is restricted to the MCP ownership ledger.
gemini_notebooks defaults to NotebookLM source metadata. source=gemini selects
the project catalog. Notebook deletion also removes its conversations.
Task schedules and Chat scheduled actions are separate. Saved browser and
app-action preferences are independent of native confirmation cards.
"""


def _register_full_tools(server: MCPServer) -> None:
    from ..tools.manage import _register_gem_tools, register_account_tools, register_scheduled_tools
    from ..tools.managed_skills import register_managed_skill_tools
    from ..tools.owned_cleanup import register_owned_cleanup_tools
    from ..tools.preferences import register_preference_tools
    from ..tools.task_browser import register_task_browser_tools
    from ..tools.web_tasks import register_web_task_tools

    register_account_tools(server, {"gemini_account_inventory"})
    register_scheduled_tools(server, {
        "gemini_list_scheduled_actions", "gemini_get_scheduled_action",
        "gemini_create_scheduled_action", "gemini_delete_scheduled_action",
    })
    _register_gem_tools(server, {"gemini_manage_gems"})
    register_owned_cleanup_tools(server)
    register_chat_management_tools(server)
    register_web_task_tools(server)
    register_task_browser_tools(server)
    register_preference_tools(server)
    register_managed_skill_tools(server)


def build_server(*, full: bool = False) -> MCPServer:
    server = MCPServer(name="Gemini account", version=__version__, lifespan=server_lifespan,
                       instructions=_FULL_INSTRUCTIONS if full else _INSTRUCTIONS, strict_arguments=not full)
    register_history_tools(server, {"gemini_history", "gemini_delete_chat"}, compact=True)
    notebook_tools = {"gemini_notebooks", "gemini_move_chat_to_notebook"} if full else {"gemini_notebooks"}
    register_notebook_tools(server, notebook_tools, default_source="notebooklm")
    if full:
        _register_full_tools(server)
    else:
        register_chat_management_tools(server, include_notebook_moves=True)
        register_web_task_reads(server)
    register_notebook_admin_tools(server)
    return server


mcp = build_server()


def main(*, full: bool = False) -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gemini account (v%s)", __version__)
    init_startup_infrastructure()
    server = build_server(full=True) if full else mcp
    server.run()


if __name__ == "__main__":
    main()
