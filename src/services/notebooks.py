"""Native Gemini Notebook reads and verified mutations."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState
from ..infrastructure.rpc_contracts import execute_contract, get_contract, native_notebook_metadata, native_notebook_source
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
from .account import account_contract_result


def _parse_observation(response: Any, contract_key: str, **arguments: Any) -> tuple[RPCParseResult, dict[str, Any]]:
    contract = get_contract(contract_key)
    text = str(getattr(response, "text", "") or "")
    envelope = parse_rpc_envelope(text, contract.rpc_id)
    if envelope.reject_code is not None:
        parsed = RPCParseResult("rejected", reject_code=envelope.reject_code)
    elif envelope.single_body:
        parsed = parse_contract_body(contract, envelope.bodies[0], **arguments)
    else:
        parsed = RPCParseResult("changed_shape", warnings=("missing_or_ambiguous_body",))
    valid = bool(getattr(response, "status_code", None) == 200 and envelope.parsed
                 and envelope.reject_code is None and envelope.single_body and parsed.ok and not parsed.warnings)
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


def _notebook_argument_error(message: str) -> DomainResult[dict[str, Any]]:
    return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, message)


async def _read_notebook(client: Any, notebook_id: str, locale: str) -> DomainResult[dict[str, Any]]:
    response = await execute_contract(client, "notebooks.get", notebook_id=notebook_id, locale=locale)
    return account_contract_result(response, "notebooks.get", expected_id=notebook_id)


def _notebook_mutation_result(
    data: dict[str, Any], ack: DomainResult[dict[str, Any]], verified: bool,
    verification: dict[str, Any], *, verification_status: str = "read_back_not_observed",
) -> DomainResult[dict[str, Any]]:
    return DomainResult.success(
        {**data, "accepted": True, "verified": verified},
        operation_state=OperationState.COMPLETED if verified else OperationState.ACCEPTED,
        verification_status="verified" if verified else verification_status,
        warnings=() if verified else (DomainWarning(
            "MUTATION_UNVERIFIED", "The Notebook mutation was accepted but independent read-back is unverified.",
            "Read the same Notebook/source ID again; do not repeat creation.",
        ),),
        details={"mutation": dict(ack.meta.details), "verification": verification},
    )


async def get_notebook(client: Any, notebook_id: str, *, locale: str = "zh-CN") -> DomainResult[dict[str, Any]]:
    if not isinstance(notebook_id, str) or not notebook_id.strip():
        return _notebook_argument_error("notebook_id is required.")
    result = await _read_notebook(client, notebook_id, locale)
    if not result.ok:
        return result
    value = (result.data or {}).get("value")
    return DomainResult.success({"item": value["item"] if value else None},
                                verification_status="observed", details=result.meta.details)


async def create_notebook(
    client: Any, *, title: str, description: str = "", instructions: str = "",
    subtype: int = 1, locale: str = "zh-CN",
) -> DomainResult[dict[str, Any]]:
    if (not isinstance(title, str) or not title.strip() or not isinstance(description, str)
            or not isinstance(instructions, str) or type(subtype) is not int or subtype not in {1, 2, 3}):
        return _notebook_argument_error("Provide a title, string description/instructions and Notebook subtype 1, 2 or 3.")
    if description:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "Native Notebook descriptions are not writable; use title and instructions.")
    # Current UI creates the title first; instructions are retained by the
    # separate UpdateBotMetadata write, not by the initial CreateBot request.
    metadata = native_notebook_metadata(title=title, description="", instructions="", subtype=subtype)
    response = await execute_contract(client, "notebooks.create", metadata=metadata)
    ack = account_contract_result(response, "notebooks.create")
    if not ack.ok:
        return ack
    created = (ack.data or {}).get("value") or {}
    notebook_id = created.get("id", "")
    data: dict[str, Any] = {"id": notebook_id, "item": None}
    if not notebook_id:
        return _notebook_mutation_result(data, ack, False, {}, verification_status="created_id_not_observed")
    try:
        if instructions:
            updated = await update_notebook(client, notebook_id, title=title, instructions=instructions, locale=locale)
            data["item"] = (updated.data or {}).get("item")
            item = data["item"]
            verified = bool(updated.ok and updated.operation_state == "completed" and item
                            and item.get("project_subtype") == subtype)
            return _notebook_mutation_result(
                data, ack, verified, {**dict(updated.meta.details), "update_error_code": updated.error_code},
                verification_status=updated.meta.verification_status if updated.ok else "initial_update_unverified",
            )
        read = await get_notebook(client, notebook_id, locale=locale)
        item = (read.data or {}).get("item")
        verified = bool(read.ok and item and all(item.get(k) == v for k, v in {
            "title": title, "description": description, "instructions": instructions, "project_subtype": subtype,
        }.items()))
        data["item"] = item
        return _notebook_mutation_result(data, ack, verified, dict(read.meta.details))
    except Exception as exc:
        return _notebook_mutation_result(data, ack, False, {"error_type": type(exc).__name__},
                                         verification_status="read_back_error")


async def update_notebook(
    client: Any, notebook_id: str, *, title: str | None = None, description: str | None = None,
    instructions: str | None = None, locale: str = "zh-CN",
) -> DomainResult[dict[str, Any]]:
    changes = {k: v for k, v in {"title": title, "description": description, "instructions": instructions}.items()
               if v is not None}
    if (not isinstance(notebook_id, str) or not notebook_id.strip() or not changes
            or any(not isinstance(v, str) for v in changes.values()) or (title is not None and not title.strip())):
        return _notebook_argument_error("Provide notebook_id and at least one string field; title cannot be blank.")
    if description:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "Native Notebook descriptions are not writable; use title and instructions.")
    current = await _read_notebook(client, notebook_id, locale)
    if not current.ok:
        return current
    detail = (current.data or {}).get("value")
    if detail is None:
        return _notebook_argument_error("Notebook was not found; no mutation was sent.")
    metadata = detail["metadata"]
    for name, index in (("title", 0), ("description", 1), ("instructions", 2)):
        if name in changes:
            metadata[index] = changes[name]
    response = await execute_contract(client, "notebooks.update", notebook_id=notebook_id, metadata=metadata)
    ack = account_contract_result(response, "notebooks.update")
    if not ack.ok:
        return ack
    data: dict[str, Any] = {"id": notebook_id, "item": None}
    try:
        read = await get_notebook(client, notebook_id, locale=locale)
        item = (read.data or {}).get("item")
        data["item"] = item
        # Untouched fields and source identities must survive the metadata write.
        expected = {**{k: detail["item"][k] for k in ("title", "description", "instructions")}, **changes}
        verified = bool(read.ok and item and all(item.get(k) == v for k, v in expected.items())
                        and {s["name"] for s in item["sources"]} == {s["name"] for s in detail["item"]["sources"]})
        return _notebook_mutation_result(data, ack, verified, dict(read.meta.details))
    except Exception as exc:
        return _notebook_mutation_result(data, ack, False, {"error_type": type(exc).__name__},
                                         verification_status="read_back_error")


async def delete_notebook(
    client: Any, notebook_id: str, *, delete_chats: bool = False, locale: str = "zh-CN",
) -> DomainResult[dict[str, Any]]:
    if not isinstance(notebook_id, str) or not notebook_id.strip() or type(delete_chats) is not bool:
        return _notebook_argument_error("Provide notebook_id and a boolean delete_chats.")
    current = await get_notebook(client, notebook_id, locale=locale)
    if not current.ok:
        return current
    if not (current.data or {}).get("item"):
        return _notebook_argument_error("Notebook was not found; no mutation was sent.")
    key = "notebooks.delete_with_chats" if delete_chats else "notebooks.delete"
    response = await execute_contract(client, key, notebook_id=notebook_id)
    ack = account_contract_result(response, key)
    if not ack.ok:
        return ack
    data = {"id": notebook_id, "delete_chats": delete_chats}
    try:
        items, diagnostic = await fetch_native_notebooks(client, locale)
        absent = _valid_read_back(diagnostic) and all(item["id"] != notebook_id for item in items)
        # Notebook absence cannot prove deletion of its conversations.
        return _notebook_mutation_result(
            data, ack, absent and not delete_chats, {**diagnostic, "notebook_absent": absent},
            verification_status="notebook_absent_chats_unverified" if absent and delete_chats else "read_back_not_observed",
        )
    except Exception as exc:
        return _notebook_mutation_result(data, ack, False, {"error_type": type(exc).__name__},
                                         verification_status="read_back_error")


async def list_notebook_sources(
    client: Any, notebook_id: str, *, locale: str = "zh-CN",
) -> DomainResult[dict[str, Any]]:
    read = await get_notebook(client, notebook_id, locale=locale)
    if not read.ok:
        return read
    item = (read.data or {}).get("item")
    if item is None:
        return _notebook_argument_error("Notebook was not found.")
    return DomainResult.success({"notebook_id": notebook_id, "items": item["sources"]},
                                verification_status="observed", details=read.meta.details)


async def add_notebook_source(
    client: Any, notebook_id: str, *, display_name: str, text: str | None = None,
    url: str | None = None, upload_reference: str | None = None, file_path: str | None = None,
    mime_type: str = "text/plain",
    locale: str = "zh-CN",
) -> DomainResult[dict[str, Any]]:
    from urllib.parse import urlsplit

    supplied = [v for v in (text, url, upload_reference, file_path) if v is not None]
    if (not isinstance(notebook_id, str) or not notebook_id.strip() or not isinstance(display_name, str)
            or not display_name.strip() or len(supplied) != 1 or not isinstance(supplied[0], str)
            or not supplied[0].strip() or not isinstance(mime_type, str) or not mime_type.strip()):
        return _notebook_argument_error("Provide notebook_id, display_name and exactly one nonempty text, URL, upload_reference or file_path.")
    if url is not None:
        try:
            parsed_url = urlsplit(url)
            valid_url = parsed_url.scheme in {"http", "https"} and bool(parsed_url.hostname)
        except ValueError:
            valid_url = False
        if not valid_url:
            return _notebook_argument_error("Notebook source URL must be HTTP(S).")
    source_file = Path(file_path).expanduser() if file_path is not None else None
    if source_file is not None and not source_file.is_file():
        return _notebook_argument_error("file_path must name an existing source file.")
    current = await get_notebook(client, notebook_id, locale=locale)
    if not current.ok:
        return current
    if not (current.data or {}).get("item"):
        return _notebook_argument_error("Notebook was not found; no source was added.")
    if source_file is not None:
        # Notebook file sources use the ordinary SDK uploader, unlike native
        # Skill ZIPs. Validate target ownership before uploading any bytes.
        from gemini_webapi.utils import upload_file

        session = getattr(client, "client", None)
        push_id = getattr(client, "push_id", None)
        if session is None or not push_id:
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                        "Client has no initialized source-file upload session.")
        upload_reference = await upload_file(source_file, client=session, push_id=push_id)
        if not isinstance(upload_reference, str) or not upload_reference.strip():
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED,
                                        "Notebook source file upload returned no usable reference.")
        if mime_type == "text/plain":
            mime_type = mimetypes.guess_type(source_file.name)[0] or "application/octet-stream"
    source = native_notebook_source(display_name=display_name, mime_type=mime_type, text=text,
                                    url=url, upload_reference=upload_reference)
    response = await execute_contract(client, "notebooks.add_source", notebook_id=notebook_id, source=source)
    ack = account_contract_result(response, "notebooks.add_source", expected_id=notebook_id)
    if not ack.ok:
        return ack
    created = (ack.data or {}).get("value") or {}
    source_name = created.get("source_name", "")
    data = {"notebook_id": notebook_id, "source_name": source_name, "item": None}
    try:
        read = await list_notebook_sources(client, notebook_id, locale=locale)
        item = next((s for s in (read.data or {}).get("items", []) if s["name"] == source_name), None)
        data["item"] = item
        retained = bool(source_name and read.ok and item and item["display_name"] == display_name)
        kind, field, expected = ("text", "text", text) if text is not None else (
            ("url", "url", url) if url is not None else ("upload", "upload_reference", upload_reference)
        )
        content_matches = bool(item and item.get("source_kind") == kind and item.get(field) == expected)
        if text is not None:
            content_matches = content_matches and bool(item and item.get("text_format") == source[5][1])
        elif upload_reference is not None:
            content_matches = content_matches and bool(item and item.get("mime_type") == mime_type)
        # lbYGFc:6930 xzh: 2 succeeds; 1/4 continue processing. Retention alone
        # cannot prove that Gemini can already use an asynchronously indexed file.
        verified = retained and content_matches and bool(item and item["state"] == 2)
        status = "read_back_not_observed"
        if retained and item:
            status = ("source_processing" if item["state"] in {1, 4} else
                      "source_content_unobserved" if item["state"] == 2 and item.get("source_kind") is None else
                      "source_content_mismatch" if item["state"] == 2 and not content_matches else "source_not_ready")
        return _notebook_mutation_result(data, ack, verified, dict(read.meta.details), verification_status=status)
    except Exception as exc:
        return _notebook_mutation_result(data, ack, False, {"error_type": type(exc).__name__},
                                         verification_status="read_back_error")


async def remove_notebook_source(
    client: Any, notebook_id: str, source_name: str, *, locale: str = "zh-CN",
) -> DomainResult[dict[str, Any]]:
    if (not isinstance(notebook_id, str) or not notebook_id.strip()
            or not isinstance(source_name, str) or not source_name.strip()):
        return _notebook_argument_error("Provide notebook_id and source_name.")
    before = await list_notebook_sources(client, notebook_id, locale=locale)
    if not before.ok:
        return before
    if not any(s["name"] == source_name for s in (before.data or {}).get("items", [])):
        return _notebook_argument_error("Source was not found in this Notebook; no mutation was sent.")
    response = await execute_contract(client, "notebooks.remove_source", source_name=source_name)
    ack = account_contract_result(response, "notebooks.remove_source")
    if not ack.ok:
        return ack
    data = {"notebook_id": notebook_id, "source_name": source_name}
    try:
        read = await list_notebook_sources(client, notebook_id, locale=locale)
        verified = read.ok and all(s["name"] != source_name for s in (read.data or {}).get("items", []))
        return _notebook_mutation_result(data, ack, verified, dict(read.meta.details))
    except Exception as exc:
        return _notebook_mutation_result(data, ack, False, {"error_type": type(exc).__name__},
                                         verification_status="read_back_error")


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
