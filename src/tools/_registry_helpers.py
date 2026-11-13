"""
共享的工具注册辅助函数。

供 history/account/scheduled/notebooks/cleanup/gems 各功能域模块复用，
避免重复定义。
"""

import json
import logging
from typing import Any, Callable, Literal, TypeVar

from ..adapters.mcp_sdk import MCPServer, TextContent
from ..infrastructure.rpc_contracts import RawRPCData as _RawRPCData, get_contract
from ..infrastructure.rpc_parsers import (
    extract_rpc_bodies as _extract_rpc_bodies,
    parse_conversation_metadata as _parse_conversation_metadata,
    parse_library_capability as _parse_library_capability,
    parse_native_notebook as _parse_native_notebook,
    parse_notebook_category as _parse_notebook_category,
    parse_public_link_entry as _parse_public_link_entry,
    parse_remy_goal_entry as _parse_remy_goal_entry,
    parse_scheduled_action_create_body as _parse_scheduled_action_create_body,
    parse_scheduled_action_task_entry as _parse_scheduled_action_task_entry,
    scheduled_task_state as _scheduled_task_state,
    parse_tool_mode_entry as _parse_tool_mode_entry,
    parse_usage_entry as _parse_usage_entry,
    summarize_rpc_response as _summarize_probe_response,
)
from ..services.gems import (
    _find_gem_by_id as _registered_find_gem_by_id,
    _gem_field as _registered_gem_field,
    iter_gem_values as _registered_iter_gems,
)
from ..services.history import (
    conversation_history_payload as _registered_conversation_history_payload,
)
from ..services.manifest import (
    format_tool_manifest_markdown as _format_registered_tool_manifest,
    format_web_capabilities_markdown as _format_registered_web_capabilities,
    tool_manifest_payload as _registered_tool_manifest_payload,
    web_capabilities_payload as _registered_web_capabilities_payload,
)
from ..services.notebooks import (
    move_chat_to_notebook_payload as _registered_move_chat_payload,
    native_notebooks_payload as _registered_notebooks_payload,
    notebook_chats_payload as _registered_notebook_chats_payload,
)
from ..services.scheduled import (
    scheduled_daily_payload as _registered_scheduled_daily_payload,
)
from .manifest_data import WEB_FEATURE_PROBES

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

# ── 公共类型定义（供各功能域模块复用）──────────────────────────────

ResponseFormat = Literal["markdown", "json"]
FeatureSurface = Literal[
    "all",
    "history",
    "library",
    "notebooks",
    "remy",
    "sharing",
    "usage",
    "personalization",
    "import",
    "scheduled",
    "tool_modes",
]
UsageScope = Literal["quota", "model_state", "all"]
ScheduledScope = Literal["active", "inactive", "all"]
HistoryAction = Literal["list", "scan", "search", "read", "export"]
NotebookAction = Literal["list", "chats"]
AccountInventorySurface = Literal[
    "capabilities",
    "status",
    "features",
    "links",
    "usage",
    "library",
    "notebooks",
    "notebook_chats",
    "scheduled",
    "modes",
    "models",
]
ManifestScope = Literal[
    "all",
    "chat",
    "core",
    "history",
    "account",
    "notebooks",
    "scheduled",
    "media",
    "files",
    "research",
    "gems",
    "cookie",
    "prompts",
]
DoctorStatus = Literal["ok", "warn", "error", "skip"]
CleanupTarget = Literal["all", "chats", "scheduled"]

CONVERSATION_HISTORY_FILTERS: tuple[dict[str, Any], ...] = (
    {
        "name": "ui_recent",
        "filter": [False, None, True],
        "description": "Main Gemini Web recent conversation history bucket.",
    },
    {
        "name": "ui_pinned",
        "filter": [True, None, True],
        "description": "Main Gemini Web pinned conversation history bucket.",
    },
    {
        "name": "recent_p3_true",
        "filter": [False, None, True, None, True],
        "description": "Recent bucket with the frontend P3 metadata flag set true.",
    },
    {
        "name": "pinned_p3_true",
        "filter": [True, None, True, None, True],
        "description": "Pinned bucket with the frontend P3 metadata flag set true.",
    },
    {
        "name": "recent_p3_false",
        "filter": [False, None, True, None, False],
        "description": "Recent bucket variant used by frontend refill paths after history mutations.",
    },
    {
        "name": "pinned_p3_false",
        "filter": [True, None, True, None, False],
        "description": "Pinned bucket variant used by frontend refill paths after history mutations.",
    },
    {
        "name": "recent_field3_false_p3_true",
        "filter": [False, None, False, None, True],
        "description": "Recent bucket with the frontend field-3 boolean disabled and P3 set true.",
    },
)

