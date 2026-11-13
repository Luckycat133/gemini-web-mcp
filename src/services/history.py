"""Shared history helpers used by full and compact MCP adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
import inspect
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterator

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
# --- ported from main (v0.2.2) for the rebased branch ---
async def search_chat_history_result(
    client: Any, query: str, limit: int, offset: int, *, scan_turns: bool = False,
    turns_per_chat: int = 20, max_chars_per_turn: int = 1000, max_limit: int = 50,
    max_turns_per_chat: int = 50, max_chars: int = 4000,
) -> DomainResult[dict[str, Any]]:
    if not str(query or "").strip() or (scan_turns and not hasattr(client, "read_chat")):
        return await search_chats_result(client, [], query, limit, offset, scan_turns=scan_turns)
    target = clamp_int(limit, default=10, minimum=1, maximum=max_limit) + clamp_int(offset, 0, 0, 5000)
    fetched = await fetch_history_metadata(client, target)
    if not fetched.ok:
        return fetched
    assert fetched.data is not None
    return await search_chats_result(
        client, fetched.data["items"], query, limit, offset,
        scan_turns=scan_turns, turns_per_chat=turns_per_chat, max_chars_per_turn=max_chars_per_turn,
        diagnostic=fetched.data["diagnostic"], max_limit=max_limit,
        max_turns_per_chat=max_turns_per_chat, max_chars=max_chars,
    )


# --- ported from main (v0.2.2) for the rebased branch ---
async def list_chat_history_result(
    client: Any, limit: int, offset: int, *, max_limit: int = 50,
) -> DomainResult[dict[str, Any]]:
    target = clamp_int(limit, default=10, minimum=1, maximum=max_limit) + clamp_int(offset, 0, 0, 5000)
    fetched = await fetch_history_metadata(client, target)
    if not fetched.ok:
        return fetched
    assert fetched.data is not None
    return list_chats_result(fetched.data["items"], limit, offset,
                             diagnostic=fetched.data["diagnostic"], max_limit=max_limit)

async def read_history_metadata_page(
    client: Any, *, contract_key: str = "history.page", **arguments: Any,
) -> DomainResult[dict[str, Any]]:
    contract = get_contract(contract_key)
    response = await execute_contract(client, contract.key, **arguments)
    response_text = str(getattr(response, "text", "") or "")
    envelope = parse_rpc_envelope(response_text, contract.rpc_id)
    diagnostic = {"contract_key": contract.key, "status_code": getattr(response, "status_code", None),
                  "reject_code": envelope.reject_code, "response_length": len(response_text)}
    if diagnostic["status_code"] == 200 and envelope.parsed and envelope.reject_code is None and envelope.single_body:
        body = envelope.bodies[0]
        parsed = parse_contract_body(contract, body)
        diagnostic["parser_status"] = parsed.status
        token = body[1] if isinstance(body, list) and len(body) > 1 else None
        if parsed.ok and not parsed.warnings and isinstance(parsed.value, list) and (token is None or isinstance(token, str)):
            return DomainResult.success({"items": parsed.value, "continuation": token},
                                        verification_status="observed", details=diagnostic)
    return history_metadata_failure(diagnostic)

async def fetch_history_metadata(client: Any, target_count: int) -> DomainResult[dict[str, Any]]:
    """Refresh both history buckets, keeping cache-only compatibility explicit."""
    if not hasattr(client, "_batch_execute"):
        if not hasattr(client, "list_chats"):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                        "The current client cannot list history metadata.",
                                        verification_status="capability_unavailable")
        cached = list(client.list_chats() or [])
        return DomainResult.success({"items": cached, "diagnostic": {
            "source": "client_cache", "fetched_count": len(cached), "has_remote_more": False,
        }}, verification_status="observed")
    target_count = clamp_int(target_count, default=50, minimum=1, maximum=5000)
    combined: dict[str, dict[str, Any]] = {}
    more = False
    for pinned in (True, False):
        token: str | None = None
        seen_tokens: set[str] = set()
        count = 0
        while count < target_count:
            result = await read_history_metadata_page(
                client, filter_payload=[pinned, None, True],
                page_size=min(max(target_count, 10), 100), next_page_token=token,
            )
            if not result.ok:
                return result
            assert result.data is not None
            entries = result.data["items"]
            for item in entries:
                previous = combined.get(item["id"])
                if previous is not None and any(previous.get(key) != item.get(key) for key in ("title", "is_pinned")):
                    return DomainResult.failure(
                        DomainErrorCode.UPSTREAM_CHANGED,
                        "The history pages returned conflicting metadata for the same chat.",
                        retryable=True, suggested_action="Refresh the history metadata before relying on this state.",
                        verification_status="metadata_conflict", details={"conflicting_record_count": 1},
                    )
                combined.setdefault(item["id"], item)
            count += len(entries)
            token = result.data["continuation"]
            if not token or not entries:
                break
            if token in seen_tokens:
                more = True
                break
            seen_tokens.add(token)
        more = more or bool(token)
    items = list(combined.values())
    return DomainResult.success({"items": items, "diagnostic": {
        "source": "history.page", "has_remote_more": more,
        "max_offset": 5000, "fetched_count": len(items),
    }}, verification_status="observed")

def history_metadata_failure(diagnostic: dict[str, Any]) -> DomainResult[dict[str, Any]]:
    code = DomainErrorCode.UPSTREAM_REJECTED if diagnostic.get("reject_code") is not None else DomainErrorCode.UPSTREAM_CHANGED
    return DomainResult.failure(code, "The history metadata response could not be verified.",
                                verification_status="read_back_unavailable", details=diagnostic)

def _remote_pagination(pagination: dict[str, Any], diagnostic: dict[str, Any]) -> tuple[DomainWarning, ...]:
    next_offset = pagination["offset"] + pagination["count"]
    max_offset = diagnostic.get("max_offset")
    more_records = bool(diagnostic.get("has_remote_more") or pagination["has_more"])
    bounded = more_records and isinstance(max_offset, int) and next_offset > max_offset
    if not bounded:
        if not diagnostic.get("has_remote_more") or pagination["has_more"]:
            return ()
        if pagination["count"] > 0:
            pagination["has_more"] = True
            pagination["next_offset"] = next_offset
            return ()
    if not more_records:
        return ()
    pagination.update(has_more=False, next_offset=None)
    diagnostic.update(coverage_complete=False, stopped_reason="source_bound_reached")
    return (DomainWarning(
        "HISTORY_SOURCE_INCOMPLETE",
        "The bounded history source has more remote records, but this page cannot advance.",
        "Use the deeper history metadata scan with a larger source bound.",
    ),)




_read_account: ContextVar[str | None] = ContextVar("gemini_history_read_account", default=None)


def check_account_read() -> None:
    owner = _read_account.get()
    if owner and current_account_key() != owner:
        raise PermissionError("Gemini account changed or is unavailable.")


@contextmanager
def account_read_scope(owner: str) -> Iterator[None]:
    token = _read_account.set(owner)
    try:
        check_account_read()
        yield
        check_account_read()
    finally:
        _read_account.reset(token)


class _AccountReadClient:
    def __init__(self, client: Any):
        self._wrapped = client

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._wrapped, name)
        if not callable(value):
            check_account_read()
            return value
        def call(*args: Any, **kwargs: Any) -> Any:
            check_account_read()
            result = value(*args, **kwargs)
            if inspect.isawaitable(result):
                async def checked() -> Any:
                    try:
                        check_account_read()
                    except Exception:
                        if inspect.iscoroutine(result):
                            result.close()
                        raise
                    try:
                        output = await result
                    finally:
                        check_account_read()
                    return output
                return checked()
            check_account_read()
            return result
        return call


def bind_account_read_client(client: Any) -> Any:
    return _AccountReadClient(client) if _read_account.get() else client



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


def compact_history_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep actionable identities and pagination; omit repeated metadata prose."""
    if action not in {"list", "search", "read"}:
        return payload
    keys = ("chat_id", "query", "count", "match_count", "scanned_count", "total_count",
            "offset", "limit", "has_more", "next_offset", "scan_turns", "turns")
    result = {key: payload[key] for key in keys if key in payload}
    diagnostic = payload.get("diagnostic")
    if isinstance(diagnostic, dict):
        coverage = {key: diagnostic[key] for key in ("source", "source_rpc", "fetched_count", "has_remote_more")
                    if key in diagnostic}
        coverage["scope"] = "current_metadata_page" if action == "search" else "fetched_metadata"
        result["coverage"] = coverage
    items_key = "matches" if action == "search" else "items"
    if items_key in payload:
        fields = ("id", "title", "is_pinned", "matched_fields", "snippets")
        result[items_key] = [{key: item[key] for key in fields if key in item}
                             for item in payload[items_key]]
    return result


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
                snippets.append({"error": type(error).__name__})
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
                metadata["metadata_warning"] = type(error).__name__

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


