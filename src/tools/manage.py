"""
会话和 Gem 管理 MCP 工具
"""

import json
import os
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Any, Literal, Optional
import logging

from ..client_wrapper import get_gemini_client, initialize_client
from ..constants import MODEL_CONFIG
from .annotations import DESTRUCTIVE_REMOTE, READ_ONLY_LOCAL, READ_ONLY_REMOTE, READS_PRIVATE_REMOTE

logger = logging.getLogger(__name__)


ResponseFormat = Literal["markdown", "json"]
FeatureSurface = Literal[
    "all",
    "library",
    "sharing",
    "usage",
    "personalization",
    "import",
    "scheduled",
    "tool_modes",
]
UsageScope = Literal["quota", "model_state", "all"]
ScheduledScope = Literal["active", "inactive", "all"]
ManifestScope = Literal[
    "all",
    "chat",
    "core",
    "history",
    "account",
    "media",
    "files",
    "research",
    "gems",
    "cookie",
    "prompts",
]
TOOL_GROUP_MODULES = {
    "core": {"core", "media", "files", "research"},
    "basic": {"core"},
    "media": {"media"},
    "advanced": {"prompts", "research"},
    "manage": {"history", "account", "gems"},
    "file": {"files"},
    "research": {"research"},
    "prompts": {"prompts"},
    "all": {"core", "media", "files", "research", "history", "account", "gems"},
}


WEB_UI_CAPABILITIES = {
    "observed_at": "2026-06-18",
    "account_tier": "Gemini Web Pro",
    "locale": "zh-CN",
    "models": [
        {
            "alias": "flash-lite",
            "display_name": "3.1 Flash-Lite",
            "description": "极速回答",
            "thinking_mode_id": 6,
            "advanced_only": False,
        },
        {
            "alias": "flash",
            "display_name": "3.5 Flash",
            "description": "全方位帮助",
            "thinking_mode_id": 1,
            "advanced_only": False,
        },
        {
            "alias": "pro",
            "display_name": "3.1 Pro",
            "description": "高等数学与代码",
            "thinking_mode_id": 3,
            "advanced_only": True,
        },
    ],
    "thinking_levels": [
        {
            "id": "standard",
            "display_name": "标准",
            "description": "最适合回答大多数问题",
            "level_id": 1,
        },
        {
            "id": "extended",
            "display_name": "扩展",
            "description": "擅长解决复杂问题",
            "level_id": 2,
        },
    ],
    "tool_menu": [
        {"name": "upload_file", "label": "上传文件", "coverage": "gemini_upload_file"},
        {"name": "google_drive", "label": "从云端硬盘添加", "coverage": "ui_only"},
        {"name": "import_code", "label": "导入代码", "coverage": "gemini_upload_file"},
        {"name": "create_image", "label": "图片", "coverage": "gemini_generate_media"},
        {"name": "create_video", "label": "视频", "coverage": "gemini_generate_media"},
        {"name": "canvas", "label": "Canvas", "coverage": "library_capability"},
        {"name": "deep_research", "label": "Deep Research", "coverage": "gemini_deep_research"},
        {"name": "create_music", "label": "音乐", "coverage": "gemini_generate_music"},
        {"name": "guided_learning", "label": "学习辅导", "coverage": "library_capability"},
        {"name": "personalization_labs", "label": "个性化 / Labs", "coverage": "probe_only"},
    ],
    "settings_menu": [
        {"name": "activity", "label": "活动记录", "coverage": "external_google_activity"},
        {"name": "personalization", "label": "个性化智能服务", "coverage": "probe_only"},
        {"name": "memory_import", "label": "将记忆导入 Gemini", "coverage": "probe_only"},
        {"name": "usage_limits", "label": "用量限额", "coverage": "gemini_get_usage_limits"},
        {"name": "scheduled_actions", "label": "定时操作", "coverage": "gemini_list_scheduled_actions"},
        {"name": "gems", "label": "Gem", "coverage": "gemini_manage_gems"},
        {"name": "public_links", "label": "你的公开链接", "coverage": "gemini_list_public_links"},
        {"name": "theme", "label": "主题", "coverage": "ui_only"},
        {"name": "subscription", "label": "管理订阅", "coverage": "external_google_one"},
        {"name": "ultra_upsell", "label": "升级到 Google AI Ultra", "coverage": "external_google_one"},
        {"name": "notebooklm", "label": "NotebookLM", "coverage": "external_notebooklm"},
        {"name": "feedback", "label": "发送反馈", "coverage": "ui_only"},
        {"name": "help", "label": "帮助", "coverage": "ui_only"},
        {"name": "location", "label": "位置", "coverage": "ui_only"},
    ],
    "notes": [
        "Runtime model registry is still preferred when available.",
        "Drive picker, link mutation, settings mutation, and memory import mutation are not automated without a safer confirmed RPC contract.",
        "Scheduled actions are read-only; create/update/delete are intentionally not exposed.",
        "Probe tools intentionally omit raw response bodies and private account content.",
    ],
}


WEB_FEATURE_PROBES = [
    {
        "surface": "library",
        "name": "library_index",
        "rpcid": "sJBwce",
        "payload": "[[1,2]]",
        "source_path": "/app/library",
        "observed": "2026-06-18 Pro UI / Library",
    },
    {
        "surface": "library",
        "name": "library_assets",
        "rpcid": "VxUbXb",
        "payload": "[]",
        "source_path": "/app/library",
        "observed": "2026-06-18 Pro UI / Library",
    },
    {
        "surface": "library",
        "name": "library_locale_capabilities",
        "rpcid": "cYRIkd",
        "payload": '["zh-CN"]',
        "source_path": "/app/library",
        "observed": "2026-06-18 Pro UI / Library",
    },
    {
        "surface": "sharing",
        "name": "public_links_index",
        "rpcid": "K4WWud",
        "payload": '[[1],["zh-CN"]]',
        "source_path": "/app/sharing",
        "observed": "2026-06-18 Pro UI / Your public links",
    },
    {
        "surface": "sharing",
        "name": "sharing_state",
        "rpcid": "GPRiHf",
        "payload": "[]",
        "source_path": "/app/sharing",
        "observed": "2026-06-18 Pro UI / Your public links",
    },
    {
        "surface": "sharing",
        "name": "sharing_preferences",
        "rpcid": "maGuAc",
        "payload": "[1]",
        "source_path": "/app/sharing",
        "observed": "2026-06-18 Pro UI / Your public links",
    },
    {
        "surface": "usage",
        "name": "usage_quota",
        "rpcid": "qpEbW",
        "payload": "[[[1,11],[2,11],[6,11]]]",
        "source_path": "/app/usage",
        "observed": "2026-06-18 Pro UI / Usage limits",
    },
    {
        "surface": "usage",
        "name": "usage_model_state",
        "rpcid": "qpEbW",
        "payload": "[[[1,4],[6,6],[1,15]]]",
        "source_path": "/app/usage",
        "observed": "2026-06-18 Pro UI / Usage limits",
    },
    {
        "surface": "personalization",
        "name": "personalization_state",
        "rpcid": "GPRiHf",
        "payload": "[]",
        "source_path": "/app/personalization-settings",
        "observed": "2026-06-18 Pro UI / Personalization settings",
    },
    {
        "surface": "personalization",
        "name": "personalization_preferences",
        "rpcid": "maGuAc",
        "payload": "[1]",
        "source_path": "/app/personalization-settings",
        "observed": "2026-06-18 Pro UI / Personalization settings",
    },
    {
        "surface": "personalization",
        "name": "personalization_labels",
        "rpcid": "Te6DCf",
        "payload": '[["zh-CN"],[1]]',
        "source_path": "/app/personalization-settings",
        "observed": "2026-06-18 Pro UI / Personalization settings",
    },
    {
        "surface": "import",
        "name": "memory_import_state",
        "rpcid": "Te6DCf",
        "payload": '[["zh-CN"],[1]]',
        "source_path": "/app/import",
        "observed": "2026-06-18 Pro UI / Memory import",
    },
    {
        "surface": "scheduled",
        "name": "scheduled_actions_state",
        "rpcid": "otAQ7b",
        "payload": "[]",
        "source_path": "/scheduled",
        "observed": "2026-06-18 Pro UI / Scheduled actions",
    },
    {
        "surface": "scheduled",
        "name": "scheduled_actions_active",
        "rpcid": "MaZiqc",
        "payload": "[13,null,[1,null,1]]",
        "source_path": "/scheduled",
        "observed": "2026-06-18 Pro UI / Scheduled actions",
    },
    {
        "surface": "scheduled",
        "name": "scheduled_actions_inactive",
        "rpcid": "MaZiqc",
        "payload": "[13,null,[0,null,1]]",
        "source_path": "/scheduled",
        "observed": "2026-06-18 Pro UI / Scheduled actions",
    },
    {
        "surface": "tool_modes",
        "name": "tool_mode_status",
        "rpcid": "MyzX6c",
        "payload": "[]",
        "source_path": "/app",
        "observed": "2026-06-19 Pro UI / Canvas and Guided Learning tool mode toggles",
    },
]