# ── 向后兼容的私有别名（manage.py 原定义，供外部测试导入）──────────

_summarize_probe_response = _summarize_probe_response  # type: ignore[assignment]
_extract_rpc_bodies = _extract_rpc_bodies  # type: ignore[assignment]
_parse_public_link_entry = _parse_public_link_entry
_parse_usage_entry = _parse_usage_entry
_parse_library_capability = _parse_library_capability
_parse_native_notebook = _parse_native_notebook
_parse_notebook_category = _parse_notebook_category
_parse_conversation_metadata = _parse_conversation_metadata
_parse_remy_goal_entry = _parse_remy_goal_entry
_parse_scheduled_action_create_body = _parse_scheduled_action_create_body
_scheduled_task_state = _scheduled_task_state
_parse_scheduled_action_task_entry = _parse_scheduled_action_task_entry
_parse_tool_mode_entry = _parse_tool_mode_entry
_native_notebooks_payload = _registered_notebooks_payload
_conversation_history_payload = _registered_conversation_history_payload
_notebook_chats_payload = _registered_notebook_chats_payload
_move_chat_to_notebook_payload = _registered_move_chat_payload
_scheduled_daily_payload = _registered_scheduled_daily_payload
_tool_manifest_payload = _registered_tool_manifest_payload
_format_tool_manifest_markdown = _format_registered_tool_manifest
_web_capabilities_payload = _registered_web_capabilities_payload
_format_web_capabilities_markdown = _format_registered_web_capabilities
_iter_gem_values = _registered_iter_gems
_find_gem_by_id = _registered_find_gem_by_id
_gem_field = _registered_gem_field


# ── 共享辅助函数────────────────────────────────────────────────────


def _json_response(payload: Any) -> list[TextContent]:
    """Serialize payload as a single JSON TextContent (for response_format='json')."""
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _get_probe(surface: str, name: str) -> dict[str, str]:
    for probe in WEB_FEATURE_PROBES:
        if probe["surface"] == surface and probe["name"] == name:
            return probe
    raise KeyError(f"Unknown Gemini Web probe: {surface}.{name}")


async def _execute_observed_rpc(client, probe: dict[str, str]):
    return await client._batch_execute(
        [_RawRPCData(probe["rpcid"], probe["payload"])],
        source_path=probe["source_path"],
        close_on_error=False,
    )


async def _fetch_scheduled_registry(client, max_chars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probe = _get_probe("scheduled", "scheduled_actions_registry")
    response = await _execute_observed_rpc(client, probe)
    bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
    body = bodies[0] if bodies else []
    raw_entries = body[0] if isinstance(body, list) and body and isinstance(body[0], list) else []
    entries = [_parse_scheduled_action_task_entry(item, max_chars) for item in raw_entries]
    diagnostic = {
        "source_rpc": probe["rpcid"],
        "observed": probe["observed"],
        "status_code": getattr(response, "status_code", None),
        "response_length": len(getattr(response, "text", "") or ""),
        "body_present": bool(bodies),
        "raw_entry_count": len(raw_entries),
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


def _get_scheduled_task_entry_from_body(body: Any) -> Any:
    if not isinstance(body, list) or not body:
        return None
    first = body[0]
    if isinstance(first, list) and first and isinstance(first[0], str):
        return first
    if isinstance(first, str):
        return body
    return None


async def _fetch_scheduled_task_by_id(
    client,
    action_id: str,
    max_chars: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    contract = get_contract("scheduled.get")
    response = await client._batch_execute(
        [_RawRPCData(contract.rpc_id, contract.build_payload(action_id=action_id))],
        source_path=contract.source_path,
        close_on_error=False,
    )
    bodies = _extract_rpc_bodies(response.text, contract.rpc_id)
    body = bodies[0] if bodies else []
    raw_entry = _get_scheduled_task_entry_from_body(body)
    entry = _parse_scheduled_action_task_entry(raw_entry, max_chars) if raw_entry is not None else None
    matched_task = bool(entry and entry.get("id") == action_id)
    diagnostic = {
        "source_rpc": contract.rpc_id,
        "contract_key": contract.key,
        "observed": contract.observed,
        "status_code": getattr(response, "status_code", None),
        "response_length": len(getattr(response, "text", "") or ""),
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


def _conditional_tool_decorator(mcp: MCPServer, enabled_tool_names: set[str]):
    """Build the @_tool decorator that registers only profile-enabled tools."""

    def _tool(tool_name: str, annotations) -> Callable[[_F], _F]:
        def decorator(func: _F) -> _F:
            if tool_name in enabled_tool_names:
                mcp.tool(annotations=annotations)(func)
            return func

        return decorator

    return _tool
