"""Narrow cleanup workflow for explicitly marked test artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .history import chat_to_dict, clamp_int, read_chat_turns
from .scheduled import delete_action, fetch_scheduled_registry

CleanupTarget = Literal["all", "chats", "scheduled"]


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
            if not options.dry_run:
                if not hasattr(client, "delete_chat"):
                    delete_error = "delete_chat unavailable"
                else:
                    try:
                        await client.delete_chat(item["id"])  # type: ignore[attr-defined]
                        deleted = True
                    except Exception as exc:
                        delete_error = f"{type(exc).__name__}: {exc}"
            matched_chats.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "matched_fields": sorted(set(matched_fields)),
                    "matched_markers": sorted(set(matched_markers)),
                    "deleted": deleted,
                    "delete_error": delete_error,
                }
            )
    return matched_chats, errors


async def _cleanup_matching_scheduled(
    client: object,
    options: _CleanupScanOptions,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    matched_scheduled: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not hasattr(client, "_batch_execute"):
        errors.append({"target": "scheduled", "error": "_batch_execute unavailable"})
        return matched_scheduled, errors

    try:
        entries, _diagnostic = await fetch_scheduled_registry(client, 300)
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
                    result = await delete_action(client, action_id=str(item["id"]), max_chars=300)
                    verification_status = str(result["verification_status"])
                    deleted = bool(
                        result["ok"]
                        and (
                            result.get("deleted_by_id_after_delete") is True
                            or result.get("visible_after_delete") is not True
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
        matched_scheduled, scheduled_errors = await _cleanup_matching_scheduled(client, options)
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
            status = "deleted" if item.get("deleted") else "matched"
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
