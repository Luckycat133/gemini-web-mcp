"""Scheduled-action reads and mutations with explicit read-back evidence."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..infrastructure.rpc_contracts import RawRPCData, execute_contract, get_contract
from ..infrastructure.rpc_parsers import (
    extract_rpc_bodies,
    parse_contract_body,
    parse_scheduled_action_create_body,
    parse_scheduled_action_task_entry,
)


async def fetch_scheduled_registry(
    client: Any,
    max_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = get_contract("scheduled.registry")
    response = await execute_contract(client, contract.key)
    response_text = getattr(response, "text", "") or ""
    bodies = extract_rpc_bodies(response_text, contract.rpc_id)
    body = bodies[0] if bodies else []
    parsed = parse_contract_body(contract, body, max_chars=max_chars)
    entries = parsed.value if isinstance(parsed.value, list) else []
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "parser_status": parsed.status,
        "observed": contract.observed,
        "status_code": getattr(response, "status_code", None),
        "response_length": len(response_text),
        "body_present": bool(bodies),
        "raw_entry_count": len(entries),
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


async def fetch_scheduled_task_by_id(
    client: Any,
    action_id: str,
    max_chars: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    contract = get_contract("scheduled.get")
    response = await execute_contract(client, contract.key, action_id=action_id)
    response_text = getattr(response, "text", "") or ""
    bodies = extract_rpc_bodies(response_text, contract.rpc_id)
    body = bodies[0] if bodies else []
    parsed = parse_contract_body(contract, body, max_chars=max_chars, expected_id=action_id)
    entry = parsed.value if isinstance(parsed.value, dict) else None
    matched_task = bool(entry and entry.get("id") == action_id)
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "parser_status": parsed.status,
        "parser_warnings": list(parsed.warnings),
        "observed": contract.observed,
        "status_code": getattr(response, "status_code", None),
        "response_length": len(response_text),
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


def scheduled_daily_payload(
    title: str,
    instructions: str,
    hour: int,
    timezone_name: str,
    locale: str,
) -> str:
    return get_contract("scheduled.create_daily").build_payload(
        title=title,
        instructions=instructions,
        hour=hour,
        timezone_name=timezone_name,
        locale=locale,
    )


FetchRegistry = Callable[[Any, int], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]
FetchByID = Callable[[Any, str, int], Awaitable[tuple[dict[str, Any] | None, dict[str, Any]]]]
ExtractBodies = Callable[[str, str], list[Any]]
CreateParser = Callable[[Any], dict[str, Any]]
DailyPayloadBuilder = Callable[[str, str, int, str, str], str]


async def create_daily_action(
    client: Any,
    *,
    title: str,
    instructions: str,
    hour: int,
    timezone_name: str,
    locale: str,
    max_chars: int = 400,
    fetch_registry: FetchRegistry = fetch_scheduled_registry,
    fetch_by_id: FetchByID = fetch_scheduled_task_by_id,
    extract_bodies: ExtractBodies = extract_rpc_bodies,
    parse_create: CreateParser = parse_scheduled_action_create_body,
    payload_builder: DailyPayloadBuilder = scheduled_daily_payload,
) -> dict[str, Any]:
    """Create a daily action and return registry/get-by-id verification."""

    contract = get_contract("scheduled.create_daily")
    response = await client._batch_execute(
        [RawRPCData(contract.rpc_id, payload_builder(title, instructions, hour, timezone_name, locale))],
        source_path=contract.source_path,
        close_on_error=False,
    )
    response_text = getattr(response, "text", "") or ""
    bodies = extract_bodies(response_text, contract.rpc_id)
    body = bodies[0] if bodies else []
    if isinstance(body, list) and body and isinstance(body[0], list):
        body = body[0]
    created = parse_create(body)
    created_id = str(created.get("id") or "")
    visible_in_registry = False
    readable_by_id_after_create = None
    task_state_after_create = ""
    task_state_id_after_create = None
    verification_error = ""
    get_task_error = ""
    get_task_diagnostic: dict[str, Any] = {}
    verification_status = "not_attempted"
    if created_id:
        try:
            registry_entries, _ = await fetch_registry(client, max_chars)
            visible_in_registry = any(item.get("id") == created_id for item in registry_entries)
            if visible_in_registry:
                verification_status = "visible_in_registry"
            elif registry_entries:
                verification_status = "not_visible_in_nonempty_registry"
            else:
                verification_status = "registry_empty_unverified"
        except Exception as exc:
            verification_error = str(exc)
            verification_status = "verification_error"
        try:
            task_by_id, get_task_diagnostic = await fetch_by_id(client, created_id, max_chars)
            readable_by_id_after_create = task_by_id is not None
            if task_by_id:
                task_state_after_create = str(task_by_id.get("task_state") or "")
                task_state_id_after_create = task_by_id.get("task_state_id")
            if readable_by_id_after_create and verification_status == "registry_empty_unverified":
                verification_status = "readable_by_id_registry_empty"
            elif readable_by_id_after_create and verification_status == "not_visible_in_nonempty_registry":
                verification_status = "readable_by_id_not_visible_in_registry"
        except Exception as exc:
            get_task_error = str(exc)
    return {
        "ok": getattr(response, "status_code", None) == 200 and bool(created_id),
        "id": created_id,
        "title": created.get("title") or title,
        "instructions": created.get("instructions") or instructions,
        "schedule_label": created.get("schedule_label", ""),
        "enabled": created.get("enabled"),
        "hour": hour,
        "timezone_name": timezone_name,
        "locale": locale,
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "body_present": bool(bodies),
        "visible_in_registry": visible_in_registry,
        "readable_by_id_after_create": readable_by_id_after_create,
        "task_state_after_create": task_state_after_create,
        "task_state_id_after_create": task_state_id_after_create,
        "verification_status": verification_status,
        "verification_error": verification_error,
        "get_task_error": get_task_error,
        "get_task_diagnostic": get_task_diagnostic,
    }


async def delete_action(
    client: Any,
    *,
    action_id: str,
    max_chars: int = 400,
    fetch_registry: FetchRegistry = fetch_scheduled_registry,
    fetch_by_id: FetchByID = fetch_scheduled_task_by_id,
    extract_bodies: ExtractBodies = extract_rpc_bodies,
) -> dict[str, Any]:
    """Delete an action and return registry/get-by-id verification."""

    contract = get_contract("scheduled.delete")
    response = await execute_contract(client, contract.key, action_id=action_id)
    response_text = getattr(response, "text", "") or ""
    bodies = extract_bodies(response_text, contract.rpc_id)
    visible_after_delete = None
    readable_by_id_after_delete = None
    deleted_by_id_after_delete = None
    task_state_after_delete = ""
    task_state_id_after_delete = None
    verification_status = "rpc_unconfirmed"
    verification_error = ""
    get_task_error = ""
    get_task_diagnostic: dict[str, Any] = {}
    if bodies:
        try:
            registry_entries, _ = await fetch_registry(client, max_chars)
            visible_after_delete = any(item.get("id") == action_id for item in registry_entries)
            if visible_after_delete:
                verification_status = "still_visible_in_registry"
            elif registry_entries:
                verification_status = "not_visible_in_nonempty_registry"
            else:
                verification_status = "registry_empty_unverified"
        except Exception as exc:
            verification_error = str(exc)
            verification_status = "verification_error"
        try:
            task_after_delete, get_task_diagnostic = await fetch_by_id(client, action_id, max_chars)
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
        except Exception as exc:
            get_task_error = str(exc)
    status_code = getattr(response, "status_code", None)
    return {
        "ok": status_code in {None, 200} and bool(bodies),
        "id": action_id,
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "body_present": bool(bodies),
        "status_code": status_code,
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


# Compatibility aliases.
_fetch_scheduled_registry = fetch_scheduled_registry
_fetch_scheduled_task_by_id = fetch_scheduled_task_by_id
_parse_scheduled_action_create_body = parse_scheduled_action_create_body
_parse_scheduled_action_task_entry = parse_scheduled_action_task_entry
_scheduled_daily_payload = scheduled_daily_payload
