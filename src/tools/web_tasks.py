"""Compact, explicit account management for Gemini Task threads."""

from dataclasses import replace
import json
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

from ..adapters import domain_error_boundary, domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..domain import DomainErrorCode, DomainResult
from ..domain.task_progress import TaskProgress
from ..domain.web_tasks import TaskMetadataAction, WebTask, WebTaskPage
from ..services.web_context_transport import (
    delete_task_metadata, fetch_task_metadata, fetch_task_metadata_page, fetch_task_progress_turn, update_task_metadata,
)
from ..services.web_tasks import WebTaskDependencies, WebTaskService
from ..services.task_progress import TaskProgressService
from ..infrastructure.account_identity import current_account_key
from .annotations import DESTRUCTIVE_REMOTE, READS_PRIVATE_REMOTE

if TYPE_CHECKING:
    from ..services.task_schedules import TaskScheduleService

logger = logging.getLogger(__name__)


async def _get(client: Any, chat_id: str) -> WebTask | None:
    item = await fetch_task_metadata(client, chat_id)
    return WebTask(**item) if item is not None else None


async def _list(client: Any, limit: int, token: str | None) -> WebTaskPage[WebTask]:
    page = await fetch_task_metadata_page(client, limit, token)
    return WebTaskPage(items=tuple(WebTask(**item) for item in page["items"]),
                       next_page_token=page["next_page_token"],
                       unaddressable_count=page["unaddressable_count"])


def _service() -> WebTaskService:
    return WebTaskService(WebTaskDependencies(
        client_initializer=initialize_client, client_provider=get_gemini_client,
        get_task=_get, list_tasks=_list, delete_task=delete_task_metadata,
        readback_delays=(0.25, 0.75, 1.5, 2.5),
        update_metadata=update_task_metadata,
    ))


def _schedule_service() -> "TaskScheduleService":
    from ..services.task_schedules import TaskScheduleDependencies, TaskScheduleService
    from ..services.task_schedule_transport import (
        create_task_schedule, delete_task_schedule, get_task_schedule, list_task_schedules, update_task_schedule,
    )

    return TaskScheduleService(TaskScheduleDependencies(
        client_initializer=initialize_client, client_provider=get_gemini_client,
        list_schedules=list_task_schedules, get_schedule=get_task_schedule,
        create_schedule=create_task_schedule, update_schedule=update_task_schedule,
        delete_schedule=delete_task_schedule,
    ))


def _progress_service(candidate_id: str | None = None) -> TaskProgressService:
    async def exact(cid: str, rid: str) -> dict[str, Any] | None:
        return await fetch_task_progress_turn(get_gemini_client(), cid, rid, candidate_id)

    async def latest(cid: str) -> dict[str, Any] | None:
        return await fetch_task_progress_turn(get_gemini_client(), cid)

    return TaskProgressService(initialize_client, exact, latest, current_account_key)


async def _read_task(
    action: Literal["list", "get", "progress"], chat_id: str | None, limit: int,
    page_token: str | None, max_chars: int, response_id: str | None, candidate_id: str | None,
) -> DomainResult[Any]:
    if action == "list":
        return await _service().list_tasks(limit=limit, page_token=page_token)
    if action == "get":
        return await _service().get_task(chat_id or "", max_chars=max_chars)
    if bool(response_id) != bool(candidate_id):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Provide both response_id and candidate_id.")
    if response_id and candidate_id:
        return await _progress_service(candidate_id).read_exact_turn(chat_id or "", response_id, candidate_id)
    return await _progress_service().read_latest_task(chat_id or "")


def _present(result: DomainResult[Any]) -> list[TextContent]:
    text = (json.dumps(result.to_dict()["data"], ensure_ascii=False, separators=(",", ":"))
            if result.ok else domain_failure_text(result))
    if isinstance(result.data, TaskProgress) and result.data.pending_approval is not None:
        approval = result.data.pending_approval
        text = f"🚩 {approval.action}\n{approval.url}\n{text}"
    return domain_text(result, text, use_result_data=True)