def _deletion_payload(chat_id: str, deleted: bool | None, status: str, source: str | None) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "delete_requested": True,
        "deleted": deleted,
        "verification": {"status": status, "source": source},
    }


async def delete_chat_result(
    client: object,
    chat_id: str,
    *,
    expected_account: str | None = None,
    account_key_provider: Callable[[], str | None] = current_account_key,
    reconcile_owned_cleanup: bool = False,
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

    owner = expected_account or account_key_provider()
    def account_failure() -> DomainResult[dict[str, Any]]:
        return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "Gemini account changed or is unavailable.",
                                    verification_status="account_mismatch")
    if not owner or account_key_provider() != owner:
        return account_failure()
    from ..thinking_client import mutation_scope
    try:
        with mutation_scope(owner=owner, account_key_provider=account_key_provider):
            await client.delete_chat(normalized_chat_id)  # type: ignore[attr-defined]
    except Exception:
        if account_key_provider() != owner:
            return account_failure()
        raise
    if account_key_provider() != owner:
        return account_failure()
    if not hasattr(client, "_batch_execute"):
        return DomainResult.success(
            _deletion_payload(normalized_chat_id, None, "not_available", None),
            operation_state=OperationState.ACCEPTED,
            verification_status="not_available",
            details={"source": "client_delete_chat"},
        )

    try:
        absent, observation_details = await observe_chat_absence(client, normalized_chat_id,
                                                                  expected_account=owner,
                                                                  account_key_provider=account_key_provider)
        if account_key_provider() != owner:
            return account_failure()
    except Exception as error:
        if account_key_provider() != owner:
            return account_failure()
        return DomainResult.failure(
            DomainErrorCode.VERIFICATION_FAILED,
            "The chat deletion request returned, but read-back verification failed.",
            data=_deletion_payload(normalized_chat_id, None, "read_back_error", "history.exact"),
            retryable=True,
            suggested_action="Read the chat again before retrying deletion.",
            verification_status="read_back_error",
            details={"source": "history.exact", "error_type": type(error).__name__},
        )

    if absent is None:
        return DomainResult.success(
            _deletion_payload(normalized_chat_id, None, "not_available", "history.exact"),
            operation_state=OperationState.ACCEPTED,
            verification_status="not_available",
            details=observation_details,
        )

    if absent:
        warnings: tuple[DomainWarning, ...] = ()
        if reconcile_owned_cleanup:
            from ..client_wrapper import _cleanup_manager

            try:
                _cleanup_manager.record_explicit_verified_absence(normalized_chat_id, expected_account=owner)
            except (OSError, sqlite3.Error):
                warnings = (DomainWarning(code="cleanup_ledger_unverified",
                                          message="The chat is absent, but its local cleanup ledger could not be updated."),)
        return DomainResult.success(
            _deletion_payload(normalized_chat_id, True, "verified_absent", "history.exact"),
            verification_status="verified_absent",
            details=observation_details,
            warnings=warnings,
        )

    return DomainResult.failure(
        DomainErrorCode.VERIFICATION_FAILED,
        "The chat is still readable after the deletion request.",
        data=_deletion_payload(normalized_chat_id, False, "still_present", "history.exact"),
        retryable=True,
        suggested_action="Retry later or inspect the chat before issuing another deletion request.",
        verification_status="still_present",
        details=observation_details,
    )


