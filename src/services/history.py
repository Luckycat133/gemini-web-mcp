"""Shared history helpers used by full and compact MCP adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..infrastructure.rpc_contracts import get_contract


def format_timestamp(timestamp: object) -> str:
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def conversation_history_payload(
    filter_payload: list[Any],
    page_size: int,
    next_page_token: str | None = None,
) -> str:
    return get_contract("history.page").build_payload(
        filter_payload=filter_payload,
        page_size=page_size,
        next_page_token=next_page_token,
    )


def truncate(text: object, max_chars: int) -> str:
    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]"


def clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        result: int = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(result, maximum))


def paginate_items(
    items: list[Any],
    limit: int,
    offset: int,
    max_limit: int = 100,
) -> tuple[list[Any], dict[str, Any]]:
    safe_limit = clamp_int(limit, default=max_limit, minimum=1, maximum=max_limit)
    safe_offset = clamp_int(offset, default=0, minimum=0, maximum=max(len(items), 0))
    page = items[safe_offset : safe_offset + safe_limit]
    has_more = safe_offset + len(page) < len(items)
    return page, {
        "total_count": len(items),
        "count": len(page),
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
        "next_offset": safe_offset + len(page) if has_more else None,
    }


def get_attr(item: object, name: str, default: object = "") -> object:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def get_chat_id(chat: object) -> str:
    return str(get_attr(chat, "cid", "") or get_attr(chat, "id", "") or "")


def get_chat_title(chat: object) -> str:
    return str(get_attr(chat, "title", "") or "Untitled")


def chat_to_dict(chat: object) -> dict[str, Any]:
    timestamp = get_attr(chat, "timestamp", None)
    return {
        "id": get_chat_id(chat),
        "title": get_chat_title(chat),
        "is_pinned": bool(get_attr(chat, "is_pinned", False)),
        "timestamp": timestamp,
        "time": format_timestamp(timestamp),
    }


def turn_to_dict(turn: object, max_chars: int) -> dict[str, str]:
    return {
        "role": str(get_attr(turn, "role", "unknown") or "unknown"),
        "text": truncate(get_attr(turn, "text", ""), max_chars),
    }


async def read_chat_turns(
    client: object,
    chat_id: str,
    limit: int,
    max_chars: int,
) -> tuple[object, list[dict[str, str]]]:
    if not hasattr(client, "read_chat"):
        raise RuntimeError("当前 gemini-webapi 不支持 read_chat。")
    history = await client.read_chat(chat_id, limit=limit)  # type: ignore[attr-defined]
    turns_raw = get_attr(history, "turns", []) if history else []
    turns: list[Any] = turns_raw if isinstance(turns_raw, list) else []
    return history, [turn_to_dict(turn, max_chars) for turn in turns[:limit]]


def turn_matches_query(turn: dict[str, str], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return False
    return needle in turn.get("role", "").lower() or needle in turn.get("text", "").lower()


def chat_export_payload(
    chat_id: str,
    history: object,
    turns: list[dict[str, str]],
    metadata: dict[str, Any] | None,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": str(get_attr(history, "cid", "") or chat_id),
        "count": len(turns),
        "limit": limit,
        "max_chars_per_turn": max_chars,
        "turns": turns,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def format_chat_export_markdown(payload: dict[str, Any]) -> str:
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
    for index, turn in enumerate(payload["turns"], 1):
        lines.extend(["", f"### {index}. {turn['role']}", turn["text"]])
    return "\n".join(lines)


# Compatibility aliases intentionally mirror the previous private helper names.
_format_timestamp = format_timestamp
_conversation_history_payload = conversation_history_payload
_truncate = truncate
_clamp_int = clamp_int
_paginate_items = paginate_items
_get_attr = get_attr
_get_chat_id = get_chat_id
_get_chat_title = get_chat_title
_chat_to_dict = chat_to_dict
_turn_to_dict = turn_to_dict
_read_chat_turns = read_chat_turns
_turn_matches_query = turn_matches_query
_chat_export_payload = chat_export_payload
_format_chat_export_markdown = format_chat_export_markdown
