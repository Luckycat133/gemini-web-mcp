"""Shared history helpers used by full and compact MCP adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from ..domain import DomainErrorCode, DomainResult, OperationState
from ..infrastructure.rpc_contracts import RawRPCData, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope


_DELETION_HISTORY_FILTERS: tuple[tuple[object, ...], ...] = (
    (False, None, True),
    (True, None, True),
)


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


def normalize_chat_item(chat: object) -> dict[str, Any]:
    """Normalize object/mapping history records while preserving observed fields."""
    normalized = dict(chat) if isinstance(chat, dict) and "id" in chat else {}
    canonical = chat_to_dict(chat)
    if not canonical["time"] and normalized.get("time"):
        canonical["time"] = str(normalized["time"])
    normalized.update(canonical)
    return normalized


def list_chats_result(
    chats: Sequence[object],
    limit: int,
    offset: int,
    *,
    diagnostic: dict[str, Any] | None = None,
    max_limit: int = 50,
) -> DomainResult[dict[str, Any]]:
    """Build the shared typed list payload used by primary and compact adapters."""
    items = [normalize_chat_item(chat) for chat in chats]
    page, pagination = paginate_items(items, limit, offset, max_limit=max_limit)
    observed = dict(
        diagnostic
        or {
            "source": "client_cache",
            "fetched_count": len(items),
            "has_remote_more": False,
        }
    )
    if observed.get("has_remote_more") and not pagination["has_more"]:
        pagination["has_more"] = True
        pagination["next_offset"] = pagination["offset"] + pagination["count"]
    payload = {**pagination, "items": page, "diagnostic": observed}
    return DomainResult.success(
        payload,
        verification_status="observed",
        details={"source": observed.get("source") or observed.get("source_rpc") or "unknown"},
    )


def turn_to_dict(turn: object, max_chars: int) -> dict[str, str]:
    return {
        "role": str(get_attr(turn, "role", "unknown") or "unknown"),
        "text": truncate(get_attr(turn, "text", ""), max_chars),
    }


async def read_chat_result(
    client: object,
    chat_id: str,
    limit: int,
    max_chars: int,
    *,
    max_limit: int = 100,
) -> DomainResult[dict[str, Any]]:
    """Read and normalize one history record into a stable typed payload."""
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            "chat_id is required.",
            suggested_action="Provide the chat ID returned by a history listing.",
            verification_status="input_rejected",
        )
    if not hasattr(client, "read_chat"):
        return DomainResult.failure(
            DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "The current Gemini Web client does not support read_chat.",
            suggested_action="Upgrade gemini-webapi or use a client profile that supports history reads.",
            verification_status="capability_unavailable",
        )

    safe_limit = clamp_int(limit, default=10, minimum=1, maximum=max_limit)
    safe_chars = clamp_int(max_chars, default=4000, minimum=1, maximum=20000)
    history = await client.read_chat(normalized_chat_id, limit=safe_limit)  # type: ignore[attr-defined]
    turns_raw = get_attr(history, "turns", []) if history else []
    turns = turns_raw if isinstance(turns_raw, list) else []
    items = [turn_to_dict(turn, safe_chars) for turn in turns[:safe_limit]]
    payload = {
        "chat_id": str(get_attr(history, "cid", normalized_chat_id) or normalized_chat_id),
        "count": len(items),
        "limit": safe_limit,
        "turns": items,
    }
    return DomainResult.success(
        payload,
        verification_status="observed",
        details={"found": history is not None, "source": "client_read_chat"},
    )


async def search_chats_result(
    client: object,
    chats: Sequence[object],
    query: str,
    limit: int,
    offset: int,
    *,
    scan_turns: bool = False,
    turns_per_chat: int = 20,
    max_chars_per_turn: int = 1000,
    diagnostic: dict[str, Any] | None = None,
    max_limit: int = 50,
) -> DomainResult[dict[str, Any]]:
    """Search one source page and expose the same typed contract to both adapters."""
    needle = str(query or "").strip()
    if not needle:
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            "query is required.",
            suggested_action="Provide a title, chat ID, role, or turn-text fragment to search for.",
            verification_status="input_rejected",
        )
    if scan_turns and not hasattr(client, "read_chat"):
        return DomainResult.failure(
            DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "The current Gemini Web client does not support read_chat for turn scanning.",
            suggested_action="Disable scan_turns or upgrade gemini-webapi.",
            verification_status="capability_unavailable",
        )

    items = [normalize_chat_item(chat) for chat in chats]
    page, pagination = paginate_items(items, limit, offset, max_limit=max_limit)
    observed = dict(
        diagnostic
        or {
            "source": "client_cache",
            "fetched_count": len(items),
            "has_remote_more": False,
        }
    )
    if observed.get("has_remote_more") and not pagination["has_more"]:
        pagination["has_more"] = True
        pagination["next_offset"] = pagination["offset"] + pagination["count"]

    safe_turn_limit = clamp_int(turns_per_chat, default=20, minimum=1, maximum=50)
    safe_chars = clamp_int(max_chars_per_turn, default=1000, minimum=100, maximum=4000)
    lowered = needle.lower()
    matches: list[dict[str, Any]] = []

    for item in page:
        fields: list[str] = []
        snippets: list[dict[str, Any]] = []
        if lowered in item["title"].lower():
            fields.append("title")
        if item["id"] and lowered in item["id"].lower():
            fields.append("id")

        if scan_turns and item["id"]:
            try:
                _history, turns = await read_chat_turns(
                    client,
                    item["id"],
                    safe_turn_limit,
                    safe_chars,
                )
            except Exception as error:
                snippets.append({"error": f"{type(error).__name__}: {error}"})
                turns = []
            for index, turn in enumerate(turns, 1):
                if turn_matches_query(turn, needle):
                    fields.append("turn")
                    snippets.append(
                        {
                            "turn_index": index,
                            "role": turn["role"],
                            "text": truncate(turn["text"], safe_chars),
                        }
                    )

        if fields:
            match = {**item, "matched_fields": sorted(set(fields))}
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
        "diagnostic": observed,
        "note": "正文搜索只会在 scan_turns=true 时读取当前页聊天内容。",
    }
    return DomainResult.success(
        payload,
        verification_status="observed",
        details={"source": observed.get("source") or observed.get("source_rpc") or "unknown"},
    )


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


async def export_chat_result(
    client: object,
    chat_id: str,
    limit: int,
    max_chars_per_turn: int,
    *,
    include_metadata: bool = True,
    metadata_loader: Callable[[], Awaitable[Sequence[object]]] | None = None,
    max_limit: int = 200,
) -> DomainResult[dict[str, Any]]:
    """Read one chat export and normalize its optional metadata once for both adapters."""
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            "chat_id is required.",
            suggested_action="Provide the chat ID returned by a history listing or search.",
            verification_status="input_rejected",
        )
    if not hasattr(client, "read_chat"):
        return DomainResult.failure(
            DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "The current Gemini Web client does not support read_chat.",
            suggested_action="Upgrade gemini-webapi or use a client profile that supports history reads.",
            verification_status="capability_unavailable",
        )

    safe_limit = clamp_int(limit, default=100, minimum=1, maximum=max_limit)
    safe_chars = clamp_int(max_chars_per_turn, default=20000, minimum=200, maximum=20000)
    history, turns = await read_chat_turns(client, normalized_chat_id, safe_limit, safe_chars)
    if not history:
        payload = chat_export_payload(
            normalized_chat_id,
            history,
            turns,
            None,
            safe_limit,
            safe_chars,
        )
        return DomainResult.success(
            payload,
            verification_status="observed",
            details={"found": False, "source": "client_read_chat"},
        )

    metadata: dict[str, Any] | None = None
    if include_metadata:
        metadata = {"id": normalized_chat_id}
        if metadata_loader is not None:
            try:
                for chat in await metadata_loader():
                    item = normalize_chat_item(chat)
                    if item["id"] == normalized_chat_id:
                        metadata = item
                        break
            except Exception as error:
                metadata["metadata_warning"] = f"{type(error).__name__}: {error}"

    payload = chat_export_payload(
        normalized_chat_id,
        history,
        turns,
        metadata,
        safe_limit,
        safe_chars,
    )
    return DomainResult.success(
        payload,
        verification_status="observed",
        details={"found": True, "source": "client_read_chat"},
    )


async def delete_chat_result(
    client: object,
    chat_id: str,
) -> DomainResult[dict[str, Any]]:
    """Request deletion and distinguish accepted work from positive read-back evidence."""
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            "chat_id is required.",
            suggested_action="Provide the chat ID returned by a history listing or search.",
            verification_status="input_rejected",
        )
    if not hasattr(client, "delete_chat"):
        return DomainResult.failure(
            DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "The current Gemini Web client does not support delete_chat.",
            suggested_action="Upgrade gemini-webapi or use a client profile that supports chat deletion.",
            verification_status="capability_unavailable",
        )

    await client.delete_chat(normalized_chat_id)  # type: ignore[attr-defined]
    if not hasattr(client, "_batch_execute"):
        payload = {
            "chat_id": normalized_chat_id,
            "delete_requested": True,
            "deleted": None,
            "verification": {"status": "not_available", "source": None},
        }
        return DomainResult.success(
            payload,
            operation_state=OperationState.ACCEPTED,
            verification_status="not_available",
            details={"source": "client_delete_chat"},
        )

    try:
        absent, observation_details = await observe_chat_absence(client, normalized_chat_id)
    except Exception as error:
        payload = {
            "chat_id": normalized_chat_id,
            "delete_requested": True,
            "deleted": None,
            "verification": {
                "status": "read_back_error",
                "source": "history.page",
            },
        }
        return DomainResult.failure(
            DomainErrorCode.VERIFICATION_FAILED,
            "The chat deletion request returned, but read-back verification failed.",
            data=payload,
            retryable=True,
            suggested_action="Read the chat again before retrying deletion.",
            verification_status="read_back_error",
            details={"source": "history.page", "error_type": type(error).__name__},
        )

    if absent is None:
        payload = {
            "chat_id": normalized_chat_id,
            "delete_requested": True,
            "deleted": None,
            "verification": {"status": "not_available", "source": "history.page"},
        }
        return DomainResult.success(
            payload,
            operation_state=OperationState.ACCEPTED,
            verification_status="not_available",
            details=observation_details,
        )

    if absent:
        payload = {
            "chat_id": normalized_chat_id,
            "delete_requested": True,
            "deleted": True,
            "verification": {
                "status": "verified_absent",
                "source": "history.page",
            },
        }
        return DomainResult.success(
            payload,
            verification_status="verified_absent",
            details=observation_details,
        )

    payload = {
        "chat_id": normalized_chat_id,
        "delete_requested": True,
        "deleted": False,
        "verification": {
            "status": "still_present",
            "source": "history.page",
        },
    }
    return DomainResult.failure(
        DomainErrorCode.VERIFICATION_FAILED,
        "The chat is still readable after the deletion request.",
        data=payload,
        retryable=True,
        suggested_action="Retry later or inspect the chat before issuing another deletion request.",
        verification_status="still_present",
        details=observation_details,
    )


async def observe_chat_absence(
    client: object,
    chat_id: str,
    *,
    page_size: int = 100,
    max_pages_per_source: int = 50,
) -> tuple[bool | None, dict[str, Any]]:
    """Check the canonical recent/pinned metadata buckets without treating ``read_chat(None)`` as absence."""
    if not hasattr(client, "_batch_execute"):
        return None, {"source": None, "complete": False, "reason": "batch_execute_unavailable"}

    contract = get_contract("history.page")
    safe_page_size = clamp_int(page_size, default=100, minimum=1, maximum=100)
    safe_max_pages = clamp_int(max_pages_per_source, default=50, minimum=1, maximum=200)
    page_count = 0

    for filter_values in _DELETION_HISTORY_FILTERS:
        next_page_token: str | None = None
        for _page_index in range(safe_max_pages):
            response = await client._batch_execute(  # type: ignore[attr-defined]
                [
                    RawRPCData(
                        contract.rpc_id,
                        contract.build_payload(
                            filter_payload=list(filter_values),
                            page_size=safe_page_size,
                            next_page_token=next_page_token,
                        ),
                    )
                ],
                source_path=contract.source_path,
                close_on_error=False,
            )
            response_text = str(getattr(response, "text", "") or "")
            envelope = parse_rpc_envelope(response_text, contract.rpc_id)
            if not envelope.parsed or envelope.reject_code is not None or not envelope.bodies:
                raise RuntimeError("History metadata read-back did not return a usable RPC envelope.")

            body = envelope.bodies[0]
            parsed = parse_contract_body(contract, body)
            if not parsed.ok or not isinstance(parsed.value, list):
                raise RuntimeError("History metadata read-back changed shape.")

            page_count += 1
            if any(str(item.get("id") or "") == chat_id for item in parsed.value if isinstance(item, dict)):
                return False, {
                    "source": contract.key,
                    "source_rpc": contract.rpc_id,
                    "complete": True,
                    "pages_checked": page_count,
                }

            next_page_token = (
                body[1]
                if isinstance(body, list) and len(body) > 1 and isinstance(body[1], str) and body[1]
                else None
            )
            if next_page_token is None:
                break
        else:
            return None, {
                "source": contract.key,
                "source_rpc": contract.rpc_id,
                "complete": False,
                "pages_checked": page_count,
                "reason": "page_bound_reached",
            }

    return True, {
        "source": contract.key,
        "source_rpc": contract.rpc_id,
        "complete": True,
        "pages_checked": page_count,
    }


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
