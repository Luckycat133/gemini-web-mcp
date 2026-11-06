"""Pure parsers for observed Gemini Web RPC response shapes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Literal, Mapping

from .rpc_contracts import GRAPHQL_READ_CONTRACTS, GraphQLReadContract, RPCContract, get_contract
from .task_confirmation import parse_confirmation_replies
from .task_setup import parse_app_setup_card

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


def parse_conversation_presence(body: Any, *, chat_id: str = "", **_: Any) -> RPCParseResult:
    """Verify the identity of one exact turn without returning its content."""
    raw_type = type(body).__name__
    turns = _nested(body, [0])
    if not chat_id.startswith("c_") or not isinstance(turns, list):
        return RPCParseResult("changed_shape", raw_type=raw_type)
    if not turns:
        return RPCParseResult("empty", raw_type=raw_type)
    if len(turns) != 1 or _nested(turns[0], [0, 0]) != chat_id:
        return RPCParseResult("changed_shape", raw_type=raw_type)
    return RPCParseResult("success", raw_type=raw_type)


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
    if (not isinstance(body, list) or len(body) < 3 or not isinstance(body[2], list)
            or (body[1] is not None and not isinstance(body[1], str))):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    if any(not isinstance(item, list) or not item or not isinstance(item[0], str) or not item[0].strip()
           for item in body[2]):
        return RPCParseResult("changed_shape", raw_type="conversation_entry")
    return _result_for_items(body, 2, parse_conversation_metadata)


def parse_notebook_conversation_page(body: Any, **_: Any) -> RPCParseResult:
    # A fresh, empty Notebook returned an explicit [] for its filtered page.
    # That shape has not been verified for unfiltered account history.
    if isinstance(body, list) and not body:
        return RPCParseResult("empty", value=[])
    return parse_conversation_page(body)


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


def _chat_control_metadata(entry: Any) -> dict[str, Any] | None:
    if (not isinstance(entry, list) or not entry or not isinstance(entry[0], str)
            or not entry[0].startswith("c_")):
        return None
    pinned = entry[2] if len(entry) > 2 else None
    if isinstance(pinned, int) and not isinstance(pinned, bool) and pinned in (0, 1):
        pinned = bool(pinned)
    return {
        "id": entry[0],
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else None,
        "is_pinned": pinned if isinstance(pinned, bool) else None,
    }


def parse_chat_metadata_page(body: Any, **_: Any) -> RPCParseResult:
    if (not isinstance(body, list) or len(body) < 3 or not isinstance(body[2], list)
            or (body[1] is not None and not isinstance(body[1], str))):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    items = [_chat_control_metadata(entry) for entry in body[2]]
    if any(item is None for item in items):
        return RPCParseResult("changed_shape", warnings=("chat_identity_unavailable",))
    return RPCParseResult("success" if items else "empty",
                          value={"items": items, "next_page_token": body[1] or None})


def parse_conversation_update(body: Any, **_: Any) -> RPCParseResult:
    if body == [] or body == [None, None]:
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list) or len(body) < 2:
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    metadata = _chat_control_metadata(body[1])
    if metadata is None:
        return RPCParseResult("changed_shape", warnings=("chat_identity_unavailable",))
    return RPCParseResult("success", value=metadata)


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


def parse_research_chat_progress(body: Any, **_: Any) -> RPCParseResult:
    """Recognize a live research document on the newest model turn only."""
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    if _upstream_utils is None:
        return RPCParseResult("changed_shape", warnings=("research_parser_unavailable",))
    for turn in body[0]:
        if not isinstance(turn, list):
            return RPCParseResult("changed_shape", raw_type=type(turn).__name__)
        candidates = _nested(turn, [3, 0])
        if candidates is None:
            continue
        if not isinstance(candidates, list) or not candidates:
            return RPCParseResult("changed_shape", raw_type=type(candidates).__name__)
        for candidate in candidates:
            if not isinstance(candidate, list):
                return RPCParseResult("changed_shape", raw_type=type(candidate).__name__)
            document = _upstream_utils.extract_deep_research_document(candidate)
            status = _nested(candidate, [8, 0])
            if status == 1 and document is not None and not str(document.get("content") or "").strip():
                return RPCParseResult("success", value=True)
        return RPCParseResult("empty", value=False)
    return RPCParseResult("empty", value=False)


def parse_research_chat_plan(body: Any, *, chat_id: str = "", **_: Any) -> RPCParseResult:
    """Read a completed plan from the selected candidate of the newest model turn."""
    if not isinstance(chat_id, str) or not chat_id.strip():
        return RPCParseResult("changed_shape", warnings=("chat_id_missing",))
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape", raw_type=type(body).__name__)
    if _upstream_utils is None:
        return RPCParseResult("changed_shape", warnings=("research_parser_unavailable",))
    for turn in body[0]:
        if not isinstance(turn, list):
            return RPCParseResult("changed_shape", raw_type=type(turn).__name__)
        candidates = _nested(turn, [3, 0])
        if candidates is None:
            continue
        if not isinstance(candidates, list) or not candidates:
            return RPCParseResult("changed_shape", raw_type=type(candidates).__name__)
        candidate = candidates[0]
        if not isinstance(candidate, list):
            return RPCParseResult("changed_shape", raw_type=type(candidate).__name__)
        rid, rcid = _nested(turn, [0, 1]), _nested(candidate, [0])
        if not isinstance(rid, str) or not rid.strip() or not isinstance(rcid, str) or not rcid.strip():
            return RPCParseResult("changed_shape", warnings=("model_turn_identity_missing",))
        if _nested(candidate, [8, 0]) != 2:
            return RPCParseResult("empty", value=None)
        text = _nested(candidate, [1, 0])
        plan = _upstream_utils.extract_deep_research_plan(
            candidate, fallback_text=text if isinstance(text, str) else ""
        )
        if plan is None:
            document = _upstream_utils.extract_deep_research_document(candidate)
            if text == "我是一个语言模型，这超出了我的设计用途。" and document is None:
                return RPCParseResult("rejected", value={"metadata": [chat_id, rid, rcid]})
            return RPCParseResult("empty", value=None)
        return RPCParseResult("success", value={"plan": plan, "metadata": [chat_id, rid, rcid]})
    return RPCParseResult("empty", value=None)


def parse_music_chat_turn_evidence(body: Any, *, rid: str, rcid: str) -> RPCParseResult:
    """Require one completed exact candidate before interpreting absent music cards."""
    if not rid or not rcid or not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape")
    matches = []
    for turn in body[0]:
        if not isinstance(turn, list):
            return RPCParseResult("changed_shape")
        if _nested(turn, [0, 1]) != rid:
            continue
        candidates = _nested(turn, [3, 0])
        if not isinstance(candidates, list):
            return RPCParseResult("changed_shape")
        for candidate in candidates:
            if not isinstance(candidate, list):
                return RPCParseResult("changed_shape")
            if _nested(candidate, [0]) == rcid:
                matches.append(candidate)
    if len(matches) != 1 or _nested(matches[0], [8, 0]) != 2:
        return RPCParseResult("changed_shape")
    rich = _nested(matches[0], [12])
    if rich is not None and not isinstance(rich, list):
        return RPCParseResult("changed_shape")
    group = _nested(matches[0], [12, 0])
    if group is None:
        return RPCParseResult("empty", value=[])
    if not isinstance(group, dict) or not isinstance(group.get("87", []), list):
        return RPCParseResult("changed_shape")
    if "73" in group:
        # This shape is observed with a technical failure notice. The caller
        # must independently match that notice before treating it as terminal.
        card = group["73"]
        if (isinstance(rich, list) and len(rich) == 1 and set(group) == {"8", "73"} and group["8"] == []
                and isinstance(card, list) and len(card) == 4 and card[0] is None
                and card[1] == "Generating your track" and card[2] is None
                and type(card[3]) is int and card[3] == 3):
            return RPCParseResult("rejected")
        return RPCParseResult("changed_shape")
    cards = group.get("87", [])
    if any(not isinstance(_nested(card, [1, 7, 1]), str) or not _nested(card, [1, 7, 1]).strip() for card in cards):
        return RPCParseResult("changed_shape")
    candidate = matches[0]
    target_turn = [[None, rid], None, None, [[candidate]]]
    return parse_music_chat([[target_turn]])


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
                    continue
                media.append(
                    {
                        "title": title if isinstance(title, str) else "[Media]",
                        "url": url.strip(),
                        "rid": rid if isinstance(rid, str) else "",
                        "rcid": rcid if isinstance(rcid, str) else "",
                    }
                )
    return RPCParseResult("success" if media else "empty", value=media)


def _task_goal_metadata(entry: Any) -> dict[str, Any] | None:
    """Project only fields evidenced by the public RemyGoal schema."""
    if not isinstance(entry, list) or len(entry) < 14:
        return None
    identifier = entry[13]
    if not isinstance(identifier, str) or not identifier.startswith("c_") or len(identifier) > 256:
        return None
    description = entry[1] if len(entry) > 1 else None
    status = entry[2] if len(entry) > 2 else None
    pinned = entry[19] if len(entry) > 19 else None
    if description is not None and not isinstance(description, str):
        return None
    if status is not None and (type(status) is not int or status < 0):
        return None
    if type(pinned) is int and pinned in {0, 1}:
        pinned = bool(pinned)
    if pinned is not None and type(pinned) is not bool:
        return None
    return {"chat_id": identifier, "description": description, "upstream_status": status,
            "is_pinned": pinned,
            "created_at": _task_timestamp(_nested(entry, [16, 0])),
            "updated_at": _task_timestamp(_nested(entry, [17, 0]))}


def _task_timestamp(value: Any) -> str | None:
    if type(value) not in {int, float}:
        return None
    try:
        return _format_timestamp(value) or None
    except (ValueError, OverflowError, OSError):
        return None


def parse_task_goal(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty", value=None)
    goal = _task_goal_metadata(body[0])
    return RPCParseResult("success", value=goal) if goal is not None else RPCParseResult("changed_shape")


def parse_task_goal_page(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape")
    token = body[1] if len(body) > 1 else None
    if token is not None and (not isinstance(token, str) or not token):
        return RPCParseResult("changed_shape")
    items = [_task_goal_metadata(entry) for entry in body[0]]
    if any(item is None for item in items):
        return RPCParseResult("changed_shape")
    identifiers = [item["chat_id"] for item in items if item is not None]
    if len(set(identifiers)) != len(identifiers) or (token and not items):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success" if items else "empty", value={"items": items, "next_page_token": token})


def parse_task_schedule_refs(body: Any, **_: Any) -> RPCParseResult:
    goal = parse_task_goal(body)
    if not goal.ok or goal.value is None:
        return goal
    rows = body[1] if len(body) > 1 and body[1] is not None else []
    if not isinstance(rows, list):
        return RPCParseResult("changed_shape")
    ids: list[str] = []
    for row in rows:
        parsed = parse_remy_schedule_object(row)
        if not parsed.ok:
            return RPCParseResult("changed_shape")
        associated = parsed.value['conversation_id']
        if associated and associated != goal.value['chat_id']:
            return RPCParseResult("changed_shape")
        ids.append(parsed.value['schedule_id'])
    if len(set(ids)) != len(ids):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success", value={"chat_id": goal.value['chat_id'], "schedule_ids": ids})


def _task_progress_turn(turn: Any) -> dict[str, Any] | None:
    if not isinstance(turn, list) or len(turn) < 4:
        return None
    metadata = turn[0]
    if (not isinstance(metadata, list) or len(metadata) < 2 or not isinstance(metadata[0], str)
            or not metadata[0].startswith('c_') or not isinstance(metadata[1], str) or not metadata[1].startswith('r_')):
        return None
    response = turn[3]
    if not isinstance(response, list) or not response or not isinstance(response[0], list):
        return None
    candidates = response[0]
    ids = []
    for candidate in candidates:
        if not isinstance(candidate, list) or not candidate or not isinstance(candidate[0], str) or not candidate[0].startswith('rc_'):
            return None
        ids.append(candidate[0])
    if len(set(ids)) != len(ids):
        return None
    return {'chat_id': metadata[0], 'response_id': metadata[1], 'candidates': candidates}


def parse_task_progress_turn(body: Any, **_: Any) -> RPCParseResult:
    if body == [] or body == [None]:
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list) or not body:
        return RPCParseResult("changed_shape")
    turn = _task_progress_turn(body[0])
    return RPCParseResult("success", value=turn) if turn is not None else RPCParseResult("changed_shape")


def parse_task_progress_turn_list(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape")
    items = [_task_progress_turn(turn) for turn in body[0]]
    if any(item is None for item in items):
        return RPCParseResult("changed_shape")
    ids = [item['response_id'] for item in items if item is not None]
    if len(set(ids)) != len(ids):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success" if items else "empty", value=items)


def parse_task_browser_credentials(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list) or not body or not isinstance(body[0], list) or len(body[0]) > 32:
        return RPCParseResult('changed_shape')
    for server in body[0]:
        if (not isinstance(server, list) or not server or len(server) > 3 or not isinstance(server[0], list)
                or not server[0] or len(server[0]) > 32
                or any(not isinstance(url, str) or len(url) > 2048 or not url.startswith(('stun:', 'stuns:', 'turn:', 'turns:')) for url in server[0])
                or any(value is not None and (not isinstance(value, str) or len(value) > 16384) for value in server[1:])):
            return RPCParseResult('changed_shape')
    return RPCParseResult('success' if body[0] else 'empty', value=body[0])


def parse_task_browser_answer(body: Any, **_: Any) -> RPCParseResult:
    if body == [] or body == [None]:
        return RPCParseResult('empty', value=None)
    if (not isinstance(body, list) or len(body) < 3 or not isinstance(body[0], str)
            or not body[0].startswith('v=0') or len(body[0]) > 262144 or body[1] != 'answer'
            or not isinstance(body[2], str) or not body[2] or len(body[2]) > 8192):
        return RPCParseResult('changed_shape')
    candidates = body[3] if len(body) > 3 and body[3] is not None else []
    if not isinstance(candidates, list) or len(candidates) > 256:
        return RPCParseResult('changed_shape')
    for row in candidates:
        if (not isinstance(row, list) or not row or not isinstance(row[0], str) or len(row[0]) > 8192
                or (len(row) > 2 and row[2] is not None and not isinstance(row[2], str))
                or (len(row) > 3 and row[3] is not None and (type(row[3]) is not int or not 0 <= row[3] <= 128))):
            return RPCParseResult('changed_shape')
    return RPCParseResult('success', value={'sdp': body[0], 'type': body[1], 'session_id': body[2], 'candidates': candidates})


def parse_notebooklm_picker(body: Any, **_: Any) -> RPCParseResult:
    """Validate the observed persisted-query envelope without hiding GraphQL errors."""
    if isinstance(body, list):
        if len(body) != 1 or not isinstance(body[0], dict) or body[0].get("path") != []:
            return RPCParseResult("changed_shape")
        body = body[0]
    if not isinstance(body, dict):
        return RPCParseResult("changed_shape")
    if body.get("errors"):
        return RPCParseResult("rejected")
    try:
        viewer = body["data"]["viewer"]
        if viewer.get("errors") or viewer["rpcs"].get("errors"):
            return RPCParseResult("rejected")
        entries = viewer["rpcs"]["listNotebooks"]["notebooks"]
    except (KeyError, TypeError, AttributeError):
        return RPCParseResult("changed_shape")
    if not isinstance(entries, list):
        return RPCParseResult("changed_shape")
    items = []
    for entry in entries:
        if not isinstance(entry, dict):
            return RPCParseResult("changed_shape")
        identifier, title = entry.get("id"), entry.get("displayName")
        count, created = entry.get("sourceCount"), entry.get("createTime")
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256:
            return RPCParseResult("changed_shape")
        if not isinstance(title, str) or (created is not None and not isinstance(created, str)):
            return RPCParseResult("changed_shape")
        if count is not None:
            if type(count) is int:
                if count < 0:
                    return RPCParseResult("changed_shape")
            elif type(count) is not float or count < 0 or not count.is_integer():
                return RPCParseResult("changed_shape")
        items.append({"notebook_id": identifier, "title": title,
                      "source_count": int(count) if count is not None else None, "created_at": created})
    if len({item["notebook_id"] for item in items}) != len(items):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success" if items else "empty", value=items)


def parse_notebooklm_list(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape")
    items = []
    for entry in body[0]:
        if not isinstance(entry, list) or len(entry) < 2:
            return RPCParseResult("changed_shape")
        identifier, title = entry[:2]
        count = entry[3] if len(entry) > 3 else None
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256:
            return RPCParseResult("changed_shape")
        if not isinstance(title, str) or (count is not None and (type(count) is not int or count < 0)):
            return RPCParseResult("changed_shape")
        items.append({"notebook_id": identifier, "title": title, "source_count": count,
                      "created_at": _task_timestamp(_nested(entry, [2, 0]))})
    if len({item["notebook_id"] for item in items}) != len(items):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success" if items else "empty", value=items)


def parse_workflow_turn(body: Any, *, chat_id: str = "", rid: str = "", rcid: str | None = None,
                         **_: Any) -> RPCParseResult:
    """Read one explicit reply; missing identity or alternative candidates are not completion."""
    if (not isinstance(body, list) or not body or not isinstance(body[0], list)
            or not isinstance(chat_id, str) or not chat_id.startswith("c_") or not isinstance(rid, str) or not rid):
        return RPCParseResult("changed_shape")
    turns = [turn for turn in body[0] if _nested(turn, [0, 1]) == rid]
    if not turns:
        return RPCParseResult("empty", value=None)
    if len(turns) != 1:
        return RPCParseResult("changed_shape")
    candidates = _nested(turns[0], [3, 0])
    if not isinstance(candidates, list):
        return RPCParseResult("changed_shape")
    selected = [candidate for candidate in candidates if not rcid or _nested(candidate, [0]) == rcid]
    if len(selected) != 1:
        return RPCParseResult("empty" if rcid and not selected else "changed_shape", value=None)
    candidate = selected[0]
    candidate_id, text, state = _nested(candidate, [0]), _nested(candidate, [1, 0]), _nested(candidate, [8, 0])
    if not isinstance(candidate_id, str) or not candidate_id or (text is not None and not isinstance(text, str)):
        return RPCParseResult("changed_shape")
    complete = type(state) is int and state == 2 and isinstance(text, str) and bool(text.strip())
    # A completed message can be a confirmation card, not the Task's final
    # answer. This marker was observed on the native Remy confirmation card.
    input_required = complete and isinstance(text, str) and "googleusercontent.com/action_card_content/" in text
    confirmation: dict[str, Any] = {"input_options": []}
    card_group = _nested(candidate, [12, 34, 0, 1])
    card = card_group.get("154") if isinstance(card_group, dict) else None
    if isinstance(card, list):
        input_required = complete
        if complete:
            confirmation = parse_confirmation_replies(card, chat_id=chat_id, response_id=rid,
                                                      candidate_id=candidate_id)
    setup = parse_app_setup_card(candidate)
    if setup["setup_card_present"]:
        # App connection and OAuth have their own native UI flow. Never turn
        # these cards into final answers or userQuery approval options.
        input_required = False
        confirmation = {"input_options": []}
    return RPCParseResult("success", value={"chat_id": chat_id, "rid": rid, "rcid": candidate_id,
                                             "completed": complete and not input_required and not setup["setup_card_present"],
                                             "text": text if complete and not input_required and not setup["setup_card_present"] else None,
                                             "input_required": input_required,
                                             **confirmation,
                                             **setup,
                                             "input_prompt": text if input_required else None})


# Remy schedules: fields are one-based protobuf numbers stored as JSPB arrays.
_REMY_TRIGGER_FIELDS = (4, 5, 6, 11, 13, 16, 18, 19, 20, 21)
_REMY_WRITABLE_FIELDS = frozenset((1, 2, 4, 5, 6, 7, 8, 9, 10, 12, 14, 15, 16, 18, 19, 20, 21, 23, 24, 25, 27))
_REMY_READ_FIELDS = _REMY_WRITABLE_FIELDS | {3, 11, 13, 22, 26}


def _remy_field(raw: list[Any], number: int) -> Any:
    return raw[number - 1] if len(raw) >= number else None


def _remy_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > 2:
        raise ValueError("Invalid timestamp")
    sec = value[0]
    if isinstance(sec, bool) or not (isinstance(sec, int) or isinstance(sec, str) and sec.lstrip('-').isdigit()):
        raise ValueError("Invalid timestamp seconds")
    nanos = value[1] if len(value) > 1 and value[1] is not None else 0
    if type(nanos) is not int or not 0 <= nanos < 1_000_000_000:
        raise ValueError("Invalid timestamp nanos")
    dt = datetime.fromtimestamp(int(sec), timezone.utc)
    if nanos:
        return dt.strftime('%Y-%m-%dT%H:%M:%S') + ('.' + f'{nanos:09d}'.rstrip('0')) + '+00:00'
    return dt.isoformat()


def _remy_time(value: Any) -> str:
    if not isinstance(value, list) or not value or len(value) > 4:
        raise ValueError("Invalid time")
    hour = value[0] if value[0] is not None else 0
    minute = value[1] if len(value) > 1 and value[1] is not None else 0
    if type(hour) is not int or type(minute) is not int or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid time")
    if any(v not in (None, 0) for v in value[2:]):
        raise ValueError("Unsupported sub-minute time")
    return f'{hour:02d}:{minute:02d}'


def _remy_days(value: Any, maximum: int) -> list[int]:
    if not isinstance(value, list) or not value or any(type(n) is not int or not 1 <= n <= maximum for n in value):
        raise ValueError("Invalid day list")
    if len(set(value)) != len(value):
        raise ValueError("Duplicate day")
    return sorted(value)


def _remy_trigger(number: int, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list):
        raise ValueError('Invalid trigger')
    if number in (11, 13):
        return {'frequency': 'unknown'}
    if not isinstance(raw, list):
        raise ValueError("Invalid trigger")
    def f(n: int) -> Any:
        return _remy_field(raw, n)

    result: dict[str, Any]
    window_field = 0
    if number == 4:
        result, window_field = {'frequency': 'daily', 'time': _remy_time(f(1))}, 2
    elif number in (5, 19):
        result = {'frequency': 'weekly' if number == 5 else 'monthly',
                  'weekdays' if number == 5 else 'days_of_month': _remy_days(f(1), 7 if number == 5 else 31),
                  'time': _remy_time(f(2))}
        window_field = 3
    elif number == 18:
        minute = f(1) if f(1) is not None else 0
        if type(minute) is not int or not 0 <= minute <= 59:
            raise ValueError("Invalid minute")
        result = {'frequency': 'hourly', 'minute': minute}
    elif number == 20:
        date = f(1)
        if not isinstance(date, list) or len(date) < 3 or date[0] not in (None, 0):
            raise ValueError("Invalid yearly date")
        month, day = date[1], date[2]
        if type(month) is not int or type(day) is not int:
            raise ValueError("Invalid yearly date")
        datetime(2000, month, day)
        result, window_field = {'frequency': 'yearly', 'month': month, 'day': day, 'time': _remy_time(f(2))}, 3
    elif number == 21:
        at = _remy_timestamp(f(1))
        if at is None:
            raise ValueError("Missing once time")
        result, window_field = {'frequency': 'once', 'at': at}, 2
    elif number == 6:
        if not isinstance(f(1), str):
            raise ValueError("Invalid event")
        result = {'frequency': 'event', 'condition': f(1)}
    elif number == 16:
        if not isinstance(f(1), str) or not isinstance(f(2), str):
            raise ValueError("Invalid gmail")
        result = {'frequency': 'gmail'}
    else:
        raise ValueError("Unsupported trigger")
    if window_field:
        window = f(window_field)
        if window is not None and (type(window) is not int or window not in (0, 1, 2, 3)):
            raise ValueError("Invalid window")
        if window:
            result['window'] = {1: 'overnight', 2: 'daytime', 3: 'evening'}[window]
    max_fields = {4: 2, 5: 3, 6: 1, 16: 2, 18: 1, 19: 3, 20: 3, 21: 2}[number]
    if any(v is not None for v in raw[max_fields:]):
        result['frequency'] = 'unknown'
    return result


def parse_remy_schedule_object(raw: Any, **_: Any) -> RPCParseResult:
    """Normalize one Schedule; unrecognized shapes are never editable."""
    import hashlib

    try:
        if not isinstance(raw, list) or not raw:
            raise ValueError("Invalid object")
        def f(n: int) -> Any:
            return _remy_field(raw, n)

        schedule_id = f(1)
        if not isinstance(schedule_id, str) or not schedule_id.strip() or len(schedule_id) > 256:
            raise ValueError("Invalid ID")
        for n in (2, 7, 8, 9, 12, 23, 24, 27):
            if f(n) is not None and not isinstance(f(n), str):
                raise ValueError("Invalid string")
        # Current UI replies include an opaque false field3. The public Cif
        # read-to-write projection omits it, as it also omits fields22/26.
        # Both boolean values were observed across controlled resume/pause.
        # No meaning is assigned; other shapes remain unsupported.
        if f(3) is not None and type(f(3)) is not bool:
            raise ValueError('Unknown read-only flag shape')
        status, kind = f(10), f(25)
        if status is None:
            status = 0
        if kind is None:
            kind = 0
        if type(status) is not int or status < 0 or type(kind) is not int or kind < 0:
            raise ValueError("Invalid enum")
        if f(26) is not None and (type(f(26)) is not int or f(26) < 0):
            raise ValueError("Invalid read-only enum")
        triggers = [n for n in _REMY_TRIGGER_FIELDS if f(n) is not None]
        if any(not isinstance(f(n), list) for n in triggers):
            raise ValueError('Invalid trigger type')
        trigger = _remy_trigger(triggers[0], f(triggers[0])) if len(triggers) == 1 else {'frequency': 'unknown'}
        known = not any(v is not None and i not in _REMY_READ_FIELDS for i, v in enumerate(raw, 1))
        editable = kind == 1 and status in (1, 2) and len(triggers) == 1 and trigger['frequency'] not in ('unknown', 'gmail') and known
        projection = [_remy_field(raw, i) if i in _REMY_WRITABLE_FIELDS else None for i in range(1, 28)]
        revision = hashlib.sha256(json.dumps(projection, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        result = {'schedule_id': schedule_id, 'title': f(2) or '', 'instructions': f(8) or '',
                  'timezone': f(7) or '', 'status': status, 'kind': kind, 'trigger': trigger,
                  'conversation_id': f(12), 'project_id': f(27), 'starts_at': _remy_timestamp(f(14)),
                  'ends_at': _remy_timestamp(f(15)), 'editable': editable, 'revision': revision}
        return RPCParseResult('success', value=result)
    except (ValueError, OverflowError, OSError, TypeError):
        return RPCParseResult('changed_shape')


def parse_remy_schedule_reply(body: Any, **_: Any) -> RPCParseResult:
    """An empty body is not authoritative evidence of remote absence."""
    if body == [] or body == [None]:
        return RPCParseResult("empty", value=None)
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult('changed_shape')
    return parse_remy_schedule_object(body[0])


def parse_remy_schedule_list(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult('changed_shape')
    flag = body[1] if len(body) > 1 else None
    if flag is not None and type(flag) is not bool and not (type(flag) is int and flag in (0, 1)):
        return RPCParseResult('changed_shape')
    schedules: list[dict[str, Any]] = []
    for raw in body[0]:
        parsed = parse_remy_schedule_object(raw)
        if not parsed.ok:
            return parsed
        schedules.append(parsed.value)
    if len({item['schedule_id'] for item in schedules}) != len(schedules):
        return RPCParseResult('changed_shape')
    return RPCParseResult('success' if schedules else 'empty', value={'schedules': schedules, 'active_limit_reached': bool(flag)})


def _permission_record(raw: Any, *, source: str = "stored") -> dict[str, Any] | None:
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], list):
        return None
    native_id = raw[0]
    handle = native_id[0] if native_id else None
    if handle is not None and (not isinstance(handle, list) or len(handle) < 2
                               or any(not isinstance(value, str) for value in handle[:2])):
        return None
    attributes = native_id[9] if len(native_id) > 9 else None
    if attributes is not None and (not isinstance(attributes, list) or any(
            not isinstance(entry, list) or len(entry) < 2
            or not isinstance(entry[0], str) or not isinstance(entry[1], str) for entry in attributes)):
        return None
    native_grant = raw[1] if len(raw) > 1 and raw[1] is not None else []
    if not isinstance(native_grant, list):
        return None
    grant_id = native_grant[0] if native_grant and native_grant[0] is not None else ""
    scope = native_grant[1] if len(native_grant) > 1 and native_grant[1] is not None else 0
    if not isinstance(grant_id, str) or type(scope) is not int:
        return None
    if not grant_id and (not isinstance(handle, list) or not all(value.strip() for value in handle[:2])):
        return None
    context = raw[2] if len(raw) > 2 else None
    if context is not None and not isinstance(context, list):
        return None
    metadata = raw[4] if len(raw) > 4 and raw[4] is not None else []
    if not isinstance(metadata, list):
        return None
    label = metadata[3] if len(metadata) > 3 and metadata[3] is not None else ""
    category = metadata[4] if len(metadata) > 4 else None
    state = raw[5] if len(raw) > 5 and raw[5] is not None else 0
    if not isinstance(label, str) or (category is not None and type(category) is not int) or type(state) is not int:
        return None
    try:
        json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None
    return {
        "native_permission_id": deepcopy(native_id), "grant_id": grant_id, "scope": scope,
        "context": deepcopy(context), "state": state, "label": label, "category": category,
        "native_grant_identity": deepcopy(native_grant), "source": source,
    }


def _unique_permissions(records: list[dict[str, Any]]) -> bool:
    grants: set[str] = set()
    identities: set[str] = set()
    for record in records:
        grant = record["grant_id"]
        identity = json.dumps([record["native_permission_id"], record["scope"], record["context"]],
                              ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if (grant and grant in grants) or identity in identities:
            return False
        if grant:
            grants.add(grant)
        identities.add(identity)
    return True


def parse_preference_list(body: Any, **_: Any) -> RPCParseResult:
    """One observed ListPermissions response; no inferred pagination completion."""
    # Live metadata has an empty string after the permission rows. Nonempty
    # trailing data remains unknown; it is not a proved pagination contract.
    if not isinstance(body, list) or any(value is not None and value != "" for value in body[1:]):
        return RPCParseResult("changed_shape")
    entries = body[0] if body and body[0] is not None else []
    if not isinstance(entries, list):
        return RPCParseResult("changed_shape")
    records: list[dict[str, Any]] = []
    for entry in entries:
        record = _permission_record(entry)
        if record is None:
            return RPCParseResult("changed_shape")
        records.append(record)
    if not _unique_permissions(records):
        return RPCParseResult("changed_shape", warnings=("ambiguous_permission_identity",))
    return RPCParseResult("success" if records else "empty", value=records)


def parse_preference_update(body: Any, **_: Any) -> RPCParseResult:
    """A mutation reply is accepted metadata, never read-back verification."""
    if not isinstance(body, list) or any(value is not None for value in body[1:]):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty")
    record = _permission_record(body[0])
    return RPCParseResult("success", value=record) if record is not None else RPCParseResult("changed_shape")


def parse_preference_catalog(body: Any, **_: Any) -> RPCParseResult:
    """Only native returned actions, recursively through App field18/field23."""
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    apps = body[0] if body and body[0] is not None else []
    if not isinstance(apps, list):
        return RPCParseResult("changed_shape")
    if not apps and any(value is not None for value in body[1:]):
        return RPCParseResult("changed_shape")
    records: list[dict[str, Any]] = []

    def collect(app: Any, depth: int) -> bool:
        if (depth > 32 or not isinstance(app, list) or not app
                or not isinstance(app[0], str) or not app[0].strip()):
            return False
        children = app[17] if len(app) > 17 and app[17] is not None else []
        permissions = app[22] if len(app) > 22 and app[22] is not None else []
        if not isinstance(children, list) or not isinstance(permissions, list):
            return False
        for permission in permissions:
            record = _permission_record(permission, source="catalog")
            if record is None:
                return False
            records.append(record)
        return all(collect(child, depth + 1) for child in children)

    if not all(collect(app, 0) for app in apps) or not _unique_permissions(records):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success" if records else "empty", value=records)


def parse_managed_skills_list(body: Any, **_: Any) -> RPCParseResult:
    from .managed_skills import parse_list
    return parse_list(body)


def parse_managed_skills_get(body: Any, *, skill_id: str = "", include_instructions: bool = False,
                             **_: Any) -> RPCParseResult:
    from .managed_skills import parse_get
    return parse_get(body, skill_id=skill_id, include_instructions=include_instructions)


def parse_managed_skills_saved(body: Any, **_: Any) -> RPCParseResult:
    if body in (None, []):
        return RPCParseResult("empty")
    if (not isinstance(body, list) or not body or not isinstance(body[0], str)
            or not body[0].strip() or len(body[0]) > 256):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success", value=body[0])


def parse_notebook_admin_created(body: Any, **arguments: Any) -> RPCParseResult:
    from .notebook_admin import parse_created
    return parse_created(body, **arguments)


def parse_notebook_admin_project(body: Any, **arguments: Any) -> RPCParseResult:
    from .notebook_admin import parse_project
    return parse_project(body, **arguments)


def parse_notebook_admin_source_created(body: Any, **arguments: Any) -> RPCParseResult:
    from .notebook_admin import parse_source_created
    return parse_source_created(body, **arguments)


def parse_custom_gems(body: Any, *, include_instructions_for: str | None = None, **_: Any) -> RPCParseResult:
    """Parse one complete custom-Gem catalog; missing field 3 is not an empty list."""
    if not isinstance(body, list) or len(body) < 3 or not isinstance(body[2], list):
        return RPCParseResult("changed_shape")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in body[2]:
        if (not isinstance(entry, list) or len(entry) < 3 or not isinstance(entry[0], str)
                or not entry[0].strip() or entry[0] in seen or not isinstance(entry[1], list)
                or len(entry[1]) < 2 or not isinstance(entry[1][0], str)
                or (entry[1][1] is not None and not isinstance(entry[1][1], str))):
            return RPCParseResult("changed_shape")
        seen.add(entry[0])
        item = {"id": entry[0], "name": entry[1][0], "description": entry[1][1] or ""}
        if entry[0] == include_instructions_for:
            prompt = entry[2]
            if not isinstance(prompt, list) or not prompt or not isinstance(prompt[0], str):
                return RPCParseResult("changed_shape")
            item["instructions"] = prompt[0]
        items.append(item)
    return RPCParseResult("success" if items else "empty", value=items)


PARSER_FUNCTIONS: Mapping[str, Callable[..., RPCParseResult]] = {
    "custom_gems": parse_custom_gems,
    "notebook_admin_created": parse_notebook_admin_created,
    "notebook_admin_project": parse_notebook_admin_project,
    "notebook_admin_source_created": parse_notebook_admin_source_created,
    "managed_skills_list": parse_managed_skills_list,
    "managed_skills_get": parse_managed_skills_get,
    "managed_skills_saved": parse_managed_skills_saved,
    "preference_list": parse_preference_list,
    "preference_update": parse_preference_update,
    "preference_catalog": parse_preference_catalog,
    # The public RevokePermission caller ignores its empty response message.
    # This only acknowledges an envelope, never verifies grant absence.
    "preference_revoke": parse_mutation_ack,
    "task_browser_credentials": parse_task_browser_credentials,
    "task_browser_answer": parse_task_browser_answer,
    "task_schedule_refs": parse_task_schedule_refs,
    "task_progress_turn": parse_task_progress_turn,
    "task_progress_turn_list": parse_task_progress_turn_list,
    "remy_schedule_reply": parse_remy_schedule_reply,
    "remy_schedule_list": parse_remy_schedule_list,
    "workflow_turn": parse_workflow_turn,
    "conversation_presence": parse_conversation_presence,
    "task_goal": parse_task_goal,
    "task_goal_update": parse_task_goal,
    "task_goal_page": parse_task_goal_page,
    "notebooklm_picker": parse_notebooklm_picker,
    "notebooklm_list": parse_notebooklm_list,
    "conversation_page": parse_conversation_page,
    "notebook_conversation_page": parse_notebook_conversation_page,
    "remy_goals_page": parse_remy_goals_page,
    "notebook_list": parse_notebook_list,
    "notebook_move": parse_notebook_move,
    "chat_metadata_page": parse_chat_metadata_page,
    "conversation_update": parse_conversation_update,
    "scheduled_registry": parse_scheduled_registry,
    "scheduled_get": parse_scheduled_get,
    "scheduled_create": parse_scheduled_create,
    "public_links": parse_public_links,
    "usage_entries": parse_usage_entries,
    "library_capabilities": parse_library_capabilities,
    "tool_modes": parse_tool_modes,
    "opaque": parse_opaque,
    "mutation_ack": parse_mutation_ack,
    "music_chat": parse_music_chat,
    "research_chat_progress": parse_research_chat_progress,
    "research_chat_plan": parse_research_chat_plan,
}


def parse_contract_body(
    contract: str | RPCContract | GraphQLReadContract,
    body: Any,
    *,
    reject_code: int | None = None,
    **arguments: Any,
) -> RPCParseResult:
    resolved = (GRAPHQL_READ_CONTRACTS.get(contract) or get_contract(contract)) if isinstance(contract, str) else contract
    if reject_code is not None:
        return RPCParseResult("rejected", reject_code=reject_code, raw_type=type(body).__name__)
    parser = PARSER_FUNCTIONS[resolved.parser]
    return parser(body, **arguments)


@dataclass(frozen=True, slots=True)
class CandidateSourceEvidence:
    """Source record with transient text-span evidence, never serialized to callers."""

    url: str
    title: str | None
    snippet: str
    spans: tuple[tuple[int, int], ...]


def attribution_matches_answer(evidence: CandidateSourceEvidence, answer: str) -> bool:
    """Check every observed span against the answer in code points or UTF-16 units."""
    if not evidence.snippet or not evidence.spans or not isinstance(answer, str):
        return False
    encoded = answer.encode("utf-16-le")
    for start, end in evidence.spans:
        codepoint_match = 0 <= start <= end <= len(answer) and answer[start:end] == evidence.snippet
        utf16_match = False
        if 0 <= start <= end <= len(encoded) // 2:
            try:
                utf16_match = encoded[start * 2:end * 2].decode("utf-16-le") == evidence.snippet
            except UnicodeDecodeError:
                pass
        if not (codepoint_match or utf16_match):
            return False
    return True


def parse_candidate_attribution_sources(candidate: Any) -> tuple[CandidateSourceEvidence, ...]:
    """Extract only source records linked to the observed candidate attribution block."""
    from urllib.parse import urlsplit

    groups = _nested(candidate, [2, 1])
    if not isinstance(groups, list):
        return ()
    found: list[CandidateSourceEvidence] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list) or len(group) < 3:
            continue
        snippet, records = group[0], group[2]
        if not isinstance(snippet, list) or not snippet or not isinstance(snippet[0], str):
            continue
        if not snippet[0].strip() or not isinstance(records, list):
            continue
        raw_spans = snippet[3] if len(snippet) > 3 else None
        spans: tuple[tuple[int, int], ...] = ()
        if isinstance(raw_spans, list) and raw_spans and all(
            isinstance(pair, list) and len(pair) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in pair)
            for pair in raw_spans
        ):
            spans = tuple((pair[0], pair[1]) for pair in raw_spans)
        for record in records:
            if not isinstance(record, list) or len(record) < 5:
                continue
            url, title, favicon, _, associations = record[:5]
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            if not isinstance(favicon, str) or not isinstance(associations, list):
                continue
            if snippet not in associations:
                continue
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                continue
            if parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico")):
                continue
            if "favicon" in parsed.path.lower() or url in seen:
                continue
            seen.add(url)
            found.append(CandidateSourceEvidence(url, title.strip() or None, snippet[0], spans))
    return tuple(found)
