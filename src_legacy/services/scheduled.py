"""Scheduled-action reads and mutations with explicit read-back evidence."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date
import logging
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState, result_from_exception
from ..infrastructure.rpc_contracts import RawRPCData, execute_contract, get_contract
from ..infrastructure.rpc_parsers import (
    RPCParseResult,
    extract_rpc_bodies,
    parse_contract_body,
    parse_rpc_envelope,
    parse_scheduled_action_create_body,
    parse_scheduled_action_task_entry,
)

logger = logging.getLogger(__name__)


def _parse_observation(response: Any, contract_key: str, **arguments: Any) -> tuple[RPCParseResult, dict[str, Any]]:
    contract = get_contract(contract_key)
    response_text = str(getattr(response, "text", "") or "")
    envelope = parse_rpc_envelope(response_text, contract.rpc_id)
    if envelope.reject_code is not None:
        parsed = RPCParseResult("rejected", reject_code=envelope.reject_code)
    elif envelope.single_body:
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
        "raw_body_type": type(envelope.bodies[0]).__name__ if envelope.single_body else None,
        "raw_top_level_count": (
            len(envelope.bodies[0]) if envelope.single_body and isinstance(envelope.bodies[0], list) else None
        ),
        "reject_code": envelope.reject_code,
        "read_back_valid": (
            getattr(response, "status_code", None) == 200
            and envelope.parsed and envelope.reject_code is None
            and envelope.single_body and parsed.ok
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
    entries = [{**entry, "backend": "legacy"} for entry in entries]
    diagnostic.update({
        "backend": "legacy",
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
        "backend": "legacy",
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
    return ({**entry, "backend": "legacy"} if entry is not None and matched_task else None), diagnostic


async def fetch_remy_schedule_registry(
    client: Any,
    max_chars: int = 500,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read the current /scheduled registry without consulting legacy Tasks.

    Its IDs/status enum belong to Remy schedules and are never verification
    evidence for the legacy daily-create/delete mutations below.
    """
    contract = get_contract("scheduled.remy_registry")
    response = await execute_contract(client, contract.key)
    parsed, diagnostic = _parse_observation(response, contract.key, max_chars=max_chars)
    entries = parsed.value if _valid_read_back(diagnostic) and isinstance(parsed.value, list) else []
    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), contract.rpc_id)
    body = envelope.bodies[0] if envelope.single_body else None
    # L6d uses an empty request and reads all schedules from field 1; fields
    # 2/3 are metadata, not a cursor. Unknown fields cannot prove coverage.
    known_shape = isinstance(body, list) and not any(value is not None for value in body[3:])
    complete = bool(_valid_read_back(diagnostic) and known_shape
                    and len({item["id"] for item in entries}) == len(entries))
    diagnostic.update({"backend": "remy", "raw_entry_count": len(entries), "complete": complete})
    return entries, diagnostic


