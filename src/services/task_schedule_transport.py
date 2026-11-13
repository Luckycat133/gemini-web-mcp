"""Single-dispatch Remy schedule transport over registered Web contracts."""

from __future__ import annotations

from typing import Any

from ..domain.task_schedules import TaskSchedule, TaskSchedulePage, TaskScheduleTransportError, build_update_spec, validate_create_spec
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .deadlines import await_before_deadline


class ScheduleTransportError(TaskScheduleTransportError):
    """Unknown delivery is distinct from a rejected local preflight."""

    code = "UPSTREAM_CHANGED"

    def __init__(self, message: str, *, mutation_sent: bool | None = False):
        super().__init__(message)
        self.mutation_sent = mutation_sent


def _account(owner: str | None, *, mutation_sent: bool | None = False) -> None:
    if not owner or current_account_key() != owner:
        raise ScheduleTransportError("The schedule account changed or is unavailable.", mutation_sent=mutation_sent)


async def _request(client: Any, key: str, **arguments: Any) -> tuple[Any, Any]:
    contract = get_contract(key)
    owner = current_account_key()
    _account(owner)
    try:
        contract.build_payload(**arguments)
    except Exception:
        raise ScheduleTransportError("Schedule validation failed before dispatch.") from None
    sent: bool | None = None if contract.mode == "mutation" else False
    try:
        response = await await_before_deadline(execute_contract(
            client, key, retry_count=0, **arguments,
        ), timeout=30)
    except Exception:
        raise ScheduleTransportError("Schedule transport did not return an accepted response.", mutation_sent=sent) from None
    _account(owner, mutation_sent=sent)
    if getattr(response, "status_code", None) != 200:
        raise ScheduleTransportError("The schedule HTTP response was not accepted.", mutation_sent=sent)
    envelope = parse_rpc_envelope(getattr(response, "text", "") or "", contract.rpc_id)
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise ScheduleTransportError("The schedule response envelope was not accepted.", mutation_sent=sent)
    body = envelope.bodies[0]
    parsed = parse_contract_body(contract, body)
    if not parsed.ok:
        raise ScheduleTransportError("The schedule response shape changed.", mutation_sent=sent)
    return parsed.value, body


async def _snapshot(client: Any, schedule_id: str) -> tuple[TaskSchedule, list[Any]]:
    value, body = await _request(client, "task_schedules.read", schedule_id=schedule_id)
    # Empty ReadRemySchedule responses have not been proved to mean absence.
    if not isinstance(value, dict) or value.get("schedule_id") != schedule_id:
        raise ScheduleTransportError("A fresh exact schedule was not observed.")
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        raise ScheduleTransportError("The schedule snapshot is unavailable.")
    return TaskSchedule(**value), body[0]


async def list_task_schedules(client: Any) -> TaskSchedulePage:
    value, _body = await _request(client, "task_schedules.list")
    if not isinstance(value, dict) or not isinstance(value.get("schedules"), list):
        raise ScheduleTransportError("A complete schedule listing was not observed.")
    return TaskSchedulePage(tuple(TaskSchedule(**item) for item in value["schedules"]),
                            active_limit_reached=value.get("active_limit_reached"))


async def get_task_schedule(client: Any, schedule_id: str) -> TaskSchedule:
    return (await _snapshot(client, schedule_id))[0]


async def create_task_schedule(client: Any, spec: dict[str, Any]) -> TaskSchedule | None:
    value, _body = await _request(client, "task_schedules.create", spec=spec)
    return TaskSchedule(**value) if isinstance(value, dict) else None


async def update_task_schedule(client: Any, schedule_id: str, patch: dict[str, Any],
                               *, expected_before: TaskSchedule) -> TaskSchedule | None:
    owner = current_account_key()
    fresh, raw = await _snapshot(client, schedule_id)
    _account(owner)
    if (fresh.schedule_id != expected_before.schedule_id or fresh.kind != 1
            or not fresh.editable or not fresh.revision or fresh.revision != expected_before.revision):
        raise ScheduleTransportError("The schedule changed before dispatch; read it again before editing.")
    try:
        normalized = (patch if set(patch) == {"status"}
                      else validate_create_spec(build_update_spec(fresh, patch)))
    except Exception:
        raise ScheduleTransportError("The schedule patch was rejected before dispatch.") from None
    value, _body = await _request(client, "task_schedules.update", raw_schedule=raw, patch=normalized)
    return TaskSchedule(**value) if isinstance(value, dict) else None


async def delete_task_schedule(client: Any, schedule_id: str) -> None:
    await _request(client, "task_schedules.delete", schedule_id=schedule_id)
