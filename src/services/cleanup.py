"""Narrow cleanup workflow for explicitly marked test artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from ..domain import CleanupObservation, CleanupState, DomainErrorCode, DomainResult, OperationState
from ..infrastructure.state_store import CleanupJobRecord
from ..infrastructure.rpc_parsers import extract_rpc_bodies
from .history import chat_to_dict, clamp_int, delete_chat_result, read_chat_turns
from .scheduled import delete_action, fetch_scheduled_registry, fetch_scheduled_task_by_id

CleanupTarget = Literal["all", "chats", "scheduled"]
FetchRegistry = Callable[[Any, int], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]
FetchByID = Callable[[Any, str, int], Awaitable[tuple[dict[str, Any] | None, dict[str, Any]]]]
ExtractBodies = Callable[[str, str], list[Any]]


def _job_payload(record: CleanupJobRecord) -> dict[str, Any]:
    """Scoped diagnostics omit the authentication digest and lease authority."""
    return {
        "job_id": record.job_id, "upstream_chat_id": record.resource_id, "state": record.state,
        "due_at": record.due_at, "attempts": record.attempts, "source": record.source,
        "diagnostic_id": record.diagnostic_id, "error_code": record.error_code,
        "verification_status": record.verification_status,
        "created_at": record.created_at, "updated_at": record.updated_at,
    }


def _cleanup_owner(client: Any = None) -> Any:
    # Keep this import below the lifecycle owner to avoid service/wrapper cycles.
    from ..client_wrapper import get_remote_chat_cleanup_manager

    return get_remote_chat_cleanup_manager(client)


def _authentication_failure() -> DomainResult[dict[str, Any]]:
    return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED,
                                "Cleanup recovery requires the original credential scope.",
                                verification_status="authentication_context_unavailable")


async def list_cleanup_jobs(
    client: Any = None, *, limit: int = 50, offset: int = 0, states: tuple[str, ...] = (),
) -> DomainResult[dict[str, Any]]:
    """Read registered local jobs for the current credentials; never scan chats."""
    if not 1 <= limit <= 100 or offset < 0 or any(
        state not in {"pending", "running", "failed", "completed", "retained", "cancelled"} for state in states
    ):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid cleanup pagination or state.")
    owner = _cleanup_owner(client)
    if not owner.durable_scope_available():
        return _authentication_failure()
    rows = owner.list_durable_jobs(limit=limit, offset=offset, states=states)
    more = owner.list_durable_jobs(limit=1, offset=offset + len(rows), states=states) if len(rows) == limit else ()
    return DomainResult.success({
        "jobs": [_job_payload(row) for row in rows], "count": len(rows),
        "offset": offset, "limit": limit, "has_more": bool(more),
        "next_offset": offset + len(rows) if more else None,
    }, verification_status="local_metadata_observed")


def _cleanup_result(observation: CleanupObservation, record: CleanupJobRecord) -> DomainResult[dict[str, Any]]:
    data = {"job": _job_payload(record), "cleanup_state": observation.state.value}
    if observation.state in {CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED}:
        return DomainResult.success(data, verification_status="verified_absent")
    if observation.state is CleanupState.PENDING:
        return DomainResult.success(data, operation_state=OperationState.QUEUED,
                                    verification_status="cleanup_pending")
    return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED,
                                "Cleanup did not observe verified absence.", data=data,
                                retryable=observation.state is CleanupState.FAILED,
                                diagnostic_id=observation.diagnostic_id,
                                verification_status=record.verification_status)


async def retry_cleanup_job(job_id: str, client: Any = None) -> DomainResult[dict[str, Any]]:
    """Retry one authorized job; retained/cancelled work stays withdrawn."""
    owner = _cleanup_owner(client)
    if not owner.durable_scope_available():
        return _authentication_failure()
    observation = await owner.retry_durable_job(job_id, client=client)
    record = owner.get_durable_job(job_id)
    if observation is None or record is None:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Cleanup job was not found in this scope.")
    return _cleanup_result(observation, record)


async def cancel_cleanup_job(job_id: str, client: Any = None) -> DomainResult[dict[str, Any]]:
    """Cancel future attempts; an already sent upstream request is irreversible."""
    owner = _cleanup_owner(client)
    if not owner.durable_scope_available():
        return _authentication_failure()
    previous = owner.get_durable_job(job_id)
    record = owner.cancel_durable_job(job_id)
    if record is None:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Cleanup job was not found in this scope.")
    return DomainResult.success({
        "job": _job_payload(record), "cancelled": record.state == "cancelled",
        "already_completed": record.state == "completed",
        "upstream_cancellation_verified": False,
        "inflight_at_request": previous is not None and previous.state == "running",
    }, verification_status="local_cleanup_authority_withdrawn" if record.state == "cancelled" else record.verification_status)


async def cleanup_due_jobs(client: Any = None, *, limit: int = 50) -> DomainResult[dict[str, Any]]:
    """Run a bounded batch of registered due jobs without discovering remote IDs."""
    if not 1 <= limit <= 100:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Cleanup limit must be between 1 and 100.")
    owner = _cleanup_owner(client)
    if not owner.durable_scope_available():
        return _authentication_failure()
    observations = await owner.cleanup_due_chat_results(client=client, limit=limit)
    failed = sum(item.state is CleanupState.FAILED for item in observations)
    completed = sum(item.state in {CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED} for item in observations)
    return DomainResult.success({
        "attempted_count": len(observations), "verified_deleted_count": completed,
        "failed_count": failed, "pending_count": len(owner.list_pending_cleanup()),
        "jobs": [{"upstream_chat_id": item.upstream_chat_id, "state": item.state.value,
                  "diagnostic_id": item.diagnostic_id} for item in observations],
    }, operation_state=OperationState.PARTIAL if failed else OperationState.COMPLETED,
       verification_status="bounded_cleanup_batch_observed")


def split_cleanup_markers(markers: str) -> list[str]:
    return [item for item in (value.strip() for value in markers.split(",")) if item]


def marker_hits(text: object, markers: list[str]) -> list[str]:
    haystack = str(text or "").lower()
    return [marker for marker in markers if marker.lower() in haystack]


@dataclass(frozen=True)
class _CleanupScanOptions:
    """Immutable inputs shared by both cleanup scan phases."""

    markers: list[str]
    chat_limit: int
    scan_turns: bool
    dry_run: bool


async def _cleanup_matching_chats(
    client: object,
    options: _CleanupScanOptions,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    matched_chats: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not hasattr(client, "list_chats"):
        errors.append({"target": "chats", "error": "list_chats unavailable"})
        return matched_chats, errors

    chats = (client.list_chats() or [])[: options.chat_limit]  # type: ignore[attr-defined]
    for chat in chats:
        item = chat_to_dict(chat)
        matched_fields: list[str] = []
        matched_markers = marker_hits(item.get("id"), options.markers)
        if matched_markers:
            matched_fields.append("id")
        title_hits = marker_hits(item.get("title"), options.markers)
        if title_hits:
            matched_fields.append("title")
            matched_markers.extend(title_hits)
        if options.scan_turns and item.get("id") and hasattr(client, "read_chat"):
            try:
                _history, turns = await read_chat_turns(client, item["id"], 20, 300)
                for turn in turns:
                    hits = marker_hits(turn.get("text"), options.markers)
                    if hits:
                        matched_fields.append("turn")
                        matched_markers.extend(hits)
                        break
            except Exception as exc:
                errors.append({"target": f"chat:{item.get('id')}", "error": f"{type(exc).__name__}: {exc}"})
        if matched_fields:
            deleted = False
            delete_error = ""
            verification_status = "dry_run" if options.dry_run else "not_attempted"
            if not options.dry_run:
                if not hasattr(client, "delete_chat"):
                    delete_error = "delete_chat unavailable"
                    verification_status = "capability_unavailable"
                else:
                    try:
                        result = await delete_chat_result(client, item["id"])
                        deleted = bool(result.data and result.data.get("deleted") is True)
                        verification_status = result.meta.verification_status
                        if not result.ok and result.error is not None:
                            delete_error = f"{result.error.code.value}: {result.error.message}"
                    except Exception as exc:
                        delete_error = f"{type(exc).__name__}: {exc}"
                        verification_status = "delete_error"
            matched_chats.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "matched_fields": sorted(set(matched_fields)),
                    "matched_markers": sorted(set(matched_markers)),
                    "deleted": deleted,
                    "verification_status": verification_status,
                    "delete_error": delete_error,
                }
            )
    return matched_chats, errors


async def _cleanup_matching_scheduled(
    client: object,
    options: _CleanupScanOptions,
    *,
    fetch_registry: FetchRegistry,
    fetch_by_id: FetchByID,
    extract_bodies: ExtractBodies,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    matched_scheduled: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not hasattr(client, "_batch_execute"):
        errors.append({"target": "scheduled", "error": "_batch_execute unavailable"})
        return matched_scheduled, errors

    try:
        entries, _diagnostic = await fetch_registry(client, 300)
        for item in entries:
            search_text = "\n".join(
                str(item.get(key, "")) for key in ("id", "title", "instructions", "schedule_label")
            )
            matched_markers = marker_hits(search_text, options.markers)
            if not matched_markers:
                continue
            deleted = False
            delete_error = ""
            verification_status = "dry_run"
            if not options.dry_run:
                try:
                    result = await delete_action(
                        client,
                        action_id=str(item["id"]),
                        max_chars=300,
                        fetch_registry=fetch_registry,
                        fetch_by_id=fetch_by_id,
                        extract_bodies=extract_bodies,
                    )
                    verification_status = str(result["verification_status"])
                    deleted = bool(
                        result["ok"]
                        and (
                            result.get("deleted_by_id_after_delete") is True
                            or verification_status == "not_visible_not_readable_by_id"
                        )
                    )
                except Exception as exc:
                    delete_error = f"{type(exc).__name__}: {exc}"
                    verification_status = "delete_error"
            matched_scheduled.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "task_state": item.get("task_state"),
                    "matched_markers": sorted(set(matched_markers)),
                    "deleted": deleted,
                    "verification_status": verification_status,
                    "delete_error": delete_error,
                }
            )
    except Exception as exc:
        errors.append({"target": "scheduled", "error": f"{type(exc).__name__}: {exc}"})
    return matched_scheduled, errors


async def cleanup_test_artifacts_payload(
    client: object,
    markers: str = "codex-,Cleanup Verification Marker",
    target: CleanupTarget = "all",
    dry_run: bool = True,
    max_chats: int = 25,
    scan_turns: bool = False,
    *,
    fetch_registry: FetchRegistry = fetch_scheduled_registry,
    fetch_by_id: FetchByID = fetch_scheduled_task_by_id,
    extract_bodies: ExtractBodies = extract_rpc_bodies,
) -> dict[str, Any]:
    marker_list = split_cleanup_markers(markers) or ["codex-"]
    options = _CleanupScanOptions(
        markers=marker_list,
        chat_limit=clamp_int(max_chats, default=25, minimum=1, maximum=100),
        scan_turns=scan_turns,
        dry_run=dry_run,
    )

    matched_chats: list[dict[str, Any]] = []
    matched_scheduled: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if target in {"all", "chats"}:
        matched_chats, chat_errors = await _cleanup_matching_chats(client, options)
        errors.extend(chat_errors)
    if target in {"all", "scheduled"}:
        matched_scheduled, scheduled_errors = await _cleanup_matching_scheduled(
            client,
            options,
            fetch_registry=fetch_registry,
            fetch_by_id=fetch_by_id,
            extract_bodies=extract_bodies,
        )
        errors.extend(scheduled_errors)

    return {
        "name": "gemini_cleanup_test_artifacts",
        "dry_run": dry_run,
        "target": target,
        "markers": marker_list,
        "scan_turns": scan_turns,
        "max_chats": options.chat_limit,
        "matched_chat_count": len(matched_chats),
        "matched_scheduled_count": len(matched_scheduled),
        "deleted_chat_count": sum(1 for item in matched_chats if item.get("deleted")),
        "deleted_scheduled_count": sum(1 for item in matched_scheduled if item.get("deleted")),
        "matched_chats": matched_chats,
        "matched_scheduled_actions": matched_scheduled,
        "errors": errors,
    }


def format_cleanup_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Gemini Test Artifact Cleanup",
        f"Dry run: {payload['dry_run']} · Target: {payload['target']} · Markers: {', '.join(payload['markers'])}",
        (
            f"Matches: chats={payload['matched_chat_count']}, "
            f"scheduled={payload['matched_scheduled_count']} · "
            f"Deleted: chats={payload['deleted_chat_count']}, scheduled={payload['deleted_scheduled_count']}"
        ),
    ]
    if payload["matched_chats"]:
        lines.extend(["", "### Chats"])
        for item in payload["matched_chats"]:
            status = "deleted" if item.get("deleted") else item.get("verification_status") or "matched"
            if item.get("delete_error"):
                status = f"error={item['delete_error']}"
            lines.append(
                f"- {item.get('title') or '(untitled)'} ({item.get('id')}) "
                f"[{status}; fields={','.join(item.get('matched_fields', []))}]"
            )
    if payload["matched_scheduled_actions"]:
        lines.extend(["", "### Scheduled Actions"])
        for item in payload["matched_scheduled_actions"]:
            status = item.get("verification_status") or ("deleted" if item.get("deleted") else "matched")
            if item.get("delete_error"):
                status = f"error={item['delete_error']}"
            lines.append(f"- {item.get('title') or '(untitled)'} ({item.get('id')}) [{status}]")
    if payload["errors"]:
        lines.extend(["", "### Errors"])
        for item in payload["errors"]:
            lines.append(f"- {item['target']}: {item['error']}")
    if payload["dry_run"]:
        lines.extend(["", "Set dry_run=false to delete the matched test artifacts."])
    return "\n".join(lines)


_cleanup_test_artifacts_payload = cleanup_test_artifacts_payload
_format_cleanup_markdown = format_cleanup_markdown
