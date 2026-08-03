"""
会话和 Gem 管理 MCP 工具
"""

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from ..adapters.mcp_sdk import MCPServer, TextContent
from typing import Any, Callable, Literal, Optional, TypeVar
import logging

from ..client_wrapper import (
    get_cookie_status,
    get_gemini_client,
    initialize_client,
    list_browser_cookie_profiles,
)
from ..infrastructure.rpc_contracts import (
    RawRPCData as _RawRPCData,
    get_contract,
)
from ..infrastructure.rpc_parsers import (
    extract_rpc_bodies as extract_registered_rpc_bodies,
    parse_conversation_metadata as parse_registered_conversation_metadata,
    parse_library_capability as parse_registered_library_capability,
    parse_native_notebook as parse_registered_native_notebook,
    parse_notebook_category as parse_registered_notebook_category,
    parse_public_link_entry as parse_registered_public_link_entry,
    parse_remy_goal_entry as parse_registered_remy_goal,
    parse_scheduled_action_create_body as parse_registered_scheduled_create,
    parse_scheduled_action_task_entry as parse_registered_scheduled_task,
    scheduled_task_state as registered_scheduled_task_state,
    parse_tool_mode_entry as parse_registered_tool_mode,
    parse_usage_entry as parse_registered_usage,
    summarize_rpc_response as summarize_registered_rpc_response,
)
from ..services.compatibility import sanitized_error_code, sanitized_error_type
from ..services.gems import (
    create_gem as create_gem_service,
    delete_gem as delete_gem_service,
    iter_gem_values as registered_iter_gems,
    update_gem as update_gem_service,
)
from ..services.history import conversation_history_payload as registered_conversation_history_payload
from ..services.manifest import (
    _current_enabled_manifest_groups,
    format_tool_manifest_markdown as format_registered_tool_manifest,
    format_web_capabilities_markdown as format_registered_web_capabilities,
    resolve_manage_tool_names,
    tool_manifest_payload as registered_tool_manifest_payload,
    web_capabilities_payload as registered_web_capabilities_payload,
)
from ..services.notebooks import (
    move_chat_to_notebook as move_chat_to_notebook_service,
    move_chat_to_notebook_payload as registered_move_chat_payload,
    native_notebooks_payload as registered_notebooks_payload,
    notebook_chats_payload as registered_notebook_chats_payload,
)
from ..services.scheduled import (
    create_daily_action as create_daily_action_service,
    delete_action as delete_action_service,
    scheduled_daily_payload as registered_scheduled_daily_payload,
)
from .annotations import (
    DESTRUCTIVE_REMOTE,
    MUTATES_REMOTE,
    READ_ONLY_LOCAL,
    READ_ONLY_REMOTE,
    READS_PRIVATE_REMOTE,
)
from .manifest_data import WEB_FEATURE_PROBES

# TypeVar for the @_tool decorator: preserves the wrapped function's declared
# signature so in-process callers retain the annotated return type.
_F = TypeVar("_F", bound=Callable[..., Any])

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
        number: int = int(value)  # type: ignore[call-overload]
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

    summary_raw = status.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    rpc_raw = status.get("rpc")
    rpc = rpc_raw if isinstance(rpc_raw, dict) else {}
    rpc_status: dict[str, dict[str, Any]] = {}
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
    turns_raw = _get_attr(history, "turns", []) if history else []
    turns: list[Any] = turns_raw if isinstance(turns_raw, list) else []
    return history, [_turn_to_dict(turn, max_chars) for turn in turns[:limit]]


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
    metadata_raw = payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
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
    contract = get_contract("scheduled.get")
    response = await client._batch_execute(
        [_RawRPCData(contract.rpc_id, contract.build_payload(action_id=action_id))],
        source_path=contract.source_path,
        close_on_error=False,
    )
    bodies = _extract_rpc_bodies(response.text, contract.rpc_id)
    body = bodies[0] if bodies else []
    raw_entry = _get_scheduled_task_entry_from_body(body)
    entry = _parse_scheduled_action_task_entry(raw_entry, max_chars) if raw_entry is not None else None
    matched_task = bool(entry and entry.get("id") == action_id)
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "observed": contract.observed,
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





