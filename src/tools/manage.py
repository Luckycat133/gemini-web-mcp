"""
会话和 Gem 管理 MCP 工具
"""

import copy
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Any, Literal, Optional
import logging

from ..client_wrapper import (
    get_cookie_status,
    get_gemini_client,
    initialize_client,
    list_browser_cookie_profiles,
)
from .annotations import (
    DESTRUCTIVE_REMOTE,
    MUTATES_REMOTE,
    READ_ONLY_LOCAL,
    READ_ONLY_REMOTE,
    READS_PRIVATE_REMOTE,
)
from .manifest_data import (
    TOOL_MANIFEST,
    WEB_FEATURE_PROBES,
    WEB_UI_CAPABILITIES,
)

# Hoist optional gemini_webapi utils to module level (used in hot RPC parsing paths).
try:
    from gemini_webapi.utils import extract_json_from_response as _extract_json_from_response, get_nested_value as _get_nested_value
    _GEMINI_WEBAPI_UTILS_AVAILABLE = True
except ImportError:
    _GEMINI_WEBAPI_UTILS_AVAILABLE = False

logger = logging.getLogger(__name__)


ResponseFormat = Literal["markdown", "json"]
FeatureSurface = Literal[
    "all",
    "history",
    "library",
    "notebooks",
    "remy",
    "sharing",
    "usage",
    "personalization",
    "import",
    "scheduled",
    "tool_modes",
]
UsageScope = Literal["quota", "model_state", "all"]
ScheduledScope = Literal["active", "inactive", "all"]
HistoryAction = Literal["list", "scan", "search", "read", "export"]
NotebookAction = Literal["list", "chats"]
AccountInventorySurface = Literal[
    "capabilities",
    "status",
    "features",
    "links",
    "usage",
    "library",
    "notebooks",
    "notebook_chats",
    "scheduled",
    "modes",
    "models",
]
ManifestScope = Literal[
    "all",
    "chat",
    "core",
    "history",
    "account",
    "notebooks",
    "scheduled",
    "media",
    "files",
    "research",
    "gems",
    "cookie",
    "prompts",
]
DoctorStatus = Literal["ok", "warn", "error", "skip"]
CleanupTarget = Literal["all", "chats", "scheduled"]
TOOL_GROUP_MODULES = {
    "core": {"core", "media", "files", "research"},
    "basic": {"core"},
    "model": {"core"},
    "chat": {"core"},
    "invoke": {"core"},
    "media": {"media"},
    "advanced": {"prompts", "research"},
    "manage": {"history", "account", "gems"},
    "history": {"history"},
    "history-read": {"history"},
    "history-organize": {"history", "account"},
    "account-read": {"account"},
    "scheduled-read": {"account"},
    "scheduled-admin": {"account"},
    "admin": {"history", "account", "gems"},
    "file": {"files"},
    "files": {"files"},
    "research": {"research"},
    "prompts": {"prompts"},
    "all": {"core", "media", "files", "research", "history", "account", "gems"},
}



class _RawRPCData:
    """Small compatible RPC payload for observed Gemini Web RPC ids not yet in gemini-webapi."""

    def __init__(self, rpcid: str, payload: str, identifier: str = "generic"):
        self.rpcid = rpcid
        self.payload = payload
        self.identifier = identifier

    def serialize(self) -> list:
        return [self.rpcid, self.payload, None, self.identifier]


CONVERSATION_HISTORY_FILTERS: tuple[dict[str, Any], ...] = (
    {
        "name": "ui_recent",
        "filter": [False, None, True],
        "description": "Main Gemini Web recent conversation history bucket.",
    },
    {
        "name": "ui_pinned",
        "filter": [True, None, True],
        "description": "Main Gemini Web pinned conversation history bucket.",
    },
    {
        "name": "recent_p3_true",
        "filter": [False, None, True, None, True],
        "description": "Recent bucket with the frontend P3 metadata flag set true.",
    },
    {
        "name": "pinned_p3_true",
        "filter": [True, None, True, None, True],
        "description": "Pinned bucket with the frontend P3 metadata flag set true.",
    },
    {
        "name": "recent_p3_false",
        "filter": [False, None, True, None, False],
        "description": "Recent bucket variant used by frontend refill paths after history mutations.",
    },
    {
        "name": "pinned_p3_false",
        "filter": [True, None, True, None, False],
        "description": "Pinned bucket variant used by frontend refill paths after history mutations.",
    },
    {
        "name": "recent_field3_false_p3_true",
        "filter": [False, None, False, None, True],
        "description": "Recent bucket with the frontend field-3 boolean disabled and P3 set true.",
    },
)


def _format_timestamp(timestamp: object) -> str:
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _truncate(text: object, max_chars: int) -> str:
    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]"


