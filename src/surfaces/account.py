"""Seven explicit Gemini account facades with action-specific input schemas."""

import logging

from .. import __version__
from ..adapters.focused_results import AccountToolResult, focused_result
from ..adapters.mcp_sdk import MCPServer
from ..client_wrapper import init_cookie_manager_integration
from ..domain.account_requests import (
    AccountRequest, CleanupRequest, GemsRequest, HistoryRequest,
    NotebooksRequest, PromptsRequest, ScheduledRequest,
)
from ..services.account_facade import get_account_facade_service
from ..tools.annotations import DESTRUCTIVE_LOCAL, DESTRUCTIVE_REMOTE, READS_PRIVATE_REMOTE

logger = logging.getLogger(__name__)
SERVER_NAME = "gemini_account_mcp"
mcp = MCPServer(
    name=SERVER_NAME, version=__version__,
    instructions="""Explicit Gemini account administration only.
Seven facades accept a typed request whose action determines read or mutation.
Use account history/content reads and deletion only with explicit user intent;
do not inspect account data as a prerequisite to creation or assistance.
List/read to resolve exact IDs before targeted mutation. Accepted requests do
not establish verified state: inspect verification_status and operation_state.
Cleanup status/run/cancel refer only to this account's registered durable jobs;
test_artifacts is an explicit bounded marker scan with dry_run=true by default.
Operation metadata expires after seven days; pending cleanup authority may be renewed.
The state store contains no prompts/chat contents.
""",
)


def _semantics(*, read: list[str], mutate: list[str], local: bool = False) -> dict:
    return {"actionSemantics": {"read": read, "mutation": mutate,
                                "scope": "local" if local else "account"}}


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["list", "read", "search", "export"], mutate=["delete"]))
async def gemini_history(request: HistoryRequest) -> AccountToolResult:
    """List/search metadata or explicitly read/export/delete one Gemini chat.

    Delete requires an exact chat_id; verified_absent is deletion proof. Bounded
    metadata listings and title searches do not establish full account coverage.
    """
    return focused_result(await get_account_facade_service().execute("history", request), "History result with observed coverage.")


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["list", "chats"], mutate=["move"]))
async def gemini_notebooks(request: NotebooksRequest) -> AccountToolResult:
    """List notebooks/chats or move one exact chat into one exact notebook.

    A move is complete only after a strict read-back observes target membership.
    """
    return focused_result(await get_account_facade_service().execute("notebooks", request), "Notebook result and read-back evidence.")


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["list", "get"], mutate=["create_daily", "delete"]))
async def gemini_scheduled(request: ScheduledRequest) -> AccountToolResult:
    """List/get scheduled actions or explicitly create_daily/delete one action.

    Hour/timezone/instructions belong to create_daily. Delete requires action_id.
    RPC acknowledgement alone is accepted; inspect observed verification state.
    """
    return focused_result(await get_account_facade_service().execute("scheduled", request), "Scheduled action result and verification.")


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["list"], mutate=["create", "update", "delete"]))
async def gemini_gems(request: GemsRequest) -> AccountToolResult:
    """List or explicitly create/update/delete Gems with read-back verification.

    Partial updates preserve omitted fields; update/delete require exact gem_id.
    """
    return focused_result(await get_account_facade_service().execute("gems", request), "Gem result and verified fields.")


@mcp.tool(annotations=DESTRUCTIVE_LOCAL, meta=_semantics(read=["list", "categories", "get", "render"], mutate=["create", "update", "delete"], local=True))
async def gemini_prompts(request: PromptsRequest) -> AccountToolResult:
    """Manage the local prompts.json library or render a saved template locally.

    These actions do not call Gemini. Mutations use the shared atomic storage;
    prompt_id identifies get/render/update/delete targets.
    """
    return focused_result(await get_account_facade_service().execute("prompts", request), "Local Prompt library result.")


@mcp.tool(annotations=READS_PRIVATE_REMOTE, meta=_semantics(read=["capabilities", "status", "models", "features", "links", "usage", "library", "modes"], mutate=[]))
async def gemini_account(request: AccountRequest) -> AccountToolResult:
    """Read explicit account inventory or auth-free documented capabilities.

    Features reports sanitized RPC reachability, not feature entitlement. Models
    and usage report observed registry data without inventing availability/quota.
    """
    return focused_result(await get_account_facade_service().execute("account", request), "Account inventory with observed evidence.")


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["status", "test_artifacts(dry_run=true)"], mutate=["run", "cancel", "test_artifacts(dry_run=false)"]))
async def gemini_cleanup(request: CleanupRequest) -> AccountToolResult:
    """Observe/run/cancel account-scoped durable cleanup jobs.

    Run without job_id processes due registered jobs; with an ID retries only
    that job. Cancel is best effort and cannot undo an upstream deletion already
    in flight. test_artifacts explicitly scans bounded marker matches; preview
    its default dry_run before requesting deletion. No general history sweep.
    """
    return focused_result(await get_account_facade_service().execute("cleanup", request), "Cleanup observations; verified_absent alone confirms deletion.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting %s (v%s)", SERVER_NAME, __version__)
    init_cookie_manager_integration()
    mcp.run()


if __name__ == "__main__":
    main()