async def fetch_remy_schedule_by_id(
    client: Any,
    action_id: str,
    max_chars: int = 500,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read one current schedule by exact ID; do not generate or mutate it."""
    contract = get_contract("scheduled.remy_get")
    response = await execute_contract(client, contract.key, action_id=action_id)
    parsed, diagnostic = _parse_observation(response, contract.key, max_chars=max_chars, expected_id=action_id)
    entry = parsed.value if isinstance(parsed.value, dict) else None
    matched = bool(_valid_read_back(diagnostic) and entry is not None and entry.get("id") == action_id)
    diagnostic.update({"backend": "remy", "matched_task": matched})
    return (entry if matched else None), diagnostic


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
        and envelope.parsed and envelope.reject_code is None and envelope.single_body and bool(bodies)
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



# Current Remy mutations stay separate from legacy Tasks and their status enum.
_REMY_TRIGGER_FIELDS = (3, 4, 5, 15, 17, 18, 19, 20)


def _remy_integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid schedule integer.")
    return value


def _remy_trigger(trigger: dict[str, Any]) -> tuple[int, list[Any]]:
    if not isinstance(trigger, dict):
        raise ValueError("A native schedule trigger is required.")
    kind = trigger.get("type")
    fields = {"daily": {"hour", "minute"}, "weekly": {"hour", "minute", "days_of_week"},
              "monthly": {"hour", "minute", "days_of_month"}, "yearly": {"hour", "minute", "month", "day"},
              "hourly": {"minute", "interval"}, "one_off": {"unix_seconds"}}
    if kind not in fields or set(trigger) - ({"type"} | fields[kind]):
        raise ValueError("Unsupported native schedule trigger or fields.")
    if kind == "hourly":
        minute = trigger.get("minute", trigger.get("interval", 0))
        if "minute" in trigger and "interval" in trigger and trigger["minute"] != trigger["interval"]:
            raise ValueError("Conflicting hourly minute offsets.")
        return 17, [_remy_integer(minute, 0, 59)]
    if kind == "one_off":
        return 20, [[str(_remy_integer(trigger.get("unix_seconds"), 1, 253402300799)), 0]]
    time = [_remy_integer(trigger.get("hour"), 0, 23), _remy_integer(trigger.get("minute", 0), 0, 59)]
    if kind == "daily":
        return 3, [time]
    if kind == "yearly":
        month, day = _remy_integer(trigger.get("month"), 1, 12), _remy_integer(trigger.get("day"), 1, 31)
        date(2000, month, day)
        return 19, [[None, month, day], time]
    key, maximum, index = ("days_of_week", 7, 4) if kind == "weekly" else ("days_of_month", 31, 18)
    days = trigger.get(key)
    if not isinstance(days, list) or not days or len(set(days)) != len(days):
        raise ValueError("A nonempty unique day list is required.")
    return index, [[_remy_integer(day, 1, maximum) for day in days], time]


def _remy_changes(row: list[Any], *, title: str | None, instructions: str | None,
                  trigger: dict[str, Any] | None, timezone_name: str | None,
                  enabled: bool | None) -> list[str]:
    changes = []
    for key, value, index, maximum in (("title", title, 1, 500), ("instructions", instructions, 7, 20000)):
        if value is not None:
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError("A nonblank title/instructions value within the supported bound is required.")
            row[index] = value
            changes.append(key)
    if timezone_name is not None:
        if not isinstance(timezone_name, str) or not timezone_name:
            raise ValueError("A valid IANA timezone is required.")
        ZoneInfo(timezone_name)
        row[6] = timezone_name
        changes.append("timezone_name")
    if enabled is not None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean.")
        row[9] = 1 if enabled else 2
        changes.append("schedule_status_id")
    if trigger is not None:
        index, message = _remy_trigger(trigger)
        for field in _REMY_TRIGGER_FIELDS:
            row[field] = None
        row[index] = message
        changes.append("trigger")
    return changes


async def _read_remy_row(client: Any, action_id: str) -> tuple[list[Any] | None, dict[str, Any]]:
    response = await execute_contract(client, "scheduled.remy_get", action_id=action_id)
    parsed, diagnostic = _parse_observation(response, "scheduled.remy_get", expected_id=action_id, max_chars=20000)
    if not _valid_read_back(diagnostic) or parsed.status != "success":
        return None, diagnostic
    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), get_contract("scheduled.remy_get").rpc_id)
    return copy.deepcopy(envelope.bodies[0][0]), diagnostic


def _remy_unverified(payload: dict[str, Any], diagnostic: dict[str, Any]) -> DomainResult[dict[str, Any]]:
    return DomainResult.success(payload, operation_state=OperationState.ACCEPTED,
                                verification_status="read_back_unverified", details=diagnostic,
                                warnings=(DomainWarning("MUTATION_UNVERIFIED", "The native schedule mutation lacks independent matching read-back."),))


async def _write_remy_schedule_result(client: Any, contract_key: str, row: list[Any], changes: list[str]) -> DomainResult[dict[str, Any]]:
    action_id = row[0]
    response, failure = await _remy_mutate_once(client, contract_key, schedule=row)
    if failure is not None:
        return failure
    parsed, diagnostic = _parse_observation(response, contract_key, expected_id=action_id, max_chars=20000)
    if not _valid_read_back(diagnostic) or parsed.status != "success":
        code = DomainErrorCode.UPSTREAM_REJECTED if diagnostic.get("reject_code") is not None else DomainErrorCode.UPSTREAM_CHANGED
        return DomainResult.failure(code, "The native schedule mutation acknowledgement could not be verified.", details=diagnostic)
    action_id = parsed.value["id"]
    payload = {"backend": "remy", "id": action_id, "accepted": True, "verified": False}
    try:
        observed, read_diagnostic = await _read_remy_row(client, action_id)
        diagnostic["read_back"] = read_diagnostic
        if observed is not None:
            expected_row = copy.deepcopy(row)
            expected_row[0] = action_id
            expected = parse_contract_body("scheduled.remy_get", [expected_row], max_chars=20000).value
            actual = parse_contract_body("scheduled.remy_get", [observed], max_chars=20000).value
            indexes = {"title": 1, "instructions": 7, "timezone_name": 6, "schedule_status_id": 9,
                       "context": 8, "source_chat_id": 11, "project_id": 26}
            # Update submits the whole message. Its omitted business fields
            # must survive independently, including source/project membership.
            fields = list(dict.fromkeys([*changes, *indexes, "trigger"])) if contract_key == "scheduled.remy_update" else changes

            def field_value(value: list[Any], key: str) -> Any:
                index = indexes[key]
                item = value[index] if len(value) > index else None
                return (item or "") if key != "schedule_status_id" else (0 if item is None else item)

            mismatched = [key for key in fields if (
                expected[key] != actual[key] if key == "trigger" else field_value(expected_row, key) != field_value(observed, key)
            )]
            if not mismatched:
                payload.update(verified=True, item=actual)
                return DomainResult.success(payload, verification_status="verified_updated" if row[0] else "verified_created", details=diagnostic)
            payload["mismatched_fields"] = mismatched
    except Exception as error:
        diagnostic["read_back_error_type"] = type(error).__name__
    return _remy_unverified(payload, diagnostic)


def _remy_input_failure() -> DomainResult[dict[str, Any]]:
    return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Provide a native schedule ID and supported schedule fields/trigger.",
                                verification_status="input_rejected")


async def _remy_mutate_once(client: Any, key: str, **arguments: Any) -> tuple[Any, DomainResult[dict[str, Any]] | None]:
    if not hasattr(client, "_batch_execute"):
        return None, DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Native schedule mutations are unavailable.")
    try:
        return await execute_contract(client, key, **arguments), None
    except Exception as error:
        result = result_from_exception(error, logger=logger, operation=key)
        assert result.error is not None
        action_id = arguments.get("action_id") or (arguments.get("schedule") or [None])[0]
        return None, replace(result, data={"backend": "remy", "id": action_id, "accepted": None, "verified": False},
                             error=replace(result.error, retryable=False,
                                           suggested_action="Read the native schedule registry/by-ID state; do not automatically repeat this mutation."))


async def create_remy_schedule_result(
    client: Any, *, title: str, instructions: str, trigger: dict[str, Any],
    timezone_name: str = "UTC", enabled: bool = True,
) -> DomainResult[dict[str, Any]]:
    """Create one current Remy schedule, then independently read its assigned ID."""
    row: list[Any] = [None] * 27
    row[24] = 1  # x9QgSe a_l.save creates native schedules with PJ=1.
    try:
        changes = _remy_changes(row, title=title, instructions=instructions, trigger=trigger,
                                timezone_name=timezone_name, enabled=enabled)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return _remy_input_failure()
    return await _write_remy_schedule_result(client, "scheduled.remy_create", row, changes)


async def update_remy_schedule_result(
    client: Any, *, action_id: str, title: str | None = None, instructions: str | None = None,
    trigger: dict[str, Any] | None = None, timezone_name: str | None = None, enabled: bool | None = None,
) -> DomainResult[dict[str, Any]]:
    """Read/modify/write the native full message; preserve every omitted field."""
    if not isinstance(action_id, str) or not action_id.strip():
        return _remy_input_failure()
    try:
        changes = _remy_changes([None] * 27, title=title, instructions=instructions, trigger=trigger,
                                timezone_name=timezone_name, enabled=enabled)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return _remy_input_failure()
    if not changes:
        return _remy_input_failure()
    try:
        row, diagnostic = await _read_remy_row(client, action_id)
    except Exception as error:
        return result_from_exception(error, logger=logger, operation="scheduled.remy_update.pre_read")
    if row is None:
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The native schedule cannot be read before update.",
                                    verification_status="read_back_unavailable", details=diagnostic)
    row.extend([None] * max(0, 27 - len(row)))
    _remy_changes(row, title=title, instructions=instructions, trigger=trigger, timezone_name=timezone_name, enabled=enabled)
    return await _write_remy_schedule_result(client, "scheduled.remy_update", row, changes)


async def delete_remy_schedule_result(client: Any, action_id: str) -> DomainResult[dict[str, Any]]:
    """Delete one schedule and verify its absence from the complete fresh registry."""
    if not isinstance(action_id, str) or not action_id.strip():
        return _remy_input_failure()
    response, failure = await _remy_mutate_once(client, "scheduled.remy_delete", action_id=action_id)
    if failure is not None:
        return failure
    parsed, diagnostic = _parse_observation(response, "scheduled.remy_delete")
    if not _valid_read_back(diagnostic) or parsed.status != "success":
        code = DomainErrorCode.UPSTREAM_REJECTED if diagnostic.get("reject_code") is not None else DomainErrorCode.UPSTREAM_CHANGED
        return DomainResult.failure(code, "The native schedule delete acknowledgement could not be verified.", details=diagnostic)
    payload: dict[str, Any] = {"backend": "remy", "id": action_id, "accepted": True, "verified": False,
                               "by_id_observation": "unavailable"}
    registry_absent = False
    try:
        entries, registry_diagnostic = await fetch_remy_schedule_registry(client)
        diagnostic["registry"] = registry_diagnostic
        registry_absent = (registry_diagnostic.get("complete") is True
                           and not any(item.get("id") == action_id for item in entries))
    except Exception as error:
        diagnostic["registry_error_type"] = type(error).__name__
    try:
        row, read_diagnostic = await _read_remy_row(client, action_id)
        diagnostic["read_back"] = read_diagnostic
        if _valid_read_back(read_diagnostic):
            payload["by_id_observation"] = "present" if row is not None else "absent"
    except Exception as error:
        diagnostic["read_back_error_type"] = type(error).__name__
    # A rejected/unavailable exact read is not absence evidence. The current
    # UI reloads this complete registry after delete; a positive exact read
    # contradicts that absence and must keep the mutation unverified.
    if registry_absent and payload["by_id_observation"] != "present":
        payload.update(verified=True, verification_evidence="complete_registry_absence")
        return DomainResult.success(payload, verification_status="verified_deleted_registry_absence", details=diagnostic)
    return _remy_unverified(payload, diagnostic)


# Compatibility aliases.
_fetch_scheduled_registry = fetch_scheduled_registry
_fetch_scheduled_task_by_id = fetch_scheduled_task_by_id
_parse_scheduled_action_create_body = parse_scheduled_action_create_body
_parse_scheduled_action_task_entry = parse_scheduled_action_task_entry
_scheduled_daily_payload = scheduled_daily_payload