async def observe_chat_absence(
    client: object,
    chat_id: str,
    *,
    expected_account: str | None = None,
    account_key_provider: Callable[[], str | None] = current_account_key,
) -> tuple[bool | None, dict[str, Any]]:
    """Verify an exact conversation independently of recent, pinned or Notebook lists."""
    if expected_account and account_key_provider() != expected_account:
        raise PermissionError("Gemini account changed or is unavailable.")
    if not hasattr(client, "_batch_execute"):
        return None, {"source": None, "complete": False, "reason": "batch_execute_unavailable"}

    contract = get_contract("history.exact")
    response = await execute_contract(client, contract.key, chat_id=chat_id, limit=1)
    if expected_account and account_key_provider() != expected_account:
        raise PermissionError("Gemini account changed or is unavailable.")
    if getattr(response, "status_code", None) != 200:
        raise RuntimeError("Exact conversation read-back failed its HTTP status check.")

    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), contract.rpc_id)
    details = {"source": contract.key, "source_rpc": contract.rpc_id, "complete": True}
    if envelope.parsed and envelope.reject_code == 5 and not envelope.bodies:
        return True, details
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise RuntimeError("Exact conversation read-back did not return a usable RPC envelope.")

    parsed = parse_contract_body(contract, envelope.bodies[0], chat_id=chat_id)
    if parsed.status == "success":
        return False, details
    if parsed.status == "empty":
        return None, {**details, "complete": False, "reason": "empty_turns"}
    raise RuntimeError("Exact conversation read-back changed shape.")


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