async def _fetch_native_notebooks(client, locale: str = "zh-CN") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = get_contract("notebooks.list")
    response = await client._batch_execute(
        [_RawRPCData(contract.rpc_id, _native_notebooks_payload(locale))],
        source_path=contract.source_path,
        close_on_error=False,
    )
    bodies = _extract_rpc_bodies(response.text, contract.rpc_id)
    body = bodies[0] if bodies else []
    raw_entries = body[2] if isinstance(body, list) and len(body) > 2 and isinstance(body[2], list) else []
    raw_categories = body[3] if isinstance(body, list) and len(body) > 3 and isinstance(body[3], list) else []
    notebooks = [_parse_native_notebook(item) for item in raw_entries]
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "observed": contract.observed,
        "status_code": getattr(response, "status_code", None),
        "response_length": len(getattr(response, "text", "") or ""),
        "body_present": bool(bodies),
        "raw_entry_count": len(raw_entries),
        "categories": [_parse_notebook_category(item) for item in raw_categories],
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
    }
    return notebooks, diagnostic





async def _fetch_conversation_metadata_source(
    client,
    source_name: str,
    filter_payload: list[Any],
    max_items: int,
    page_size: int = 100,
    max_pages: int = 50,
) -> dict[str, Any]:
    contract = get_contract("history.page")
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
            [_RawRPCData(contract.rpc_id, _conversation_history_payload(filter_payload, safe_page_size, next_page_token))],
            source_path=contract.source_path,
            close_on_error=False,
        )
        response_text = getattr(response, "text", "") or ""
        response_length += len(response_text)
        bodies = _extract_rpc_bodies(response_text, contract.rpc_id)
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
        "rpcid": contract.rpc_id,
        "filter_payload": filter_payload,
        "items": items,
        "diagnostic": {
            "source_rpc": contract.rpc_id,
            "contract_key": contract.key,
            "observed": contract.observed,
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





async def _fetch_remy_goal_conversation_refs(
    client,
    max_items: int,
    page_size: int = 100,
    max_pages: int = 50,
) -> dict[str, Any]:
    contract = get_contract("history.remy_goals")
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
        response = await client._batch_execute(
            [_RawRPCData(contract.rpc_id, contract.build_payload(page_size=safe_page_size, next_page_token=next_page_token))],
            source_path=contract.source_path,
            close_on_error=False,
        )
        response_text = getattr(response, "text", "") or ""
        response_length += len(response_text)
        bodies = _extract_rpc_bodies(response_text, contract.rpc_id)
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
        "rpcid": contract.rpc_id,
        "items": items,
        "diagnostic": {
            "source_rpc": contract.rpc_id,
            "contract_key": contract.key,
            "observed": contract.observed,
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





async def _fetch_notebook_chats(
    client,
    notebook_id: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = get_contract("notebooks.chats")
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
            [_RawRPCData(contract.rpc_id, _notebook_chats_payload(notebook_id, page_size, next_page_token))],
            source_path=contract.source_path.format(notebook_slug=notebook_id.rsplit("/", 1)[-1]),
            close_on_error=False,
        )
        response_length += len(getattr(response, "text", "") or "")
        page_count += 1
        bodies = _extract_rpc_bodies(response.text, contract.rpc_id)
        body = bodies[0] if bodies else []
        raw_entries = body[2] if isinstance(body, list) and len(body) > 2 and isinstance(body[2], list) else []
        items.extend(_parse_conversation_metadata(item) for item in raw_entries)
        next_page_token = body[1] if isinstance(body, list) and len(body) > 1 and isinstance(body[1], str) else None
        if not next_page_token or not raw_entries:
            break

    page = items[safe_offset : safe_offset + safe_limit]
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "observed": contract.observed,
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
    contract = get_contract("history.page")
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
    pinned_diag: dict[str, Any] = next((source["diagnostic"] for source in sources if source["name"] == "ui_pinned"), {})
    recent_diag: dict[str, Any] = next((source["diagnostic"] for source in sources if source["name"] == "ui_recent"), {})
    return combined, {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "observed": contract.observed,
        "target_count_per_bucket": safe_target,
        "pinned": pinned_diag,
        "recent": recent_diag,
        "has_remote_more": bool(
            pinned_diag.get("next_page_token_present") or recent_diag.get("next_page_token_present")
        ),
    }








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
                            delete_result = await delete_action_service(
                                client,
                                action_id=item["id"],
                                max_chars=300,
                                fetch_registry=_fetch_scheduled_registry,
                                fetch_by_id=_fetch_scheduled_task_by_id,
                                extract_bodies=_extract_rpc_bodies,
                            )
                            verification_status = delete_result["verification_status"]
                            deleted = bool(
                                delete_result["ok"]
                                and (
                                    delete_result.get("deleted_by_id_after_delete") is True
                                    or delete_result.get("visible_after_delete") is not True
                                )
                            )
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








# Central contract/parser implementations are rebound at the compatibility
# boundary so existing tests and in-process imports retain their historical
# private names while both MCP adapters execute the same pure code.
_summarize_probe_response = summarize_registered_rpc_response  # type: ignore[assignment]
_extract_rpc_bodies = extract_registered_rpc_bodies  # type: ignore[assignment]
_parse_public_link_entry = parse_registered_public_link_entry
_parse_usage_entry = parse_registered_usage
_parse_library_capability = parse_registered_library_capability
_parse_native_notebook = parse_registered_native_notebook
_parse_notebook_category = parse_registered_notebook_category
_parse_conversation_metadata = parse_registered_conversation_metadata
_parse_remy_goal_entry = parse_registered_remy_goal
_parse_scheduled_action_create_body = parse_registered_scheduled_create
_scheduled_task_state = registered_scheduled_task_state
_parse_scheduled_action_task_entry = parse_registered_scheduled_task
_parse_tool_mode_entry = parse_registered_tool_mode
_native_notebooks_payload = registered_notebooks_payload
_conversation_history_payload = registered_conversation_history_payload
_notebook_chats_payload = registered_notebook_chats_payload
_move_chat_to_notebook_payload = registered_move_chat_payload
_scheduled_daily_payload = registered_scheduled_daily_payload
_tool_manifest_payload = registered_tool_manifest_payload
_format_tool_manifest_markdown = format_registered_tool_manifest
_web_capabilities_payload = registered_web_capabilities_payload
_format_web_capabilities_markdown = format_registered_web_capabilities
_iter_gem_values = registered_iter_gems


def register_manage_tools(mcp: MCPServer, layers: list[str] | set[str] | tuple[str, ...] | None = None):
    enabled_tool_names = resolve_manage_tool_names(layers)

    def _tool(tool_name: str, annotations) -> Callable[[_F], _F]:
        def decorator(func: _F) -> _F:
            if tool_name in enabled_tool_names:
                # Register with MCPServer for external MCP dispatch, but keep the
                # original typed function for in-process calls so callers retain
                # the declared return type (avoids cascading Any returns).
                mcp.tool(annotations=annotations)(func)
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
                            "rpcid": get_contract("notebooks.chats").rpc_id,
                            "items": notebook_items,
                            "diagnostic": {
                                "source_rpc": get_contract("notebooks.chats").rpc_id,
                                "observed": get_contract("notebooks.chats").observed,
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
                "source_rpc": get_contract("history.page").rpc_id,
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
            matches: list[dict[str, Any]] = []
            lowered = needle.lower()

            for chat in page:
                item = chat if isinstance(chat, dict) else _chat_to_dict(chat)
                fields: list[str] = []
                snippets: list[dict[str, Any]] = []
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
                fields_str = ", ".join(match["matched_fields"])
                time_text = f" · {match['time']}" if match.get("time") else ""
                lines.append(f"{idx}. {match['title']} (ID: {match['id']}) · fields={fields_str}{time_text}")
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
                        "error_code": sanitized_error_code(e),
                        "error_type": sanitized_error_type(e),
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
                if item.get("error_code"):
                    suffix += f", error={item['error_code']} ({item.get('error_type', 'Exception')})"
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

        results: list[dict[str, Any]] = []
        try:
            for name in probe_names:
                probe = _get_probe("usage", name)
                response = await _execute_observed_rpc(client, probe)
                bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
                entries: list[dict[str, Any]] = []
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
                payload: dict[str, Any] = {
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
                "source_rpc": get_contract("notebooks.chats").rpc_id,
                "observed": get_contract("notebooks.chats").observed,
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
            payload = await move_chat_to_notebook_service(
                client,
                chat_id=clean_chat_id,
                notebook_id=notebook_id,
                notebook_title=notebook_title,
                locale=locale,
                fetch_notebooks=_fetch_native_notebooks,
                fetch_chats=_fetch_notebook_chats,
                extract_bodies=_extract_rpc_bodies,
            )
            notebook = payload.get("notebook")
            if not notebook:
                available = payload.get("available_titles", [])
                if response_format == "json":
                    return _json_response(payload)
                return [TextContent(type="text", text=f"未找到匹配的 Gemini 原生笔记本。可用标题: {', '.join(available)}")]
            if response_format == "json":
                return _json_response(payload)

            if payload["ok"] and payload["verified_in_target_notebook"]:
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
            payload = await create_daily_action_service(
                client,
                title=clean_title,
                instructions=clean_instructions,
                hour=hour,
                timezone_name=clean_timezone,
                locale=clean_locale,
                max_chars=400,
                fetch_registry=_fetch_scheduled_registry,
                fetch_by_id=_fetch_scheduled_task_by_id,
                extract_bodies=_extract_rpc_bodies,
                parse_create=_parse_scheduled_action_create_body,
                payload_builder=_scheduled_daily_payload,
            )
            if response_format == "json":
                return _json_response(payload)

            if payload["ok"]:
                label = f" ({payload['schedule_label']})" if payload.get("schedule_label") else ""
                if payload["visible_in_registry"]:
                    visibility = ""
                elif payload["readable_by_id_after_create"]:
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
            payload = await delete_action_service(
                client,
                action_id=clean_id,
                max_chars=400,
                fetch_registry=_fetch_scheduled_registry,
                fetch_by_id=_fetch_scheduled_task_by_id,
                extract_bodies=_extract_rpc_bodies,
            )
            if response_format == "json":
                return _json_response(payload)

            if payload["ok"]:
                if payload["deleted_by_id_after_delete"] is True:
                    return [TextContent(type="text", text=f"✅ 已删除 Gemini 定时操作: {clean_id}；按 ID 校验状态为 deleted。")]
                if payload["readable_by_id_after_delete"] is True:
                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"⚠️ 删除 RPC 已被 Gemini 接受: {clean_id}；"
                                f"但按 ID 仍可读取，校验状态: {payload['verification_status']}。请在 Gemini UI 中核对。"
                            ),
                        )
                    ]
                if payload["verification_status"] in {"not_visible_in_nonempty_registry", "not_visible_not_readable_by_id"}:
                    return [TextContent(type="text", text=f"✅ 已删除 Gemini 定时操作: {clean_id}")]
                if payload["verification_status"] in {"registry_empty_unverified", "registry_empty_not_readable_by_id"}:
                    return [TextContent(type="text", text=f"✅ 删除请求已被 Gemini 接受: {clean_id}；当前 registry 为空，按 ID 校验状态: {payload['verification_status']}。")]
                return [TextContent(type="text", text=f"✅ 删除请求已被 Gemini 接受: {clean_id}；校验状态: {payload['verification_status']}")]
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
                payload = await create_gem_service(
                    client,
                    name=name,
                    description=description,
                    instructions=instructions or "",
                )
                return [TextContent(
                    type="text",
                    text=(
                        f"✅ Gem 创建成功！\nID: {payload['id']}\n名称: {name}\n"
                        f"读回校验: {payload['verification_status']}"
                    ),
                )]

            elif action == "update":
                if not gem_id:
                    return [TextContent(type="text", text="❌ 更新 Gem 需要提供 gem_id。")]
                payload = await update_gem_service(
                    client,
                    gem_id=gem_id,
                    name=name,
                    description=description,
                    instructions=instructions,
                )
                if payload.get("verification_status") == "target_not_found":
                    return [
                        TextContent(
                            type="text",
                            text="❌ 局部更新 Gem 前需要读取现有 Gem，但未找到该 gem_id。请提供完整 name、instructions 和 description 后重试。",
                        )
                    ]
                if not payload.get("ok"):
                    missing_fields = payload.get("missing_fields", [])
                    return [TextContent(type="text", text=f"❌ 局部更新 Gem 缺少现有字段: {', '.join(missing_fields)}。请显式提供这些字段后重试。")]
                return [TextContent(type="text", text=f"✅ Gem {gem_id} 更新成功。读回校验: {payload['verification_status']}")]

            elif action == "delete":
                if not gem_id:
                    return [TextContent(type="text", text="❌ 删除 Gem 需要提供 gem_id。")]
                payload = await delete_gem_service(client, gem_id=gem_id)
                return [TextContent(type="text", text=f"✅ Gem {gem_id} 删除成功。读回校验: {payload['verification_status']}")]

            return [TextContent(type="text", text="❌ 无效的 action。")]

        except Exception as e:
            logger.error(f"Gem 操作失败: {e}")
            return [TextContent(type="text", text=f"❌ 失败: {str(e)}")]
