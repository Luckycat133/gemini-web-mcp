"""Native Gemini Notebook reads and verified mutations."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..infrastructure.account_identity import current_account_key
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import (
    extract_rpc_bodies,
    parse_contract_body,
    parse_conversation_metadata,
    parse_native_notebook,
    parse_notebook_category,
    parse_rpc_envelope,
)
from .history import clamp_int


class NotebookChatReadError(RuntimeError):
    """A notebook chat listing could not be verified from upstream evidence."""

    def __init__(self, status: str):
        self.status = status
        super().__init__(f"Notebook chat read-back unavailable: {status}")


def native_notebooks_payload(locale: str = "zh-CN") -> str:
    return get_contract("notebooks.list").build_payload(locale=locale)


def notebook_chats_payload(notebook_id: str, page_size: int, next_page_token: str | None = None) -> str:
    return get_contract("notebooks.chats").build_payload(
        notebook_id=notebook_id,
        page_size=page_size,
        next_page_token=next_page_token,
    )


def move_chat_to_notebook_payload(chat_id: str, notebook_id: str, project_type: int = 2) -> str:
    return get_contract("notebooks.move_chat").build_payload(
        chat_id=chat_id,
        notebook_id=notebook_id,
        project_type=project_type,
    )


async def fetch_native_notebooks(
    client: Any,
    locale: str = "zh-CN",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = get_contract("notebooks.list")
    response = await execute_contract(client, contract.key, locale=locale)
    envelope = parse_rpc_envelope(getattr(response, "text", "") or "", contract.rpc_id)
    if getattr(response, "status_code", None) != 200:
        raise NotebookChatReadError("http_error")
    if envelope.reject_code is not None:
        raise NotebookChatReadError("rejected")
    if not envelope.parsed or len(envelope.bodies) != 1:
        raise NotebookChatReadError("missing_envelope")
    bodies = envelope.bodies
    body = bodies[0]
    parsed = parse_contract_body(contract, body)
    if not parsed.ok:
        raise NotebookChatReadError(parsed.status)
    value = parsed.value if isinstance(parsed.value, dict) else {}
    notebooks = value.get("items", []) if isinstance(value.get("items", []), list) else []
    categories = value.get("categories", []) if isinstance(value.get("categories", []), list) else []
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "parser_status": parsed.status,
        "observed": contract.observed,
        "status_code": getattr(response, "status_code", None),
        "response_length": len(getattr(response, "text", "") or ""),
        "body_present": bool(bodies),
        "raw_entry_count": len(notebooks),
        "categories": categories,
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
    }
    return notebooks, diagnostic


async def fetch_notebook_chats(
    client: Any,
    notebook_id: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = get_contract("notebooks.chats")
    safe_limit = clamp_int(limit, default=20, minimum=1, maximum=100)
    safe_offset = clamp_int(offset, default=0, minimum=0, maximum=10000)
    target_count = safe_offset + safe_limit
    page_size = min(max(target_count, 10), 100)
    items: list[dict[str, Any]] = []
    seen_chat_ids: set[str] = set()
    seen_page_tokens: set[str] = set()
    next_page_token: str | None = None
    response_length = 0
    page_count = 0
    parser_status = "empty"
    source_path = contract.source_path.format(notebook_slug=notebook_id.rsplit("/", 1)[-1])
    while len(items) < target_count:
        response = await execute_contract(
            client,
            contract.key,
            source_path=source_path,
            notebook_id=notebook_id,
            page_size=page_size,
            next_page_token=next_page_token,
        )
        response_text = getattr(response, "text", "") or ""
        response_length += len(response_text)
        page_count += 1
        envelope = parse_rpc_envelope(response_text, contract.rpc_id)
        if envelope.reject_code is not None:
            raise NotebookChatReadError("rejected")
        if not envelope.parsed or not envelope.bodies:
            raise NotebookChatReadError("missing_envelope")
        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            raise NotebookChatReadError("http_error")
        body = envelope.bodies[0]
        parsed = parse_contract_body(contract, body)
        if not parsed.ok or not isinstance(parsed.value, list):
            raise NotebookChatReadError(parsed.status)
        parser_status = parsed.status
        parsed_items = parsed.value if isinstance(parsed.value, list) else []
        if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip()
               for item in parsed_items):
            raise NotebookChatReadError("changed_shape")
        page_ids = [item["id"] for item in parsed_items]
        if len(page_ids) != len(set(page_ids)) or any(chat_id in seen_chat_ids for chat_id in page_ids):
            raise NotebookChatReadError("duplicate_chat_id")
        token = body[1] if isinstance(body, list) and len(body) > 1 else None
        if token is not None and not isinstance(token, str):
            raise NotebookChatReadError("changed_shape")
        next_page_token = token or None
        if next_page_token and not parsed_items:
            raise NotebookChatReadError("incomplete_page")
        if next_page_token and next_page_token in seen_page_tokens:
            raise NotebookChatReadError("repeated_page_token")
        seen_chat_ids.update(page_ids)
        if next_page_token:
            seen_page_tokens.add(next_page_token)
        items.extend(parsed_items)
        if not next_page_token:
            break
    page = items[safe_offset : safe_offset + safe_limit]
    has_more = bool(next_page_token) or safe_offset + len(page) < len(items)
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "parser_status": parser_status,
        "observed": contract.observed,
        "response_length": response_length,
        "page_count": page_count,
        "fetched_count": len(items),
        "has_remote_more": bool(next_page_token),
        "next_page_token_present": bool(next_page_token),
    }
    return page, {
        "total_count": len(items),
        "count": len(page),
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
        "next_offset": safe_offset + len(page) if has_more else None,
        "diagnostic": diagnostic,
    }


def find_notebook(
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


FetchNotebooks = Callable[[Any, str], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]
FetchChats = Callable[[Any, str, int, int], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]
ExtractBodies = Callable[[str, str], list[Any]]


async def move_chat_to_notebook(
    client: Any,
    *,
    chat_id: str,
    notebook_id: str = "",
    notebook_title: str = "",
    locale: str = "zh-CN",
    fetch_notebooks: FetchNotebooks = fetch_native_notebooks,
    fetch_chats: FetchChats = fetch_notebook_chats,
    extract_bodies: ExtractBodies = extract_rpc_bodies,
    expected_account: str | None = None,
    account_key_provider: Callable[[], str | None] = current_account_key,
) -> dict[str, Any]:
    """Move a chat and return explicit read-back verification evidence."""

    owner = expected_account or account_key_provider()
    def fence() -> None:
        if not owner or account_key_provider() != owner:
            raise PermissionError("Gemini account changed or is unavailable.")

    fence()
    contract = get_contract("notebooks.move_chat")
    notebooks, list_diagnostic = await fetch_notebooks(client, locale)
    fence()
    notebook = find_notebook(notebooks, notebook_id, notebook_title)
    if notebook is None:
        return {
            "ok": False,
            "chat_id": chat_id,
            "notebook_id": notebook_id,
            "notebook_title": notebook_title,
            "available_titles": [item.get("title", "") for item in notebooks if item.get("title")],
            "verification_status": "target_not_found",
            "diagnostic": list_diagnostic,
        }

    project_type_raw = notebook.get("project_type")
    project_type = project_type_raw if isinstance(project_type_raw, int) else 2
    response = await execute_contract(
        client,
        contract.key,
        chat_id=chat_id,
        notebook_id=str(notebook["id"]),
        project_type=project_type,
        retry_count=0,
    )
    fence()
    bodies = extract_bodies(getattr(response, "text", "") or "", contract.rpc_id)
    body = bodies[0] if bodies else []
    parsed = parse_contract_body(contract, body)
    updated_entry = parsed.value if parsed.status == "success" else None
    verified = False
    verification_status = "rpc_unconfirmed"
    verification: dict[str, Any] = {}
    verification_error = ""
    if bodies:
        verification_status = "read_back_not_observed"
        try:
            verify_items, verification = await fetch_chats(client, str(notebook["id"]), 100, 0)
            fence()
            verified = any(item.get("id") == chat_id for item in verify_items)
            verification_status = "verified" if verified else "read_back_not_observed"
        except PermissionError:
            raise
        except Exception as exc:
            fence()
            verification_status = "read_back_error"
            verification_error = type(exc).__name__
    status_ok = getattr(response, "status_code", None) == 200
    return {
        "ok": status_ok and verified,
        "accepted": status_ok and parsed.status == "success",
        "chat_id": chat_id,
        "notebook": notebook,
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "status_code": getattr(response, "status_code", None),
        "body_present": bool(bodies),
        "parser_status": parsed.status,
        "updated_entry": updated_entry,
        "verified_in_target_notebook": verified,
        "verification_status": verification_status,
        "verification_error": verification_error,
        "verification": verification,
    }


# Compatibility exports for older in-process imports.
_fetch_native_notebooks = fetch_native_notebooks
_fetch_notebook_chats = fetch_notebook_chats
_find_notebook = find_notebook
_native_notebooks_payload = native_notebooks_payload
_notebook_chats_payload = notebook_chats_payload
_move_chat_to_notebook_payload = move_chat_to_notebook_payload
_parse_native_notebook = parse_native_notebook
_parse_notebook_category = parse_notebook_category
_parse_conversation_metadata = parse_conversation_metadata
