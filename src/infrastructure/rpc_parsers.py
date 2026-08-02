"""Pure parsers for observed Gemini Web RPC response shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Literal, Mapping

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


def parse_rpc_envelope(response_text: str, rpc_id: str) -> RPCEnvelope:
    """Decode only matching ``wrb.fr`` parts without interpreting body shape."""

    try:
        parts = _extract_parts(response_text)
    except Exception as exc:
        logger.debug("RPC response parse failed for rpcid=%s: %s", rpc_id, exc)
        return RPCEnvelope(parsed=False, response_parts=0, bodies=())

    bodies: list[Any] = []
    reject_code: int | None = None
    for part in parts:
        if _nested(part, [0]) != "wrb.fr" or _nested(part, [1]) != rpc_id:
            continue
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


def _result_for_items(body: Any, index: int, item_parser: Callable[[Any], Any]) -> RPCParseResult:
    if not isinstance(body, list) or len(body) <= index or not isinstance(body[index], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    values = [item_parser(item) for item in body[index]]
    return RPCParseResult("success" if values else "empty", value=values)


def parse_conversation_page(body: Any, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 2, parse_conversation_metadata)


def parse_remy_goals_page(body: Any, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 0, parse_remy_goal_entry)


def parse_notebook_list(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list) or len(body) <= 2 or not isinstance(body[2], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    notebooks = [parse_native_notebook(item) for item in body[2]]
    categories = (
        [parse_notebook_category(item) for item in body[3]] if len(body) > 3 and isinstance(body[3], list) else []
    )
    return RPCParseResult(
        "success" if notebooks else "empty",
        value={"items": notebooks, "categories": categories},
    )


def parse_notebook_move(body: Any, **_: Any) -> RPCParseResult:
    if body == [] or body == [None, None]:
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list) or len(body) <= 1 or not isinstance(body[1], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    return RPCParseResult("success", value=parse_conversation_metadata(body[1]))


def parse_scheduled_registry(body: Any, *, max_chars: int = 500, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 0, lambda item: parse_scheduled_action_task_entry(item, max_chars))


def _scheduled_entry_from_body(body: Any) -> Any:
    if not isinstance(body, list) or not body:
        return None
    first = body[0]
    if isinstance(first, list) and first and isinstance(first[0], str):
        return first
    if isinstance(first, str):
        return body
    return None


def parse_scheduled_get(body: Any, *, max_chars: int = 500, expected_id: str | None = None, **_: Any) -> RPCParseResult:
    if body == []:
        return RPCParseResult("empty", value=None)
    entry = _scheduled_entry_from_body(body)
    if entry is None:
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    value = parse_scheduled_action_task_entry(entry, max_chars)
    warnings: tuple[str, ...] = ()
    if expected_id and value.get("id") != expected_id:
        warnings = ("returned_id_mismatch",)
    return RPCParseResult("success", value=value, warnings=warnings)


def parse_scheduled_create(body: Any, **_: Any) -> RPCParseResult:
    if body == []:
        return RPCParseResult("empty", value=None)
    candidate = body[0] if isinstance(body, list) and body and isinstance(body[0], list) else body
    if not isinstance(candidate, list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    value = parse_scheduled_action_create_body(candidate)
    if not value.get("id"):
        return RPCParseResult("changed_shape", value=value, raw_type=type(body).__name__)
    return RPCParseResult("success", value=value)


def parse_public_links(body: Any, **_: Any) -> RPCParseResult:
    return _result_for_items(body, 0, parse_public_link_entry)


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


PARSER_FUNCTIONS: Mapping[str, Callable[..., RPCParseResult]] = {
    "conversation_page": parse_conversation_page,
    "remy_goals_page": parse_remy_goals_page,
    "notebook_list": parse_notebook_list,
    "notebook_move": parse_notebook_move,
    "scheduled_registry": parse_scheduled_registry,
    "scheduled_get": parse_scheduled_get,
    "scheduled_create": parse_scheduled_create,
    "public_links": parse_public_links,
    "usage_entries": parse_usage_entries,
    "library_capabilities": parse_library_capabilities,
    "tool_modes": parse_tool_modes,
    "opaque": parse_opaque,
    "mutation_ack": parse_mutation_ack,
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
