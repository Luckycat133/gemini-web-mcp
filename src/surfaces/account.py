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
from ..tools.annotations import DESTRUCTIVE_LOCAL, DESTRUCTIVE_REMOTE

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
gemini_account includes typed nested Skills, sharing, Library assets and Canvas
requests. Skills support ZIP import/replacement. Library assets support list/get/
media delete; Canvas/document removal is unavailable. Canvas supports get/chat_artifacts/versions/create/plain-document edit/
local export. These mixed-action facades retain mutation/destructive annotations;
inspect actionSemantics to distinguish reads and explicit writes.
""",
)


def _semantics(*, read: list[str], mutate: list[str], local: bool = False) -> dict:
    return {"actionSemantics": {"read": read, "mutation": mutate,
                                "scope": "local" if local else "account"}}


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["list", "read", "search", "export", "task_status"], mutate=["update", "delete"]))
async def gemini_history(request: HistoryRequest) -> AccountToolResult:
    """List/search metadata or explicitly read/export/update/delete one Gemini chat.

    Update changes only title and/or pinned; verified_updated requires fresh
    metadata read-back. pinned=false unpins. Omitted fields are preserved.
    task_status reads one native Task by chat_id without resubmitting or deleting.
    Delete requires an exact chat_id; verified_absent is deletion proof. Bounded
    metadata listings and title searches do not establish full account coverage.
    """
    return focused_result(await get_account_facade_service().execute("history", request), "History result with observed coverage.")


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["list", "get", "chats", "sources"], mutate=["move", "create", "update", "delete", "add_source", "remove_source"]))
async def gemini_notebooks(request: NotebooksRequest) -> AccountToolResult:
    """Read/manage one native Gemini Web Notebook, its sources, or chat membership.

    Supports list/get/chats/sources, create/update/delete, move, add_source and
    remove_source. These bot-based Notebooks have IDs distinct from NotebookLM
    references accepted as understanding attachments.
    A move is complete only after a strict read-back observes target membership.
    """
    return focused_result(await get_account_facade_service().execute("notebooks", request), "Notebook result and read-back evidence.")


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(read=["list", "get"], mutate=["create_daily", "create", "update", "pause", "resume", "delete"]))
async def gemini_scheduled(request: ScheduledRequest) -> AccountToolResult:
    """Read or manage one exact scheduled action with verified read-back.

    Hour/timezone/instructions belong to create_daily. Delete requires action_id.
    backend=remy supports native create/update/pause/resume/delete and six
    time triggers. The default legacy backend preserves daily compatibility.
    Preserve the backend when reusing an observed ID.
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


@mcp.tool(annotations=DESTRUCTIVE_REMOTE, meta=_semantics(
    read=["capabilities", "status", "models", "features", "links", "usage", "library", "modes",
          "skills(list/get)", "sharing(list)", "library_assets(list/get)", "canvas(get/chat_artifacts/versions)"],
    mutate=["skills(create/update/enable/disable/delete/upload)", "sharing(create/delete)",
            "library_assets(delete)", "canvas(create/edit/export)"],
))
async def gemini_account(request: AccountRequest) -> AccountToolResult:
    """Inventory and typed native Skills, sharing, library and Canvas actions.

    Features reports sanitized RPC reachability, not feature entitlement. Models
    and usage report observed registry data without inventing availability/quota.
    skills/sharing/library_assets/canvas take a nested request with its own action.
    Skills support list/get/create/update/enable/disable/delete and upload of a
    complete ZIP via file_path, optionally replacing one exact skill_id. Native
    Skills are distinct from Gems and local Prompts; bundle files are not executed locally.

    Sharing supports list/create/delete. Create publishes the explicitly selected
    chat_id; optional response_id defaults to its latest observed model draft.
    Delete revokes one exact token. Library assets support list/get/delete using
    an observed asset_id and section; continuation and incomplete reads remain
    explicit. Canvas/document Library removal is unavailable; supported media
    kinds can be removed. Inventory action=library lists localized capability/template entries.

    Canvas supports get/versions by artifact_id, chat_artifacts by chat_id, and
    create from a prompt with reported source-retention evidence. Edit takes artifact_id and text
    to replace a supported plain document's entire text and formatting, without
    another generation; completion requires independent content read-back. Export
    reads one artifact_id and writes a verified local output_path. Reuse an issued
    source/asset ID after interruption; do not automatically repeat a mutation.
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