class _RawRPCData:
    """Small compatible RPC payload for observed Gemini Web RPC ids not yet in gemini-webapi."""

    def __init__(self, rpcid: str, payload: str, identifier: str = "generic"):
        self.rpcid = rpcid
        self.payload = payload
        self.identifier = identifier

    def serialize(self) -> list:
        return [self.rpcid, self.payload, None, self.identifier]


def _format_timestamp(timestamp: object) -> str:
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _truncate(text: object, max_chars: int) -> str:
    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]"


def _paginate_items(items: list[Any], limit: int, offset: int, max_limit: int = 100) -> tuple[list[Any], dict[str, Any]]:
    safe_limit = min(max(limit, 1), max_limit)
    safe_offset = max(offset, 0)
    page = items[safe_offset : safe_offset + safe_limit]
    next_offset = safe_offset + len(page)
    has_more = next_offset < len(items)
    return page, {
        "total_count": len(items),
        "count": len(page),
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
    }


def _get_attr(item: object, name: str, default: object = "") -> object:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _get_chat_id(chat: object) -> str:
    return str(_get_attr(chat, "cid", "") or _get_attr(chat, "id", "") or "")


def _get_chat_title(chat: object) -> str:
    return str(_get_attr(chat, "title", "") or "Untitled")


def _chat_to_dict(chat) -> dict:
    timestamp = _get_attr(chat, "timestamp", None)
    return {
        "id": _get_chat_id(chat),
        "title": _get_chat_title(chat),
        "is_pinned": bool(_get_attr(chat, "is_pinned", False)),
        "timestamp": timestamp,
        "time": _format_timestamp(timestamp),
    }


def _sanitize_account_status(status: object) -> dict:
    if not isinstance(status, dict):
        return {"status": str(status)}

    summary = status.get("summary") if isinstance(status.get("summary"), dict) else {}
    rpc = status.get("rpc") if isinstance(status.get("rpc"), dict) else {}
    rpc_status = {}
    for name, payload in rpc.items():
        if not isinstance(payload, dict):
            rpc_status[name] = {"ok": bool(payload)}
            continue
        rpc_status[name] = {
            "ok": bool(payload.get("ok")),
            "status_code": payload.get("status_code"),
            "reject_code": payload.get("reject_code"),
        }

    return {
        "source_path": status.get("source_path"),
        "account_path": status.get("account_path"),
        "summary": summary,
        "rpc": rpc_status,
    }


def _turn_to_dict(turn, max_chars: int) -> dict:
    return {
        "role": str(_get_attr(turn, "role", "unknown") or "unknown"),
        "text": _truncate(_get_attr(turn, "text", ""), max_chars),
    }


async def _read_chat_turns(client: object, chat_id: str, limit: int, max_chars: int) -> tuple[object, list[dict]]:
    if not hasattr(client, "read_chat"):
        raise RuntimeError("当前 gemini-webapi 不支持 read_chat。")
    history = await client.read_chat(chat_id, limit=limit)
    turns = _get_attr(history, "turns", []) if history else []
    return history, [_turn_to_dict(turn, max_chars) for turn in (turns or [])[:limit]]


