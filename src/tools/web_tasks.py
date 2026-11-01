"""Compact, explicit account management for Gemini Task threads."""

from dataclasses import replace
import json
import logging
from typing import Any, Literal, cast

from ..adapters import domain_error_boundary, domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..client_wrapper import finalize_remote_chat_cleanup
from ..client_wrapper import _cleanup_manager
from ..domain import DomainResult
from ..domain.web_tasks import WebTask, WebTaskPage
from ..services.web_context_transport import delete_task_metadata, fetch_task_metadata, fetch_task_metadata_page, fetch_task_progress_turn
from ..services.web_tasks import WebTaskDependencies, WebTaskService
from ..services.task_progress import TaskProgressService
from ..services.task_schedules import TaskScheduleDependencies, TaskScheduleService
from ..services.task_schedule_transport import list_task_schedules, get_task_schedule, create_task_schedule, update_task_schedule, delete_task_schedule
from ..services.task_schedule_lifecycle import delete_schedule_and_release
from ..services.task_account_lifecycle import delete_task_and_reconcile
from ..domain import DomainErrorCode
from ..infrastructure.account_identity import current_account_key
from .annotations import DESTRUCTIVE_REMOTE

logger = logging.getLogger(__name__)


async def _get(client: Any, chat_id: str) -> WebTask | None:
    item = await fetch_task_metadata(client, chat_id)
    return WebTask(**item) if item is not None else None


async def _list(client: Any, limit: int, token: str | None) -> WebTaskPage[WebTask]:
    page = await fetch_task_metadata_page(client, limit, token)
    return WebTaskPage(items=tuple(WebTask(**item) for item in page["items"]),
                       next_page_token=page["next_page_token"])


def _service() -> WebTaskService:
    return WebTaskService(WebTaskDependencies(
        client_initializer=initialize_client, client_provider=get_gemini_client,
        get_task=_get, list_tasks=_list, delete_task=delete_task_metadata,
        readback_delays=(0.25, 0.75, 1.5, 2.5),
    ))


def _schedule_service() -> TaskScheduleService:
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


def register_web_task_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("gemini_tasks", logger)
    async def gemini_tasks(
        action: Literal["list", "get", "delete", "progress", "list_schedules", "get_schedule", "create_schedule",
                        "update_schedule", "pause_schedule", "resume_schedule", "delete_schedule"] = "list", chat_id: str | None = None,
        limit: int = 20, page_token: str | None = None, max_chars: int = 800,
        schedule_id: str | None = None, schedule: dict[str, Any] | None = None, offset: int = 0,
        response_id: str | None = None, candidate_id: str | None = None,
    ) -> list[TextContent]:
        """Manage Task threads, compact activity, and Task schedules (separate from Chat schedules).

        list/get are read-only and never read conversation messages. Descriptions
        are bounded; unrecognized numeric states remain unknown. delete requires
        explicit user intent and the exact c_ Task ID, with independent absence
        verification. Sent but unverified deletion must not be blindly repeated.
        Use gemini_run_task and its operation handle to start work or read its answer.

        progress reads activity labels/tool names, with unknown step statuses. Omit
        response_id/candidate_id for the latest Task turn, or provide both exact IDs.
        Schedule actions use schedule_id. create_schedule/update_schedule take a
        schedule object: title, instructions, timezone (IANA zone), frequency (once/daily/
        weekly/hourly/monthly/yearly); time HH:MM, ISO weekdays 1..7, days_of_month,
        month/day, hourly minute, or aware ISO at. Update takes only changed fields.
        starts_at/ends_at are optional aware ISO bounds. Time windows are optional
        alongside time. Lists paginate locally with offset/limit. Mutations send
        once and verify fresh read-back; unknown delivery must not be replayed.
        Daily example: schedule={"title":"Check","instructions":"Read only",
        "timezone":"Asia/Shanghai","frequency":"daily","time":"10:00"}.
        The nested key is timezone, not the Chat schedule tool's timezone_name;
        a cron expression is not a supported schedule field.
        Deleting a Task thread can also delete its associated schedules.
        """
        service = _service()
        result: DomainResult[Any]
        if action == "list":
            result = await service.list_tasks(limit=limit, page_token=page_token)
        elif action == "get":
            result = await service.get_task(chat_id or "", max_chars=max_chars)
        elif action == "delete":
            result = await delete_task_and_reconcile(service, chat_id or '',
                cleanup_observer=_cleanup_manager.record_explicit_verified_absence)
            if result.data is not None:
                result = replace(result, data=replace(result.data, pre_read_task=None, post_read_task=None))
        elif action == 'progress':
            if bool(response_id) != bool(candidate_id):
                result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Provide both response_id and candidate_id.')
            elif response_id and candidate_id:
                result = await _progress_service(candidate_id).read_exact_turn(chat_id or '', response_id, candidate_id)
            else:
                result = await _progress_service().read_latest_task(chat_id or '')
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
            result = await delete_schedule_and_release(_schedule_service(), schedule_id or '', max_chars=max_chars,
                client_provider=get_gemini_client, finalize_cleanup=finalize_remote_chat_cleanup)
        else:
            result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Unknown Task action.')
        text = (json.dumps(result.to_dict()["data"], ensure_ascii=False, separators=(",", ":"))
                if result.ok else domain_failure_text(result))
        return domain_text(cast(DomainResult[Any], result), text, use_result_data=True)
