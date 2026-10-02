"""Scheduled-action reads and mutations with explicit read-back evidence."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..infrastructure.rpc_contracts import RawRPCData, execute_contract, get_contract
from ..infrastructure.rpc_parsers import (
    RPCParseResult,
    extract_rpc_bodies,
    parse_contract_body,
    parse_rpc_envelope,
    parse_scheduled_action_create_body,
    parse_scheduled_action_task_entry,
)


def _parse_observation(response: Any, contract_key: str, **arguments: Any) -> tuple[RPCParseResult, dict[str, Any]]:
    contract = get_contract(contract_key)
    response_text = str(getattr(response, "text", "") or "")
    envelope = parse_rpc_envelope(response_text, contract.rpc_id)
    if envelope.reject_code is not None:
        parsed = RPCParseResult("rejected", reject_code=envelope.reject_code)
    elif len(envelope.bodies) == 1:
        parsed = parse_contract_body(contract, envelope.bodies[0], **arguments)
    else:
        parsed = RPCParseResult("changed_shape", warnings=("missing_or_ambiguous_body",))
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "parser_status": parsed.status,
        "parser_warnings": list(parsed.warnings),
        "observed": contract.observed,
        "status_code": getattr(response, "status_code", None),
        "response_length": len(response_text),
        "body_present": bool(envelope.bodies),
        "raw_body_type": type(envelope.bodies[0]).__name__ if len(envelope.bodies) == 1 else None,
        "raw_top_level_count": (
            len(envelope.bodies[0]) if len(envelope.bodies) == 1 and isinstance(envelope.bodies[0], list) else None
        ),
        "reject_code": envelope.reject_code,
        "read_back_valid": (
            getattr(response, "status_code", None) == 200
            and envelope.parsed and envelope.reject_code is None
            and len(envelope.bodies) == 1 and parsed.ok
        ),
    }
    return parsed, diagnostic


def _valid_read_back(diagnostic: dict[str, Any]) -> bool:
    return (
        diagnostic.get("read_back_valid") is True
        and diagnostic.get("status_code") == 200
        and diagnostic.get("body_present") is True
        and diagnostic.get("reject_code") is None
        and diagnostic.get("parser_status") in {"success", "empty"}
        and not diagnostic.get("parser_warnings")
    )


async def fetch_scheduled_registry(
    client: Any,
    max_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = get_contract("scheduled.registry")
    response = await execute_contract(client, contract.key)
    parsed, diagnostic = _parse_observation(response, contract.key, max_chars=max_chars)
    entries = parsed.value if _valid_read_back(diagnostic) and isinstance(parsed.value, list) else []
    diagnostic.update({
        "raw_entry_count": len(entries),
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
        "has_session_id": bool(getattr(client, "session_id", None)),
        "account_status": str(getattr(client, "account_status", "")),
    })
    if not entries and _valid_read_back(diagnostic):
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
    parsed, diagnostic = _parse_observation(response, contract.key, max_chars=max_chars, expected_id=action_id)
    entry = parsed.value if isinstance(parsed.value, dict) else None
    matched_task = bool(_valid_read_back(diagnostic) and entry and entry.get("id") == action_id)
    diagnostic.update({
        "matched_task": matched_task,
        "client_language": getattr(client, "language", None),
        "client_build_label": getattr(client, "build_label", None),
        "has_session_id": bool(getattr(client, "session_id", None)),
        "account_status": str(getattr(client, "account_status", "")),
    })
    if entry and not matched_task:
        diagnostic["returned_id"] = entry.get("id", "")
    if not matched_task and _valid_read_back(diagnostic):
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
    mutation_parse, mutation_diagnostic = _parse_observation(response, contract.key)
    body = bodies[0] if _valid_read_back(mutation_diagnostic) and bodies else []
    if isinstance(body, list) and body and isinstance(body[0], list):
        body = body[0]
    created = parse_create(body)
    created_id = str(created.get("id") or "")
    acknowledged = bool(_valid_read_back(mutation_diagnostic) and mutation_parse.status == "success" and created_id)
    visible_in_registry = False
    readable_by_id_after_create = None
    task_state_after_create = ""
    task_state_id_after_create = None
    verification_error = ""
    get_task_error = ""
    get_task_diagnostic: dict[str, Any] = {}
    registry_diagnostic: dict[str, Any] = {}
    verification_status = "not_attempted"
    if acknowledged:
        try:
            registry_entries, registry_diagnostic = await fetch_registry(client, max_chars)
            if not _valid_read_back(registry_diagnostic):
                raise RuntimeError("Scheduled registry read-back is not valid evidence.")
            visible_in_registry = any(item.get("id") == created_id and item.get("task_state_id") != 6
                                      for item in registry_entries)
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
            if not _valid_read_back(get_task_diagnostic):
                raise RuntimeError("Scheduled task read-back is not valid evidence.")
            if task_by_id is not None and task_by_id.get("id") != created_id:
                raise RuntimeError("Scheduled task read-back returned a different ID.")
            readable_by_id_after_create = task_by_id is not None and task_by_id.get("task_state_id") != 6
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
        "ok": acknowledged,
        "accepted": acknowledged,
        "verified": visible_in_registry or readable_by_id_after_create is True,
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
        "registry_diagnostic": registry_diagnostic,
        "mutation_diagnostic": mutation_diagnostic,
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
    envelope = parse_rpc_envelope(response_text, contract.rpc_id)
    acknowledged = (
        getattr(response, "status_code", None) == 200
        and envelope.parsed and envelope.reject_code is None and len(envelope.bodies) == 1 and bool(bodies)
        and parse_contract_body(contract, envelope.bodies[0]).ok
    )
    visible_after_delete = None
    readable_by_id_after_delete = None
    deleted_by_id_after_delete = None
    task_state_after_delete = ""
    task_state_id_after_delete = None
    verification_status = "rpc_unconfirmed"
    verification_error = ""
    get_task_error = ""
    get_task_diagnostic: dict[str, Any] = {}
    registry_diagnostic: dict[str, Any] = {}
    if acknowledged:
        try:
            registry_entries, registry_diagnostic = await fetch_registry(client, max_chars)
            if not _valid_read_back(registry_diagnostic):
                raise RuntimeError("Scheduled registry read-back is not valid evidence.")
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
            if not _valid_read_back(get_task_diagnostic):
                raise RuntimeError("Scheduled task read-back is not valid evidence.")
            if task_after_delete is not None and task_after_delete.get("id") != action_id:
                raise RuntimeError("Scheduled task read-back returned a different ID.")
            if task_after_delete is None and get_task_diagnostic.get("parser_status") != "empty":
                raise RuntimeError("Scheduled task read-back did not observe an empty result.")
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
            if verification_status != "still_visible_in_registry":
                verification_status = "read_back_unverified"
    status_code = getattr(response, "status_code", None)
    return {
        "ok": acknowledged,
        "id": action_id,
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "body_present": bool(bodies),
        "status_code": status_code,
        "reject_code": envelope.reject_code,
        "visible_after_delete": visible_after_delete,
        "readable_by_id_after_delete": readable_by_id_after_delete,
        "deleted_by_id_after_delete": deleted_by_id_after_delete,
        "task_state_after_delete": task_state_after_delete,
        "task_state_id_after_delete": task_state_id_after_delete,
        "verification_status": verification_status,
        "verification_error": verification_error,
        "get_task_error": get_task_error,
        "get_task_diagnostic": get_task_diagnostic,
        "registry_diagnostic": registry_diagnostic,
    }


# Compatibility aliases.
_fetch_scheduled_registry = fetch_scheduled_registry
_fetch_scheduled_task_by_id = fetch_scheduled_task_by_id
_parse_scheduled_action_create_body = parse_scheduled_action_create_body
_parse_scheduled_action_task_entry = parse_scheduled_action_task_entry
_scheduled_daily_payload = scheduled_daily_payload