def _turn_matches_query(turn: dict[str, str], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return False
    return needle in turn.get("role", "").lower() or needle in turn.get("text", "").lower()


def _chat_export_payload(
    chat_id: str,
    history: object,
    turns: list[dict],
    metadata: dict[str, Any] | None,
    limit: int,
    max_chars_per_turn: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": str(_get_attr(history, "cid", "") or chat_id),
        "count": len(turns),
        "limit": limit,
        "max_chars_per_turn": max_chars_per_turn,
        "turns": turns,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def _format_chat_export_markdown(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    title = metadata.get("title") or payload["chat_id"]
    lines = [
        f"## Gemini Chat Export: {title}",
        f"Chat ID: {payload['chat_id']}",
        f"Turns: {payload['count']}",
    ]
    if metadata.get("time"):
        lines.append(f"Time: {metadata['time']}")
    for idx, turn in enumerate(payload["turns"], 1):
        lines.extend(["", f"### {idx}. {turn['role']}", turn["text"]])
    return "\n".join(lines)


def _summarize_probe_response(response_text: str, rpcid: str) -> dict[str, Any]:
    try:
        from gemini_webapi.utils import extract_json_from_response, get_nested_value
    except ImportError:
        return {"parsed": False, "response_parts": 0}

    try:
        parts = extract_json_from_response(response_text)
    except Exception:
        return {"parsed": False, "response_parts": 0}

    body_count = 0
    reject_code = None
    for part in parts:
        if get_nested_value(part, [0]) != "wrb.fr":
            continue
        if get_nested_value(part, [1]) != rpcid:
            continue
        code = get_nested_value(part, [5, 0])
        if isinstance(code, int):
            reject_code = code
        body = get_nested_value(part, [2])
        if body is not None:
            body_count += 1

    return {
        "parsed": True,
        "response_parts": len(parts),
        "body_count": body_count,
        "reject_code": reject_code,
    }


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


def _extract_rpc_bodies(response_text: str, rpcid: str) -> list[Any]:
    from gemini_webapi.utils import extract_json_from_response, get_nested_value

    bodies = []
    for part in extract_json_from_response(response_text):
        if get_nested_value(part, [0]) != "wrb.fr":
            continue
        if get_nested_value(part, [1]) != rpcid:
            continue
        body = get_nested_value(part, [2])
        if isinstance(body, str):
            try:
                bodies.append(json.loads(body))
            except json.JSONDecodeError:
                bodies.append(body)
        elif body is not None:
            bodies.append(body)
    return bodies


def _parse_public_link_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "id": entry[0] if len(entry) > 0 and isinstance(entry[0], str) else "",
        "title": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "disabled": bool(entry[2]) if len(entry) > 2 else False,
        "url": entry[4] if len(entry) > 4 and isinstance(entry[4], str) else "",
        "field_count": len(entry),
    }


def _parse_usage_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    key = entry[0] if len(entry) > 0 else None
    reset = entry[3] if len(entry) > 3 else None
    reset_time = ""
    reset_timestamp = None
    if isinstance(reset, list) and reset:
        seconds = reset[0]
        nanos = reset[1] if len(reset) > 1 else 0
        if isinstance(seconds, (int, float)):
            reset_timestamp = float(seconds) + (float(nanos or 0) / 1e9)
            reset_time = _format_timestamp(reset_timestamp)

    return {
        "key": key,
        "status": entry[1] if len(entry) > 1 else None,
        "tier": entry[2] if len(entry) > 2 else None,
        "reset_timestamp": reset_timestamp,
        "reset_time": reset_time,
        "limit_value": entry[4] if len(entry) > 4 else None,
        "remaining_value": entry[5] if len(entry) > 5 else None,
        "field_count": len(entry),
    }


def _parse_library_capability(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    aliases = entry[0] if len(entry) > 0 and isinstance(entry[0], list) else []
    return {
        "aliases": [alias for alias in aliases if isinstance(alias, str)],
        "name": entry[1] if len(entry) > 1 and isinstance(entry[1], str) else "",
        "description": entry[2] if len(entry) > 2 and isinstance(entry[2], str) else "",
        "details": entry[3] if len(entry) > 3 and isinstance(entry[3], str) else "",
        "field_count": len(entry),
    }


def _parse_scheduled_action_entry(entry: Any, max_chars: int) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}

    scheduled_at = entry[5] if len(entry) > 5 else None
    scheduled_timestamp = None
    scheduled_time = ""
    if isinstance(scheduled_at, list) and scheduled_at:
        seconds = scheduled_at[0]
        nanos = scheduled_at[1] if len(scheduled_at) > 1 else 0
        if isinstance(seconds, (int, float)):
            scheduled_timestamp = float(seconds) + (float(nanos or 0) / 1e9)
            scheduled_time = _format_timestamp(scheduled_timestamp)

    return {
        "id": entry[0] if len(entry) > 0 and isinstance(entry[0], str) else "",
        "title": _truncate(entry[1], max_chars) if len(entry) > 1 and isinstance(entry[1], str) else "",
        "enabled": entry[2] if len(entry) > 2 and isinstance(entry[2], bool) else None,
        "scheduled_timestamp": scheduled_timestamp,
        "scheduled_time": scheduled_time,
        "schedule_label": _truncate(entry[7], max_chars) if len(entry) > 7 and isinstance(entry[7], str) else "",
        "kind": entry[9] if len(entry) > 9 else None,
        "field_count": len(entry),
    }


def _parse_tool_mode_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, list):
        return {"raw_type": type(entry).__name__}
    return {
        "mode_id": entry[0] if len(entry) > 0 else None,
        "available": entry[1] if len(entry) > 1 and isinstance(entry[1], bool) else None,
        "quota_value": entry[2] if len(entry) > 2 else None,
        "used_value": entry[3] if len(entry) > 3 else None,
        "reset_or_extra": entry[4] if len(entry) > 4 else None,
        "state": entry[5] if len(entry) > 5 else None,
        "field_count": len(entry),
    }


def _web_capabilities_payload() -> dict[str, Any]:
    payload = json.loads(json.dumps(WEB_UI_CAPABILITIES, ensure_ascii=False))
    payload["feature_probes"] = [
        {
            "surface": probe["surface"],
            "name": probe["name"],
            "rpcid": probe["rpcid"],
            "source_path": probe["source_path"],
            "observed": probe["observed"],
        }
        for probe in WEB_FEATURE_PROBES
    ]
    payload["mcp_tools"] = {
        "chat": ["gemini_chat", "gemini_chat_stream", "gemini_start_chat", "gemini_send_message"],
        "history": [
            "gemini_list_chats",
            "gemini_search_chats",
            "gemini_read_chat",
            "gemini_export_chat",
            "gemini_delete_chat",
        ],
        "account": [
            "gemini_inspect_account",
            "gemini_get_tool_manifest",
            "gemini_get_web_capabilities",
            "gemini_probe_web_features",
            "gemini_list_public_links",
            "gemini_get_usage_limits",
            "gemini_list_library_capabilities",
            "gemini_list_scheduled_actions",
            "gemini_get_tool_mode_status",
            "gemini_list_models",
        ],
        "media": ["gemini_generate_media", "gemini_generate_music"],
        "files": ["gemini_upload_file", "gemini_analyze_url"],
        "research": ["gemini_deep_research"],
        "gems": ["gemini_manage_gems"],
    }
    return payload


TOOL_MANIFEST: list[dict[str, Any]] = [
    {
        "name": "gemini_chat",
        "group": "core",
        "purpose": "One-shot Gemini Web chat with model, thinking_level, Gem, image, temporary chat, and learning_mode options.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_user_prompt_and_optional_files",
        "pagination": False,
    },
    {
        "name": "gemini_chat_stream",
        "group": "core",
        "purpose": "Streaming one-shot Gemini Web chat with the same request controls as gemini_chat.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_user_prompt_and_optional_files",
        "pagination": False,
    },
    {
        "name": "gemini_start_chat",
        "group": "core",
        "purpose": "Create a local multi-turn session backed by Gemini Web; can retain or schedule cleanup of the remote chat.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_user_prompt_when_initial_message_is_provided",
        "pagination": False,
    },
    {
        "name": "gemini_send_message",
        "group": "core",
        "purpose": "Send a message to an existing local session.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_user_prompt_and_optional_files",
        "pagination": False,
    },
    {
        "name": "gemini_send_message_stream",
        "group": "core",
        "purpose": "Stream a response from an existing local session.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_user_prompt_and_optional_files",
        "pagination": False,
    },
    {
        "name": "gemini_list_sessions",
        "group": "core",
        "purpose": "List local in-process sessions only.",
        "read_only": True,
        "destructive": False,
        "privacy": "local_session_metadata",
        "pagination": False,
    },
    {
        "name": "gemini_reset_session",
        "group": "core",
        "purpose": "Reset a local session and optionally delete its remote Gemini Web chat.",
        "read_only": False,
        "destructive": True,
        "privacy": "local_session_metadata",
        "pagination": False,
    },
    {
        "name": "gemini_generate_media",
        "group": "media",
        "purpose": "Generate image, video, or music through Gemini Web generation surfaces.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_user_prompt_and_optional_reference_files",
        "pagination": False,
    },
    {
        "name": "gemini_generate_music",
        "group": "media",
        "purpose": "Convenience music-generation wrapper using the observed Flash/Pro backend split.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_user_prompt",
        "pagination": False,
    },
    {
        "name": "gemini_upload_file",
        "group": "files",
        "purpose": "Upload and analyze a local file through Gemini Web.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_local_file_content",
        "pagination": False,
    },
    {
        "name": "gemini_analyze_url",
        "group": "files",
        "purpose": "Ask Gemini Web to analyze a URL.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_url_to_gemini",
        "pagination": False,
    },
    {
        "name": "gemini_deep_research",
        "group": "research",
        "purpose": "Run the Gemini Web Deep Research planning and report workflow.",
        "read_only": False,
        "destructive": False,
        "privacy": "sends_research_query",
        "pagination": False,
    },
    {
        "name": "gemini_list_chats",
        "group": "history",
        "purpose": "List Gemini Web chat-history metadata.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_private_chat_metadata",
        "pagination": True,
    },
    {
        "name": "gemini_search_chats",
        "group": "history",
        "purpose": "Search chat titles/IDs by default; scan turn text only when scan_turns=true.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_private_chat_metadata_and_optional_turn_text",
        "pagination": True,
    },
    {
        "name": "gemini_read_chat",
        "group": "history",
        "purpose": "Read turns from a selected Gemini Web chat.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_private_chat_text",
        "pagination": True,
    },
    {
        "name": "gemini_export_chat",
        "group": "history",
        "purpose": "Export one selected Gemini Web chat as Markdown or JSON.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_private_chat_text",
        "pagination": True,
    },
    {
        "name": "gemini_delete_chat",
        "group": "history",
        "purpose": "Delete a selected remote Gemini Web chat.",
        "read_only": False,
        "destructive": True,
        "privacy": "uses_private_chat_id",
        "pagination": False,
    },
    {
        "name": "gemini_inspect_account",
        "group": "account",
        "purpose": "Inspect account feature/RPC status without raw RPC previews.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_sanitized_account_status",
        "pagination": False,
    },
    {
        "name": "gemini_get_web_capabilities",
        "group": "account",
        "purpose": "Return the observed Gemini Web Pro capability map and MCP coverage.",
        "read_only": True,
        "destructive": False,
        "privacy": "static_no_account_content",
        "pagination": False,
    },
    {
        "name": "gemini_get_tool_manifest",
        "group": "account",
        "purpose": "Return this agent-facing tool manifest with safety, privacy, and workflow metadata.",
        "read_only": True,
        "destructive": False,
        "privacy": "static_no_account_content",
        "pagination": False,
    },
    {
        "name": "gemini_probe_web_features",
        "group": "account",
        "purpose": "Probe observed read-only Gemini Web RPC reachability without returning raw bodies.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_rpc_status_only",
        "pagination": False,
    },
    {
        "name": "gemini_list_public_links",
        "group": "account",
        "purpose": "List Gemini Web public-link entries.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_private_public_link_index",
        "pagination": True,
    },
    {
        "name": "gemini_get_usage_limits",
        "group": "account",
        "purpose": "Read Gemini Web usage/quota structures.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_private_usage_state",
        "pagination": False,
    },
    {
        "name": "gemini_list_library_capabilities",
        "group": "account",
        "purpose": "List localized Library capability/template entries, not private Library assets.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_template_capabilities",
        "pagination": True,
    },
    {
        "name": "gemini_list_scheduled_actions",
        "group": "account",
        "purpose": "Read active/inactive scheduled-action entries.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_private_scheduled_action_titles",
        "pagination": True,
    },
    {
        "name": "gemini_get_tool_mode_status",
        "group": "account",
        "purpose": "Read Web-internal Canvas/Guided Learning mode status rows.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_mode_status_only",
        "pagination": True,
    },
    {
        "name": "gemini_list_models",
        "group": "account",
        "purpose": "List MCP model aliases and runtime model registry.",
        "read_only": True,
        "destructive": False,
        "privacy": "reads_account_model_registry",
        "pagination": False,
    },
    {
        "name": "gemini_manage_gems",
        "group": "gems",
        "purpose": "List, create, update, or delete Gems depending on action.",
        "read_only": False,
        "destructive": True,
        "privacy": "reads_or_modifies_private_gems",
        "pagination": False,
    },
    {
        "name": "gemini_get_cookie_status",
        "group": "cookie",
        "purpose": "Report local cookie availability without printing cookie values.",
        "read_only": True,
        "destructive": False,
        "privacy": "local_auth_status_only",
        "pagination": False,
    },
    {
        "name": "gemini_get_cookie_from_browser",
        "group": "cookie",
        "purpose": "Load Gemini cookies from a local browser into the server runtime.",
        "read_only": False,
        "destructive": False,
        "privacy": "reads_local_browser_auth_secret",
        "pagination": False,
    },
    {
        "name": "gemini_reset",
        "group": "cookie",
        "purpose": "Reset the local Gemini client instance.",
        "read_only": False,
        "destructive": False,
        "privacy": "local_runtime_state",
        "pagination": False,
    },
    {
        "name": "gemini_manage_prompts",
        "group": "prompts",
        "purpose": "Manage local prompt snippets when GEMINI_TOOLS=prompts is enabled.",
        "read_only": False,
        "destructive": True,
        "privacy": "local_prompt_library",
        "pagination": False,
    },
]


MANIFEST_WORKFLOWS = [
    {
        "name": "safe_account_audit",
        "steps": [
            "gemini_get_tool_manifest",
            "gemini_get_web_capabilities",
            "gemini_inspect_account",
            "gemini_probe_web_features",
        ],
        "notes": "Read-only; avoids raw private RPC bodies.",
    },
    {
        "name": "chat_history_find_and_export",
        "steps": [
            "gemini_list_chats",
            "gemini_search_chats",
            "gemini_read_chat or gemini_export_chat",
        ],
        "notes": "Start with metadata search. Use scan_turns=true only when the user asks to search chat text.",
    },
    {
        "name": "current_pro_generation",
        "steps": [
            "gemini_list_models",
            "gemini_chat with model=pro and thinking_level=extended",
            "optional learning_mode for guided study outputs",
        ],
        "notes": "Sends user prompts to Gemini Web and may create remote chats unless temporary/cleanup settings are used.",
    },
    {
        "name": "web_surface_inventory",
        "steps": [
            "gemini_list_public_links",
            "gemini_get_usage_limits",
            "gemini_list_library_capabilities",
            "gemini_list_scheduled_actions",
            "gemini_get_tool_mode_status",
        ],
        "notes": "Read-only but may reveal account-private metadata such as links and scheduled-action titles.",
    },
]


def _tool_availability(tool: dict[str, Any]) -> list[str]:
    if tool["name"] == "gemini_get_tool_manifest":
        return ["always"]
    group = tool["group"]
    if group in {"core", "media", "files", "research"}:
        return ["core", "all"]
    if group in {"history", "account", "gems"}:
        return ["manage", "all"]
    if group == "cookie":
        return ["always"]
    if group == "prompts":
        return ["prompts"]
    return []


def _current_enabled_manifest_groups() -> tuple[list[str], set[str]]:
    configured = [
        item.strip()
        for item in os.environ.get("GEMINI_TOOLS", "core").split(",")
        if item.strip()
    ]
    enabled = {"cookie"}
    for group in configured or ["core"]:
        enabled.update(TOOL_GROUP_MODULES.get(group, {group}))
    enabled.add("manifest")
    return configured or ["core"], enabled


def _tool_manifest_payload(scope: ManifestScope = "all") -> dict[str, Any]:
    current_tool_groups, enabled_groups = _current_enabled_manifest_groups()
    filter_scope = "core" if scope == "chat" else scope
    tools = [
        {
            **item,
            "availability": _tool_availability(item),
            "current_enabled": item["group"] in enabled_groups or item["name"] == "gemini_get_tool_manifest",
        }
        for item in TOOL_MANIFEST
        if filter_scope == "all"
        or item["group"] == filter_scope
        or (filter_scope == "core" and item["group"] == "core")
    ]
    groups: dict[str, int] = {}
    for tool in tools:
        groups[tool["group"]] = groups.get(tool["group"], 0) + 1
    return {
        "server": "gemini_web_mcp",
        "observed_web_ui": WEB_UI_CAPABILITIES["observed_at"],
        "scope": scope,
        "total_count": len(tools),
        "current_tool_groups": current_tool_groups,
        "current_enabled_count": sum(1 for item in tools if item["current_enabled"]),
        "groups": groups,
        "tools": tools,
        "workflows": MANIFEST_WORKFLOWS if filter_scope in {"all", "core", "history", "account"} else [],
        "safety_notes": [
            "Annotations and manifest metadata are planning hints, not a permission system.",
            "Tools marked privacy=reads_private_chat_text return private Gemini chat content.",
            "Tools marked destructive=true can delete or overwrite remote or local user data.",
            "Probe tools intentionally omit raw response bodies.",
        ],
    }


def _format_tool_manifest_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Gemini MCP Tool Manifest",
        (
            f"Scope: {payload['scope']} · Tools: {payload['total_count']} · "
            f"Current enabled: {payload['current_enabled_count']} · "
            f"Observed Web UI: {payload['observed_web_ui']}"
        ),
        f"Current GEMINI_TOOLS: {', '.join(payload['current_tool_groups'])}",
        "",
        "### Groups",
    ]
    for group, count in sorted(payload["groups"].items()):
        lines.append(f"- {group}: {count}")

    lines.extend(["", "### Tools"])
    for item in payload["tools"]:
        flags = []
        flags.append("read-only" if item["read_only"] else "writes")
        if item["destructive"]:
            flags.append("destructive")
        if item["pagination"]:
            flags.append("paginated")
        lines.append(f"- `{item['name']}` [{item['group']}; {', '.join(flags)}]: {item['purpose']}")
        lines.append(f"  privacy: {item['privacy']}")
        lines.append(f"  availability: {', '.join(item['availability']) or 'custom'}")
        lines.append(f"  current_enabled: {item['current_enabled']}")

    if payload["workflows"]:
        lines.extend(["", "### Recommended Workflows"])
        for workflow in payload["workflows"]:
            lines.append(f"- {workflow['name']}: {' -> '.join(workflow['steps'])}")
            lines.append(f"  {workflow['notes']}")

    lines.extend(["", "### Safety Notes"])
    lines.extend(f"- {note}" for note in payload["safety_notes"])
    return "\n".join(lines)


