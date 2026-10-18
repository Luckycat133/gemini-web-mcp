"""Pure parsers for observed Gemini Web RPC response shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Literal, Mapping
from urllib.parse import quote

from .rpc_contracts import RPCContract, get_contract

logger = logging.getLogger(__name__)

try:
    import gemini_webapi.utils as _upstream_utils
except ImportError:  # pragma: no cover - exercised by clean import smoke tests
    _upstream_utils = None  # type: ignore[assignment]


RPCParseStatus = Literal["success", "empty", "rejected", "changed_shape"]


@dataclass(frozen=True, slots=True)
class RPCParseResult:
    """Machine-readable outcome of a pure upstream body parser."""

    status: RPCParseStatus
    value: Any = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    reject_code: int | None = None
    raw_type: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"success", "empty"}


@dataclass(frozen=True, slots=True)
class RPCEnvelope:
    """Decoded response bodies and rejection evidence for one RPC id."""

    parsed: bool
    response_parts: int
    bodies: tuple[Any, ...]
    reject_code: int | None = None
    matching_parts: int = 0

    @property
    def single_body(self) -> bool:
        return self.matching_parts == 1 and len(self.bodies) == 1


def _nested(value: Any, path: list[int]) -> Any:
    if _upstream_utils is not None:
        return _upstream_utils.get_nested_value(value, path)
    current = value
    for index in path:
        if not isinstance(current, (list, tuple)) or index >= len(current):
            return None
        current = current[index]
    return current


def _extract_parts(response_text: str) -> list[Any]:
    if _upstream_utils is not None:
        return list(_upstream_utils.extract_json_from_response(response_text))
    clean = response_text.strip()
    if clean.startswith(")]}'"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else ""
    parsed = json.loads(clean or "[]")
    return parsed if isinstance(parsed, list) else [parsed]


def parse_rpc_envelope(response_text: str, rpc_id: str, *, expected_identifier: str | None = None) -> RPCEnvelope:
    """Decode only matching ``wrb.fr`` parts without interpreting body shape."""

    try:
        parts = _extract_parts(response_text)
    except Exception as exc:
        logger.debug("RPC response parse failed for rpcid=%s: %s", rpc_id, exc)
        return RPCEnvelope(parsed=False, response_parts=0, bodies=())

    bodies: list[Any] = []
    reject_code: int | None = None
    matching_parts = 0
    for part in parts:
        if _nested(part, [0]) != "wrb.fr" or _nested(part, [1]) != rpc_id:
            continue
        if expected_identifier is not None and _nested(part, [-1]) != expected_identifier:
            continue
        matching_parts += 1
        code = _nested(part, [5, 0])
        if isinstance(code, int):
            reject_code = code
        body = _nested(part, [2])
        if isinstance(body, str):
            try:
                bodies.append(json.loads(body))
            except json.JSONDecodeError:
                bodies.append(body)
        elif body is not None:
            bodies.append(body)
    return RPCEnvelope(
        parsed=True,
        response_parts=len(parts),
        bodies=tuple(bodies),
        reject_code=reject_code,
        matching_parts=matching_parts,
    )


def extract_rpc_bodies(response_text: str, rpc_id: str) -> list[Any]:
    return list(parse_rpc_envelope(response_text, rpc_id).bodies)


def summarize_rpc_response(response_text: str, rpc_id: str) -> dict[str, Any]:
    envelope = parse_rpc_envelope(response_text, rpc_id)
    return {
        "parsed": envelope.parsed,
        "response_parts": envelope.response_parts,
        "body_count": len(envelope.bodies),
        "reject_code": envelope.reject_code,
    }


def _format_timestamp(timestamp: object) -> str:
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _truncate(value: object, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def parse_public_link_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "id": entry[0] if len(entry) > 0 and isinstance(entry[0], str) else "",
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "disabled": bool(entry[2]) if len(entry) > 2 else False,
        "url": entry[4] if len(entry) > 4 and isinstance(entry[4], str) else "",
        "field_count": len(entry),
    }


def parse_usage_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    reset = entry[3] if len(entry) > 3 else None
    reset_timestamp = None
    reset_time = ""
    if isinstance(reset, list) and reset and isinstance(reset[0], (int, float)):
        reset_timestamp = float(reset[0]) + (float(reset[1] if len(reset) > 1 else 0) / 1e9)
        reset_time = _format_timestamp(reset_timestamp)
    return {
        "key": entry[0] if entry else None,
        "status": entry[1] if len(entry) > 1 else None,
        "tier": entry[2] if len(entry) > 2 else None,
        "reset_timestamp": reset_timestamp,
        "reset_time": reset_time,
        "limit_value": entry[4] if len(entry) > 4 else None,
        "remaining_value": entry[5] if len(entry) > 5 else None,
        "field_count": len(entry),
    }


def parse_library_capability(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    aliases = entry[0] if entry and isinstance(entry[0], list) else []
    return {
        "aliases": [alias for alias in aliases if isinstance(alias, str)],
        "name": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "description": entry[2] if len(entry) > 2 and isinstance(entry[2], str) else "",
        "details": entry[3] if len(entry) > 3 and isinstance(entry[3], str) else "",
        "field_count": len(entry),
    }


def parse_native_notebook(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    metadata = entry[1] if len(entry) > 1 and isinstance(entry[1], list) else []
    summary = entry[2] if len(entry) > 2 and isinstance(entry[2], list) else []
    project_metadata = metadata[12] if len(metadata) > 12 and isinstance(metadata[12], list) else []
    display = metadata[14] if len(metadata) > 14 and isinstance(metadata[14], list) else []
    sources = metadata[10] if len(metadata) > 10 and isinstance(metadata[10], list) else []
    source_rows = sources[1] if len(sources) > 1 and isinstance(sources[1], list) else []
    return {
        "id": entry[0] if entry and isinstance(entry[0], str) else "",
        "title": metadata[0] if metadata and isinstance(metadata[0], str) else "",
        "description": metadata[1] if len(metadata) > 1 and isinstance(metadata[1], str) else "",
        "summary": summary[0] if summary and isinstance(summary[0], str) else "",
        "emoji": display[0] if display and isinstance(display[0], str) else "",
        "source_count": len(source_rows),
        "project_type": project_metadata[0] if project_metadata else None,
        "project_subtype": project_metadata[4] if len(project_metadata) > 4 else None,
        "pinned": entry[3] if len(entry) > 3 and isinstance(entry[3], bool) else None,
        "field_count": len(entry),
    }


def parse_notebook_category(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "subtype": entry[0] if entry else None,
        "label": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
    }


def _conversation_project_id(entry: Any) -> str:
    if not isinstance(entry, list):
        return ""
    bot_id = entry[7] if len(entry) > 7 and isinstance(entry[7], str) else ""
    project_metadata = entry[13] if len(entry) > 13 and isinstance(entry[13], list) else None
    return bot_id if bot_id and project_metadata is not None else ""


def parse_conversation_metadata(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    timestamp = None
    value = entry[5] if len(entry) > 5 else None
    if isinstance(value, list) and value and isinstance(value[0], (int, float)):
        timestamp = float(value[0]) + (float(value[1] if len(value) > 1 else 0) / 1e9)
    return {
        "id": entry[0] if entry and isinstance(entry[0], str) else "",
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "is_pinned": bool(entry[2]) if len(entry) > 2 and entry[2] is not None else False,
        "timestamp": timestamp,
        "time": _format_timestamp(timestamp),
        "project_id": _conversation_project_id(entry),
        "bot_id": entry[7] if len(entry) > 7 and isinstance(entry[7], str) else "",
        "field_count": len(entry),
    }


def parse_remy_goal_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    created_timestamp = None
    created = entry[16] if len(entry) > 16 else None
    if isinstance(created, list) and created and isinstance(created[0], (int, float)):
        created_timestamp = float(created[0]) + (float(created[1] if len(created) > 1 else 0) / 1e9)
    updated_timestamp = None
    updated = entry[17] if len(entry) > 17 else None
    if isinstance(updated, list) and updated and isinstance(updated[0], (int, float)):
        updated_timestamp = float(updated[0]) + (float(updated[1] if len(updated) > 1 else 0) / 1e9)
    return {
        "id": entry[13] if len(entry) > 13 and isinstance(entry[13], str) else "",
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "description": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "is_pinned": bool(entry[19]) if len(entry) > 19 and entry[19] is not None else False,
        "status": entry[2] if len(entry) > 2 else None,
        "channel": entry[4] if len(entry) > 4 and isinstance(entry[4], str) else "",
        "created_timestamp": created_timestamp,
        "created_time": _format_timestamp(created_timestamp),
        "updated_timestamp": updated_timestamp,
        "updated_time": _format_timestamp(updated_timestamp),
        "field_count": len(entry),
    }


def parse_scheduled_action_create_body(body: Any) -> dict[str, Any]:
    if not isinstance(body, list):
        return {"raw_type": type(body).__name__}
    task_id = body[0] if body and isinstance(body[0], str) else ""
    details = body[1] if len(body) > 1 and isinstance(body[1], list) else []
    summary = details[1] if len(details) > 1 and isinstance(details[1], list) else []
    title = instructions = schedule_label = ""
    if summary and isinstance(summary[0], list):
        row = summary[0]
        instructions = row[0] if row and isinstance(row[0], str) else ""
        schedule_label = row[1] if len(row) > 1 and isinstance(row[1], str) else ""
        title = row[2] if len(row) > 2 and isinstance(row[2], str) else ""
    enabled = None
    if len(details) > 5 and isinstance(details[5], list) and details[5]:
        enabled = details[5][0] if isinstance(details[5][0], bool) else None
    return {
        "id": task_id,
        "title": title,
        "instructions": instructions,
        "schedule_label": schedule_label,
        "enabled": enabled,
        "field_count": len(body),
    }


SCHEDULED_TASK_STATES = {
    1: "created",
    3: "running",
    4: "paused",
    5: "completed",
    6: "deleted",
    7: "error",
}


def scheduled_task_state(metadata: Any) -> tuple[int | None, str]:
    if not isinstance(metadata, list):
        return None, ""
    if len(metadata) > 0 and metadata[0] is not None:
        state_id = 1
    elif len(metadata) > 1 and metadata[1] is not None:
        state_id = 3
    elif len(metadata) > 4 and metadata[4] is not None:
        state_id = 4
    elif len(metadata) > 2 and metadata[2] is not None:
        state_id = 5
    elif len(metadata) > 3 and metadata[3] is not None:
        state_id = 6
    else:
        state_id = None
    return state_id, SCHEDULED_TASK_STATES.get(state_id, "") if state_id is not None else ""


def parse_scheduled_action_task_entry(entry: Any, max_chars: int = 500) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    task_id = entry[0] if entry and isinstance(entry[0], str) else ""
    details = entry[1] if len(entry) > 1 and isinstance(entry[1], list) else []
    metadata = entry[2] if len(entry) > 2 and isinstance(entry[2], list) else []
    row = details[0] if details and isinstance(details[0], list) else []
    instructions = row[0] if row and isinstance(row[0], str) else ""
    schedule_label = row[1] if len(row) > 1 and isinstance(row[1], str) else ""
    title = row[2] if len(row) > 2 and isinstance(row[2], str) else ""
    schedule = details[2] if len(details) > 2 and isinstance(details[2], list) else []
    schedule_rule = schedule[0] if schedule and isinstance(schedule[0], list) else []
    hour = None
    if schedule_rule and isinstance(schedule_rule[0], list) and schedule_rule[0]:
        hour_row = schedule_rule[0][0]
        if isinstance(hour_row, list) and hour_row and isinstance(hour_row[0], int):
            hour = hour_row[0]
    timezone_name = ""
    if len(schedule_rule) > 7 and isinstance(schedule_rule[7], list) and schedule_rule[7]:
        timezone_name = schedule_rule[7][0] if isinstance(schedule_rule[7][0], str) else ""
    source = details[3] if len(details) > 3 and isinstance(details[3], list) else []
    enabled_flags = details[5] if len(details) > 5 and isinstance(details[5], list) else []
    enabled = enabled_flags[0] if enabled_flags and isinstance(enabled_flags[0], bool) else None
    created_timestamp = None
    created_at = metadata[5] if len(metadata) > 5 else None
    if isinstance(created_at, list) and created_at and isinstance(created_at[0], (int, float)):
        created_timestamp = float(created_at[0]) + (float(created_at[1] if len(created_at) > 1 else 0) / 1e9)
    updated_timestamp = None
    updated_at = metadata[6] if len(metadata) > 6 else None
    if isinstance(updated_at, list) and updated_at and isinstance(updated_at[0], (int, float)):
        updated_timestamp = float(updated_at[0]) + (float(updated_at[1] if len(updated_at) > 1 else 0) / 1e9)
    task_state_id, task_state = scheduled_task_state(metadata)
    return {
        "id": task_id,
        "title": _truncate(title, max_chars),
        "instructions": _truncate(instructions, max_chars),
        "schedule_label": _truncate(schedule_label, max_chars),
        "enabled": enabled,
        "task_state_id": task_state_id,
        "task_state": task_state,
        "is_deleted": task_state_id == 6,
        "hour": hour,
        "timezone_name": timezone_name,
        "source_chat_id": source[0] if source and isinstance(source[0], str) else "",
        "created_timestamp": created_timestamp,
        "created_time": _format_timestamp(created_timestamp),
        "updated_timestamp": updated_timestamp,
        "updated_time": _format_timestamp(updated_timestamp),
        "metadata_field_count": len(metadata),
        "field_count": len(entry),
    }


def parse_tool_mode_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "mode_id": entry[0] if entry else None,
        "available": entry[1] if len(entry) > 1 and isinstance(entry[1], bool) else None,
        "quota_value": entry[2] if len(entry) > 2 else None,
        "used_value": entry[3] if len(entry) > 3 else None,
        "reset_or_extra": entry[4] if len(entry) > 4 else None,
        "state": entry[5] if len(entry) > 5 else None,
        "field_count": len(entry),
    }


def _result_for_items(
    body: Any, index: int, item_parser: Callable[[Any], Any], *, require_id: bool = False,
    item_validator: Callable[[Any], bool] | None = None,
) -> RPCParseResult:
    if not isinstance(body, list) or len(body) <= index or not isinstance(body[index], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    values = []
    for row_index, item in enumerate(body[index]):
        if not isinstance(item, list) or (item_validator is not None and not item_validator(item)):
            return RPCParseResult("changed_shape", warnings=(f"invalid_entry:{row_index}",), raw_type=type(item).__name__)
        try:
            value = item_parser(item)
        except (TypeError, ValueError, IndexError, OverflowError):
            return RPCParseResult("changed_shape", warnings=(f"invalid_entry:{row_index}",), raw_type=type(item).__name__)
        if require_id and (not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].strip()):
            return RPCParseResult("changed_shape", warnings=(f"missing_entry_id:{row_index}",), raw_type=type(item).__name__)
        values.append(value)
    return RPCParseResult("success" if values else "empty", value=values)


def parse_conversation_page(body: Any, **_: Any) -> RPCParseResult:
    if isinstance(body, list) and len(body) > 1 and body[1] is not None and not isinstance(body[1], str):
        return RPCParseResult("changed_shape", warnings=("invalid_continuation",), raw_type=type(body[1]).__name__)
    return _result_for_items(
        body, 2, parse_conversation_metadata, require_id=True,
        item_validator=lambda row: (
            (len(row) < 2 or row[1] is None or isinstance(row[1], str))
            and (len(row) < 3 or row[2] is None or isinstance(row[2], bool))
        ),
    )


def parse_remy_goals_page(body: Any, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 0, parse_remy_goal_entry)


def parse_notebook_list(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list) or len(body) <= 2 or not isinstance(body[2], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    parsed_items = _result_for_items(
        body, 2, parse_native_notebook, require_id=True,
        item_validator=lambda row: len(row) >= 2 and isinstance(row[1], list),
    )
    if not parsed_items.ok:
        return parsed_items
    notebooks = parsed_items.value
    if len(body) > 3 and body[3] is not None and not isinstance(body[3], list):
        return RPCParseResult("changed_shape", warnings=("invalid_categories",))
    if len(body) > 3 and isinstance(body[3], list) and any(not isinstance(item, list) for item in body[3]):
        return RPCParseResult("changed_shape", warnings=("invalid_category_entry",))
    categories = (
        [parse_notebook_category(item) for item in body[3]] if len(body) > 3 and isinstance(body[3], list) else []
    )
    return RPCParseResult(
        "success" if notebooks else "empty",
        value={"items": notebooks, "categories": categories},
    )


def parse_custom_gems(body: Any, **_: Any) -> RPCParseResult:
    """Parse the shared system/custom registry shape in supported SDK versions."""
    def item(row: list[Any]) -> dict[str, str]:
        return {"id": row[0], "name": row[1][0], "description": row[1][1] or "",
                "instructions": row[2][0] if row[2] else ""}

    def valid(row: Any) -> bool:
        return bool(isinstance(row, list) and len(row) >= 3 and isinstance(row[0], str)
                    and isinstance(row[1], list) and len(row[1]) >= 2 and isinstance(row[1][0], str)
                    and (row[1][1] is None or isinstance(row[1][1], str))
                    and (row[2] is None or row[2] == [] or
                         (isinstance(row[2], list) and row[2] and isinstance(row[2][0], str))))

    return _result_for_items(body, 2, item, require_id=True, item_validator=valid)


def parse_notebook_move(body: Any, *, expected_id: str | None = None, **_: Any) -> RPCParseResult:
    if body == [] or body == [None, None]:
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list) or len(body) <= 1 or not isinstance(body[1], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    value = parse_conversation_metadata(body[1])
    if not value.get("id") or (expected_id is not None and value.get("id") != expected_id):
        return RPCParseResult("changed_shape", value=value, warnings=("returned_id_mismatch",))
    return RPCParseResult("success", value=value)


def parse_conversation_update(body: Any, *, expected_id: str | None = None, **_: Any) -> RPCParseResult:
    """The updated metadata is response field 2; it is acknowledgement, not read-back."""
    result = parse_notebook_move(body, expected_id=expected_id)
    if result.status != "success":
        return result
    row = body[1]
    if ((len(row) > 1 and row[1] is not None and not isinstance(row[1], str))
            or (len(row) > 2 and row[2] is not None and not isinstance(row[2], bool))):
        return RPCParseResult("changed_shape", warnings=("invalid_metadata_fields",))
    return result


def parse_scheduled_registry(body: Any, *, max_chars: int = 500, **_: Any) -> RPCParseResult:
    # A matching XPSWpd response can serialize an empty protobuf as []. This
    # says nothing about the separate current Remy schedule registry.
    if body == []:
        return RPCParseResult("empty", value=[])
    return _result_for_items(
        body, 0, lambda item: parse_scheduled_action_task_entry(item, max_chars),
        require_id=True, item_validator=_valid_scheduled_entry,
    )


def _remy_schedule_trigger(row: list[Any], max_chars: int) -> dict[str, Any]:
    fields = ((3, "daily"), (4, "weekly"), (5, "event"), (15, "gmail"),
              (17, "hourly"), (18, "monthly"), (19, "yearly"), (20, "one_off"))
    populated = [(index, name) for index, name in fields if len(row) > index and row[index] is not None]
    if not populated:
        return {"type": "unspecified"}
    if len(populated) != 1:
        raise ValueError("Multiple schedule triggers")
    index, name = populated[0]
    message = row[index]
    if not isinstance(message, list):
        raise ValueError("Invalid schedule trigger")
    trigger: dict[str, Any] = {"type": name}

    def integer(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        if type(value) is not int:
            raise ValueError("Invalid schedule integer")
        return value

    if name in {"daily", "weekly", "monthly", "yearly"}:
        time_index = 0 if name == "daily" else 1
        value = message[time_index] if len(message) > time_index else None
        if value is not None:
            if not isinstance(value, list):
                raise ValueError("Invalid schedule time")
            hour = integer(value[0] if value else None)
            minute = integer(value[1] if len(value) > 1 else None)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("Invalid schedule time")
            trigger.update(hour=hour, minute=minute)
    if name in {"weekly", "monthly"}:
        days = message[0] if message else None
        if days is not None and (not isinstance(days, list) or any(type(day) is not int for day in days)):
            raise ValueError("Invalid schedule days")
        trigger["days_of_week" if name == "weekly" else "days_of_month"] = days or []
    elif name == "hourly":
        trigger["interval"] = integer(message[0] if message else None)
    elif name == "yearly":
        date = message[0] if message else None
        if date is not None:
            if not isinstance(date, list):
                raise ValueError("Invalid schedule date")
            trigger.update(month=integer(date[1] if len(date) > 1 else None),
                           day=integer(date[2] if len(date) > 2 else None))
    elif name == "one_off":
        timestamp = message[0] if message else None
        if timestamp is not None:
            if not isinstance(timestamp, list) or not timestamp:
                raise ValueError("Invalid schedule timestamp")
            seconds = timestamp[0]
            if isinstance(seconds, str) and seconds.lstrip("-").isdigit():
                seconds = int(seconds)
            trigger["unix_seconds"] = integer(seconds)
    elif name in {"event", "gmail"}:
        condition_index = 1 if name == "gmail" else 0
        condition = message[condition_index] if len(message) > condition_index else None
        if condition is not None and not isinstance(condition, str):
            raise ValueError("Invalid schedule condition")
        trigger["condition"] = _truncate(condition, max_chars)
    return trigger


def _remy_schedule_entry(row: Any, max_chars: int) -> dict[str, Any]:
    if not isinstance(row, list) or len(row) < 10 or not isinstance(row[0], str) or not row[0].strip():
        raise ValueError("Invalid Remy schedule")
    for index in (1, 6, 7, 8, 11, 26):
        if len(row) > index and row[index] is not None and not isinstance(row[index], str):
            raise ValueError("Invalid Remy schedule text")
    status = row[9] if row[9] is not None else 0
    if type(status) is not int or status not in range(4):
        raise ValueError("Invalid Remy schedule status")
    trigger = _remy_schedule_trigger(row, max_chars)
    state = ("unspecified", "active", "paused", "completed")[status]
    return {
        "backend": "remy", "id": row[0], "title": _truncate(row[1], max_chars),
        "instructions": _truncate(row[7], max_chars), "timezone_name": row[6] or "",
        "enabled": True if status == 1 else False if status in {2, 3} else None,
        # Remy 3 means completed, while legacy Task 3 means running. Keep the
        # native enum separate from legacy task_state_id/deleted-state evidence.
        "schedule_status_id": status, "task_state_id": None, "task_state": state,
        "trigger": trigger, "schedule_label": trigger["type"],
        "hour": trigger.get("hour"), "minute": trigger.get("minute"),
        "source_chat_id": row[11] or "" if len(row) > 11 else "",
        "project_id": row[26] or "" if len(row) > 26 else "",
        "field_count": len(row),
    }


def parse_remy_schedule_registry(body: Any, *, max_chars: int = 500, **_: Any) -> RPCParseResult:
    if body == []:
        return RPCParseResult("empty", value=[])
    if not isinstance(body, list) or not body:
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    if ((len(body) > 1 and body[1] is not None and type(body[1]) is not bool)
            or (len(body) > 2 and body[2] is not None and (type(body[2]) is not int or body[2] not in range(3)))):
        return RPCParseResult("changed_shape", warnings=("invalid_registry_metadata",))
    rows = body[0] if body[0] is not None else []
    return _result_for_items([rows], 0, lambda row: _remy_schedule_entry(row, max_chars), require_id=True)


def parse_remy_schedule_get(
    body: Any, *, expected_id: str | None = None, max_chars: int = 500, **_: Any,
) -> RPCParseResult:
    if body == [] or body == [None]:
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list) or not body:
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    try:
        item = _remy_schedule_entry(body[0], max_chars)
    except (TypeError, ValueError, IndexError):
        return RPCParseResult("changed_shape", warnings=("invalid_schedule",))
    if expected_id is not None and item["id"] != expected_id:
        return RPCParseResult("changed_shape", warnings=("returned_id_mismatch",))
    return RPCParseResult("success", value=item)


def parse_task_goal(body: Any, *, expected_id: str | None = None, **_: Any) -> RPCParseResult:
    """Read only Task identity/status, excluding descriptions and other goal text."""
    if body == [] or body == [None]:
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    goal = body[0]
    if len(goal) < 14:
        return RPCParseResult("changed_shape", warnings=("missing_goal_fields",))
    chat_id, status = goal[13], goal[2]
    if not isinstance(chat_id, str) or not chat_id.strip() or (expected_id is not None and chat_id != expected_id):
        return RPCParseResult("changed_shape", warnings=("returned_id_mismatch",))
    if type(status) is not int or status not in range(9):
        return RPCParseResult("changed_shape", warnings=("invalid_task_status",))
    names = ("unspecified", "pending", "running", "done", "failed", "blocked", "monitoring", "scheduled", "cancelled")
    value = {"chat_id": chat_id, "status": names[status], "status_id": status, "terminal": status in {3, 4, 8}}
    if isinstance(goal[0], str) and goal[0].strip():
        value["goal_id"] = goal[0]
    return RPCParseResult("success", value=value)


def _valid_scheduled_entry(entry: Any) -> bool:
    return (
        isinstance(entry, list) and len(entry) >= 3 and isinstance(entry[0], str) and bool(entry[0].strip())
        and isinstance(entry[1], list) and isinstance(entry[2], list)
        and all(marker is None or isinstance(marker, list) for marker in entry[2][:5])
    )


def _scheduled_entry_from_body(body: Any) -> Any:
    if not isinstance(body, list) or not body:
        return None
    first = body[0]
    if isinstance(first, list) and first and isinstance(first[0], str):
        return first if len(body) == 1 else None
    if isinstance(first, str):
        return body
    return None


def parse_scheduled_get(body: Any, *, max_chars: int = 500, expected_id: str | None = None, **_: Any) -> RPCParseResult:
    if body == []:
        return RPCParseResult("empty", value=None)
    entry = _scheduled_entry_from_body(body)
    if not _valid_scheduled_entry(entry):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    value = parse_scheduled_action_task_entry(entry, max_chars)
    if not isinstance(value.get("id"), str) or not value["id"].strip():
        return RPCParseResult("changed_shape", value=value, warnings=("missing_entry_id",))
    if expected_id and value.get("id") != expected_id:
        return RPCParseResult("changed_shape", value=value, warnings=("returned_id_mismatch",))
    return RPCParseResult("success", value=value)


def parse_scheduled_create(body: Any, **_: Any) -> RPCParseResult:
    if body == []:
        return RPCParseResult("empty", value=None)
    candidate = body[0] if isinstance(body, list) and body and isinstance(body[0], list) else body
    if not isinstance(candidate, list) or len(candidate) < 2 or not isinstance(candidate[1], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    value = parse_scheduled_action_create_body(candidate)
    if not value.get("id"):
        return RPCParseResult("changed_shape", value=value, raw_type=type(body).__name__)
    return RPCParseResult("success", value=value)


def parse_public_links(body: Any, **_: Any) -> RPCParseResult:
    """ListSharedConversations field 1 contains rows, not the legacy link index."""
    if body == []:
        return RPCParseResult("empty", value=[])

    def valid_row(row: Any) -> bool:
        if (not isinstance(row, list) or len(row) < 4 or not isinstance(row[2], list)
                or not isinstance(row[3], str) or not row[3].strip()):
            return False
        metadata = row[2]
        return ((not metadata or metadata[0] is None or isinstance(metadata[0], bool))
                and (len(metadata) < 2 or metadata[1] is None or isinstance(metadata[1], str)))

    def parse_row(row: list[Any]) -> dict[str, Any]:
        token, metadata = row[3], row[2]
        return {
            "id": token,
            "title": metadata[1] if len(metadata) > 1 and isinstance(metadata[1], str) else "",
            "disabled": None,  # The native listing does not expose this legacy property.
            "url": "https://gemini.google.com/share/" + quote(token, safe=""),
            "field_count": len(row),
        }

    return _result_for_items(body, 0, parse_row, require_id=True, item_validator=valid_row)


def parse_usage_entries(body: Any, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 0, parse_usage_entry)


def parse_library_capabilities(body: Any, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 0, parse_library_capability)


def parse_tool_modes(body: Any, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 1, parse_tool_mode_entry)


def parse_opaque(body: Any, **_: Any) -> RPCParseResult:
    if body in (None, [], ""):
        return RPCParseResult("empty", value=body)
    if not isinstance(body, list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    return RPCParseResult("success", value=body)


def parse_mutation_ack(body: Any, **_: Any) -> RPCParseResult:
    if body in (None, [], ""):
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    return RPCParseResult("success", value=body)


def parse_music_chat(body: Any, **_: Any) -> RPCParseResult:
    """Extract generated music cards from one read-back chat body."""
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    turns = body[0]
    media: list[dict[str, str]] = []
    for turn in turns:
        if not isinstance(turn, list):
            return RPCParseResult("changed_shape", raw_type=type(turn).__name__)
        rid = _nested(turn, [0, 1])
        candidates = _nested(turn, [3, 0])
        if candidates is None:
            continue
        if not isinstance(candidates, list):
            return RPCParseResult("changed_shape", raw_type=type(candidates).__name__)
        for candidate in candidates:
            if not isinstance(candidate, list):
                return RPCParseResult("changed_shape", raw_type=type(candidate).__name__)
            rcid = _nested(candidate, [0])
            card_group = _nested(candidate, [12, 0])
            if card_group is None:
                continue
            if not isinstance(card_group, dict):
                return RPCParseResult("changed_shape", raw_type=type(card_group).__name__)
            cards = card_group.get("87", [])
            if not isinstance(cards, list):
                return RPCParseResult("changed_shape", raw_type=type(cards).__name__)
            for card in cards:
                title = _nested(card, [1, 2])
                url = _nested(card, [1, 7, 1])
                if not isinstance(url, str) or not url.strip():
                    return RPCParseResult("changed_shape", raw_type=type(url).__name__)
                media.append(
                    {
                        "title": title if isinstance(title, str) else "[Media]",
                        "url": url.strip(),
                        "rid": rid if isinstance(rid, str) else "",
                        "rcid": rcid if isinstance(rcid, str) else "",
                    }
                )
    return RPCParseResult("success" if media else "empty", value=media)


PARSER_FUNCTIONS: Mapping[str, Callable[..., RPCParseResult]] = {
    "task_goal": parse_task_goal,
    "conversation_page": parse_conversation_page,
    "remy_goals_page": parse_remy_goals_page,
    "notebook_list": parse_notebook_list,
    "notebook_move": parse_notebook_move,
    "conversation_update": parse_conversation_update,
    "custom_gems": parse_custom_gems,
    "scheduled_registry": parse_scheduled_registry,
    "remy_schedule_registry": parse_remy_schedule_registry,
    "remy_schedule_get": parse_remy_schedule_get,
    "scheduled_get": parse_scheduled_get,
    "scheduled_create": parse_scheduled_create,
    "public_links": parse_public_links,
    "usage_entries": parse_usage_entries,
    "library_capabilities": parse_library_capabilities,
    "tool_modes": parse_tool_modes,
    "opaque": parse_opaque,
    "mutation_ack": parse_mutation_ack,
    "music_chat": parse_music_chat,
}


def parse_contract_body(
    contract: str | RPCContract,
    body: Any,
    *,
    reject_code: int | None = None,
    **arguments: Any,
) -> RPCParseResult:
    resolved = get_contract(contract) if isinstance(contract, str) else contract
    if reject_code is not None:
        return RPCParseResult("rejected", reject_code=reject_code, raw_type=type(body).__name__)
    parser = PARSER_FUNCTIONS[resolved.parser]
    return parser(body, **arguments)
