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
Manage Gemini account data only when the user requests it. gemini_history
defaults to compact metadata and pagination; read conversation text only when
needed for the requested task. gemini_manage_chat renames, pins or unpins an
exact chat ID and verifies fresh read-back. A sent mutation with an unknown
outcome is not confirmed and must not be blindly replayed. Delete chats only
through explicit deletion intent; gemini_cleanup is limited to the owned ledger.
gemini_notebooks defaults to NotebookLM/Gemini Notebook source metadata; use
source=gemini for the legacy project catalog. gemini_tasks manages Task threads,
which have separate execution state from ordinary chat scheduled actions.
Task schedules support six time recurrences and fresh read-back verification.
gemini_task_browser returns a compact verified frame and explicit one-shot inputs;
it does not claim exclusive takeover or verify website side effects automatically.
gemini_preferences reads or explicitly changes persistent browser choice and scoped
app-action permission preferences, with independent read-back and no mutation replay.
Settings read-back does not prove runtime effects; native confirmations remain separate.
gemini_skills manages native Gemini Skills; read instructions only on explicit request.
gemini_manage_notebook manages exact Notebook projects and plaintext sources, with
independent metadata read-back. Notebook deletion also removes its conversations.
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