def register_web_task_reads(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    @domain_error_boundary("gemini_tasks", logger)
    async def gemini_tasks(
        action: Literal["list", "get", "progress"] = "list", chat_id: str | None = None,
        limit: int = 20, page_token: str | None = None, max_chars: int = 800,
        response_id: str | None = None, candidate_id: str | None = None,
    ) -> list[TextContent]:
        """Read Gemini Task metadata or activity without running or changing a task.

        list is paginated. get and progress require an exact Task chat_id.
        progress returns activity labels for the latest verified turn, or for
        both response_id and candidate_id. Latest progress can expose a pending
        approval and its native Task link. Read answers by operation handle.
        """
        return _present(await _read_task(action, chat_id, limit, page_token, max_chars, response_id, candidate_id))


def register_web_task_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("gemini_tasks", logger)
    async def gemini_tasks(
        action: Literal["list", "get", "rename", "pin", "unpin", "delete", "progress", "list_schedules", "get_schedule", "create_schedule",
                        "update_schedule", "pause_schedule", "resume_schedule", "delete_schedule"] = "list", chat_id: str | None = None,
        limit: int = 20, page_token: str | None = None, max_chars: int = 800,
        schedule_id: str | None = None, schedule: dict[str, Any] | None = None, offset: int = 0,
        response_id: str | None = None, candidate_id: str | None = None,
        title: str | None = None,
    ) -> list[TextContent]:
        """Manage Task metadata, activity and schedules. Chat schedules are separate.

        list/get return bounded metadata without conversation text. Unknown states
        stay unknown. rename, pin and unpin require explicit intent and an exact
        c_ Task ID. Provide title only for rename. delete can remove associated
        schedules and verifies absence. Start work with gemini_run_task and read
        answers by operation handle.
        progress returns labels and tool names. Provide both exact response_id and
        candidate_id or omit both for the latest turn and pending approval.
        Step statuses may be unknown.
        Schedule actions use schedule_id and lists use offset/limit. A schedule uses
        title, instructions, IANA timezone and frequency once/daily/weekly/hourly/
        monthly/yearly. Use HH:MM time, ISO weekdays 1..7, days_of_month, month and
        day, hourly minute or aware ISO at as appropriate. Optional bounds starts_at
        and ends_at use aware ISO. A window can accompany time. Updates take changed
        fields only. Send mutations once, verify read-back and never replay unknown delivery.
        """
        result: DomainResult[Any]
        if title is not None and action != "rename":
            result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "title is only valid for rename.")
        elif action in ("list", "get", "progress"):
            result = await _read_task(action, chat_id, limit, page_token, max_chars, response_id, candidate_id)
        elif action in ("rename", "pin", "unpin"):
            result = await _service().update_task(cast(TaskMetadataAction, action), chat_id or "", title)
        elif action == "delete":
            from ..client_wrapper import _cleanup_manager
            from ..services.task_account_lifecycle import delete_task_and_reconcile

            result = await delete_task_and_reconcile(_service(), chat_id or '',
                cleanup_observer=_cleanup_manager.record_explicit_verified_absence)
            if result.data is not None:
                result = replace(result, data=replace(result.data, pre_read_task=None, post_read_task=None))
        elif action == 'list_schedules':
            result = await _schedule_service().list_schedules(limit=limit, offset=offset, max_chars=max_chars)
        elif action == 'get_schedule':
            result = await _schedule_service().get_schedule(schedule_id or '', max_chars=max_chars)
        elif action == 'create_schedule':
            result = await _schedule_service().create_schedule(schedule or {}, max_chars=max_chars)
        elif action == 'update_schedule':
            result = await _schedule_service().update_schedule(schedule_id or '', schedule or {}, max_chars=max_chars)
        elif action in {'pause_schedule', 'resume_schedule'}:
            result = await _schedule_service().set_status(schedule_id or '', 2 if action == 'pause_schedule' else 1, max_chars=max_chars)
        elif action == 'delete_schedule':
            from ..client_wrapper import finalize_remote_chat_cleanup
            from ..services.task_schedule_lifecycle import delete_schedule_and_release

            result = await delete_schedule_and_release(_schedule_service(), schedule_id or '', max_chars=max_chars,
                client_provider=get_gemini_client, finalize_cleanup=finalize_remote_chat_cleanup)
        else:
            result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Unknown Task action.')
        return _present(result)