def _format_web_capabilities_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Gemini Web Pro 能力清单",
        f"观察日期: {payload['observed_at']} · 账号层级: {payload['account_tier']} · Locale: {payload['locale']}",
        "",
        "### 模型",
    ]
    for model in payload["models"]:
        advanced = "Pro/高级" if model.get("advanced_only") else "通用"
        lines.append(
            "- {alias}: {display_name} ({description}) · thinking_mode_id={mode} · {advanced}".format(
                alias=model["alias"],
                display_name=model["display_name"],
                description=model["description"],
                mode=model["thinking_mode_id"],
                advanced=advanced,
            )
        )

    lines.extend(["", "### 思考等级"])
    for level in payload["thinking_levels"]:
        lines.append(
            f"- {level['id']}: {level['display_name']} ({level['description']}) · level_id={level['level_id']}"
        )

    lines.extend(["", "### 网页工具菜单"])
    for item in payload["tool_menu"]:
        lines.append(f"- {item['label']} ({item['name']}): {item['coverage']}")

    lines.extend(["", "### 设置入口"])
    for item in payload["settings_menu"]:
        lines.append(f"- {item['label']} ({item['name']}): {item['coverage']}")

    lines.extend(["", "### 可探测 RPC"])
    grouped: dict[str, list[dict]] = {}
    for probe in payload["feature_probes"]:
        grouped.setdefault(probe["surface"], []).append(probe)
    for surface, probes in grouped.items():
        names = ", ".join(f"{probe['name']}={probe['rpcid']}" for probe in probes)
        lines.append(f"- {surface}: {names}")

    lines.extend(["", "### MCP 工具覆盖"])
    for group, tools in payload["mcp_tools"].items():
        lines.append(f"- {group}: {', '.join(tools)}")

    lines.extend(["", "### 说明"])
    lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines)


