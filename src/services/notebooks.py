"""Native Gemini Notebook reads and verified mutations."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import (
    RPCParseResult,
    extract_rpc_bodies,
    parse_contract_body,
    parse_conversation_metadata,
    parse_native_notebook,
    parse_notebook_category,
    parse_rpc_envelope,
)
from .history import clamp_int


def _parse_observation(response: Any, contract_key: str, **arguments: Any) -> tuple[RPCParseResult, dict[str, Any]]:
    contract = get_contract(contract_key)
    text = str(getattr(response, "text", "") or "")
    envelope = parse_rpc_envelope(text, contract.rpc_id)
    if envelope.reject_code is not None:
        parsed = RPCParseResult("rejected", reject_code=envelope.reject_code)
    elif len(envelope.bodies) == 1:
        parsed = parse_contract_body(contract, envelope.bodies[0], **arguments)
    else:
        parsed = RPCParseResult("changed_shape", warnings=("missing_or_ambiguous_body",))
    valid = bool(getattr(response, "status_code", None) == 200 and envelope.parsed
                 and envelope.reject_code is None and len(envelope.bodies) == 1 and parsed.ok and not parsed.warnings)
    return parsed, {"source_rpc": contract.rpc_id, "contract_key": contract.key, "observed": contract.observed,
                    "parser_status": parsed.status, "parser_warnings": list(parsed.warnings),
                    "status_code": getattr(response, "status_code", None), "body_present": bool(envelope.bodies),
                    "reject_code": envelope.reject_code, "response_length": len(text), "read_back_valid": valid}


def _valid_read_back(diagnostic: dict[str, Any]) -> bool:
    return diagnostic.get("read_back_valid") is True and diagnostic.get("parser_status") in {"success", "empty"}


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
    parsed, diagnostic = _parse_observation(response, contract.key)
    value = parsed.value if _valid_read_back(diagnostic) and isinstance(parsed.value, dict) else {}
    notebooks = value.get("items", []) if isinstance(value.get("items", []), list) else []
    categories = value.get("categories", []) if isinstance(value.get("categories", []), list) else []
    diagnostic.update({
        "raw_entry_count": len(notebooks),
        "categories": categories,
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
    })
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
    next_page_token: str | None = None
    response_length = 0
    page_count = 0
    parser_status = "empty"
    seen_tokens: set[str] = set()
    seen_ids: set[str] = set()
    valid = True
    incomplete_reason: str | None = None
    last_diagnostic: dict[str, Any] = {}
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
        bodies = extract_rpc_bodies(response_text, contract.rpc_id)
        body = bodies[0] if bodies else []
        parsed, last_diagnostic = _parse_observation(response, contract.key)
        parser_status = parsed.status
        if not _valid_read_back(last_diagnostic):
            valid = False
            incomplete_reason = "invalid_read_back"
            next_page_token = None
            break
        parsed_items = parsed.value if isinstance(parsed.value, list) else []
        new_items = [item for item in parsed_items if item.get("id") not in seen_ids]
        seen_ids.update(str(item.get("id")) for item in new_items)
        items.extend(new_items)
        next_page_token = body[1] if isinstance(body, list) and len(body) > 1 and isinstance(body[1], str) else None
        if next_page_token and (next_page_token in seen_tokens or not new_items):
            incomplete_reason = "pagination_not_progressing"
            break
        if next_page_token:
            seen_tokens.add(next_page_token)
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
        "read_back_valid": valid,
        "status_code": last_diagnostic.get("status_code"),
        "body_present": last_diagnostic.get("body_present", False),
        "reject_code": last_diagnostic.get("reject_code"),
        "parser_warnings": last_diagnostic.get("parser_warnings", []),
        "complete": valid and incomplete_reason is None,
        "incomplete_reason": incomplete_reason,
    }
    return page, {
        "total_count": len(items),
        "count": len(page),
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
        "next_offset": safe_offset + len(page) if has_more and page else None,
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
) -> dict[str, Any]:
    """Move a chat and return explicit read-back verification evidence."""

    contract = get_contract("notebooks.move_chat")
    notebooks, list_diagnostic = await fetch_notebooks(client, locale)
    if not _valid_read_back(list_diagnostic):
        return {"ok": False, "accepted": False, "chat_id": chat_id,
                "verification_status": "target_read_back_unverified", "diagnostic": list_diagnostic}
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
    )
    bodies = extract_bodies(getattr(response, "text", "") or "", contract.rpc_id)
    parsed, mutation_diagnostic = _parse_observation(response, contract.key, expected_id=chat_id)
    updated_entry = parsed.value if parsed.status == "success" else None
    verified = False
    verification_status = "rpc_unconfirmed"
    verification: dict[str, Any] = {}
    verification_error = ""
    accepted = _valid_read_back(mutation_diagnostic)
    if accepted:
        verification_status = "read_back_not_observed"
        try:
            verify_items, verification = await fetch_chats(client, str(notebook["id"]), 100, 0)
            read_diagnostic = verification.get("diagnostic", {})
            verified = _valid_read_back(read_diagnostic) and any(item.get("id") == chat_id for item in verify_items)
            verification_status = ("verified" if verified else "read_back_not_observed") if _valid_read_back(read_diagnostic) else "read_back_unverified"
        except Exception as exc:
            verification_status = "read_back_error"
            verification_error = f"{type(exc).__name__}: {exc}"
    return {
        "ok": accepted and verified,
        "accepted": accepted,
        "chat_id": chat_id,
        "notebook": notebook,
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "status_code": getattr(response, "status_code", None),
        "body_present": bool(bodies),
        "parser_status": parsed.status,
        "mutation_diagnostic": mutation_diagnostic,
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