def _clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    """Normalize user-provided numeric tool arguments into a safe inclusive range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def _paginate_items(items: list[Any], limit: int, offset: int, max_limit: int = 100) -> tuple[list[Any], dict[str, Any]]:
    safe_limit = _clamp_int(limit, default=max_limit, minimum=1, maximum=max_limit)
    safe_offset = _clamp_int(offset, default=0, minimum=0, maximum=max(len(items), 0))
    page = items[safe_offset : safe_offset + safe_limit]
    next_offset = safe_offset + len(page)
    has_more = next_offset < len(items)
    return page, {
        "total_count": len(items),
        "count": len(page),
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
    }


def _json_response(payload: Any) -> list[TextContent]:
    """Serialize payload as a single JSON TextContent (for response_format='json')."""
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _get_attr(item: object, name: str, default: object = "") -> object:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _get_chat_id(chat: object) -> str:
    return str(_get_attr(chat, "cid", "") or _get_attr(chat, "id", "") or "")


def _get_chat_title(chat: object) -> str:
    return str(_get_attr(chat, "title", "") or "Untitled")


def _chat_to_dict(chat) -> dict:
    timestamp = _get_attr(chat, "timestamp", None)
    return {
        "id": _get_chat_id(chat),
        "title": _get_chat_title(chat),
        "is_pinned": bool(_get_attr(chat, "is_pinned", False)),
        "timestamp": timestamp,
        "time": _format_timestamp(timestamp),
    }


def _sanitize_account_status(status: object) -> dict:
    if not isinstance(status, dict):
        return {"status": str(status)}

    summary = status.get("summary") if isinstance(status.get("summary"), dict) else {}
    rpc = status.get("rpc") if isinstance(status.get("rpc"), dict) else {}
    rpc_status = {}
    for name, payload in rpc.items():
        if not isinstance(payload, dict):
            rpc_status[name] = {"ok": bool(payload)}
            continue
        rpc_status[name] = {
            "ok": bool(payload.get("ok")),
            "status_code": payload.get("status_code"),
            "reject_code": payload.get("reject_code"),
        }

    return {
        "source_path": status.get("source_path"),
        "account_path": status.get("account_path"),
        "summary": summary,
        "rpc": rpc_status,
    }


def _turn_to_dict(turn, max_chars: int) -> dict:
    return {
        "role": str(_get_attr(turn, "role", "unknown") or "unknown"),
        "text": _truncate(_get_attr(turn, "text", ""), max_chars),
    }


async def _read_chat_turns(client: object, chat_id: str, limit: int, max_chars: int) -> tuple[object, list[dict]]:
    if not hasattr(client, "read_chat"):
        raise RuntimeError("当前 gemini-webapi 不支持 read_chat。")
    history = await client.read_chat(chat_id, limit=limit)
    turns = _get_attr(history, "turns", []) if history else []
    return history, [_turn_to_dict(turn, max_chars) for turn in (turns or [])[:limit]]


def _turn_matches_query(turn: dict[str, str], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return False
    return needle in turn.get("role", "").lower() or needle in turn.get("text", "").lower()


def _chat_export_payload(
    chat_id: str,
    history: object,
    turns: list[dict],
    metadata: dict[str, Any] | None,
    limit: int,
    max_chars_per_turn: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": str(_get_attr(history, "cid", "") or chat_id),
        "count": len(turns),
        "limit": limit,
        "max_chars_per_turn": max_chars_per_turn,
        "turns": turns,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def _format_chat_export_markdown(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    title = metadata.get("title") or payload["chat_id"]
    lines = [
        f"## Gemini Chat Export: {title}",
        f"Chat ID: {payload['chat_id']}",
        f"Turns: {payload['count']}",
    ]
    if metadata.get("time"):
        lines.append(f"Time: {metadata['time']}")
    for idx, turn in enumerate(payload["turns"], 1):
        lines.extend(["", f"### {idx}. {turn['role']}", turn["text"]])
    return "\n".join(lines)


def _summarize_probe_response(response_text: str, rpcid: str) -> dict[str, Any]:
    if not _GEMINI_WEBAPI_UTILS_AVAILABLE:
        return {"parsed": False, "response_parts": 0}

    try:
        parts = _extract_json_from_response(response_text)
    except Exception:
        return {"parsed": False, "response_parts": 0}

    body_count = 0
    reject_code = None
    for part in parts:
        if _get_nested_value(part, [0]) != "wrb.fr":
            continue
        if _get_nested_value(part, [1]) != rpcid:
            continue
        code = _get_nested_value(part, [5, 0])
        if isinstance(code, int):
            reject_code = code
        body = _get_nested_value(part, [2])
        if body is not None:
            body_count += 1

    return {
        "parsed": True,
        "response_parts": len(parts),
        "body_count": body_count,
        "reject_code": reject_code,
    }


def _get_probe(surface: str, name: str) -> dict[str, str]:
    for probe in WEB_FEATURE_PROBES:
        if probe["surface"] == surface and probe["name"] == name:
            return probe
    raise KeyError(f"Unknown Gemini Web probe: {surface}.{name}")


async def _execute_observed_rpc(client, probe: dict[str, str]):
    return await client._batch_execute(
        [_RawRPCData(probe["rpcid"], probe["payload"])],
        source_path=probe["source_path"],
        close_on_error=False,
    )


def _extract_rpc_bodies(response_text: str, rpcid: str) -> list[Any]:
    bodies = []
    for part in _extract_json_from_response(response_text):
        if _get_nested_value(part, [0]) != "wrb.fr":
            continue
        if _get_nested_value(part, [1]) != rpcid:
            continue
        body = _get_nested_value(part, [2])
        if isinstance(body, str):
            try:
                bodies.append(json.loads(body))
            except json.JSONDecodeError:
                bodies.append(body)
        elif body is not None:
            bodies.append(body)
    return bodies


async def _fetch_scheduled_registry(client, max_chars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probe = _get_probe("scheduled", "scheduled_actions_registry")
    response = await _execute_observed_rpc(client, probe)
    bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
    body = bodies[0] if bodies else []
    raw_entries = body[0] if isinstance(body, list) and body and isinstance(body[0], list) else []
    entries = [_parse_scheduled_action_task_entry(item, max_chars) for item in raw_entries]
    diagnostic = {
        "source_rpc": probe["rpcid"],
        "observed": probe["observed"],
        "status_code": getattr(response, "status_code", None),
        "response_length": len(getattr(response, "text", "") or ""),
        "body_present": bool(bodies),
        "raw_entry_count": len(raw_entries),
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
        "has_session_id": bool(getattr(client, "session_id", None)),
        "account_status": str(getattr(client, "account_status", "")),
    }
    if not entries:
        diagnostic["empty_hint"] = (
            "The current Gemini cookie/session returned an empty scheduled-actions registry. "
            "If the Gemini Web UI shows scheduled actions, refresh cookies from the same signed-in "
            "Chrome profile or check Google multi-account context."
        )
    return entries, diagnostic


def _get_scheduled_task_entry_from_body(body: Any) -> Any:
    if not isinstance(body, list) or not body:
        return None
    first = body[0]
    if isinstance(first, list) and first and isinstance(first[0], str):
        return first
    if isinstance(first, str):
        return body
    return None


async def _fetch_scheduled_task_by_id(
    client,
    action_id: str,
    max_chars: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    response = await client._batch_execute(
        [_RawRPCData("kwDCne", json.dumps([action_id], ensure_ascii=False, separators=(",", ":")))],
        source_path="/scheduled",
        close_on_error=False,
    )
    bodies = _extract_rpc_bodies(response.text, "kwDCne")
    body = bodies[0] if bodies else []
    raw_entry = _get_scheduled_task_entry_from_body(body)
    entry = _parse_scheduled_action_task_entry(raw_entry, max_chars) if raw_entry is not None else None
    matched_task = bool(entry and entry.get("id") == action_id)
    diagnostic = {
        "source_rpc": "kwDCne",
        "observed": "2026-06-20 Pro UI / Scheduled action get-by-id",
        "status_code": getattr(response, "status_code", None),
        "response_length": len(getattr(response, "text", "") or ""),
        "body_present": bool(bodies),
        "raw_body_type": type(body).__name__,
        "raw_top_level_count": len(body) if isinstance(body, list) else None,
        "matched_task": matched_task,
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
        "has_session_id": bool(getattr(client, "session_id", None)),
        "account_status": str(getattr(client, "account_status", "")),
    }
    if entry and not matched_task:
        diagnostic["returned_id"] = entry.get("id", "")
    if not matched_task:
        diagnostic["empty_hint"] = (
            "The current Gemini cookie/session did not return this scheduled action by id. "
            "Check that the id belongs to the same Gemini account/profile context."
        )
    return (entry if matched_task else None), diagnostic


def _parse_public_link_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "id": entry[0] if len(entry) > 0 and isinstance(entry[0], str) else "",
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "disabled": bool(entry[2]) if len(entry) > 2 else False,
        "url": entry[4] if len(entry) > 4 and isinstance(entry[4], str) else "",
        "field_count": len(entry),
    }


def _parse_usage_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    key = entry[0] if len(entry) > 0 else None
    reset = entry[3] if len(entry) > 3 else None
    reset_time = ""
    reset_timestamp = None
    if isinstance(reset, list) and reset:
        seconds = reset[0]
        nanos = reset[1] if len(reset) > 1 else 0
        if isinstance(seconds, (int, float)):
            reset_timestamp = float(seconds) + (float(nanos or 0) / 1e9)
            reset_time = _format_timestamp(reset_timestamp)

    return {
        "key": key,
        "status": entry[1] if len(entry) > 1 else None,
        "tier": entry[2] if len(entry) > 2 else None,
        "reset_timestamp": reset_timestamp,
        "reset_time": reset_time,
        "limit_value": entry[4] if len(entry) > 4 else None,
        "remaining_value": entry[5] if len(entry) > 5 else None,
        "field_count": len(entry),
    }


def _parse_library_capability(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    aliases = entry[0] if len(entry) > 0 and isinstance(entry[0], list) else []
    return {
        "aliases": [alias for alias in aliases if isinstance(alias, str)],
        "name": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "description": entry[2] if len(entry) > 2 and isinstance(entry[2], str) else "",
        "details": entry[3] if len(entry) > 3 and isinstance(entry[3], str) else "",
        "field_count": len(entry),
    }


def _parse_native_notebook(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    metadata = entry[1] if len(entry) > 1 and isinstance(entry[1], list) else []
    summary = entry[2] if len(entry) > 2 and isinstance(entry[2], list) else []
    project_metadata = metadata[12] if len(metadata) > 12 and isinstance(metadata[12], list) else []
    display = metadata[14] if len(metadata) > 14 and isinstance(metadata[14], list) else []
    sources = metadata[10] if len(metadata) > 10 and isinstance(metadata[10], list) else []
    source_rows = sources[1] if len(sources) > 1 and isinstance(sources[1], list) else []

    return {
        "id": entry[0] if len(entry) > 0 and isinstance(entry[0], str) else "",
        "title": metadata[0] if len(metadata) > 0 and isinstance(metadata[0], str) else "",
        "description": metadata[1] if len(metadata) > 1 and isinstance(metadata[1], str) else "",
        "summary": summary[0] if len(summary) > 0 and isinstance(summary[0], str) else "",
        "emoji": display[0] if len(display) > 0 and isinstance(display[0], str) else "",
        "source_count": len(source_rows),
        "project_type": project_metadata[0] if len(project_metadata) > 0 else None,
        "project_subtype": project_metadata[4] if len(project_metadata) > 4 else None,
        "pinned": entry[3] if len(entry) > 3 and isinstance(entry[3], bool) else None,
        "field_count": len(entry),
    }


def _parse_notebook_category(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "subtype": entry[0] if len(entry) > 0 else None,
        "label": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
    }


def _native_notebooks_payload(locale: str = "zh-CN") -> str:
    return json.dumps([2, [locale or "zh-CN"], False, None, [2]], ensure_ascii=False, separators=(",", ":"))


async def _fetch_native_notebooks(client, locale: str = "zh-CN") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = await client._batch_execute(
        [_RawRPCData("CNgdBe", _native_notebooks_payload(locale))],
        source_path="/notebooks/view",
        close_on_error=False,
    )
    bodies = _extract_rpc_bodies(response.text, "CNgdBe")
    body = bodies[0] if bodies else []
    raw_entries = body[2] if isinstance(body, list) and len(body) > 2 and isinstance(body[2], list) else []
    raw_categories = body[3] if isinstance(body, list) and len(body) > 3 and isinstance(body[3], list) else []
    notebooks = [_parse_native_notebook(item) for item in raw_entries]
    diagnostic = {
        "source_rpc": "CNgdBe",
        "observed": "2026-07-04 Pro UI / Native Gemini Notebooks",
        "status_code": getattr(response, "status_code", None),
        "response_length": len(getattr(response, "text", "") or ""),
        "body_present": bool(bodies),
        "raw_entry_count": len(raw_entries),
        "categories": [_parse_notebook_category(item) for item in raw_categories],
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
    }
    return notebooks, diagnostic


def _conversation_project_id(entry: Any) -> str:
    if not isinstance(entry, list):
        return ""
    bot_id = entry[7] if len(entry) > 7 and isinstance(entry[7], str) else ""
    project_metadata = entry[13] if len(entry) > 13 and isinstance(entry[13], list) else None
    return bot_id if bot_id and project_metadata is not None else ""


def _parse_conversation_metadata(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    timestamp = None
    timestamp_value = entry[5] if len(entry) > 5 else None
    if isinstance(timestamp_value, list) and timestamp_value and isinstance(timestamp_value[0], (int, float)):
        timestamp = float(timestamp_value[0]) + (float(timestamp_value[1] if len(timestamp_value) > 1 else 0) / 1e9)

    return {
        "id": entry[0] if len(entry) > 0 and isinstance(entry[0], str) else "",
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "is_pinned": bool(entry[2]) if len(entry) > 2 and entry[2] is not None else False,
        "timestamp": timestamp,
        "time": _format_timestamp(timestamp),
        "project_id": _conversation_project_id(entry),
        "bot_id": entry[7] if len(entry) > 7 and isinstance(entry[7], str) else "",
        "field_count": len(entry),
    }


def _conversation_history_payload(
    filter_payload: list[Any],
    page_size: int,
    next_page_token: str | None = None,
) -> str:
    return json.dumps(
        [page_size, next_page_token, filter_payload],
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _fetch_conversation_metadata_source(
    client,
    source_name: str,
    filter_payload: list[Any],
    max_items: int,
    page_size: int = 100,
    max_pages: int = 50,
) -> dict[str, Any]:
    safe_page_size = _clamp_int(page_size, default=100, minimum=1, maximum=100)
    safe_max_pages = _clamp_int(max_pages, default=50, minimum=1, maximum=200)
    safe_max_items = _clamp_int(max_items, default=5000, minimum=1, maximum=10000)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    next_page_token: str | None = None
    response_length = 0
    pages: list[dict[str, Any]] = []
    stopped_reason = "max_pages"

    for page_index in range(safe_max_pages):
        response = await client._batch_execute(
            [_RawRPCData("MaZiqc", _conversation_history_payload(filter_payload, safe_page_size, next_page_token))],
            source_path="/app",
            close_on_error=False,
        )
        response_text = getattr(response, "text", "") or ""
        response_length += len(response_text)
        bodies = _extract_rpc_bodies(response_text, "MaZiqc")
        body = bodies[0] if bodies else []
        raw_entries = body[2] if isinstance(body, list) and len(body) > 2 and isinstance(body[2], list) else []
        parsed_entries = [_parse_conversation_metadata(item) for item in raw_entries]
        new_unique_count = 0
        for item in parsed_entries:
            item_id = item.get("id")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            item["history_source"] = source_name
            items.append(item)
            new_unique_count += 1
            if len(items) >= safe_max_items:
                break

        next_page_token = body[1] if isinstance(body, list) and len(body) > 1 and isinstance(body[1], str) else None
        pages.append(
            {
                "page": page_index + 1,
                "raw_count": len(raw_entries),
                "new_unique_count": new_unique_count,
                "unique_so_far": len(items),
                "next_page_token_present": bool(next_page_token),
                "response_length": len(response_text),
                "first_id": parsed_entries[0].get("id") if parsed_entries else "",
                "last_id": parsed_entries[-1].get("id") if parsed_entries else "",
                "last_title": parsed_entries[-1].get("title") if parsed_entries else "",
            }
        )

        if len(items) >= safe_max_items:
            stopped_reason = "max_items"
            break
        if not raw_entries:
            stopped_reason = "empty_page"
            break
        if not next_page_token:
            stopped_reason = "no_next_page_token"
            break
        if new_unique_count == 0:
            stopped_reason = "no_new_unique_items"
            break

    return {
        "name": source_name,
        "rpcid": "MaZiqc",
        "filter_payload": filter_payload,
        "items": items,
        "diagnostic": {
            "source_rpc": "MaZiqc",
            "observed": "2026-07-04 Pro UI / conversation history metadata source",
            "filter_name": source_name,
            "filter_payload": filter_payload,
            "page_size": safe_page_size,
            "max_pages": safe_max_pages,
            "max_items": safe_max_items,
            "page_count": len(pages),
            "fetched_count": len(items),
            "response_length": response_length,
            "next_page_token_present": bool(next_page_token),
            "stopped_reason": stopped_reason,
            "pages": pages,
        },
    }


async def _fetch_conversation_metadata_sources(
    client,
    filters: tuple[dict[str, Any], ...] = CONVERSATION_HISTORY_FILTERS,
    max_items_per_source: int = 5000,
    page_size: int = 100,
    max_pages_per_source: int = 50,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source in filters:
        sources.append(
            await _fetch_conversation_metadata_source(
                client,
                source["name"],
                list(source["filter"]),
                max_items=max_items_per_source,
                page_size=page_size,
                max_pages=max_pages_per_source,
            )
        )
    return sources


def _merge_conversation_source_items(source_blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    by_id: dict[str, dict[str, Any]] = {}
    sources_by_id: dict[str, set[str]] = {}
    for block in source_blocks:
        source_name = str(block.get("name") or block.get("notebook_title") or block.get("source") or "unknown")
        for item in block.get("items", []):
            item_id = item.get("id")
            if not item_id:
                continue
            sources_set = sources_by_id.setdefault(item_id, set())
            sources_set.add(source_name)
            if item_id not in by_id:
                merged_item = dict(item)
                by_id[item_id] = merged_item
                continue

            existing = by_id[item_id]
            if not existing.get("project_id") and item.get("project_id"):
                existing["project_id"] = item["project_id"]
            if not existing.get("bot_id") and item.get("bot_id"):
                existing["bot_id"] = item["bot_id"]
            if not existing.get("time") and item.get("time"):
                existing["time"] = item["time"]
                existing["timestamp"] = item.get("timestamp")

    # Materialize ordered source lists for the public output shape.
    sources_output: dict[str, list[str]] = {
        item_id: sorted(sources) for item_id, sources in sources_by_id.items()
    }
    for item_id, merged in by_id.items():
        merged["sources"] = sources_output[item_id]

    return sorted(
        by_id.values(),
        key=lambda item: (float(item.get("timestamp") or 0), str(item.get("id") or "")),
        reverse=True,
    ), sources_output


def _parse_remy_goal_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    created_timestamp = None
    created = entry[16] if len(entry) > 16 else None
    if isinstance(created, list) and created and isinstance(created[0], (int, float)):
        created_timestamp = float(created[0]) + (float(created[1] if len(created) > 1 else 0) / 1e9)

    updated_timestamp = None
    updated = entry[17] if len(entry) > 17 else None
    if isinstance(updated, list) and updated and isinstance(updated[0], (int, float)):
        updated_timestamp = float(updated[0]) + (float(updated[1] if len(updated) > 1 else 0) / 1e9)

    return {
        "id": entry[13] if len(entry) > 13 and isinstance(entry[13], str) else "",
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "description": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "is_pinned": bool(entry[19]) if len(entry) > 19 and entry[19] is not None else False,
        "status": entry[2] if len(entry) > 2 else None,
        "channel": entry[4] if len(entry) > 4 and isinstance(entry[4], str) else "",
        "created_timestamp": created_timestamp,
        "created_time": _format_timestamp(created_timestamp),
        "updated_timestamp": updated_timestamp,
        "updated_time": _format_timestamp(updated_timestamp),
        "field_count": len(entry),
    }


async def _fetch_remy_goal_conversation_refs(
    client,
    max_items: int,
    page_size: int = 100,
    max_pages: int = 50,
) -> dict[str, Any]:
    safe_page_size = _clamp_int(page_size, default=100, minimum=1, maximum=100)
    safe_max_pages = _clamp_int(max_pages, default=50, minimum=1, maximum=200)
    safe_max_items = _clamp_int(max_items, default=5000, minimum=1, maximum=10000)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    next_page_token: str | None = None
    pages: list[dict[str, Any]] = []
    response_length = 0
    stopped_reason = "max_pages"

    for page_index in range(safe_max_pages):
        request_payload = [safe_page_size, next_page_token] if next_page_token else [safe_page_size]
        response = await client._batch_execute(
            [_RawRPCData("GS7W1", json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")))],
            source_path="/app",
            close_on_error=False,
        )
        response_text = getattr(response, "text", "") or ""
        response_length += len(response_text)
        bodies = _extract_rpc_bodies(response_text, "GS7W1")
        body = bodies[0] if bodies else []
        raw_entries = body[0] if isinstance(body, list) and body and isinstance(body[0], list) else []
        parsed_entries = [_parse_remy_goal_entry(item) for item in raw_entries]
        new_unique_count = 0
        for item in parsed_entries:
            item_id = item.get("id")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            item["history_source"] = "remy_goals"
            items.append(item)
            new_unique_count += 1
            if len(items) >= safe_max_items:
                break

        next_page_token = body[1] if isinstance(body, list) and len(body) > 1 and isinstance(body[1], str) else None
        pages.append(
            {
                "page": page_index + 1,
                "raw_count": len(raw_entries),
                "new_unique_count": new_unique_count,
                "unique_so_far": len(items),
                "next_page_token_present": bool(next_page_token),
                "response_length": len(response_text),
            }
        )
        if len(items) >= safe_max_items:
            stopped_reason = "max_items"
            break
        if not raw_entries:
            stopped_reason = "empty_page"
            break
        if not next_page_token:
            stopped_reason = "no_next_page_token"
            break
        if new_unique_count == 0:
            stopped_reason = "no_new_unique_items"
            break

    return {
        "name": "remy_goals",
        "rpcid": "GS7W1",
        "items": items,
        "diagnostic": {
            "source_rpc": "GS7W1",
            "observed": "2026-07-04 Pro UI / Remy goals conversation references",
            "page_size": safe_page_size,
            "max_pages": safe_max_pages,
            "max_items": safe_max_items,
            "page_count": len(pages),
            "fetched_count": len(items),
            "response_length": response_length,
            "next_page_token_present": bool(next_page_token),
            "stopped_reason": stopped_reason,
            "pages": pages,
        },
    }


def _notebook_chats_payload(notebook_id: str, page_size: int, next_page_token: str | None = None) -> str:
    return json.dumps(
        [page_size, next_page_token, [None, None, True, notebook_id, True]],
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _fetch_notebook_chats(
    client,
    notebook_id: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safe_limit = _clamp_int(limit, default=20, minimum=1, maximum=100)
    safe_offset = _clamp_int(offset, default=0, minimum=0, maximum=10000)
    target_count = safe_offset + safe_limit
    page_size = min(max(target_count, 10), 100)
    items: list[dict[str, Any]] = []
    next_page_token: str | None = None
    response_length = 0
    page_count = 0

    while len(items) < target_count:
        response = await client._batch_execute(
            [_RawRPCData("MaZiqc", _notebook_chats_payload(notebook_id, page_size, next_page_token))],
            source_path=f"/notebook/{notebook_id.rsplit('/', 1)[-1]}",
            close_on_error=False,
        )
        response_length += len(getattr(response, "text", "") or "")
        page_count += 1
        bodies = _extract_rpc_bodies(response.text, "MaZiqc")
        body = bodies[0] if bodies else []
        raw_entries = body[2] if isinstance(body, list) and len(body) > 2 and isinstance(body[2], list) else []
        items.extend(_parse_conversation_metadata(item) for item in raw_entries)
        next_page_token = body[1] if isinstance(body, list) and len(body) > 1 and isinstance(body[1], str) else None
        if not next_page_token or not raw_entries:
            break

    page = items[safe_offset : safe_offset + safe_limit]
    diagnostic = {
        "source_rpc": "MaZiqc",
        "observed": "2026-07-04 Pro UI / Native Gemini Notebook recent chats",
        "response_length": response_length,
        "page_count": page_count,
        "fetched_count": len(items),
        "has_remote_more": bool(next_page_token),
        "next_page_token_present": bool(next_page_token),
    }
    page_info = {
        "total_count": len(items),
        "count": len(page),
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": bool(next_page_token) or safe_offset + len(page) < len(items),
        "next_offset": safe_offset + len(page) if bool(next_page_token) or safe_offset + len(page) < len(items) else None,
    }
    return page, {**page_info, "diagnostic": diagnostic}


def _find_notebook(
    notebooks: list[dict[str, Any]],
    notebook_id: str = "",
    notebook_title: str = "",
) -> dict[str, Any] | None:
    clean_id = notebook_id.strip()
    clean_title = notebook_title.strip()
    if clean_id:
        return next((item for item in notebooks if item.get("id") == clean_id), None)
    if clean_title:
        exact = [item for item in notebooks if item.get("title") == clean_title]
        if len(exact) == 1:
            return exact[0]
        folded = clean_title.casefold()
        matches = [item for item in notebooks if str(item.get("title", "")).casefold() == folded]
        if len(matches) == 1:
            return matches[0]
    return None


def _move_chat_to_notebook_payload(chat_id: str, notebook_id: str, project_type: int = 2) -> str:
    conversation = [None] * 14
    conversation[0] = chat_id
    conversation[7] = notebook_id
    conversation[13] = [project_type]
    return json.dumps(
        [None, [["bot_id", "bot_project_metadata"]], conversation],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _conversation_metadata_payload(
    pinned: bool,
    page_size: int,
    next_page_token: str | None = None,
) -> str:
    return _conversation_history_payload([pinned, None, True], page_size, next_page_token)


async def _fetch_conversation_metadata_bucket(
    client,
    pinned: bool,
    target_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_name = "ui_pinned" if pinned else "ui_recent"
    source = await _fetch_conversation_metadata_source(
        client,
        source_name,
        [pinned, None, True],
        max_items=target_count,
        page_size=min(max(target_count, 10), 100),
        max_pages=100,
    )
    diagnostic = dict(source["diagnostic"])
    diagnostic.update(
        {
            "pinned": pinned,
            "has_remote_more": bool(diagnostic.get("next_page_token_present")),
        }
    )
    return source["items"], {
        "pinned": pinned,
        **diagnostic,
    }


async def _fetch_recent_conversation_metadata(
    client,
    target_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safe_target = _clamp_int(target_count, default=50, minimum=1, maximum=5000)
    source_filters = tuple(source for source in CONVERSATION_HISTORY_FILTERS if source["name"] in {"ui_pinned", "ui_recent"})
    sources = await _fetch_conversation_metadata_sources(
        client,
        source_filters,
        max_items_per_source=safe_target,
        page_size=min(max(safe_target, 10), 100),
        max_pages_per_source=100,
    )
    combined, _sources_by_id = _merge_conversation_source_items(sources)
    pinned_diag = next((source["diagnostic"] for source in sources if source["name"] == "ui_pinned"), {})
    recent_diag = next((source["diagnostic"] for source in sources if source["name"] == "ui_recent"), {})
    return combined, {
        "source_rpc": "MaZiqc",
        "observed": "2026-07-04 Pro UI / paginated conversation metadata",
        "target_count_per_bucket": safe_target,
        "pinned": pinned_diag,
        "recent": recent_diag,
        "has_remote_more": bool(
            pinned_diag.get("next_page_token_present") or recent_diag.get("next_page_token_present")
        ),
    }


def _parse_scheduled_action_entry(entry: Any, max_chars: int) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    scheduled_at = entry[5] if len(entry) > 5 else None
    scheduled_timestamp = None
    scheduled_time = ""
    if isinstance(scheduled_at, list) and scheduled_at:
        seconds = scheduled_at[0]
        nanos = scheduled_at[1] if len(scheduled_at) > 1 else 0
        if isinstance(seconds, (int, float)):
            scheduled_timestamp = float(seconds) + (float(nanos or 0) / 1e9)
            scheduled_time = _format_timestamp(scheduled_timestamp)

    return {
        "id": entry[0] if len(entry) > 0 and isinstance(entry[0], str) else "",
        "title": _truncate(entry[1], max_chars) if len(entry) > 1 and isinstance(entry[1], str) else "",
        "enabled": entry[2] if len(entry) > 2 and isinstance(entry[2], bool) else None,
        "scheduled_timestamp": scheduled_timestamp,
        "scheduled_time": scheduled_time,
        "schedule_label": _truncate(entry[7], max_chars) if len(entry) > 7 and isinstance(entry[7], str) else "",
        "kind": entry[9] if len(entry) > 9 else None,
        "field_count": len(entry),
    }


def _scheduled_daily_payload(
    title: str,
    instructions: str,
    hour: int,
    timezone_name: str,
    locale: str,
) -> str:
    return json.dumps(
        [
            [
                [instructions, None, title, 1],
                None,
                [[[[hour]], None, None, None, [1, 4], None, None, [timezone_name]]],
                [None, locale],
                None,
                [1],
            ]
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse_scheduled_action_create_body(body: Any) -> dict[str, Any]:
    if not isinstance(body, list):
        return {"raw_type": type(body).__name__}

    task_id = body[0] if len(body) > 0 and isinstance(body[0], str) else ""
    details = body[1] if len(body) > 1 and isinstance(body[1], list) else []
    summary = details[1] if len(details) > 1 and isinstance(details[1], list) else []
    title = ""
    instructions = ""
    schedule_label = ""
    if summary and isinstance(summary[0], list):
        row = summary[0]
        instructions = row[0] if len(row) > 0 and isinstance(row[0], str) else ""
        schedule_label = row[1] if len(row) > 1 and isinstance(row[1], str) else ""
        title = row[2] if len(row) > 2 and isinstance(row[2], str) else ""
    enabled = None
    if len(details) > 5 and isinstance(details[5], list) and details[5]:
        enabled = details[5][0] if isinstance(details[5][0], bool) else None

    return {
        "id": task_id,
        "title": title,
        "instructions": instructions,
        "schedule_label": schedule_label,
        "enabled": enabled,
        "field_count": len(body),
    }


SCHEDULED_TASK_STATES = {
    1: "created",
    3: "running",
    4: "paused",
    5: "completed",
    6: "deleted",
    7: "error",
}


def _scheduled_task_state(metadata: Any) -> tuple[int | None, str]:
    if not isinstance(metadata, list):
        return None, ""

    if len(metadata) > 0 and metadata[0] is not None:
        state_id = 1
    elif len(metadata) > 1 and metadata[1] is not None:
        state_id = 3
    elif len(metadata) > 4 and metadata[4] is not None:
        state_id = 4
    elif len(metadata) > 2 and metadata[2] is not None:
        state_id = 5
    elif len(metadata) > 3 and metadata[3] is not None:
        state_id = 6
    else:
        state_id = None

    return state_id, SCHEDULED_TASK_STATES.get(state_id, "") if state_id is not None else ""


def _parse_scheduled_action_task_entry(entry: Any, max_chars: int) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    task_id = entry[0] if len(entry) > 0 and isinstance(entry[0], str) else ""
    details = entry[1] if len(entry) > 1 and isinstance(entry[1], list) else []
    metadata = entry[2] if len(entry) > 2 and isinstance(entry[2], list) else []

    row = details[0] if len(details) > 0 and isinstance(details[0], list) else []
    instructions = row[0] if len(row) > 0 and isinstance(row[0], str) else ""
    schedule_label = row[1] if len(row) > 1 and isinstance(row[1], str) else ""
    title = row[2] if len(row) > 2 and isinstance(row[2], str) else ""

    schedule = details[2] if len(details) > 2 and isinstance(details[2], list) else []
    schedule_rule = schedule[0] if schedule and isinstance(schedule[0], list) else []
    hour = None
    if schedule_rule and isinstance(schedule_rule[0], list) and schedule_rule[0]:
        hour_row = schedule_rule[0][0]
        if isinstance(hour_row, list) and hour_row and isinstance(hour_row[0], int):
            hour = hour_row[0]
    timezone_name = ""
    if len(schedule_rule) > 7 and isinstance(schedule_rule[7], list) and schedule_rule[7]:
        timezone_name = schedule_rule[7][0] if isinstance(schedule_rule[7][0], str) else ""

    source = details[3] if len(details) > 3 and isinstance(details[3], list) else []
    enabled_flags = details[5] if len(details) > 5 and isinstance(details[5], list) else []
    enabled = enabled_flags[0] if enabled_flags and isinstance(enabled_flags[0], bool) else None

    created_timestamp = None
    created_time = ""
    created_at = metadata[5] if len(metadata) > 5 else None
    if isinstance(created_at, list) and created_at and isinstance(created_at[0], (int, float)):
        created_timestamp = float(created_at[0]) + (float(created_at[1] if len(created_at) > 1 else 0) / 1e9)
        created_time = _format_timestamp(created_timestamp)

    updated_timestamp = None
    updated_time = ""
    updated_at = metadata[6] if len(metadata) > 6 else None
    if isinstance(updated_at, list) and updated_at and isinstance(updated_at[0], (int, float)):
        updated_timestamp = float(updated_at[0]) + (float(updated_at[1] if len(updated_at) > 1 else 0) / 1e9)
        updated_time = _format_timestamp(updated_timestamp)

    task_state_id, task_state = _scheduled_task_state(metadata)
    return {
        "id": task_id,
        "title": _truncate(title, max_chars),
        "instructions": _truncate(instructions, max_chars),
        "schedule_label": _truncate(schedule_label, max_chars),
        "enabled": enabled,
        "task_state_id": task_state_id,
        "task_state": task_state,
        "is_deleted": task_state_id == 6,
        "hour": hour,
        "timezone_name": timezone_name,
        "source_chat_id": source[0] if source and isinstance(source[0], str) else "",
        "created_timestamp": created_timestamp,
        "created_time": created_time,
        "updated_timestamp": updated_timestamp,
        "updated_time": updated_time,
        "metadata_field_count": len(metadata) if isinstance(metadata, list) else 0,
        "field_count": len(entry),
    }


def _parse_tool_mode_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "mode_id": entry[0] if len(entry) > 0 else None,
        "available": entry[1] if len(entry) > 1 and isinstance(entry[1], bool) else None,
        "quota_value": entry[2] if len(entry) > 2 else None,
        "used_value": entry[3] if len(entry) > 3 else None,
        "reset_or_extra": entry[4] if len(entry) > 4 else None,
        "state": entry[5] if len(entry) > 5 else None,
        "field_count": len(entry),
    }


def _web_capabilities_payload() -> dict[str, Any]:
    payload = copy.deepcopy(WEB_UI_CAPABILITIES)
    payload["feature_probes"] = [
        {
            "surface": probe["surface"],
            "name": probe["name"],
            "rpcid": probe["rpcid"],
            "source_path": probe["source_path"],
            "observed": probe["observed"],
        }
        for probe in WEB_FEATURE_PROBES
    ]
    payload["mcp_tools"] = {
        "chat": ["gemini_chat", "gemini_chat_stream", "gemini_start_chat", "gemini_send_message"],
        "history": [
            "gemini_history",
            "gemini_cleanup_test_artifacts",
            "gemini_list_chats",
            "gemini_scan_chat_history_sources",
            "gemini_search_chats",
            "gemini_read_chat",
            "gemini_export_chat",
            "gemini_delete_chat",
        ],
        "account": [
            "gemini_account_inventory",
            "gemini_inspect_account",
            "gemini_get_tool_manifest",
            "gemini_get_web_capabilities",
            "gemini_probe_web_features",
            "gemini_list_public_links",
            "gemini_get_usage_limits",
            "gemini_notebooks",
            "gemini_list_notebooks",
            "gemini_list_notebook_chats",
            "gemini_move_chat_to_notebook",
            "gemini_list_library_capabilities",
            "gemini_list_scheduled_actions",
            "gemini_get_scheduled_action",
            "gemini_create_scheduled_action",
            "gemini_delete_scheduled_action",
            "gemini_get_tool_mode_status",
            "gemini_list_models",
        ],
        "media": ["gemini_generate_media", "gemini_generate_music"],
        "files": ["gemini_upload_file", "gemini_analyze_url"],
        "research": [
            "gemini_deep_research",
            "gemini_list_research_report_actions",
            "gemini_create_from_research_report",
        ],
        "gems": ["gemini_manage_gems"],
        "cookie": ["gemini_doctor", "gemini_get_cookie_status", "gemini_list_browser_cookie_profiles", "gemini_get_cookie_from_browser"],
    }
    return payload



COOKIE_TOOL_NAMES = {
    "gemini_doctor",
    "gemini_get_cookie_status",
    "gemini_list_browser_cookie_profiles",
    "gemini_get_cookie_from_browser",
    "gemini_reset",
}
MANIFEST_TOOL_NAMES = {"gemini_get_tool_manifest"}
HISTORY_FACADE_TOOL_NAMES = {"gemini_history"}
NOTEBOOKS_FACADE_TOOL_NAMES = {"gemini_notebooks"}
ACCOUNT_INVENTORY_TOOL_NAMES = {"gemini_account_inventory"}
CHAT_TOOL_NAMES = {
    "gemini_chat",
    "gemini_chat_stream",
    "gemini_start_chat",
    "gemini_send_message",
    "gemini_send_message_stream",
    "gemini_list_sessions",
    "gemini_reset_session",
}
HISTORY_READ_TOOL_NAMES = {
    "gemini_list_chats",
    "gemini_scan_chat_history_sources",
    "gemini_search_chats",
    "gemini_read_chat",
    "gemini_export_chat",
}
HISTORY_WRITE_TOOL_NAMES = {
    "gemini_cleanup_test_artifacts",
    "gemini_delete_chat",
}
NOTEBOOKS_READ_TOOL_NAMES = {
    "gemini_list_notebooks",
    "gemini_list_notebook_chats",
}
NOTEBOOKS_WRITE_TOOL_NAMES = {"gemini_move_chat_to_notebook"}
ACCOUNT_READ_TOOL_NAMES = {
    "gemini_inspect_account",
    "gemini_probe_web_features",
    "gemini_get_web_capabilities",
    "gemini_list_public_links",
    "gemini_get_usage_limits",
    "gemini_list_library_capabilities",
    "gemini_get_tool_mode_status",
    "gemini_list_models",
    *NOTEBOOKS_READ_TOOL_NAMES,
}
SCHEDULED_READ_TOOL_NAMES = {
    "gemini_list_scheduled_actions",
    "gemini_get_scheduled_action",
}
SCHEDULED_WRITE_TOOL_NAMES = {
    "gemini_create_scheduled_action",
    "gemini_delete_scheduled_action",
}
GEMS_TOOL_NAMES = {"gemini_manage_gems"}
MANAGE_TOOL_LAYER_NAMES = {
    "history-read": HISTORY_FACADE_TOOL_NAMES,
    "history-write": HISTORY_WRITE_TOOL_NAMES,
    "history-granular": HISTORY_READ_TOOL_NAMES,
    "notebooks-read": NOTEBOOKS_FACADE_TOOL_NAMES,
    "notebooks-write": NOTEBOOKS_WRITE_TOOL_NAMES,
    "notebooks-granular": NOTEBOOKS_READ_TOOL_NAMES,
    "account-read": ACCOUNT_INVENTORY_TOOL_NAMES,
    "account-granular": ACCOUNT_READ_TOOL_NAMES | SCHEDULED_READ_TOOL_NAMES,
    "scheduled-read": SCHEDULED_READ_TOOL_NAMES,
    "scheduled-write": SCHEDULED_WRITE_TOOL_NAMES,
    "gems": GEMS_TOOL_NAMES,
}
ALL_MANAGE_TOOL_NAMES = (
    MANIFEST_TOOL_NAMES
    | HISTORY_FACADE_TOOL_NAMES
    | HISTORY_READ_TOOL_NAMES
    | HISTORY_WRITE_TOOL_NAMES
    | ACCOUNT_INVENTORY_TOOL_NAMES
    | ACCOUNT_READ_TOOL_NAMES
    | NOTEBOOKS_FACADE_TOOL_NAMES
    | SCHEDULED_READ_TOOL_NAMES
    | SCHEDULED_WRITE_TOOL_NAMES
    | NOTEBOOKS_WRITE_TOOL_NAMES
    | GEMS_TOOL_NAMES
)
MANAGE_TOOL_LAYER_NAMES["all"] = ALL_MANAGE_TOOL_NAMES

TOOL_PROFILE_GUIDE = [
    {
        "name": "model",
        "gemini_tools": "model",
        "purpose": "Call Gemini models only; exposes chat/session tools plus always-on manifest and cookie diagnostics.",
        "writes_remote": True,
    },
    {
        "name": "history",
        "gemini_tools": "history",
        "purpose": "Read, search, and export Gemini chat history through the gemini_history facade without delete, scheduled actions, or Gems.",
        "writes_remote": False,
    },
    {
        "name": "history-organize",
        "gemini_tools": "history-organize",
        "purpose": "Use gemini_history and gemini_notebooks, then move selected chats into native Gemini Web Notebooks.",
        "writes_remote": True,
    },
    {
        "name": "account-read",
        "gemini_tools": "account-read",
        "purpose": "Read account inventory through the gemini_account_inventory facade.",
        "writes_remote": False,
    },
    {
        "name": "scheduled-admin",
        "gemini_tools": "scheduled-admin",
        "purpose": "Create or delete scheduled actions after explicit user authorization.",
        "writes_remote": True,
    },
    {
        "name": "core",
        "gemini_tools": "core",
        "purpose": "Broad content work: model calls, media, file/URL analysis, and Deep Research.",
        "writes_remote": True,
    },
    {
        "name": "all",
        "gemini_tools": "all",
        "purpose": "Full maintenance and verification surface; not recommended as a default for general agents.",
        "writes_remote": True,
    },
]


def resolve_manage_tool_names(layers: list[str] | set[str] | tuple[str, ...] | None = None) -> set[str]:
    configured = {str(layer).strip() for layer in (layers or ["all"]) if str(layer).strip()}
    if not configured:
        configured = {"all"}
    enabled = set(MANIFEST_TOOL_NAMES)
    for layer in configured:
        enabled.update(MANAGE_TOOL_LAYER_NAMES.get(layer, {layer}))
    return enabled


def _configured_tool_groups() -> list[str]:
    configured = [
        item.strip()
        for item in os.environ.get("GEMINI_TOOLS", "core").split(",")
        if item.strip()
    ]
    return configured or ["core"]


def _configured_manage_layers(configured: list[str]) -> set[str]:
    layers: set[str] = set()
    profile_layers = {
        "manage": {"all"},
        "all": {"all"},
        "admin": {"all"},
        "history": {"history-read"},
        "history-read": {"history-read"},
        "history-organize": {"history-read", "notebooks-read", "notebooks-write"},
        "account-read": {"account-read"},
        "scheduled-read": {"scheduled-read"},
        "scheduled-admin": {"scheduled-read", "scheduled-write"},
    }
    for group in configured:
        if group.startswith("manage:"):
            layers.add(group.split(":", 1)[1])
        else:
            layers.update(profile_layers.get(group, set()))
    return layers


def _enabled_manifest_tool_names(configured: list[str], enabled_groups: set[str]) -> set[str]:
    enabled_tools = set(MANIFEST_TOOL_NAMES) | set(COOKIE_TOOL_NAMES)
    manage_layers = _configured_manage_layers(configured)
    if manage_layers:
        enabled_tools.update(resolve_manage_tool_names(manage_layers))
    for item in TOOL_MANIFEST:
        group = item["group"]
        if group == "prompts":
            if "prompts" in enabled_groups:
                enabled_tools.add(item["name"])
            continue
        if group in {"history", "account", "gems", "cookie"}:
            continue
        if item["name"] in CHAT_TOOL_NAMES:
            if any(group_name in {"model", "chat", "invoke", "basic"} for group_name in configured):
                enabled_tools.add(item["name"])
            elif group in enabled_groups:
                enabled_tools.add(item["name"])
            continue
        if group in enabled_groups:
            enabled_tools.add(item["name"])
    return enabled_tools


MANIFEST_WORKFLOWS = [
    {
        "name": "safe_account_audit",
        "steps": [
            "gemini_get_tool_manifest",
            "gemini_get_web_capabilities",
            "gemini_inspect_account",
            "gemini_probe_web_features",
        ],
        "notes": "Read-only; avoids raw private RPC bodies.",
    },
    {
        "name": "chat_history_find_and_export",
        "steps": [
            "gemini_history(action='scan') when completeness matters",
            "gemini_history(action='list'|'search')",
            "gemini_history(action='read'|'export') for one selected chat",
        ],
        "notes": "Start with metadata search. Use scan_turns=true only when the user asks to search chat text.",
    },
    {
        "name": "current_pro_generation",
        "steps": [
            "gemini_list_models",
            "gemini_chat with model=pro and thinking_level=extended",
            "optional learning_mode for guided study outputs",
        ],
        "notes": "Sends user prompts to Gemini Web and may create remote chats unless temporary/cleanup settings are used.",
    },
    {
        "name": "web_surface_inventory",
        "steps": [
            "gemini_account_inventory(surface='capabilities')",
            "gemini_account_inventory(surface='links'|'usage'|'library'|'notebooks'|'scheduled'|'modes'|'models')",
        ],
        "notes": "Read-only but may reveal account-private metadata such as links and scheduled-action titles.",
    },
    {
        "name": "chat_history_to_native_notebooks",
        "steps": [
            "gemini_history(action='scan'|'list')",
            "gemini_notebooks(action='list')",
            "gemini_move_chat_to_notebook",
            "gemini_notebooks(action='chats')",
        ],
        "notes": "Moves existing Gemini Web chats into native Gemini Web Notebooks; delete unrelated chats only through explicit destructive tools.",
    },
    {
        "name": "scheduled_action_create_and_cleanup",
        "steps": [
            "gemini_doctor",
            "gemini_create_scheduled_action",
            "gemini_get_scheduled_action",
            "gemini_list_scheduled_actions",
            "gemini_delete_scheduled_action",
        ],
        "notes": "Creates and then deletes a daily scheduled action; use only with explicit user authorization and a unique test title when validating.",
    },
    {
        "name": "operational_preflight",
        "steps": [
            "gemini_doctor",
            "gemini_get_tool_manifest",
            "gemini_list_browser_cookie_profiles",
        ],
        "notes": "Read-only local/profile diagnostics before live account workflows; use validate_browser=true only when account validation is needed.",
    },
    {
        "name": "test_artifact_cleanup",
        "steps": [
            "gemini_cleanup_test_artifacts with dry_run=true",
            "review matched IDs",
            "gemini_cleanup_test_artifacts with dry_run=false",
        ],
        "notes": "Deletes only chats and scheduled actions matching explicit test markers; scan_turns=true reads private chat text and should be used narrowly.",
    },
]


def _tool_availability(tool: dict[str, Any]) -> list[str]:
    if tool["name"] == "gemini_get_tool_manifest":
        return ["always"]
    name = tool["name"]
    group = tool["group"]
    if name in CHAT_TOOL_NAMES:
        return ["model", "chat", "core", "all"]
    if group in {"media", "files", "research"}:
        return ["core", "all"]
    if name in HISTORY_FACADE_TOOL_NAMES:
        return ["history", "history-organize", "manage", "all"]
    if name in NOTEBOOKS_FACADE_TOOL_NAMES:
        return ["history-organize", "account-read", "manage", "all"]
    if name in ACCOUNT_INVENTORY_TOOL_NAMES:
        return ["account-read", "manage", "all"]
    if name in HISTORY_READ_TOOL_NAMES:
        return ["manage", "all"]
    if name in HISTORY_WRITE_TOOL_NAMES:
        return ["admin", "manage", "all"]
    if name in NOTEBOOKS_READ_TOOL_NAMES:
        return ["manage", "all"]
    if name in NOTEBOOKS_WRITE_TOOL_NAMES:
        return ["history-organize", "admin", "manage", "all"]
    if name in SCHEDULED_READ_TOOL_NAMES:
        return ["scheduled-read", "scheduled-admin", "manage", "all"]
    if name in SCHEDULED_WRITE_TOOL_NAMES:
        return ["scheduled-admin", "admin", "manage", "all"]
    if name in ACCOUNT_READ_TOOL_NAMES:
        return ["manage", "all"]
    if name in GEMS_TOOL_NAMES:
        return ["admin", "manage", "all"]
    if group == "cookie":
        return ["always"]
    if group == "prompts":
        return ["prompts"]
    return []


def _current_enabled_manifest_groups() -> tuple[list[str], set[str]]:
    configured = _configured_tool_groups()
    enabled = {"cookie"}
    for group in configured:
        enabled.update(TOOL_GROUP_MODULES.get(group, {group}))
    enabled.add("manifest")
    return configured, enabled


def _tool_manifest_payload(scope: ManifestScope = "all") -> dict[str, Any]:
    current_tool_groups, enabled_groups = _current_enabled_manifest_groups()
    filter_scope = "core" if scope == "chat" else scope
    enabled_tool_names = _enabled_manifest_tool_names(current_tool_groups, enabled_groups)
    tools = [
        {
            **item,
            "availability": _tool_availability(item),
            "current_enabled": item["name"] in enabled_tool_names,
        }
        for item in TOOL_MANIFEST
        if filter_scope == "all"
        or item["group"] == filter_scope
        or (filter_scope == "core" and item["group"] == "core")
        or (filter_scope == "notebooks" and item["name"] in NOTEBOOKS_READ_TOOL_NAMES | NOTEBOOKS_WRITE_TOOL_NAMES)
        or (filter_scope == "scheduled" and item["name"] in SCHEDULED_READ_TOOL_NAMES | SCHEDULED_WRITE_TOOL_NAMES)
    ]
    groups: dict[str, int] = {}
    for tool in tools:
        groups[tool["group"]] = groups.get(tool["group"], 0) + 1
    return {
        "server": "gemini_web_mcp",
        "observed_web_ui": WEB_UI_CAPABILITIES["observed_at"],
        "scope": scope,
        "total_count": len(tools),
        "current_tool_groups": current_tool_groups,
        "current_enabled_count": sum(1 for item in tools if item["current_enabled"]),
        "groups": groups,
        "profiles": TOOL_PROFILE_GUIDE,
        "tools": tools,
        "workflows": MANIFEST_WORKFLOWS if filter_scope in {"all", "core", "history", "account", "notebooks", "scheduled"} else [],
        "safety_notes": [
            "Annotations and manifest metadata are planning hints, not a permission system.",
            "Tools marked privacy=reads_private_chat_text return private Gemini chat content.",
            "Tools marked destructive=true can delete or overwrite remote or local user data.",
            "Probe tools intentionally omit raw response bodies.",
        ],
    }


def _format_tool_manifest_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Gemini MCP Tool Manifest",
        (
            f"Scope: {payload['scope']} · Tools: {payload['total_count']} · "
            f"Current enabled: {payload['current_enabled_count']} · "
            f"Observed Web UI: {payload['observed_web_ui']}"
        ),
        f"Current GEMINI_TOOLS: {', '.join(payload['current_tool_groups'])}",
        "",
        "### Groups",
    ]
    for group, count in sorted(payload["groups"].items()):
        lines.append(f"- {group}: {count}")

    if payload.get("profiles"):
        lines.extend(["", "### Recommended Profiles"])
        for profile in payload["profiles"]:
            write_note = "writes remote data" if profile["writes_remote"] else "read-only"
            lines.append(f"- `{profile['gemini_tools']}` ({write_note}): {profile['purpose']}")

    lines.extend(["", "### Tools"])
    for item in payload["tools"]:
        flags = []
        flags.append("read-only" if item["read_only"] else "writes")
        if item["destructive"]:
            flags.append("destructive")
        if item["pagination"]:
            flags.append("paginated")
        lines.append(f"- `{item['name']}` [{item['group']}; {', '.join(flags)}]: {item['purpose']}")
        lines.append(f"  privacy: {item['privacy']}")
        lines.append(f"  availability: {', '.join(item['availability']) or 'custom'}")
        lines.append(f"  current_enabled: {item['current_enabled']}")

    if payload["workflows"]:
        lines.extend(["", "### Recommended Workflows"])
        for workflow in payload["workflows"]:
            lines.append(f"- {workflow['name']}: {' -> '.join(workflow['steps'])}")
            lines.append(f"  {workflow['notes']}")

    lines.extend(["", "### Safety Notes"])
    lines.extend(f"- {note}" for note in payload["safety_notes"])
    return "\n".join(lines)


def _doctor_check(name: str, status: DoctorStatus, message: str, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": {key: value for key, value in details.items() if value is not None},
    }


def _doctor_overall_status(checks: list[dict[str, Any]]) -> DoctorStatus:
    statuses = {check["status"] for check in checks}
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    if "skip" in statuses and statuses == {"skip"}:
        return "skip"
    return "ok"


def _doctor_payload(browser: str = "chrome", validate_browser: bool = False) -> dict[str, Any]:
    """Build a safe preflight report without exposing cookie values."""
    checks: list[dict[str, Any]] = []
    current_tool_groups, enabled_groups = _current_enabled_manifest_groups()
    manifest = _tool_manifest_payload("all")

    checks.append(
        _doctor_check(
            "python_runtime",
            "ok",
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            executable=sys.executable,
        )
    )

    checks.append(
        _doctor_check(
            "tool_surface",
            "ok",
            f"{manifest['current_enabled_count']} of {manifest['total_count']} manifest tools are enabled",
            current_tool_groups=current_tool_groups,
            enabled_groups=sorted(enabled_groups),
            total_count=manifest["total_count"],
            current_enabled_count=manifest["current_enabled_count"],
        )
    )

    cookie_status = get_cookie_status()
    has_cookie = bool(cookie_status.get("has_cookie"))
    needs_refresh = bool(cookie_status.get("needs_refresh", False))
    if not cookie_status.get("available", False):
        cookie_check = _doctor_check("cookie_status", "warn", "Cookie manager is unavailable")
    elif not has_cookie:
        cookie_check = _doctor_check("cookie_status", "warn", "No runtime Gemini cookie is configured")
    elif needs_refresh:
        cookie_check = _doctor_check(
            "cookie_status",
            "warn",
            "Runtime Gemini cookie exists but should be refreshed",
            source=cookie_status.get("source"),
            cookie_status=cookie_status.get("status"),
        )
    else:
        cookie_check = _doctor_check(
            "cookie_status",
            "ok",
            "Runtime Gemini cookie is configured",
            source=cookie_status.get("source"),
            cookie_status=cookie_status.get("status"),
        )
    checks.append(cookie_check)

    browser_profiles: list[dict[str, Any]] = []
    if browser:
        try:
            raw_profiles = list_browser_cookie_profiles(browser, validate=validate_browser)
            for item in raw_profiles:
                browser_profiles.append(
                    {
                        "browser": item.get("browser", browser),
                        "profile": item.get("profile"),
                        "has_psid": item.get("has_psid"),
                        "has_psidts": item.get("has_psidts"),
                        "cookie_count": item.get("cookie_count"),
                        "chrome_selected_profile": item.get("chrome_selected_profile"),
                        "chrome_selected_profile_directory": item.get("chrome_selected_profile_directory"),
                        "account_available": item.get("account_available"),
                        "scheduled_registry_count": item.get("scheduled_registry_count"),
                        "error": item.get("error"),
                    }
                )
        except Exception as e:
            browser_profiles = [{"browser": browser, "error": f"{type(e).__name__}: {e}"}]

    profile_errors = [item for item in browser_profiles if item.get("error")]
    profiles_with_psid = [item for item in browser_profiles if item.get("has_psid")]
    selected_profile = next((item for item in browser_profiles if item.get("chrome_selected_profile")), None)
    recommended_profile = next(
        (item for item in profiles_with_psid if item.get("account_available") is True),
        profiles_with_psid[0] if profiles_with_psid else None,
    )

    if not browser:
        checks.append(_doctor_check("browser_profiles", "skip", "Browser profile diagnostics were disabled"))
    elif profile_errors and not profiles_with_psid:
        checks.append(
            _doctor_check(
                "browser_profiles",
                "warn",
                f"Could not read usable {browser} Gemini cookies",
                errors=profile_errors,
            )
        )
    elif not profiles_with_psid:
        checks.append(
            _doctor_check(
                "browser_profiles",
                "warn",
                f"No {browser} profile has a Gemini PSID",
                profiles=browser_profiles,
            )
        )
    elif selected_profile and not selected_profile.get("has_psid"):
        checks.append(
            _doctor_check(
                "browser_profile_alignment",
                "warn",
                "Chrome selected profile has no Gemini PSID, but another profile does",
                selected_profile=selected_profile.get("profile"),
                selected_profile_directory=selected_profile.get("chrome_selected_profile_directory"),
                recommended_profile=recommended_profile.get("profile") if recommended_profile else None,
                validate_browser=validate_browser,
            )
        )
    else:
        checks.append(
            _doctor_check(
                "browser_profile_alignment",
                "ok",
                f"{browser} has a usable Gemini cookie profile",
                selected_profile=selected_profile.get("profile") if selected_profile else None,
                recommended_profile=recommended_profile.get("profile") if recommended_profile else None,
                validate_browser=validate_browser,
            )
        )

    ffprobe_path = shutil.which("ffprobe")
    checks.append(
        _doctor_check(
            "ffprobe",
            "ok" if ffprobe_path else "warn",
            "ffprobe is available for media duration verification" if ffprobe_path else "ffprobe was not found in PATH",
            path=ffprobe_path,
        )
    )

    generated_media_dir = os.path.abspath("generated_media")
    checks.append(
        _doctor_check(
            "generated_media_dir",
            "ok" if os.path.isdir(generated_media_dir) else "warn",
            "generated_media directory exists" if os.path.isdir(generated_media_dir) else "generated_media directory does not exist yet",
            path=generated_media_dir,
        )
    )

    recommendations: list[str] = []
    if recommended_profile and selected_profile and not selected_profile.get("has_psid"):
        recommendations.append(
            f"Use gemini_get_cookie_from_browser(browser=\"{browser}\", profile=\"{recommended_profile.get('profile')}\") before live account checks."
        )
    elif not has_cookie and recommended_profile:
        recommendations.append(
            f"Load cookies with gemini_get_cookie_from_browser(browser=\"{browser}\", profile=\"{recommended_profile.get('profile')}\")."
        )
    if validate_browser is False:
        recommendations.append("Run gemini_doctor(validate_browser=true) when you need live account/profile validation.")
    if not ffprobe_path:
        recommendations.append("Install ffmpeg/ffprobe before relying on music/video duration checks.")

    payload = {
        "name": "gemini_doctor",
        "overall_status": _doctor_overall_status(checks),
        "safe": True,
        "validate_browser": validate_browser,
        "browser": browser,
        "checks": checks,
        "browser_profiles": browser_profiles,
        "recommendations": recommendations,
    }
    return payload


def _format_doctor_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Gemini Web MCP Doctor",
        f"Overall: {payload['overall_status']}",
        f"Browser: {payload['browser'] or 'disabled'} · validate_browser={payload['validate_browser']}",
        "",
        "### Checks",
    ]
    for check in payload["checks"]:
        lines.append(f"- {check['name']}: {check['status']} - {check['message']}")
        details = check.get("details") if isinstance(check.get("details"), dict) else {}
        for key in ("source", "selected_profile", "recommended_profile", "path"):
            if details.get(key):
                lines.append(f"  {key}: {details[key]}")

    if payload.get("browser_profiles"):
        lines.extend(["", "### Browser Profiles"])
        for item in payload["browser_profiles"]:
            if item.get("error"):
                lines.append(f"- {item.get('profile') or item.get('browser')}: error={item['error']}")
                continue
            selected = "yes" if item.get("chrome_selected_profile") else "no"
            account = item.get("account_available")
            account_text = "yes" if account is True else "no" if account is False else "unvalidated"
            lines.append(
                f"- {item.get('profile')}: psid={'yes' if item.get('has_psid') else 'no'}, "
                f"selected={selected}, account={account_text}, "
                f"scheduled_registry_count={item.get('scheduled_registry_count', 'unvalidated')}"
            )

    if payload.get("recommendations"):
        lines.extend(["", "### Recommendations"])
        lines.extend(f"- {item}" for item in payload["recommendations"])
    return "\n".join(lines)


def _split_cleanup_markers(markers: str) -> list[str]:
    values = [item.strip() for item in markers.split(",")]
    return [item for item in values if item]


def _marker_hits(text: object, markers: list[str]) -> list[str]:
    haystack = str(text or "").lower()
    return [marker for marker in markers if marker.lower() in haystack]


async def _cleanup_test_artifacts_payload(
    client: object,
    markers: str = "codex-,Cleanup Verification Marker",
    target: CleanupTarget = "all",
    dry_run: bool = True,
    max_chats: int = 25,
    scan_turns: bool = False,
) -> dict[str, Any]:
    marker_list = _split_cleanup_markers(markers)
    if not marker_list:
        marker_list = ["codex-"]

    safe_chat_limit = _clamp_int(max_chats, default=25, minimum=1, maximum=100)
    include_chats = target in {"all", "chats"}
    include_scheduled = target in {"all", "scheduled"}
    matched_chats: list[dict[str, Any]] = []
    matched_scheduled: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    if include_chats:
        if not hasattr(client, "list_chats"):
            errors.append({"target": "chats", "error": "list_chats unavailable"})
        else:
            chats = (client.list_chats() or [])[:safe_chat_limit]
            for chat in chats:
                item = _chat_to_dict(chat)
                matched_fields: list[str] = []
                matched_markers = _marker_hits(item.get("id"), marker_list)
                if matched_markers:
                    matched_fields.append("id")
                title_hits = _marker_hits(item.get("title"), marker_list)
                if title_hits:
                    matched_fields.append("title")
                    matched_markers.extend(title_hits)

                if scan_turns and item.get("id") and hasattr(client, "read_chat"):
                    try:
                        _history, turns = await _read_chat_turns(client, item["id"], 20, 300)
                        for turn in turns:
                            turn_hits = _marker_hits(turn.get("text"), marker_list)
                            if turn_hits:
                                matched_fields.append("turn")
                                matched_markers.extend(turn_hits)
                                break
                    except Exception as e:
                        errors.append({"target": f"chat:{item.get('id')}", "error": f"{type(e).__name__}: {e}"})

                if matched_fields:
                    deleted = False
                    delete_error = ""
                    if not dry_run:
                        if not hasattr(client, "delete_chat"):
                            delete_error = "delete_chat unavailable"
                        else:
                            try:
                                await client.delete_chat(item["id"])
                                deleted = True
                            except Exception as e:
                                delete_error = f"{type(e).__name__}: {e}"
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

    if include_scheduled:
        if not hasattr(client, "_batch_execute"):
            errors.append({"target": "scheduled", "error": "_batch_execute unavailable"})
        else:
            try:
                entries, diagnostic = await _fetch_scheduled_registry(client, 300)
                for item in entries:
                    search_text = "\n".join(
                        str(item.get(key, ""))
                        for key in ("id", "title", "instructions", "schedule_label")
                    )
                    matched_markers = _marker_hits(search_text, marker_list)
                    if not matched_markers:
                        continue
                    deleted = False
                    delete_error = ""
                    verification_status = "dry_run"
                    if not dry_run:
                        try:
                            request_payload = json.dumps([None, [item["id"]]], ensure_ascii=False, separators=(",", ":"))
                            response = await client._batch_execute(
                                [_RawRPCData("Q4Gw3c", request_payload)],
                                source_path="/scheduled",
                                close_on_error=False,
                            )
                            bodies = _extract_rpc_bodies(response.text, "Q4Gw3c")
                            verification_status = "rpc_accepted" if bodies else "rpc_unconfirmed"
                            task_after_delete, _ = await _fetch_scheduled_task_by_id(client, item["id"], 300)
                            if task_after_delete and task_after_delete.get("task_state_id") == 6:
                                verification_status = "deleted_state_by_id"
                                deleted = True
                            elif bodies:
                                deleted = True
                        except Exception as e:
                            delete_error = f"{type(e).__name__}: {e}"
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
            except Exception as e:
                errors.append({"target": "scheduled", "error": f"{type(e).__name__}: {e}"})

    return {
        "name": "gemini_cleanup_test_artifacts",
        "dry_run": dry_run,
        "target": target,
        "markers": marker_list,
        "scan_turns": scan_turns,
        "max_chats": safe_chat_limit,
        "matched_chat_count": len(matched_chats),
        "matched_scheduled_count": len(matched_scheduled),
        "deleted_chat_count": sum(1 for item in matched_chats if item.get("deleted")),
        "deleted_scheduled_count": sum(1 for item in matched_scheduled if item.get("deleted")),
        "matched_chats": matched_chats,
        "matched_scheduled_actions": matched_scheduled,
        "errors": errors,
    }


def _format_cleanup_markdown(payload: dict[str, Any]) -> str:
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


def _format_web_capabilities_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Gemini Web Pro 能力清单",
        f"观察日期: {payload['observed_at']} · 账号层级: {payload['account_tier']} · Locale: {payload['locale']}",
        "",
        "### 模型",
    ]
    for model in payload["models"]:
        advanced = "Pro/高级" if model.get("advanced_only") else "通用"
        lines.append(
            "- {alias}: {display_name} ({description}) · thinking_mode_id={mode} · {advanced}".format(
                alias=model["alias"],
                display_name=model["display_name"],
                description=model["description"],
                mode=model["thinking_mode_id"],
                advanced=advanced,
            )
        )

    lines.extend(["", "### 思考等级"])
    for level in payload["thinking_levels"]:
        lines.append(
            f"- {level['id']}: {level['display_name']} ({level['description']}) · level_id={level['level_id']}"
        )

    lines.extend(["", "### 网页工具菜单"])
    for item in payload["tool_menu"]:
        lines.append(f"- {item['label']} ({item['name']}): {item['coverage']}")

    lines.extend(["", "### 设置入口"])
    for item in payload["settings_menu"]:
        lines.append(f"- {item['label']} ({item['name']}): {item['coverage']}")

    lines.extend(["", "### 可探测 RPC"])
    grouped: dict[str, list[dict]] = {}
    for probe in payload["feature_probes"]:
        grouped.setdefault(probe["surface"], []).append(probe)
    for surface, probes in grouped.items():
        names = ", ".join(f"{probe['name']}={probe['rpcid']}" for probe in probes)
        lines.append(f"- {surface}: {names}")

    lines.extend(["", "### MCP 工具覆盖"])
    for group, tools in payload["mcp_tools"].items():
        lines.append(f"- {group}: {', '.join(tools)}")

    lines.extend(["", "### 说明"])
    lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines)


def _iter_gem_values(gems: Any) -> list[Any]:
    if not gems:
        return []
    if hasattr(gems, "values"):
        return list(gems.values())
    return list(gems)


def _find_gem_by_id(gems: Any, gem_id: str) -> Any:
    if hasattr(gems, "get"):
        gem = gems.get(gem_id)
        if gem is not None:
            return gem
    for gem in _iter_gem_values(gems):
        if _gem_field(gem, "id", "gem_id")[1] == gem_id:
            return gem
    return None


def _gem_field(gem: Any, *names: str) -> tuple[bool, str]:
    for name in names:
        if isinstance(gem, dict) and name in gem and gem[name] is not None:
            return True, str(gem[name])
        if hasattr(gem, name):
            value = getattr(gem, name)
            if value is not None:
                return True, str(value)
    return False, ""


def register_manage_tools(mcp: FastMCP, layers: list[str] | set[str] | tuple[str, ...] | None = None):
    enabled_tool_names = resolve_manage_tool_names(layers)

    def _tool(tool_name: str, annotations):
        def decorator(func):
            if tool_name in enabled_tool_names:
                return mcp.tool(annotations=annotations)(func)
            return func

        return decorator

    @_tool("gemini_cleanup_test_artifacts", DESTRUCTIVE_REMOTE)
    async def gemini_cleanup_test_artifacts(
        markers: str = "codex-,Cleanup Verification Marker",
        target: CleanupTarget = "all",
        dry_run: bool = True,
        max_chats: int = 25,
        scan_turns: bool = False,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        查找并可选删除测试产物。

        默认 dry_run=true；只有显式 dry_run=false 才删除命中 marker 的聊天或定时任务。
        scan_turns=true 会读取最近聊天正文，仅在需要清理正文 marker 时使用。
        """
        client = get_gemini_client()
        await initialize_client()
        payload = await _cleanup_test_artifacts_payload(
            client,
            markers=markers,
            target=target,
            dry_run=dry_run,
            max_chats=max_chats,
            scan_turns=scan_turns,
        )
        if response_format == "json":
            return _json_response(payload)
        return [TextContent(type="text", text=_format_cleanup_markdown(payload))]

    @_tool("gemini_list_chats", READS_PRIVATE_REMOTE)
    async def gemini_list_chats(
        limit: int = 10,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出 Gemini 历史对话记录元数据，支持分页。"""
        client = get_gemini_client()
        await initialize_client()

        try:
            if hasattr(client, "_batch_execute"):
                target_count = _clamp_int(limit, default=10, minimum=1, maximum=50) + _clamp_int(
                    offset, default=0, minimum=0, maximum=5000
                )
                items, diagnostic = await _fetch_recent_conversation_metadata(client, target_count)
            else:
                chats = client.list_chats() or []
                items = [_chat_to_dict(chat) for chat in chats]
                diagnostic = {"source": "client_cache", "fetched_count": len(items), "has_remote_more": False}
            if not items:
                return [TextContent(type="text", text="暂无历史对话。")]

            page, pagination = _paginate_items(items, limit, offset, max_limit=50)
            if diagnostic.get("has_remote_more") and not pagination["has_more"]:
                pagination["has_more"] = True
                pagination["next_offset"] = pagination["offset"] + pagination["count"]
            payload = {
                **pagination,
                "items": page,
                "diagnostic": diagnostic,
            }

            if response_format == "json":
                return _json_response(payload)

            chat_list = [
                "## 📜 历史对话",
                f"共 {payload['total_count']} 条；当前 {payload['offset']}..{payload['offset'] + payload['count'] - 1}",
            ]
            for i, chat in enumerate(page, payload["offset"] + 1):
                pin = " 📌" if chat["is_pinned"] else ""
                time_text = f" · {chat['time']}" if chat["time"] else ""
                chat_list.append(f"{i}. {chat['title']}{pin} (ID: {chat['id']}){time_text}")
            if payload["has_more"]:
                chat_list.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(chat_list))]

        except Exception as e:
            logger.error(f"获取聊天列表失败: {e}")
            return [TextContent(type="text", text=f"❌ 获取失败: {str(e)}")]

    @_tool("gemini_scan_chat_history_sources", READS_PRIVATE_REMOTE)
    async def gemini_scan_chat_history_sources(
        limit: int = 50,
        offset: int = 0,
        max_items_per_source: int = 5000,
        page_size: int = 100,
        max_pages_per_source: int = 50,
        include_notebook_chats: bool = True,
        include_remy_goals: bool = True,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        深度枚举 Gemini Web 历史对话元数据来源。

        只读：合并前端已观测的 ListConversations 过滤器、原生 notebook 对话列表，
        以及 Remy goals 中携带的 conversationId 引用。不读取 turn 正文，不删除或移动聊天。
        """
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持 Gemini Web RPC 深度扫描。")]

        try:
            safe_max_items = _clamp_int(max_items_per_source, default=5000, minimum=1, maximum=10000)
            safe_page_size = _clamp_int(page_size, default=100, minimum=1, maximum=100)
            safe_max_pages = _clamp_int(max_pages_per_source, default=50, minimum=1, maximum=200)
            source_blocks = await _fetch_conversation_metadata_sources(
                client,
                CONVERSATION_HISTORY_FILTERS,
                max_items_per_source=safe_max_items,
                page_size=safe_page_size,
                max_pages_per_source=safe_max_pages,
            )

            notebook_summary: list[dict[str, Any]] = []
            notebook_diagnostic: dict[str, Any] | None = None
            if include_notebook_chats:
                notebooks, notebook_diagnostic = await _fetch_native_notebooks(client)
                for notebook in notebooks:
                    notebook_id = notebook.get("id", "")
                    if not notebook_id:
                        continue
                    notebook_items: list[dict[str, Any]] = []
                    notebook_pages: list[dict[str, Any]] = []
                    next_offset = 0
                    while len(notebook_items) < safe_max_items:
                        batch_limit = min(100, safe_max_items - len(notebook_items))
                        page_items, page_payload = await _fetch_notebook_chats(client, notebook_id, batch_limit, next_offset)
                        source_name = f"notebook:{notebook.get('title') or notebook_id}"
                        for item in page_items:
                            item["history_source"] = source_name
                        notebook_items.extend(page_items)
                        notebook_pages.append(
                            {
                                "offset": next_offset,
                                "count": len(page_items),
                                "has_more": bool(page_payload.get("has_more")),
                                "next_offset": page_payload.get("next_offset"),
                            }
                        )
                        if not page_payload.get("has_more") or not page_items:
                            break
                        new_offset = page_payload.get("next_offset")
                        if not isinstance(new_offset, int) or new_offset <= next_offset:
                            break
                        next_offset = new_offset
                    notebook_summary.append(
                        {
                            "notebook_id": notebook_id,
                            "title": notebook.get("title", ""),
                            "fetched_count": len(notebook_items),
                            "pages": notebook_pages,
                        }
                    )
                    source_blocks.append(
                        {
                            "name": f"notebook:{notebook.get('title') or notebook_id}",
                            "rpcid": "MaZiqc",
                            "items": notebook_items,
                            "diagnostic": {
                                "source_rpc": "MaZiqc",
                                "observed": "2026-07-04 Pro UI / Native Gemini Notebook recent chats",
                                "notebook_id": notebook_id,
                                "notebook_title": notebook.get("title", ""),
                                "fetched_count": len(notebook_items),
                                "pages": notebook_pages,
                            },
                        }
                    )

            if include_remy_goals:
                source_blocks.append(
                    await _fetch_remy_goal_conversation_refs(
                        client,
                        max_items=safe_max_items,
                        page_size=safe_page_size,
                        max_pages=safe_max_pages,
                    )
                )

            merged_items, _sources_by_id = _merge_conversation_source_items(source_blocks)
            page, page_info = _paginate_items(merged_items, limit, offset, max_limit=500)
            source_diagnostics = [
                {
                    "name": block.get("name"),
                    "rpcid": block.get("rpcid"),
                    "fetched_count": len(block.get("items", [])),
                    "diagnostic": block.get("diagnostic", {}),
                }
                for block in source_blocks
            ]
            coverage_warnings = []
            for block in source_diagnostics:
                diagnostic = block.get("diagnostic", {})
                stopped_reason = diagnostic.get("stopped_reason")
                if stopped_reason in {"max_items", "max_pages"}:
                    coverage_warnings.append(
                        {
                            "source": block.get("name"),
                            "stopped_reason": stopped_reason,
                            "message": "This source may have more remote items than this scan fetched.",
                        }
                    )

            payload = {
                "ok": True,
                **page_info,
                "items": page,
                "source_rpc": "MaZiqc",
                "observed": "2026-07-04 Pro UI / deep conversation history metadata scan",
                "scan_parameters": {
                    "max_items_per_source": safe_max_items,
                    "page_size": safe_page_size,
                    "max_pages_per_source": safe_max_pages,
                    "include_notebook_chats": include_notebook_chats,
                    "include_remy_goals": include_remy_goals,
                },
                "source_counts": {str(block.get("name")): len(block.get("items", [])) for block in source_blocks},
                "source_diagnostics": source_diagnostics,
                "notebooks": {
                    "included": include_notebook_chats,
                    "diagnostic": notebook_diagnostic,
                    "items": notebook_summary,
                },
                "coverage_warnings": coverage_warnings,
                "note": "This is metadata-only and does not read chat turns. Use read/export tools only for selected chat IDs.",
            }
            if response_format == "json":
                return _json_response(payload)

            lines = [
                "## Gemini 历史对话深度扫描",
                f"合并唯一对话: {payload['total_count']}；当前 offset={payload['offset']} count={payload['count']}",
                "",
                "### 来源计数",
            ]
            for name, count in payload["source_counts"].items():
                lines.append(f"- {name}: {count}")
            if coverage_warnings:
                lines.extend(["", "### 覆盖警告"])
                for warning in coverage_warnings:
                    lines.append(f"- {warning['source']}: {warning['stopped_reason']}")
            lines.extend(["", "### 当前页"])
            for idx, item in enumerate(page, payload["offset"] + 1):
                pin = " 📌" if item.get("is_pinned") else ""
                time_text = f" · {item['time']}" if item.get("time") else ""
                sources = ", ".join(item.get("sources", []))
                lines.append(f"{idx}. {item.get('title') or '(untitled)'}{pin} (ID: {item.get('id', '')}){time_text}")
                if sources:
                    lines.append(f"   sources: {sources}")
                if item.get("project_id"):
                    lines.append(f"   project_id: {item['project_id']}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"深度扫描聊天历史失败: {e}")
            return [TextContent(type="text", text=f"❌ 深度扫描失败: {str(e)}")]

    @_tool("gemini_read_chat", READS_PRIVATE_REMOTE)
    async def gemini_read_chat(
        chat_id: str,
        limit: int = 20,
        response_format: ResponseFormat = "markdown",
        max_chars_per_turn: int = 4000,
    ) -> list[TextContent]:
        """读取指定 Gemini 历史对话内容。会返回私人聊天文本，请只在用户需要时调用。"""
        client = get_gemini_client()
        await initialize_client()

        if not chat_id:
            return [TextContent(type="text", text="❌ 读取聊天需要提供 chat_id。")]
        if not hasattr(client, "read_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 read_chat。")]

        try:
            safe_limit = min(max(limit, 1), 100)
            safe_chars = min(max(max_chars_per_turn, 200), 20000)
            history = await client.read_chat(chat_id, limit=safe_limit)
            if not history:
                return [TextContent(type="text", text=f"未找到聊天: {chat_id}")]
            turns = getattr(history, "turns", []) or []
            items = [_turn_to_dict(turn, safe_chars) for turn in turns[:safe_limit]]
            payload = {
                "chat_id": getattr(history, "cid", chat_id),
                "count": len(items),
                "limit": safe_limit,
                "turns": items,
            }

            if response_format == "json":
                return _json_response(payload)

            lines = [f"## 💬 聊天记录: {payload['chat_id']}", f"返回 {payload['count']} 条 turn"]
            for idx, turn in enumerate(items, 1):
                lines.extend(["", f"### {idx}. {turn['role']}", turn["text"]])
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"读取聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取失败: {str(e)}")]

    @_tool("gemini_search_chats", READS_PRIVATE_REMOTE)
    async def gemini_search_chats(
        query: str,
        limit: int = 10,
        offset: int = 0,
        scan_turns: bool = False,
        turns_per_chat: int = 20,
        max_chars_per_turn: int = 1000,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        搜索 Gemini Web 历史对话。

        默认只搜索标题/ID；只有 scan_turns=true 时才读取聊天正文进行内容匹配。
        """
        client = get_gemini_client()
        await initialize_client()

        needle = (query or "").strip()
        if not needle:
            return [TextContent(type="text", text="❌ 搜索聊天需要提供 query。")]
        if scan_turns and not hasattr(client, "read_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持正文搜索需要的 read_chat。")]

        try:
            safe_limit = _clamp_int(limit, default=10, minimum=1, maximum=50)
            safe_offset = _clamp_int(offset, default=0, minimum=0, maximum=5000)
            if hasattr(client, "_batch_execute"):
                chats, diagnostic = await _fetch_recent_conversation_metadata(client, safe_limit + safe_offset)
            else:
                chats = [_chat_to_dict(chat) for chat in client.list_chats() or []]
                diagnostic = {"source": "client_cache", "fetched_count": len(chats), "has_remote_more": False}
            page, pagination = _paginate_items(chats, safe_limit, safe_offset, max_limit=50)
            if diagnostic.get("has_remote_more") and not pagination["has_more"]:
                pagination["has_more"] = True
                pagination["next_offset"] = pagination["offset"] + pagination["count"]
            safe_turn_limit = _clamp_int(turns_per_chat, default=20, minimum=1, maximum=50)
            safe_chars = _clamp_int(max_chars_per_turn, default=1000, minimum=100, maximum=4000)
            matches = []
            lowered = needle.lower()

            for chat in page:
                item = chat if isinstance(chat, dict) else _chat_to_dict(chat)
                fields = []
                snippets = []
                if lowered in item["title"].lower():
                    fields.append("title")
                if item["id"] and lowered in item["id"].lower():
                    fields.append("id")

                if scan_turns:
                    try:
                        _history, turns = await _read_chat_turns(
                            client,
                            item["id"],
                            safe_turn_limit,
                            safe_chars,
                        )
                    except Exception as e:
                        snippets.append({"error": f"{type(e).__name__}: {e}"})
                        turns = []
                    for idx, turn in enumerate(turns, 1):
                        if _turn_matches_query(turn, needle):
                            fields.append("turn")
                            snippets.append(
                                {
                                    "turn_index": idx,
                                    "role": turn["role"],
                                    "text": _truncate(turn["text"], safe_chars),
                                }
                            )

                if fields:
                    match = {
                        **item,
                        "matched_fields": sorted(set(fields)),
                    }
                    if snippets:
                        match["snippets"] = snippets[:5]
                    matches.append(match)

            payload = {
                "query": needle,
                "scan_turns": scan_turns,
                "scanned_count": len(page),
                "match_count": len(matches),
                **pagination,
                "matches": matches,
                "diagnostic": diagnostic,
                "note": "正文搜索只会在 scan_turns=true 时读取当前页聊天内容。",
            }

            if response_format == "json":
                return _json_response(payload)

            lines = [
                "## Gemini 历史搜索",
                f"Query: {needle}",
                f"Scanned: {payload['scanned_count']}/{payload['total_count']} · Matches: {payload['match_count']}",
            ]
            if not matches:
                lines.append("未在当前页找到匹配项。")
            for idx, match in enumerate(matches, 1):
                fields = ", ".join(match["matched_fields"])
                time_text = f" · {match['time']}" if match.get("time") else ""
                lines.append(f"{idx}. {match['title']} (ID: {match['id']}) · fields={fields}{time_text}")
                for snippet in match.get("snippets", []):
                    if snippet.get("error"):
                        lines.append(f"   - read error: {snippet['error']}")
                    else:
                        text = snippet.get("text", "").replace("\n", " ")
                        lines.append(f"   - turn {snippet.get('turn_index')} {snippet.get('role')}: {text}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            if not scan_turns:
                lines.append("\n说明: 当前只搜索标题/ID；如需正文匹配，传入 scan_turns=true。")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"搜索聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 搜索失败: {str(e)}")]

    @_tool("gemini_export_chat", READS_PRIVATE_REMOTE)
    async def gemini_export_chat(
        chat_id: str,
        response_format: ResponseFormat = "markdown",
        limit: int = 100,
        max_chars_per_turn: int = 20000,
        include_metadata: bool = True,
    ) -> list[TextContent]:
        """导出指定 Gemini Web 历史对话为 Markdown 或 JSON。会返回私人聊天文本。"""
        client = get_gemini_client()
        await initialize_client()

        if not chat_id:
            return [TextContent(type="text", text="❌ 导出聊天需要提供 chat_id。")]
        if not hasattr(client, "read_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 read_chat。")]

        try:
            safe_limit = min(max(limit, 1), 200)
            safe_chars = min(max(max_chars_per_turn, 200), 20000)
            history, turns = await _read_chat_turns(client, chat_id, safe_limit, safe_chars)
            if not history:
                return [TextContent(type="text", text=f"未找到聊天: {chat_id}")]

            metadata = None
            if include_metadata:
                metadata = {"id": chat_id}
                try:
                    if hasattr(client, "_batch_execute"):
                        chats, _diagnostic = await _fetch_recent_conversation_metadata(client, 500)
                    else:
                        chats = [_chat_to_dict(chat) for chat in client.list_chats()] if hasattr(client, "list_chats") else []
                    for chat in chats or []:
                        item = chat if isinstance(chat, dict) else _chat_to_dict(chat)
                        if item.get("id") == chat_id:
                            metadata = item
                            break
                except Exception as e:
                    metadata["metadata_warning"] = f"{type(e).__name__}: {e}"

            payload = _chat_export_payload(chat_id, history, turns, metadata, safe_limit, safe_chars)
            if response_format == "json":
                return _json_response(payload)
            return [TextContent(type="text", text=_format_chat_export_markdown(payload))]
        except Exception as e:
            logger.error(f"导出聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 导出失败: {str(e)}")]

    @_tool("gemini_history", READS_PRIVATE_REMOTE)
    async def gemini_history(
        action: HistoryAction = "list",
        chat_id: str = "",
        query: str = "",
        limit: int = 10,
        offset: int = 0,
        scan_turns: bool = False,
        turns_per_chat: int = 20,
        max_chars_per_turn: int = 4000,
        max_items_per_source: int = 5000,
        page_size: int = 100,
        max_pages_per_source: int = 50,
        include_notebook_chats: bool = True,
        include_remy_goals: bool = True,
        include_metadata: bool = True,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        Read, scan, search, or export Gemini Web chat history through action=list/scan/search/read/export.

        This read-only entrypoint never deletes or moves chats. Use it for narrow history agents.
        """
        if action == "list":
            return await gemini_list_chats(limit=limit, offset=offset, response_format=response_format)
        if action == "scan":
            return await gemini_scan_chat_history_sources(
                limit=limit,
                offset=offset,
                max_items_per_source=max_items_per_source,
                page_size=page_size,
                max_pages_per_source=max_pages_per_source,
                include_notebook_chats=include_notebook_chats,
                include_remy_goals=include_remy_goals,
                response_format=response_format,
            )
        if action == "search":
            return await gemini_search_chats(
                query=query,
                limit=limit,
                offset=offset,
                scan_turns=scan_turns,
                turns_per_chat=turns_per_chat,
                max_chars_per_turn=max_chars_per_turn,
                response_format=response_format,
            )
        if action == "read":
            return await gemini_read_chat(
                chat_id=chat_id,
                limit=limit,
                response_format=response_format,
                max_chars_per_turn=max_chars_per_turn,
            )
        if action == "export":
            return await gemini_export_chat(
                chat_id=chat_id,
                response_format=response_format,
                limit=limit,
                max_chars_per_turn=max_chars_per_turn,
                include_metadata=include_metadata,
            )
        return [TextContent(type="text", text=f"❌ 不支持的 history action: {action}")]

    @_tool("gemini_delete_chat", DESTRUCTIVE_REMOTE)
    async def gemini_delete_chat(chat_id: str) -> list[TextContent]:
        """删除指定 Gemini 历史对话。该操作会修改远端聊天记录。"""
        client = get_gemini_client()
        await initialize_client()

        if not chat_id:
            return [TextContent(type="text", text="❌ 删除聊天需要提供 chat_id。")]
        if not hasattr(client, "delete_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 delete_chat。")]

        try:
            await client.delete_chat(chat_id)
            return [TextContent(type="text", text=f"✅ 已删除聊天: {chat_id}")]
        except Exception as e:
            logger.error(f"删除聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 删除失败: {str(e)}")]

    @_tool("gemini_inspect_account", READS_PRIVATE_REMOTE)
    async def gemini_inspect_account(
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """检查当前 Gemini 账号可用能力和 Web RPC 状态。"""
        client = get_gemini_client()
        await initialize_client()

        if not hasattr(client, "inspect_account_status"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 inspect_account_status。")]

        try:
            status = await client.inspect_account_status()
            sanitized = _sanitize_account_status(status)
            if response_format == "json":
                return _json_response(sanitized)

            summary = sanitized.get("summary", {})
            lines = ["## Gemini 账号能力状态"]
            if summary:
                for key, value in summary.items():
                    lines.append(f"- {key}: {value}")
            rpc = sanitized.get("rpc", {})
            if rpc:
                lines.extend(["", "## Web RPC 探测"])
                for name, payload in rpc.items():
                    ok = "可用" if payload.get("ok") else "不可用"
                    status_code = payload.get("status_code")
                    reject_code = payload.get("reject_code")
                    suffix = f" HTTP {status_code}" if status_code else ""
                    if reject_code:
                        suffix += f" reject={reject_code}"
                    lines.append(f"- {name}: {ok}{suffix}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"账号状态检查失败: {e}")
            return [TextContent(type="text", text=f"❌ 检查失败: {str(e)}")]

    @_tool("gemini_probe_web_features", READ_ONLY_REMOTE)
    async def gemini_probe_web_features(
        surface: FeatureSurface = "all",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        探测新版 Gemini Web Pro 页面入口背后的只读 RPC 是否可达。

        这个工具只返回 HTTP/RPC 状态和 reject code，不返回响应正文或账号内容。
        """
        client = get_gemini_client()
        await initialize_client()

        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持底层 batch RPC 探测。")]

        selected = [
            probe
            for probe in WEB_FEATURE_PROBES
            if surface == "all" or probe["surface"] == surface
        ]
        results = []
        for probe in selected:
            try:
                response = await client._batch_execute(
                    [_RawRPCData(probe["rpcid"], probe["payload"])],
                    source_path=probe["source_path"],
                    close_on_error=False,
                )
                summary = _summarize_probe_response(response.text, probe["rpcid"])
                reject_code = summary.get("reject_code")
                ok = response.status_code == 200 and reject_code is None
                results.append(
                    {
                        "surface": probe["surface"],
                        "name": probe["name"],
                        "rpcid": probe["rpcid"],
                        "source_path": probe["source_path"],
                        "observed": probe["observed"],
                        "ok": ok,
                        "status_code": response.status_code,
                        **summary,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "surface": probe["surface"],
                        "name": probe["name"],
                        "rpcid": probe["rpcid"],
                        "source_path": probe["source_path"],
                        "observed": probe["observed"],
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )

        payload = {
            "surface": surface,
            "count": len(results),
            "ok_count": sum(1 for item in results if item.get("ok")),
            "results": results,
            "note": "Probe output intentionally omits raw response bodies and account content.",
        }

        if response_format == "json":
            return _json_response(payload)

        lines = [
            "## Gemini Web 功能探测",
            f"范围: {surface}",
            f"可用: {payload['ok_count']}/{payload['count']}",
        ]
        grouped: dict[str, list[dict]] = {}
        for item in results:
            grouped.setdefault(item["surface"], []).append(item)
        for group_name, items in grouped.items():
            lines.extend(["", f"### {group_name}"])
            for item in items:
                status = "可达" if item.get("ok") else "不可达"
                reject = item.get("reject_code")
                suffix = f", reject={reject}" if reject is not None else ""
                if item.get("error"):
                    suffix += f", error={item['error']}"
                lines.append(f"- {item['name']} ({item['rpcid']}): {status}{suffix}")
        lines.append("\n说明: 输出已省略原始响应正文和账号内容。")
        return [TextContent(type="text", text="\n".join(lines))]

    @_tool("gemini_get_web_capabilities", READ_ONLY_REMOTE)
    async def gemini_get_web_capabilities(
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        返回基于 Gemini Web Pro 实测 UI 的模型、工具菜单、设置入口和 MCP 覆盖清单。

        这是只读静态清单；实时 RPC 可达性请配合 gemini_probe_web_features 使用。
        """
        payload = _web_capabilities_payload()
        if response_format == "json":
            return _json_response(payload)
        return [TextContent(type="text", text=_format_web_capabilities_markdown(payload))]

    @_tool("gemini_get_tool_manifest", READ_ONLY_LOCAL)
    async def gemini_get_tool_manifest(
        scope: ManifestScope = "all",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        返回面向 agent 的 Gemini MCP 工具清单，包含能力、隐私、分页和安全提示。

        这是静态只读清单，不访问账号内容。
        """
        payload = _tool_manifest_payload(scope)
        if response_format == "json":
            return _json_response(payload)
        return [TextContent(type="text", text=_format_tool_manifest_markdown(payload))]

    @_tool("gemini_account_inventory", READS_PRIVATE_REMOTE)
    async def gemini_account_inventory(
        surface: AccountInventorySurface = "capabilities",
        feature_surface: FeatureSurface = "all",
        usage_scope: UsageScope = "all",
        scheduled_scope: ScheduledScope = "all",
        notebook_action: NotebookAction = "list",
        notebook_id: str = "",
        notebook_title: str = "",
        limit: int = 20,
        offset: int = 0,
        locale: str = "zh-CN",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        Read Gemini Web account inventory by surface.

        Supports capabilities, status, features, links, usage, library, notebooks, scheduled, modes, and models
        without mutating account data.
        """
        if surface == "capabilities":
            return await gemini_get_web_capabilities(response_format=response_format)
        if surface == "status":
            return await gemini_inspect_account(response_format=response_format)
        if surface == "features":
            return await gemini_probe_web_features(surface=feature_surface, response_format=response_format)
        if surface == "links":
            return await gemini_list_public_links(limit=limit, offset=offset, response_format=response_format)
        if surface == "usage":
            return await gemini_get_usage_limits(scope=usage_scope, response_format=response_format)
        if surface == "library":
            return await gemini_list_library_capabilities(limit=limit, offset=offset, response_format=response_format)
        if surface == "notebooks":
            return await gemini_notebooks(
                action=notebook_action,
                notebook_id=notebook_id,
                notebook_title=notebook_title,
                limit=limit,
                offset=offset,
                locale=locale,
                response_format=response_format,
            )
        if surface == "notebook_chats":
            return await gemini_notebooks(
                action="chats",
                notebook_id=notebook_id,
                notebook_title=notebook_title,
                limit=limit,
                offset=offset,
                locale=locale,
                response_format=response_format,
            )
        if surface == "scheduled":
            return await gemini_list_scheduled_actions(
                scope=scheduled_scope,
                limit=limit,
                offset=offset,
                response_format=response_format,
            )
        if surface == "modes":
            return await gemini_get_tool_mode_status(limit=limit, offset=offset, response_format=response_format)
        if surface == "models":
            return await gemini_list_models()
        return [TextContent(type="text", text=f"❌ 不支持的 account inventory surface: {surface}")]

    @_tool("gemini_list_public_links", READS_PRIVATE_REMOTE)
    async def gemini_list_public_links(
        limit: int = 20,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出 Gemini Web “你的公开链接”页面返回的公开链接条目。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持公开链接 RPC。")]

        try:
            probe = _get_probe("sharing", "public_links_index")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            entries = bodies[0] if bodies and isinstance(bodies[0], list) else []
            parsed_links = [_parse_public_link_entry(item) for item in entries]
            links, page_info = _paginate_items(parsed_links, limit, offset)
            payload = {
                **page_info,
                "items": links,
                "source_rpc": probe["rpcid"],
                "observed": probe["observed"],
            }
            if response_format == "json":
                return _json_response(payload)

            if not links:
                return [TextContent(type="text", text="暂无公开链接。")]
            lines = [
                "## Gemini 公开链接",
                f"共 {payload['total_count']} 条；当前 offset={payload['offset']} count={payload['count']}",
            ]
            for idx, link in enumerate(links, payload["offset"] + 1):
                title = link.get("title") or "(untitled)"
                disabled = "禁用" if link.get("disabled") else "启用"
                url = link.get("url") or "(no url)"
                lines.append(f"{idx}. {title} [{disabled}]\n   ID: {link.get('id', '')}\n   URL: {url}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"公开链接列表读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取公开链接失败: {str(e)}")]

    @_tool("gemini_get_usage_limits", READS_PRIVATE_REMOTE)
    async def gemini_get_usage_limits(
        scope: UsageScope = "all",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """读取 Gemini Web 用量限额页面返回的限额/模型状态结构。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持用量限额 RPC。")]

        probe_names = []
        if scope in {"quota", "all"}:
            probe_names.append("usage_quota")
        if scope in {"model_state", "all"}:
            probe_names.append("usage_model_state")

        results = []
        try:
            for name in probe_names:
                probe = _get_probe("usage", name)
                response = await _execute_observed_rpc(client, probe)
                bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
                entries = []
                if bodies and isinstance(bodies[0], list) and bodies[0]:
                    first = bodies[0][0]
                    if isinstance(first, list):
                        entries = [_parse_usage_entry(item) for item in first]
                results.append(
                    {
                        "name": name,
                        "source_rpc": probe["rpcid"],
                        "observed": probe["observed"],
                        "entries": entries,
                    }
                )

            payload = {"scope": scope, "count": len(results), "results": results}
            if response_format == "json":
                return _json_response(payload)

            lines = ["## Gemini 用量限额", f"范围: {scope}"]
            for result in results:
                lines.extend(["", f"### {result['name']}"])
                if not result["entries"]:
                    lines.append("- 暂无条目")
                    continue
                for item in result["entries"]:
                    reset = f", reset={item['reset_time']}" if item.get("reset_time") else ""
                    lines.append(
                        "- key={key}, status={status}, tier={tier}, limit={limit}, remaining={remaining}{reset}".format(
                            key=item.get("key"),
                            status=item.get("status"),
                            tier=item.get("tier"),
                            limit=item.get("limit_value"),
                            remaining=item.get("remaining_value"),
                            reset=reset,
                        )
                    )
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"用量限额读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取用量限额失败: {str(e)}")]

    @_tool("gemini_list_library_capabilities", READ_ONLY_REMOTE)
    async def gemini_list_library_capabilities(
        limit: int = 20,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出 Gemini Web Library 页面暴露的本地化能力/模板条目。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持 Library RPC。")]

        try:
            probe = _get_probe("library", "library_locale_capabilities")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            entries = []
            if bodies and isinstance(bodies[0], list) and bodies[0]:
                first = bodies[0][0]
                if isinstance(first, list):
                    entries = [_parse_library_capability(item) for item in first]
            page, page_info = _paginate_items(entries, limit, offset)
            payload = {
                **page_info,
                "items": page,
                "source_rpc": probe["rpcid"],
                "observed": probe["observed"],
            }
            if response_format == "json":
                return _json_response(payload)

            if not page:
                return [TextContent(type="text", text="暂无 Library 能力条目。")]
            lines = [
                "## Gemini Library 能力",
                f"共 {payload['total_count']} 条；当前 offset={payload['offset']} count={payload['count']}",
            ]
            for idx, item in enumerate(page, payload["offset"] + 1):
                aliases = ", ".join(item.get("aliases", []))
                details = f"\n   {item['details']}" if item.get("details") else ""
                lines.append(f"{idx}. {item.get('name') or aliases}\n   {item.get('description', '')}{details}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"Library 能力读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取 Library 能力失败: {str(e)}")]

    @_tool("gemini_list_notebooks", READS_PRIVATE_REMOTE)
    async def gemini_list_notebooks(
        limit: int = 50,
        offset: int = 0,
        locale: str = "zh-CN",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出 Gemini Web 原生笔记本。只读，不访问 NotebookLM。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持 Gemini Notebooks RPC。")]

        try:
            notebooks, diagnostic = await _fetch_native_notebooks(client, locale)
            page, page_info = _paginate_items(notebooks, limit, offset, max_limit=100)
            payload = {
                "ok": True,
                **page_info,
                "items": page,
                "source_rpc": diagnostic["source_rpc"],
                "observed": diagnostic["observed"],
                "diagnostic": diagnostic,
                "note": "These are native Gemini Web Notebooks, not NotebookLM notebooks.",
            }
            if response_format == "json":
                return _json_response(payload)

            if not page:
                return [TextContent(type="text", text="暂无 Gemini 原生笔记本。")]
            lines = [
                "## Gemini 原生笔记本",
                f"共 {payload['total_count']} 个；当前 offset={payload['offset']} count={payload['count']}",
            ]
            for idx, item in enumerate(page, payload["offset"] + 1):
                emoji = f"{item['emoji']} " if item.get("emoji") else ""
                sources = f" · sources={item['source_count']}" if item.get("source_count") is not None else ""
                lines.append(f"{idx}. {emoji}{item.get('title') or '(untitled)'}{sources}\n   ID: {item.get('id', '')}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"Gemini Notebooks 列表读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取 Gemini Notebooks 失败: {str(e)}")]

    @_tool("gemini_list_notebook_chats", READS_PRIVATE_REMOTE)
    async def gemini_list_notebook_chats(
        notebook_id: str = "",
        notebook_title: str = "",
        limit: int = 20,
        offset: int = 0,
        locale: str = "zh-CN",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出某个 Gemini 原生笔记本内的最近对话元数据。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持 Gemini Notebooks RPC。")]

        try:
            notebooks, diagnostic = await _fetch_native_notebooks(client, locale)
            notebook = _find_notebook(notebooks, notebook_id, notebook_title)
            if not notebook:
                available = [item.get("title", "") for item in notebooks if item.get("title")]
                payload = {
                    "ok": False,
                    "notebook_id": notebook_id,
                    "notebook_title": notebook_title,
                    "available_titles": available,
                    "diagnostic": diagnostic,
                }
                if response_format == "json":
                    return _json_response(payload)
                return [TextContent(type="text", text=f"未找到匹配的 Gemini 原生笔记本。可用标题: {', '.join(available)}")]

            items, page_payload = await _fetch_notebook_chats(client, notebook["id"], limit, offset)
            payload = {
                "ok": True,
                "notebook": notebook,
                **page_payload,
                "items": items,
                "source_rpc": "MaZiqc",
                "observed": "2026-07-04 Pro UI / Native Gemini Notebook recent chats",
            }
            if response_format == "json":
                return _json_response(payload)

            lines = [
                f"## Notebook Chats: {notebook.get('title') or notebook['id']}",
                f"当前 offset={payload['offset']} count={payload['count']}；fetched={payload['diagnostic']['fetched_count']}",
            ]
            if not items:
                lines.append("- 暂无最近对话。")
            for idx, item in enumerate(items, payload["offset"] + 1):
                time_text = f" · {item['time']}" if item.get("time") else ""
                lines.append(f"{idx}. {item.get('title') or '(untitled)'} (ID: {item.get('id', '')}){time_text}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"Gemini Notebook 对话读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取 Gemini Notebook 对话失败: {str(e)}")]

    @_tool("gemini_notebooks", READS_PRIVATE_REMOTE)
    async def gemini_notebooks(
        action: NotebookAction = "list",
        notebook_id: str = "",
        notebook_title: str = "",
        limit: int = 20,
        offset: int = 0,
        locale: str = "zh-CN",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        List native Gemini Web Notebooks or recent chats inside one notebook.

        This read-only entrypoint does not move, create, or delete notebooks/chats.
        """
        if action == "list":
            return await gemini_list_notebooks(
                limit=limit,
                offset=offset,
                locale=locale,
                response_format=response_format,
            )
        if action == "chats":
            return await gemini_list_notebook_chats(
                notebook_id=notebook_id,
                notebook_title=notebook_title,
                limit=limit,
                offset=offset,
                locale=locale,
                response_format=response_format,
            )
        return [TextContent(type="text", text=f"❌ 不支持的 notebook action: {action}")]

    @_tool("gemini_move_chat_to_notebook", MUTATES_REMOTE)
    async def gemini_move_chat_to_notebook(
        chat_id: str,
        notebook_id: str = "",
        notebook_title: str = "",
        locale: str = "zh-CN",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """把已有 Gemini Web 对话移动到 Gemini 原生笔记本。该操作修改远端聊天元数据，但不删除聊天。"""
        clean_chat_id = chat_id.strip()
        if not clean_chat_id:
            return [TextContent(type="text", text="❌ chat_id 不能为空。")]
        if not notebook_id.strip() and not notebook_title.strip():
            return [TextContent(type="text", text="❌ 需要提供 notebook_id 或 notebook_title。")]

        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持 Gemini Notebooks RPC。")]

        try:
            notebooks, list_diagnostic = await _fetch_native_notebooks(client, locale)
            notebook = _find_notebook(notebooks, notebook_id, notebook_title)
            if not notebook:
                available = [item.get("title", "") for item in notebooks if item.get("title")]
                payload = {
                    "ok": False,
                    "chat_id": clean_chat_id,
                    "notebook_id": notebook_id,
                    "notebook_title": notebook_title,
                    "available_titles": available,
                    "diagnostic": list_diagnostic,
                }
                if response_format == "json":
                    return _json_response(payload)
                return [TextContent(type="text", text=f"未找到匹配的 Gemini 原生笔记本。可用标题: {', '.join(available)}")]

            project_type = notebook.get("project_type") if isinstance(notebook.get("project_type"), int) else 2
            request_payload = _move_chat_to_notebook_payload(clean_chat_id, notebook["id"], project_type)
            response = await client._batch_execute(
                [_RawRPCData("MUAZcd", request_payload)],
                source_path="/app",
                close_on_error=False,
            )
            bodies = _extract_rpc_bodies(response.text, "MUAZcd")
            updated_entry = None
            body = bodies[0] if bodies else []
            if isinstance(body, list):
                candidate = body[1] if len(body) > 1 and isinstance(body[1], list) else None
                updated_entry = _parse_conversation_metadata(candidate) if candidate else None

            verify_items, verify_payload = await _fetch_notebook_chats(client, notebook["id"], 100, 0)
            verified = any(item.get("id") == clean_chat_id for item in verify_items)
            payload = {
                "ok": response.status_code == 200 and bool(bodies),
                "chat_id": clean_chat_id,
                "notebook": notebook,
                "source_rpc": "MUAZcd",
                "status_code": response.status_code,
                "body_present": bool(bodies),
                "updated_entry": updated_entry,
                "verified_in_target_notebook": verified,
                "verification": verify_payload,
            }
            if response_format == "json":
                return _json_response(payload)

            if payload["ok"] and verified:
                return [
                    TextContent(
                        type="text",
                        text=f"✅ 已移动聊天 {clean_chat_id} 到笔记本: {notebook.get('title')} ({notebook.get('id')})",
                    )
                ]
            if payload["ok"]:
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"⚠️ Gemini 接受了移动请求，但目标笔记本最近对话列表未验证到 {clean_chat_id}。"
                            "请稍后用 gemini_list_notebook_chats 复查。"
                        ),
                    )
                ]
            return [TextContent(type="text", text=f"❌ 移动聊天失败: {clean_chat_id}")]
        except Exception as e:
            logger.error(f"Gemini Notebook 移动失败: {e}")
            return [TextContent(type="text", text=f"❌ 移动 Gemini Notebook 聊天失败: {str(e)}")]

    @_tool("gemini_list_scheduled_actions", READS_PRIVATE_REMOTE)
    async def gemini_list_scheduled_actions(
        scope: ScheduledScope = "all",
        limit: int = 20,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
        max_chars_per_field: int = 500,
    ) -> list[TextContent]:
        """列出 Gemini Web 定时操作页面返回的定时任务条目。只读，不创建/修改/删除任务。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持定时操作 RPC。")]

        safe_chars = min(max(max_chars_per_field, 40), 4000)
        try:
            entries, diagnostic = await _fetch_scheduled_registry(client, safe_chars)
            if scope == "active":
                entries = [item for item in entries if item.get("enabled") is True]
            elif scope == "inactive":
                entries = [item for item in entries if item.get("enabled") is False]

            page, page_info = _paginate_items(entries, limit, offset)
            result = {
                "name": "scheduled_actions_registry",
                "source_rpc": diagnostic["source_rpc"],
                "observed": diagnostic["observed"],
                **page_info,
                "diagnostic": diagnostic,
                "items": page,
            }
            payload = {"scope": scope, "count": 1, "results": [result]}
            if response_format == "json":
                return _json_response(payload)

            lines = ["## Gemini 定时操作", f"范围: {scope}"]
            lines.extend(["", f"### {result['name']}"])
            if not result["items"]:
                lines.append("- 暂无条目")
                if diagnostic.get("empty_hint"):
                    lines.append(f"- 诊断: {diagnostic['empty_hint']}")
            for item in result["items"]:
                enabled = item.get("enabled")
                enabled_text = "enabled" if enabled is True else "disabled" if enabled is False else "unknown"
                label = f", label={item['schedule_label']}" if item.get("schedule_label") else ""
                hour_text = f", hour={item['hour']}" if item.get("hour") is not None else ""
                timezone_text = f", timezone={item['timezone_name']}" if item.get("timezone_name") else ""
                lines.append(
                    f"- {item.get('title') or '(untitled)'} ({item.get('id', '')}) [{enabled_text}]{label}{hour_text}{timezone_text}"
                )
            if result["has_more"] and result.get("next_offset") is not None:
                lines.append(f"- 下一页: offset={result['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"定时操作读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取定时操作失败: {str(e)}")]

    @_tool("gemini_get_scheduled_action", READS_PRIVATE_REMOTE)
    async def gemini_get_scheduled_action(
        action_id: str,
        response_format: ResponseFormat = "markdown",
        max_chars_per_field: int = 500,
    ) -> list[TextContent]:
        """按 id 读取单个 Gemini Web 定时操作。只读，不修改任务状态。"""
        clean_id = action_id.strip()
        if not clean_id:
            return [TextContent(type="text", text="❌ action_id 不能为空。")]

        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持定时操作 RPC。")]

        safe_chars = min(max(max_chars_per_field, 40), 4000)
        try:
            item, diagnostic = await _fetch_scheduled_task_by_id(client, clean_id, safe_chars)
            payload = {
                "ok": item is not None,
                "id": clean_id,
                "source_rpc": diagnostic["source_rpc"],
                "observed": diagnostic["observed"],
                "diagnostic": diagnostic,
                "item": item,
            }
            if response_format == "json":
                return _json_response(payload)

            if not item:
                hint = f" {diagnostic['empty_hint']}" if diagnostic.get("empty_hint") else ""
                return [TextContent(type="text", text=f"未读取到定时操作: {clean_id}.{hint}")]
            enabled = item.get("enabled")
            enabled_text = "enabled" if enabled is True else "disabled" if enabled is False else "unknown"
            label = f"\n计划: {item['schedule_label']}" if item.get("schedule_label") else ""
            hour_text = f"\n小时: {item['hour']}" if item.get("hour") is not None else ""
            timezone_text = f"\n时区: {item['timezone_name']}" if item.get("timezone_name") else ""
            return [
                TextContent(
                    type="text",
                    text=(
                        f"## Gemini 定时操作\n"
                        f"ID: {item.get('id', clean_id)}\n"
                        f"标题: {item.get('title') or '(untitled)'}\n"
                        f"状态: {enabled_text}{label}{hour_text}{timezone_text}"
                    ),
                )
            ]
        except Exception as e:
            logger.error(f"定时操作按 ID 读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取定时操作失败: {str(e)}")]

    @_tool("gemini_create_scheduled_action", MUTATES_REMOTE)
    async def gemini_create_scheduled_action(
        title: str,
        instructions: str,
        hour: int = 9,
        timezone_name: str = "Asia/Shanghai",
        locale: str = "zh-CN",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        创建 Gemini Web 每日定时操作。

        目前只开放已验证的 daily schedule 契约：每天在指定小时触发。edit/toggle/weekly
        等变体等待稳定 RPC 证据后再开放。
        """
        clean_title = title.strip()
        clean_instructions = instructions.strip()
        clean_timezone = timezone_name.strip()
        clean_locale = locale.strip() or "zh-CN"
        if not clean_title:
            return [TextContent(type="text", text="❌ title 不能为空。")]
        if not clean_instructions:
            return [TextContent(type="text", text="❌ instructions 不能为空。")]
        if hour < 0 or hour > 23:
            return [TextContent(type="text", text="❌ hour 必须在 0 到 23 之间。")]
        if not clean_timezone:
            return [TextContent(type="text", text="❌ timezone_name 不能为空。")]

        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持定时操作 RPC。")]

        try:
            request_payload = _scheduled_daily_payload(
                clean_title,
                clean_instructions,
                hour,
                clean_timezone,
                clean_locale,
            )
            response = await client._batch_execute(
                [_RawRPCData("Jba3ib", request_payload)],
                source_path="/scheduled",
                close_on_error=False,
            )
            bodies = _extract_rpc_bodies(response.text, "Jba3ib")
            body = bodies[0] if bodies else []
            if isinstance(body, list) and body and isinstance(body[0], list):
                body = body[0]
            created = _parse_scheduled_action_create_body(body)
            visible_in_registry = False
            readable_by_id_after_create = None
            task_state_after_create = ""
            task_state_id_after_create = None
            verification_error = ""
            get_task_error = ""
            get_task_diagnostic: dict[str, Any] = {}
            verification_status = "not_attempted"
            if created.get("id"):
                try:
                    registry_entries, _ = await _fetch_scheduled_registry(client, 400)
                    visible_in_registry = any(item.get("id") == created.get("id") for item in registry_entries)
                    if visible_in_registry:
                        verification_status = "visible_in_registry"
                    elif registry_entries:
                        verification_status = "not_visible_in_nonempty_registry"
                    else:
                        verification_status = "registry_empty_unverified"
                except Exception as e:
                    verification_error = str(e)
                    verification_status = "verification_error"
                try:
                    task_by_id, get_task_diagnostic = await _fetch_scheduled_task_by_id(client, created["id"], 400)
                    readable_by_id_after_create = task_by_id is not None
                    if task_by_id:
                        task_state_after_create = str(task_by_id.get("task_state") or "")
                        task_state_id_after_create = task_by_id.get("task_state_id")
                    if readable_by_id_after_create and verification_status == "registry_empty_unverified":
                        verification_status = "readable_by_id_registry_empty"
                    elif readable_by_id_after_create and verification_status == "not_visible_in_nonempty_registry":
                        verification_status = "readable_by_id_not_visible_in_registry"
                except Exception as e:
                    get_task_error = str(e)
            payload = {
                "ok": response.status_code == 200 and bool(created.get("id")),
                "id": created.get("id", ""),
                "title": created.get("title") or clean_title,
                "instructions": created.get("instructions") or clean_instructions,
                "schedule_label": created.get("schedule_label", ""),
                "enabled": created.get("enabled"),
                "hour": hour,
                "timezone_name": clean_timezone,
                "locale": clean_locale,
                "source_rpc": "Jba3ib",
                "visible_in_registry": visible_in_registry,
                "readable_by_id_after_create": readable_by_id_after_create,
                "task_state_after_create": task_state_after_create,
                "task_state_id_after_create": task_state_id_after_create,
                "verification_status": verification_status,
                "verification_error": verification_error,
                "get_task_error": get_task_error,
                "get_task_diagnostic": get_task_diagnostic,
            }
            if response_format == "json":
                return _json_response(payload)

            if payload["ok"]:
                label = f" ({payload['schedule_label']})" if payload.get("schedule_label") else ""
                if visible_in_registry:
                    visibility = ""
                elif readable_by_id_after_create:
                    visibility = "；按 ID 可读取，但当前 registry 未显示，请核对 Gemini 账号/profile 上下文。"
                else:
                    visibility = " ⚠️ 但当前 cookie/session 的列表校验尚未看到它，请用 gemini_list_scheduled_actions 核对账号上下文。"
                return [
                    TextContent(
                        type="text",
                        text=f"✅ 已创建 Gemini 定时操作: {payload['title']} [{payload['id']}]{label}{visibility}",
                    )
                ]
            return [
                TextContent(
                    type="text",
                    text="⚠️ 创建请求已发送，但未在响应中解析到定时操作 id。请用 gemini_list_scheduled_actions 核对。",
                )
            ]
        except Exception as e:
            logger.error(f"定时操作创建失败: {e}")
            return [TextContent(type="text", text=f"❌ 创建定时操作失败: {str(e)}")]

    @_tool("gemini_delete_scheduled_action", DESTRUCTIVE_REMOTE)
    async def gemini_delete_scheduled_action(
        action_id: str,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """按 id 删除 Gemini Web 定时操作。不会删除由该定时操作产生的历史对话。"""
        clean_id = action_id.strip()
        if not clean_id:
            return [TextContent(type="text", text="❌ action_id 不能为空。")]

        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持定时操作 RPC。")]

        try:
            request_payload = json.dumps([None, [clean_id]], ensure_ascii=False, separators=(",", ":"))
            response = await client._batch_execute(
                [_RawRPCData("Q4Gw3c", request_payload)],
                source_path="/scheduled",
                close_on_error=False,
            )
            bodies = _extract_rpc_bodies(response.text, "Q4Gw3c")
            visible_after_delete = None
            readable_by_id_after_delete = None
            deleted_by_id_after_delete = None
            task_state_after_delete = ""
            task_state_id_after_delete = None
            verification_status = "not_attempted"
            verification_error = ""
            get_task_error = ""
            get_task_diagnostic: dict[str, Any] = {}
            if bodies:
                try:
                    registry_entries, _ = await _fetch_scheduled_registry(client, 400)
                    visible_after_delete = any(item.get("id") == clean_id for item in registry_entries)
                    if visible_after_delete:
                        verification_status = "still_visible_in_registry"
                    elif registry_entries:
                        verification_status = "not_visible_in_nonempty_registry"
                    else:
                        verification_status = "registry_empty_unverified"
                except Exception as e:
                    verification_error = str(e)
                    verification_status = "verification_error"
                try:
                    task_after_delete, get_task_diagnostic = await _fetch_scheduled_task_by_id(client, clean_id, 400)
                    readable_by_id_after_delete = task_after_delete is not None
                    if task_after_delete:
                        task_state_after_delete = str(task_after_delete.get("task_state") or "")
                        task_state_id_after_delete = task_after_delete.get("task_state_id")
                    deleted_by_id_after_delete = task_state_id_after_delete == 6
                    if deleted_by_id_after_delete:
                        verification_status = "deleted_state_by_id"
                    elif readable_by_id_after_delete:
                        if verification_status == "registry_empty_unverified":
                            verification_status = "registry_empty_active_or_unknown_by_id"
                        elif verification_status == "not_visible_in_nonempty_registry":
                            verification_status = "not_visible_active_or_unknown_by_id"
                    elif verification_status == "registry_empty_unverified":
                        verification_status = "registry_empty_not_readable_by_id"
                    elif verification_status == "not_visible_in_nonempty_registry":
                        verification_status = "not_visible_not_readable_by_id"
                except Exception as e:
                    get_task_error = str(e)
            payload = {
                "ok": response.status_code == 200 and bool(bodies),
                "id": clean_id,
                "source_rpc": "Q4Gw3c",
                "visible_after_delete": visible_after_delete,
                "readable_by_id_after_delete": readable_by_id_after_delete,
                "deleted_by_id_after_delete": deleted_by_id_after_delete,
                "task_state_after_delete": task_state_after_delete,
                "task_state_id_after_delete": task_state_id_after_delete,
                "verification_status": verification_status,
                "verification_error": verification_error,
                "get_task_error": get_task_error,
                "get_task_diagnostic": get_task_diagnostic,
            }
            if response_format == "json":
                return _json_response(payload)

            if payload["ok"]:
                if deleted_by_id_after_delete is True:
                    return [TextContent(type="text", text=f"✅ 已删除 Gemini 定时操作: {clean_id}；按 ID 校验状态为 deleted。")]
                if readable_by_id_after_delete is True:
                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"⚠️ 删除 RPC 已被 Gemini 接受: {clean_id}；"
                                f"但按 ID 仍可读取，校验状态: {verification_status}。请在 Gemini UI 中核对。"
                            ),
                        )
                    ]
                if verification_status in {"not_visible_in_nonempty_registry", "not_visible_not_readable_by_id"}:
                    return [TextContent(type="text", text=f"✅ 已删除 Gemini 定时操作: {clean_id}")]
                if verification_status in {"registry_empty_unverified", "registry_empty_not_readable_by_id"}:
                    return [TextContent(type="text", text=f"✅ 删除请求已被 Gemini 接受: {clean_id}；当前 registry 为空，按 ID 校验状态: {verification_status}。")]
                return [TextContent(type="text", text=f"✅ 删除请求已被 Gemini 接受: {clean_id}；校验状态: {verification_status}")]
            return [TextContent(type="text", text=f"⚠️ 删除请求已发送，但响应无法确认: {clean_id}")]
        except Exception as e:
            logger.error(f"定时操作删除失败: {e}")
            return [TextContent(type="text", text=f"❌ 删除定时操作失败: {str(e)}")]

    @_tool("gemini_get_tool_mode_status", READ_ONLY_REMOTE)
    async def gemini_get_tool_mode_status(
        limit: int = 50,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        读取 Gemini Web 工具/模式状态枚举。

        返回 mode_id、available、quota/state 等结构化字段；这些数字枚举来自 Web RPC，
        部分语义尚未稳定命名，因此不会伪装成完整模式 CRUD。
        """
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持工具模式状态 RPC。")]

        try:
            probe = _get_probe("tool_modes", "tool_mode_status")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            body = bodies[0] if bodies else []
            entries = []
            leading_enabled = None
            if isinstance(body, list):
                leading_enabled = body[0] if body and isinstance(body[0], bool) else None
                if len(body) > 1 and isinstance(body[1], list):
                    entries = [_parse_tool_mode_entry(item) for item in body[1]]
            page, page_info = _paginate_items(entries, limit, offset, max_limit=100)

            payload = {
                **page_info,
                "leading_enabled": leading_enabled,
                "items": page,
                "source_rpc": probe["rpcid"],
                "observed": probe["observed"],
                "note": "mode_id semantics are Web-internal and may drift; use this as a read-only availability/status surface.",
            }
            if response_format == "json":
                return _json_response(payload)

            if not page:
                return [TextContent(type="text", text="暂无工具模式状态条目。")]
            lines = [
                "## Gemini 工具/模式状态",
                f"共 {payload['total_count']} 条；当前 offset={payload['offset']} count={payload['count']}；leading_enabled={leading_enabled}",
            ]
            for item in page:
                lines.append(
                    "- mode_id={mode_id}, available={available}, quota={quota}, used={used}, state={state}".format(
                        mode_id=item.get("mode_id"),
                        available=item.get("available"),
                        quota=item.get("quota_value"),
                        used=item.get("used_value"),
                        state=item.get("state"),
                    )
                )
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            lines.append("\n说明: mode_id 是 Gemini Web 内部枚举，语义可能漂移。")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"工具模式状态读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取工具模式状态失败: {str(e)}")]

    @_tool("gemini_list_models", READS_PRIVATE_REMOTE)
    async def gemini_list_models() -> list[TextContent]:
        """列出所有可用模型及其说明"""
        aliases = """🤖 MCP 模型别名:

1. flash-lite / lite → 3.1 Flash-Lite
   - 网页端极速模型

2. flash / fast → gemini-3-flash
   - 网页端 3.5 Flash；fast 保留为兼容别名

3. pro → gemini-3-pro
   - 网页端 3.1 Pro，是否可用取决于当前账户

4. thinking → gemini-3-flash-thinking
   - 旧兼容别名；新网页思考等级请用 thinking_level=standard/extended

媒体规则:
- 图像首轮生成始终使用 Nano Banana 2
- 音乐: flash 系列 → Lyria 3, pro → Lyria 3 Pro

---

运行时模型:
"""
        try:
            client = get_gemini_client()
            await initialize_client()
            models = client.list_models() if hasattr(client, "list_models") else None
        except Exception as e:
            logger.warning(f"运行时模型发现失败: {e}")
            models = None

        if not models:
            return [
                TextContent(
                    type="text",
                    text=aliases + "- 暂无运行时模型注册表；请确认 Cookie 和账户状态后重试。",
                )
            ]

        model_lines = [aliases]
        for model in models:
            display_name = getattr(model, "display_name", "") or "Unnamed"
            model_name = getattr(model, "model_name", "") or "unknown"
            available = "可用" if getattr(model, "is_available", True) else "不可用"
            description = getattr(model, "description", "") or "无描述"
            model_lines.append(f"- {display_name}: {model_name} ({available})\n  {description}")
        return [TextContent(type="text", text="\n".join(model_lines))]

    @_tool("gemini_manage_gems", DESTRUCTIVE_REMOTE)
    async def gemini_manage_gems(
        action: Literal["list", "create", "update", "delete"],
        gem_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> list[TextContent]:
        """
        管理 Gemini Gems（自定义 AI 助手）。
        
        参数:
        - action: list, create, update, delete
        - gem_id: 需要时指定 Gem ID
        - name: 创建/更新时 Gem 名称
        - description: 创建/更新时描述
        - instructions: 创建/更新时系统指令
        """
        client = get_gemini_client()
        await initialize_client()

        try:
            if action == "list":
                gems = await client.fetch_gems()
                if not gems:
                    return [TextContent(type="text", text="暂无保存的 Gems。")]

                gem_list = ["## 💎 Gems 列表"]
                for i, gem in enumerate(_iter_gem_values(gems), 1):
                    gem_name = getattr(gem, "name", "Untitled")
                    gem_id_val = getattr(gem, "id", "")
                    gem_desc = getattr(gem, "description", "")[:30]
                    gem_list.append(f"{i}. {gem_name} (ID: {gem_id_val})\n   {gem_desc}")
                
                return [TextContent(type="text", text="\n".join(gem_list))]

            elif action == "create":
                if not name:
                    return [TextContent(type="text", text="❌ 创建 Gem 需要提供名称。")]
                
                gem = await client.create_gem(
                    name=name,
                    prompt=instructions or "",
                    description=description,
                )
                gem_id_val = getattr(gem, "id", "")
                return [TextContent(
                    type="text",
                    text=f"✅ Gem 创建成功！\nID: {gem_id_val}\n名称: {name}"
                )]

            elif action == "update":
                if not gem_id:
                    return [TextContent(type="text", text="❌ 更新 Gem 需要提供 gem_id。")]

                existing_gem = None
                if name is None or instructions is None or description is None:
                    gems = await client.fetch_gems()
                    existing_gem = _find_gem_by_id(gems, gem_id)
                    if existing_gem is None:
                        return [
                            TextContent(
                                type="text",
                                text="❌ 局部更新 Gem 前需要读取现有 Gem，但未找到该 gem_id。请提供完整 name、instructions 和 description 后重试。",
                            )
                        ]

                missing_fields: list[str] = []
                if name is None:
                    found, update_name = _gem_field(existing_gem, "name")
                    if not found:
                        missing_fields.append("name")
                else:
                    update_name = name

                if instructions is None:
                    found, update_prompt = _gem_field(existing_gem, "prompt", "instructions")
                    if not found:
                        missing_fields.append("instructions")
                else:
                    update_prompt = instructions

                if description is None:
                    _found, update_description = _gem_field(existing_gem, "description")
                else:
                    update_description = description

                if missing_fields:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ 局部更新 Gem 缺少现有字段: {', '.join(missing_fields)}。请显式提供这些字段后重试。",
                        )
                    ]

                await client.update_gem(
                    gem=gem_id,
                    name=update_name,
                    prompt=update_prompt,
                    description=update_description,
                )
                return [TextContent(type="text", text=f"✅ Gem {gem_id} 更新成功。")]

            elif action == "delete":
                if not gem_id:
                    return [TextContent(type="text", text="❌ 删除 Gem 需要提供 gem_id。")]
                
                await client.delete_gem(gem_id)
                return [TextContent(type="text", text=f"✅ Gem {gem_id} 删除成功。")]

            return [TextContent(type="text", text="❌ 无效的 action。")]

        except Exception as e:
            logger.error(f"Gem 操作失败: {e}")
            return [TextContent(type="text", text=f"❌ 失败: {str(e)}")]