def register_manage_tools(mcp: FastMCP):

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_list_chats(
        limit: int = 10,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出 Gemini 历史对话记录元数据，支持分页。"""
        client = get_gemini_client()
        await initialize_client()

        try:
            chats = client.list_chats() or []
            if not chats:
                return [TextContent(type="text", text="暂无历史对话。")]

            safe_limit = min(max(limit, 1), 50)
            safe_offset = max(offset, 0)
            page = chats[safe_offset : safe_offset + safe_limit]
            items = [_chat_to_dict(chat) for chat in page]
            payload = {
                "total_count": len(chats),
                "count": len(items),
                "offset": safe_offset,
                "limit": safe_limit,
                "has_more": safe_offset + len(items) < len(chats),
                "next_offset": safe_offset + len(items) if safe_offset + len(items) < len(chats) else None,
                "items": items,
            }

            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            chat_list = [
                "## 📜 历史对话",
                f"共 {payload['total_count']} 条；当前 {payload['offset']}..{payload['offset'] + payload['count'] - 1}",
            ]
            for i, chat in enumerate(items, safe_offset + 1):
                pin = " 📌" if chat["is_pinned"] else ""
                time_text = f" · {chat['time']}" if chat["time"] else ""
                chat_list.append(f"{i}. {chat['title']}{pin} (ID: {chat['id']}){time_text}")
            if payload["has_more"]:
                chat_list.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(chat_list))]

        except Exception as e:
            logger.error(f"获取聊天列表失败: {e}")
            return [TextContent(type="text", text=f"❌ 获取失败: {str(e)}")]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_read_chat(
        chat_id: str,
        limit: int = 20,
        response_format: ResponseFormat = "markdown",
        max_chars_per_turn: int = 4000,
    ) -> list[TextContent]:
        """读取指定 Gemini 历史对话内容。会返回私人聊天文本，请只在用户需要时调用。"""
        client = get_gemini_client()
        await initialize_client()

        if not chat_id:
            return [TextContent(type="text", text="❌ 读取聊天需要提供 chat_id。")]
        if not hasattr(client, "read_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 read_chat。")]

        try:
            safe_limit = min(max(limit, 1), 100)
            safe_chars = min(max(max_chars_per_turn, 200), 20000)
            history = await client.read_chat(chat_id, limit=safe_limit)
            if not history:
                return [TextContent(type="text", text=f"未找到聊天: {chat_id}")]
            turns = getattr(history, "turns", []) or []
            items = [_turn_to_dict(turn, safe_chars) for turn in turns[:safe_limit]]
            payload = {
                "chat_id": getattr(history, "cid", chat_id),
                "count": len(items),
                "limit": safe_limit,
                "turns": items,
            }

            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            lines = [f"## 💬 聊天记录: {payload['chat_id']}", f"返回 {payload['count']} 条 turn"]
            for idx, turn in enumerate(items, 1):
                lines.extend(["", f"### {idx}. {turn['role']}", turn["text"]])
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"读取聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取失败: {str(e)}")]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_search_chats(
        query: str,
        limit: int = 10,
        offset: int = 0,
        scan_turns: bool = False,
        turns_per_chat: int = 20,
        max_chars_per_turn: int = 1000,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        搜索 Gemini Web 历史对话。

        默认只搜索标题/ID；只有 scan_turns=true 时才读取聊天正文进行内容匹配。
        """
        client = get_gemini_client()
        await initialize_client()

        needle = (query or "").strip()
        if not needle:
            return [TextContent(type="text", text="❌ 搜索聊天需要提供 query。")]
        if scan_turns and not hasattr(client, "read_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持正文搜索需要的 read_chat。")]

        try:
            chats = client.list_chats() or []
            safe_limit = min(max(limit, 1), 50)
            safe_offset = max(offset, 0)
            safe_turn_limit = min(max(turns_per_chat, 1), 50)
            safe_chars = min(max(max_chars_per_turn, 100), 4000)
            page = chats[safe_offset : safe_offset + safe_limit]
            matches = []
            lowered = needle.lower()

            for chat in page:
                item = _chat_to_dict(chat)
                fields = []
                snippets = []
                if lowered in item["title"].lower():
                    fields.append("title")
                if item["id"] and lowered in item["id"].lower():
                    fields.append("id")

                if scan_turns:
                    try:
                        _history, turns = await _read_chat_turns(
                            client,
                            item["id"],
                            safe_turn_limit,
                            safe_chars,
                        )
                    except Exception as e:
                        snippets.append({"error": f"{type(e).__name__}: {e}"})
                        turns = []
                    for idx, turn in enumerate(turns, 1):
                        if _turn_matches_query(turn, needle):
                            fields.append("turn")
                            snippets.append(
                                {
                                    "turn_index": idx,
                                    "role": turn["role"],
                                    "text": _truncate(turn["text"], safe_chars),
                                }
                            )

                if fields:
                    match = {
                        **item,
                        "matched_fields": sorted(set(fields)),
                    }
                    if snippets:
                        match["snippets"] = snippets[:5]
                    matches.append(match)

            next_offset = safe_offset + len(page)
            payload = {
                "query": needle,
                "scan_turns": scan_turns,
                "total_count": len(chats),
                "scanned_count": len(page),
                "match_count": len(matches),
                "offset": safe_offset,
                "limit": safe_limit,
                "has_more": next_offset < len(chats),
                "next_offset": next_offset if next_offset < len(chats) else None,
                "matches": matches,
                "note": "正文搜索只会在 scan_turns=true 时读取当前页聊天内容。",
            }

            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            lines = [
                "## Gemini 历史搜索",
                f"Query: {needle}",
                f"Scanned: {payload['scanned_count']}/{payload['total_count']} · Matches: {payload['match_count']}",
            ]
            if not matches:
                lines.append("未在当前页找到匹配项。")
            for idx, match in enumerate(matches, 1):
                fields = ", ".join(match["matched_fields"])
                time_text = f" · {match['time']}" if match.get("time") else ""
                lines.append(f"{idx}. {match['title']} (ID: {match['id']}) · fields={fields}{time_text}")
                for snippet in match.get("snippets", []):
                    if snippet.get("error"):
                        lines.append(f"   - read error: {snippet['error']}")
                    else:
                        text = snippet.get("text", "").replace("\n", " ")
                        lines.append(f"   - turn {snippet.get('turn_index')} {snippet.get('role')}: {text}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            if not scan_turns:
                lines.append("\n说明: 当前只搜索标题/ID；如需正文匹配，传入 scan_turns=true。")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"搜索聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 搜索失败: {str(e)}")]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_export_chat(
        chat_id: str,
        response_format: ResponseFormat = "markdown",
        limit: int = 100,
        max_chars_per_turn: int = 20000,
        include_metadata: bool = True,
    ) -> list[TextContent]:
        """导出指定 Gemini Web 历史对话为 Markdown 或 JSON。会返回私人聊天文本。"""
        client = get_gemini_client()
        await initialize_client()

        if not chat_id:
            return [TextContent(type="text", text="❌ 导出聊天需要提供 chat_id。")]
        if not hasattr(client, "read_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 read_chat。")]

        try:
            safe_limit = min(max(limit, 1), 200)
            safe_chars = min(max(max_chars_per_turn, 200), 20000)
            history, turns = await _read_chat_turns(client, chat_id, safe_limit, safe_chars)
            if not history:
                return [TextContent(type="text", text=f"未找到聊天: {chat_id}")]

            metadata = None
            if include_metadata:
                metadata = {"id": chat_id}
                try:
                    chats = client.list_chats() if hasattr(client, "list_chats") else []
                    for chat in chats or []:
                        if _get_chat_id(chat) == chat_id:
                            metadata = _chat_to_dict(chat)
                            break
                except Exception as e:
                    metadata["metadata_warning"] = f"{type(e).__name__}: {e}"

            payload = _chat_export_payload(chat_id, history, turns, metadata, safe_limit, safe_chars)
            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
            return [TextContent(type="text", text=_format_chat_export_markdown(payload))]
        except Exception as e:
            logger.error(f"导出聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 导出失败: {str(e)}")]

    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    async def gemini_delete_chat(chat_id: str) -> list[TextContent]:
        """删除指定 Gemini 历史对话。该操作会修改远端聊天记录。"""
        client = get_gemini_client()
        await initialize_client()

        if not chat_id:
            return [TextContent(type="text", text="❌ 删除聊天需要提供 chat_id。")]
        if not hasattr(client, "delete_chat"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 delete_chat。")]

        try:
            await client.delete_chat(chat_id)
            return [TextContent(type="text", text=f"✅ 已删除聊天: {chat_id}")]
        except Exception as e:
            logger.error(f"删除聊天失败: {e}")
            return [TextContent(type="text", text=f"❌ 删除失败: {str(e)}")]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_inspect_account(
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """检查当前 Gemini 账号可用能力和 Web RPC 状态。"""
        client = get_gemini_client()
        await initialize_client()

        if not hasattr(client, "inspect_account_status"):
            return [TextContent(type="text", text="❌ 当前 gemini-webapi 不支持 inspect_account_status。")]

        try:
            status = await client.inspect_account_status()
            sanitized = _sanitize_account_status(status)
            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(sanitized, ensure_ascii=False, indent=2))]

            summary = sanitized.get("summary", {})
            lines = ["## Gemini 账号能力状态"]
            if summary:
                for key, value in summary.items():
                    lines.append(f"- {key}: {value}")
            rpc = sanitized.get("rpc", {})
            if rpc:
                lines.extend(["", "## Web RPC 探测"])
                for name, payload in rpc.items():
                    ok = "可用" if payload.get("ok") else "不可用"
                    status_code = payload.get("status_code")
                    reject_code = payload.get("reject_code")
                    suffix = f" HTTP {status_code}" if status_code else ""
                    if reject_code:
                        suffix += f" reject={reject_code}"
                    lines.append(f"- {name}: {ok}{suffix}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"账号状态检查失败: {e}")
            return [TextContent(type="text", text=f"❌ 检查失败: {str(e)}")]

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    async def gemini_probe_web_features(
        surface: FeatureSurface = "all",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        探测新版 Gemini Web Pro 页面入口背后的只读 RPC 是否可达。

        这个工具只返回 HTTP/RPC 状态和 reject code，不返回响应正文或账号内容。
        """
        client = get_gemini_client()
        await initialize_client()

        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持底层 batch RPC 探测。")]

        selected = [
            probe
            for probe in WEB_FEATURE_PROBES
            if surface == "all" or probe["surface"] == surface
        ]
        results = []
        for probe in selected:
            try:
                response = await client._batch_execute(
                    [_RawRPCData(probe["rpcid"], probe["payload"])],
                    source_path=probe["source_path"],
                    close_on_error=False,
                )
                summary = _summarize_probe_response(response.text, probe["rpcid"])
                reject_code = summary.get("reject_code")
                ok = response.status_code == 200 and reject_code is None
                results.append(
                    {
                        "surface": probe["surface"],
                        "name": probe["name"],
                        "rpcid": probe["rpcid"],
                        "source_path": probe["source_path"],
                        "observed": probe["observed"],
                        "ok": ok,
                        "status_code": response.status_code,
                        **summary,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "surface": probe["surface"],
                        "name": probe["name"],
                        "rpcid": probe["rpcid"],
                        "source_path": probe["source_path"],
                        "observed": probe["observed"],
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )

        payload = {
            "surface": surface,
            "count": len(results),
            "ok_count": sum(1 for item in results if item.get("ok")),
            "results": results,
            "note": "Probe output intentionally omits raw response bodies and account content.",
        }

        if response_format == "json":
            return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

        lines = [
            "## Gemini Web 功能探测",
            f"范围: {surface}",
            f"可用: {payload['ok_count']}/{payload['count']}",
        ]
        grouped: dict[str, list[dict]] = {}
        for item in results:
            grouped.setdefault(item["surface"], []).append(item)
        for group_name, items in grouped.items():
            lines.extend(["", f"### {group_name}"])
            for item in items:
                status = "可达" if item.get("ok") else "不可达"
                reject = item.get("reject_code")
                suffix = f", reject={reject}" if reject is not None else ""
                if item.get("error"):
                    suffix += f", error={item['error']}"
                lines.append(f"- {item['name']} ({item['rpcid']}): {status}{suffix}")
        lines.append("\n说明: 输出已省略原始响应正文和账号内容。")
        return [TextContent(type="text", text="\n".join(lines))]

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    async def gemini_get_web_capabilities(
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        返回基于 Gemini Web Pro 实测 UI 的模型、工具菜单、设置入口和 MCP 覆盖清单。

        这是只读静态清单；实时 RPC 可达性请配合 gemini_probe_web_features 使用。
        """
        payload = _web_capabilities_payload()
        if response_format == "json":
            return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
        return [TextContent(type="text", text=_format_web_capabilities_markdown(payload))]

    @mcp.tool(annotations=READ_ONLY_LOCAL)
    async def gemini_get_tool_manifest(
        scope: ManifestScope = "all",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        返回面向 agent 的 Gemini MCP 工具清单，包含能力、隐私、分页和安全提示。

        这是静态只读清单，不访问账号内容。
        """
        payload = _tool_manifest_payload(scope)
        if response_format == "json":
            return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
        return [TextContent(type="text", text=_format_tool_manifest_markdown(payload))]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_list_public_links(
        limit: int = 20,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出 Gemini Web “你的公开链接”页面返回的公开链接条目。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持公开链接 RPC。")]

        try:
            probe = _get_probe("sharing", "public_links_index")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            entries = bodies[0] if bodies and isinstance(bodies[0], list) else []
            parsed_links = [_parse_public_link_entry(item) for item in entries]
            links, page_info = _paginate_items(parsed_links, limit, offset)
            payload = {
                **page_info,
                "items": links,
                "source_rpc": probe["rpcid"],
                "observed": probe["observed"],
            }
            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            if not links:
                return [TextContent(type="text", text="暂无公开链接。")]
            lines = [
                "## Gemini 公开链接",
                f"共 {payload['total_count']} 条；当前 offset={payload['offset']} count={payload['count']}",
            ]
            for idx, link in enumerate(links, payload["offset"] + 1):
                title = link.get("title") or "(untitled)"
                disabled = "禁用" if link.get("disabled") else "启用"
                url = link.get("url") or "(no url)"
                lines.append(f"{idx}. {title} [{disabled}]\n   ID: {link.get('id', '')}\n   URL: {url}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"公开链接列表读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取公开链接失败: {str(e)}")]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_get_usage_limits(
        scope: UsageScope = "all",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """读取 Gemini Web 用量限额页面返回的限额/模型状态结构。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持用量限额 RPC。")]

        probe_names = []
        if scope in {"quota", "all"}:
            probe_names.append("usage_quota")
        if scope in {"model_state", "all"}:
            probe_names.append("usage_model_state")

        results = []
        try:
            for name in probe_names:
                probe = _get_probe("usage", name)
                response = await _execute_observed_rpc(client, probe)
                bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
                entries = []
                if bodies and isinstance(bodies[0], list) and bodies[0]:
                    first = bodies[0][0]
                    if isinstance(first, list):
                        entries = [_parse_usage_entry(item) for item in first]
                results.append(
                    {
                        "name": name,
                        "source_rpc": probe["rpcid"],
                        "observed": probe["observed"],
                        "entries": entries,
                    }
                )

            payload = {"scope": scope, "count": len(results), "results": results}
            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            lines = ["## Gemini 用量限额", f"范围: {scope}"]
            for result in results:
                lines.extend(["", f"### {result['name']}"])
                if not result["entries"]:
                    lines.append("- 暂无条目")
                    continue
                for item in result["entries"]:
                    reset = f", reset={item['reset_time']}" if item.get("reset_time") else ""
                    lines.append(
                        "- key={key}, status={status}, tier={tier}, limit={limit}, remaining={remaining}{reset}".format(
                            key=item.get("key"),
                            status=item.get("status"),
                            tier=item.get("tier"),
                            limit=item.get("limit_value"),
                            remaining=item.get("remaining_value"),
                            reset=reset,
                        )
                    )
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"用量限额读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取用量限额失败: {str(e)}")]

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    async def gemini_list_library_capabilities(
        limit: int = 20,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """列出 Gemini Web Library 页面暴露的本地化能力/模板条目。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持 Library RPC。")]

        try:
            probe = _get_probe("library", "library_locale_capabilities")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            entries = []
            if bodies and isinstance(bodies[0], list) and bodies[0]:
                first = bodies[0][0]
                if isinstance(first, list):
                    entries = [_parse_library_capability(item) for item in first]
            page, page_info = _paginate_items(entries, limit, offset)
            payload = {
                **page_info,
                "items": page,
                "source_rpc": probe["rpcid"],
                "observed": probe["observed"],
            }
            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            if not page:
                return [TextContent(type="text", text="暂无 Library 能力条目。")]
            lines = [
                "## Gemini Library 能力",
                f"共 {payload['total_count']} 条；当前 offset={payload['offset']} count={payload['count']}",
            ]
            for idx, item in enumerate(page, payload["offset"] + 1):
                aliases = ", ".join(item.get("aliases", []))
                details = f"\n   {item['details']}" if item.get("details") else ""
                lines.append(f"{idx}. {item.get('name') or aliases}\n   {item.get('description', '')}{details}")
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"Library 能力读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取 Library 能力失败: {str(e)}")]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_list_scheduled_actions(
        scope: ScheduledScope = "all",
        limit: int = 20,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
        max_chars_per_field: int = 500,
    ) -> list[TextContent]:
        """列出 Gemini Web 定时操作页面返回的定时任务条目。只读，不创建/修改/删除任务。"""
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持定时操作 RPC。")]

        probe_names = []
        if scope in {"active", "all"}:
            probe_names.append("scheduled_actions_active")
        if scope in {"inactive", "all"}:
            probe_names.append("scheduled_actions_inactive")

        safe_chars = min(max(max_chars_per_field, 40), 4000)
        results = []
        try:
            for name in probe_names:
                probe = _get_probe("scheduled", name)
                response = await _execute_observed_rpc(client, probe)
                bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
                body = bodies[0] if bodies else []
                entries = []
                cursor = None
                if isinstance(body, list):
                    cursor = body[1] if len(body) > 1 and isinstance(body[1], str) else None
                    if len(body) > 2 and isinstance(body[2], list):
                        entries = [
                            _parse_scheduled_action_entry(item, safe_chars)
                            for item in body[2]
                        ]
                page, page_info = _paginate_items(entries, limit, offset)
                results.append(
                    {
                        "name": name,
                        "source_rpc": probe["rpcid"],
                        "observed": probe["observed"],
                        **page_info,
                        "has_more": page_info["has_more"] or bool(cursor),
                        "cursor_present": bool(cursor),
                        "items": page,
                    }
                )

            payload = {"scope": scope, "count": len(results), "results": results}
            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            lines = ["## Gemini 定时操作", f"范围: {scope}"]
            for result in results:
                lines.extend(["", f"### {result['name']}"])
                if not result["items"]:
                    lines.append("- 暂无条目")
                    continue
                for item in result["items"]:
                    enabled = item.get("enabled")
                    enabled_text = "enabled" if enabled is True else "disabled" if enabled is False else "unknown"
                    when = f", scheduled={item['scheduled_time']}" if item.get("scheduled_time") else ""
                    label = f", label={item['schedule_label']}" if item.get("schedule_label") else ""
                    lines.append(
                        f"- {item.get('title') or '(untitled)'} ({item.get('id', '')}) [{enabled_text}]{when}{label}"
                    )
                if result["has_more"]:
                    if result.get("next_offset") is not None:
                        lines.append(f"- 下一页: offset={result['next_offset']}")
                    else:
                        lines.append("- 远端返回了 cursor；当前工具不会自动追踪 cursor 以避免过度读取账号内容。")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"定时操作读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取定时操作失败: {str(e)}")]

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    async def gemini_get_tool_mode_status(
        limit: int = 50,
        offset: int = 0,
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        读取 Gemini Web 工具/模式状态枚举。

        返回 mode_id、available、quota/state 等结构化字段；这些数字枚举来自 Web RPC，
        部分语义尚未稳定命名，因此不会伪装成完整模式 CRUD。
        """
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="❌ 当前客户端不支持工具模式状态 RPC。")]

        try:
            probe = _get_probe("tool_modes", "tool_mode_status")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            body = bodies[0] if bodies else []
            entries = []
            leading_enabled = None
            if isinstance(body, list):
                leading_enabled = body[0] if body and isinstance(body[0], bool) else None
                if len(body) > 1 and isinstance(body[1], list):
                    entries = [_parse_tool_mode_entry(item) for item in body[1]]
            page, page_info = _paginate_items(entries, limit, offset, max_limit=100)

            payload = {
                **page_info,
                "leading_enabled": leading_enabled,
                "items": page,
                "source_rpc": probe["rpcid"],
                "observed": probe["observed"],
                "note": "mode_id semantics are Web-internal and may drift; use this as a read-only availability/status surface.",
            }
            if response_format == "json":
                return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

            if not page:
                return [TextContent(type="text", text="暂无工具模式状态条目。")]
            lines = [
                "## Gemini 工具/模式状态",
                f"共 {payload['total_count']} 条；当前 offset={payload['offset']} count={payload['count']}；leading_enabled={leading_enabled}",
            ]
            for item in page:
                lines.append(
                    "- mode_id={mode_id}, available={available}, quota={quota}, used={used}, state={state}".format(
                        mode_id=item.get("mode_id"),
                        available=item.get("available"),
                        quota=item.get("quota_value"),
                        used=item.get("used_value"),
                        state=item.get("state"),
                    )
                )
            if payload["has_more"]:
                lines.append(f"\n下一页: offset={payload['next_offset']}")
            lines.append("\n说明: mode_id 是 Gemini Web 内部枚举，语义可能漂移。")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            logger.error(f"工具模式状态读取失败: {e}")
            return [TextContent(type="text", text=f"❌ 读取工具模式状态失败: {str(e)}")]

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_list_models() -> list[TextContent]:
        """列出所有可用模型及其说明"""
        aliases = """🤖 MCP 模型别名:

1. flash-lite / lite → 3.1 Flash-Lite
   - 网页端极速模型

2. flash / fast → gemini-3-flash
   - 网页端 3.5 Flash；fast 保留为兼容别名

3. pro → gemini-3-pro
   - 网页端 3.1 Pro，是否可用取决于当前账户

4. thinking → gemini-3-flash-thinking
   - 旧兼容别名；新网页思考等级请用 thinking_level=standard/extended

媒体规则:
- 图像首轮生成始终使用 Nano Banana 2
- 音乐: flash 系列 → Lyria 3, pro → Lyria 3 Pro

---

运行时模型:
"""
        try:
            client = get_gemini_client()
            await initialize_client()
            models = client.list_models() if hasattr(client, "list_models") else None
        except Exception as e:
            logger.warning(f"运行时模型发现失败: {e}")
            models = None

        if not models:
            return [
                TextContent(
                    type="text",
                    text=aliases + "- 暂无运行时模型注册表；请确认 Cookie 和账户状态后重试。",
                )
            ]

        model_lines = [aliases]
        for model in models:
            display_name = getattr(model, "display_name", "") or "Unnamed"
            model_name = getattr(model, "model_name", "") or "unknown"
            available = "可用" if getattr(model, "is_available", True) else "不可用"
            description = getattr(model, "description", "") or "无描述"
            model_lines.append(f"- {display_name}: {model_name} ({available})\n  {description}")
        return [TextContent(type="text", text="\n".join(model_lines))]

    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    async def gemini_manage_gems(
        action: Literal["list", "create", "update", "delete"],
        gem_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> list[TextContent]:
        """
        管理 Gemini Gems（自定义 AI 助手）。
        
        参数:
        - action: list, create, update, delete
        - gem_id: 需要时指定 Gem ID
        - name: 创建/更新时 Gem 名称
        - description: 创建/更新时描述
        - instructions: 创建/更新时系统指令
        """
        client = get_gemini_client()
        await initialize_client()

        try:
            if action == "list":
                gems = await client.fetch_gems()
                if not gems:
                    return [TextContent(type="text", text="暂无保存的 Gems。")]

                gem_list = ["## 💎 Gems 列表"]
                gem_values = gems.values() if hasattr(gems, "values") else gems
                for i, gem in enumerate(gem_values, 1):
                    gem_name = getattr(gem, "name", "Untitled")
                    gem_id_val = getattr(gem, "id", "")
                    gem_desc = getattr(gem, "description", "")[:30]
                    gem_list.append(f"{i}. {gem_name} (ID: {gem_id_val})\n   {gem_desc}")
                
                return [TextContent(type="text", text="\n".join(gem_list))]

            elif action == "create":
                if not name:
                    return [TextContent(type="text", text="❌ 创建 Gem 需要提供名称。")]
                
                gem = await client.create_gem(
                    name=name,
                    prompt=instructions or "",
                    description=description,
                )
                gem_id_val = getattr(gem, "id", "")
                return [TextContent(
                    type="text",
                    text=f"✅ Gem 创建成功！\nID: {gem_id_val}\n名称: {name}"
                )]

            elif action == "update":
                if not gem_id:
                    return [TextContent(type="text", text="❌ 更新 Gem 需要提供 gem_id。")]
                
                await client.update_gem(
                    gem=gem_id,
                    name=name or "",
                    prompt=instructions or "",
                    description=description,
                )
                return [TextContent(type="text", text=f"✅ Gem {gem_id} 更新成功。")]

            elif action == "delete":
                if not gem_id:
                    return [TextContent(type="text", text="❌ 删除 Gem 需要提供 gem_id。")]
                
                await client.delete_gem(gem_id)
                return [TextContent(type="text", text=f"✅ Gem {gem_id} 删除成功。")]

            return [TextContent(type="text", text="❌ 无效的 action。")]

        except Exception as e:
            logger.error(f"Gem 操作失败: {e}")
            return [TextContent(type="text", text=f"❌ 失败: {str(e)}")]
