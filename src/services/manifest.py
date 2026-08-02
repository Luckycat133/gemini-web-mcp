"""Tool manifest and Web capability presentation independent of manage.py."""

from __future__ import annotations

import copy
import os
from typing import Any, Literal

from ..infrastructure.rpc_contracts import WEB_FEATURE_PROBES
from ..tools.manifest_data import TOOL_MANIFEST, WEB_UI_CAPABILITIES


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

TOOL_GROUP_MODULES = {
    "core": {"core", "media", "files", "research"},
    "basic": {"core"},
    "model": {"core"},
    "chat": {"core"},
    "invoke": {"core"},
    "media": {"media"},
    "advanced": {"prompts", "research"},
    "manage": {"history", "account", "gems"},
    "history": {"history"},
    "history-read": {"history"},
    "history-organize": {"history", "account"},
    "account-read": {"account"},
    "scheduled-read": {"account"},
    "scheduled-admin": {"account"},
    "admin": {"history", "account", "gems"},
    "file": {"files"},
    "files": {"files"},
    "research": {"research"},
    "prompts": {"prompts"},
    "all": {"core", "media", "files", "research", "history", "account", "gems"},
}

COOKIE_TOOL_NAMES = {
    "gemini_doctor",
    "gemini_get_cookie_status",
    "gemini_list_browser_cookie_profiles",
    "gemini_get_cookie_from_browser",
    "gemini_reset",
}
MANIFEST_TOOL_NAMES = {"gemini_get_tool_manifest"}
HISTORY_FACADE_TOOL_NAMES = {"gemini_history"}
NOTEBOOKS_FACADE_TOOL_NAMES = {"gemini_notebooks"}
ACCOUNT_INVENTORY_TOOL_NAMES = {"gemini_account_inventory"}
CHAT_TOOL_NAMES = {
    "gemini_chat",
    "gemini_chat_stream",
    "gemini_start_chat",
    "gemini_send_message",
    "gemini_send_message_stream",
    "gemini_list_sessions",
    "gemini_reset_session",
}
HISTORY_READ_TOOL_NAMES = {
    "gemini_list_chats",
    "gemini_scan_chat_history_sources",
    "gemini_search_chats",
    "gemini_read_chat",
    "gemini_export_chat",
}
HISTORY_WRITE_TOOL_NAMES = {"gemini_cleanup_test_artifacts", "gemini_delete_chat"}
NOTEBOOKS_READ_TOOL_NAMES = {"gemini_list_notebooks", "gemini_list_notebook_chats"}
NOTEBOOKS_WRITE_TOOL_NAMES = {"gemini_move_chat_to_notebook"}
ACCOUNT_READ_TOOL_NAMES = {
    "gemini_inspect_account",
    "gemini_probe_web_features",
    "gemini_get_web_capabilities",
    "gemini_list_public_links",
    "gemini_get_usage_limits",
    "gemini_list_library_capabilities",
    "gemini_get_tool_mode_status",
    "gemini_list_models",
    *NOTEBOOKS_READ_TOOL_NAMES,
}
SCHEDULED_READ_TOOL_NAMES = {"gemini_list_scheduled_actions", "gemini_get_scheduled_action"}
SCHEDULED_WRITE_TOOL_NAMES = {"gemini_create_scheduled_action", "gemini_delete_scheduled_action"}
GEMS_TOOL_NAMES = {"gemini_manage_gems"}
MANAGE_TOOL_LAYER_NAMES = {
    "history-read": HISTORY_FACADE_TOOL_NAMES,
    "history-write": HISTORY_WRITE_TOOL_NAMES,
    "history-granular": HISTORY_READ_TOOL_NAMES,
    "notebooks-read": NOTEBOOKS_FACADE_TOOL_NAMES,
    "notebooks-write": NOTEBOOKS_WRITE_TOOL_NAMES,
    "notebooks-granular": NOTEBOOKS_READ_TOOL_NAMES,
    "account-read": ACCOUNT_INVENTORY_TOOL_NAMES,
    "account-granular": ACCOUNT_READ_TOOL_NAMES | SCHEDULED_READ_TOOL_NAMES,
    "scheduled-read": SCHEDULED_READ_TOOL_NAMES,
    "scheduled-write": SCHEDULED_WRITE_TOOL_NAMES,
    "gems": GEMS_TOOL_NAMES,
}
ALL_MANAGE_TOOL_NAMES = (
    MANIFEST_TOOL_NAMES
    | HISTORY_FACADE_TOOL_NAMES
    | HISTORY_READ_TOOL_NAMES
    | HISTORY_WRITE_TOOL_NAMES
    | ACCOUNT_INVENTORY_TOOL_NAMES
    | ACCOUNT_READ_TOOL_NAMES
    | NOTEBOOKS_FACADE_TOOL_NAMES
    | SCHEDULED_READ_TOOL_NAMES
    | SCHEDULED_WRITE_TOOL_NAMES
    | NOTEBOOKS_WRITE_TOOL_NAMES
    | GEMS_TOOL_NAMES
)
MANAGE_TOOL_LAYER_NAMES["all"] = ALL_MANAGE_TOOL_NAMES

TOOL_PROFILE_GUIDE = [
    {
        "name": "model",
        "gemini_tools": "model",
        "purpose": "Call Gemini models only; exposes chat/session tools plus always-on manifest and cookie diagnostics.",
        "writes_remote": True,
    },
    {
        "name": "history",
        "gemini_tools": "history",
        "purpose": "Read, search, and export Gemini chat history through the gemini_history facade without delete, scheduled actions, or Gems.",
        "writes_remote": False,
    },
    {
        "name": "history-organize",
        "gemini_tools": "history-organize",
        "purpose": "Use gemini_history and gemini_notebooks, then move selected chats into native Gemini Web Notebooks.",
        "writes_remote": True,
    },
    {
        "name": "account-read",
        "gemini_tools": "account-read",
        "purpose": "Read account inventory through the gemini_account_inventory facade.",
        "writes_remote": False,
    },
    {
        "name": "scheduled-admin",
        "gemini_tools": "scheduled-admin",
        "purpose": "Create or delete scheduled actions after explicit user authorization.",
        "writes_remote": True,
    },
    {
        "name": "core",
        "gemini_tools": "core",
        "purpose": "Broad content work: model calls, media, file/URL analysis, and Deep Research.",
        "writes_remote": True,
    },
    {
        "name": "all",
        "gemini_tools": "all",
        "purpose": "Full maintenance and verification surface; not recommended as a default for general agents.",
        "writes_remote": True,
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
            "gemini_history(action='scan') when completeness matters",
            "gemini_history(action='list'|'search')",
            "gemini_history(action='read'|'export') for one selected chat",
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
            "gemini_account_inventory(surface='capabilities')",
            "gemini_account_inventory(surface='links'|'usage'|'library'|'notebooks'|'scheduled'|'modes'|'models')",
        ],
        "notes": "Read-only but may reveal account-private metadata such as links and scheduled-action titles.",
    },
    {
        "name": "chat_history_to_native_notebooks",
        "steps": [
            "gemini_history(action='scan'|'list')",
            "gemini_notebooks(action='list')",
            "gemini_move_chat_to_notebook",
            "gemini_notebooks(action='chats')",
        ],
        "notes": "Moves existing Gemini Web chats into native Gemini Web Notebooks; delete unrelated chats only through explicit destructive tools.",
    },
    {
        "name": "scheduled_action_create_and_cleanup",
        "steps": [
            "gemini_doctor",
            "gemini_create_scheduled_action",
            "gemini_get_scheduled_action",
            "gemini_list_scheduled_actions",
            "gemini_delete_scheduled_action",
        ],
        "notes": "Creates and then deletes a daily scheduled action; use only with explicit user authorization and a unique test title when validating.",
    },
    {
        "name": "operational_preflight",
        "steps": ["gemini_doctor", "gemini_get_tool_manifest", "gemini_list_browser_cookie_profiles"],
        "notes": "Read-only local/profile diagnostics before live account workflows; use validate_browser=true only when account validation is needed.",
    },
    {
        "name": "test_artifact_cleanup",
        "steps": [
            "gemini_cleanup_test_artifacts with dry_run=true",
            "review matched IDs",
            "gemini_cleanup_test_artifacts with dry_run=false",
        ],
        "notes": "Deletes only chats and scheduled actions matching explicit test markers; scan_turns=true reads private chat text and should be used narrowly.",
    },
]


def resolve_manage_tool_names(layers: list[str] | set[str] | tuple[str, ...] | None = None) -> set[str]:
    configured = {str(layer).strip() for layer in (layers or ["all"]) if str(layer).strip()}
    if not configured:
        configured = {"all"}
    enabled = set(MANIFEST_TOOL_NAMES)
    for layer in configured:
        enabled.update(MANAGE_TOOL_LAYER_NAMES.get(layer, {layer}))
    return enabled


def _configured_tool_groups() -> list[str]:
    configured = [item.strip() for item in os.environ.get("GEMINI_TOOLS", "core").split(",") if item.strip()]
    return configured or ["core"]


def _configured_manage_layers(configured: list[str]) -> set[str]:
    layers: set[str] = set()
    profile_layers = {
        "manage": {"all"},
        "all": {"all"},
        "admin": {"all"},
        "history": {"history-read"},
        "history-read": {"history-read"},
        "history-organize": {"history-read", "notebooks-read", "notebooks-write"},
        "account-read": {"account-read"},
        "scheduled-read": {"scheduled-read"},
        "scheduled-admin": {"scheduled-read", "scheduled-write"},
    }
    for group in configured:
        if group.startswith("manage:"):
            layers.add(group.split(":", 1)[1])
        else:
            layers.update(profile_layers.get(group, set()))
    return layers


def _enabled_manifest_tool_names(configured: list[str], enabled_groups: set[str]) -> set[str]:
    enabled_tools = set(MANIFEST_TOOL_NAMES) | set(COOKIE_TOOL_NAMES)
    manage_layers = _configured_manage_layers(configured)
    if manage_layers:
        enabled_tools.update(resolve_manage_tool_names(manage_layers))
    for item in TOOL_MANIFEST:
        group = item["group"]
        if group == "prompts":
            if "prompts" in enabled_groups:
                enabled_tools.add(item["name"])
            continue
        if group in {"history", "account", "gems", "cookie"}:
            continue
        if item["name"] in CHAT_TOOL_NAMES:
            if any(name in {"model", "chat", "invoke", "basic"} for name in configured):
                enabled_tools.add(item["name"])
            elif group in enabled_groups:
                enabled_tools.add(item["name"])
            continue
        if group in enabled_groups:
            enabled_tools.add(item["name"])
    return enabled_tools


def _tool_availability(tool: dict[str, Any]) -> list[str]:
    if tool["name"] == "gemini_get_tool_manifest":
        return ["always"]
    name = tool["name"]
    group = tool["group"]
    if name in CHAT_TOOL_NAMES:
        return ["model", "chat", "core", "all"]
    if group in {"media", "files", "research"}:
        return ["core", "all"]
    if name in HISTORY_FACADE_TOOL_NAMES:
        return ["history", "history-organize", "manage", "all"]
    if name in NOTEBOOKS_FACADE_TOOL_NAMES:
        return ["history-organize", "account-read", "manage", "all"]
    if name in ACCOUNT_INVENTORY_TOOL_NAMES:
        return ["account-read", "manage", "all"]
    if name in HISTORY_READ_TOOL_NAMES:
        return ["manage", "all"]
    if name in HISTORY_WRITE_TOOL_NAMES:
        return ["admin", "manage", "all"]
    if name in NOTEBOOKS_READ_TOOL_NAMES:
        return ["manage", "all"]
    if name in NOTEBOOKS_WRITE_TOOL_NAMES:
        return ["history-organize", "admin", "manage", "all"]
    if name in SCHEDULED_READ_TOOL_NAMES:
        return ["scheduled-read", "scheduled-admin", "manage", "all"]
    if name in SCHEDULED_WRITE_TOOL_NAMES:
        return ["scheduled-admin", "admin", "manage", "all"]
    if name in ACCOUNT_READ_TOOL_NAMES:
        return ["manage", "all"]
    if name in GEMS_TOOL_NAMES:
        return ["admin", "manage", "all"]
    if group == "cookie":
        return ["always"]
    if group == "prompts":
        return ["prompts"]
    return []


def _current_enabled_manifest_groups() -> tuple[list[str], set[str]]:
    configured = _configured_tool_groups()
    enabled = {"cookie"}
    for group in configured:
        enabled.update(TOOL_GROUP_MODULES.get(group, {group}))
    enabled.add("manifest")
    return configured, enabled


def tool_manifest_payload(scope: ManifestScope = "all") -> dict[str, Any]:
    current_tool_groups, enabled_groups = _current_enabled_manifest_groups()
    filter_scope = "core" if scope == "chat" else scope
    enabled_tool_names = _enabled_manifest_tool_names(current_tool_groups, enabled_groups)
    tools = [
        {
            **item,
            "availability": _tool_availability(item),
            "current_enabled": item["name"] in enabled_tool_names,
        }
        for item in TOOL_MANIFEST
        if filter_scope == "all"
        or item["group"] == filter_scope
        or (filter_scope == "core" and item["group"] == "core")
        or (filter_scope == "notebooks" and item["name"] in NOTEBOOKS_READ_TOOL_NAMES | NOTEBOOKS_WRITE_TOOL_NAMES)
        or (filter_scope == "scheduled" and item["name"] in SCHEDULED_READ_TOOL_NAMES | SCHEDULED_WRITE_TOOL_NAMES)
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
        "profiles": TOOL_PROFILE_GUIDE,
        "tools": tools,
        "workflows": MANIFEST_WORKFLOWS
        if filter_scope in {"all", "core", "history", "account", "notebooks", "scheduled"}
        else [],
        "safety_notes": [
            "Annotations and manifest metadata are planning hints, not a permission system.",
            "Tools marked privacy=reads_private_chat_text return private Gemini chat content.",
            "Tools marked destructive=true can delete or overwrite remote or local user data.",
            "Probe tools intentionally omit raw response bodies.",
        ],
    }


def format_tool_manifest_markdown(payload: dict[str, Any]) -> str:
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
    if payload.get("profiles"):
        lines.extend(["", "### Recommended Profiles"])
        for profile in payload["profiles"]:
            write_note = "writes remote data" if profile["writes_remote"] else "read-only"
            lines.append(f"- `{profile['gemini_tools']}` ({write_note}): {profile['purpose']}")
    lines.extend(["", "### Tools"])
    for item in payload["tools"]:
        flags = ["read-only" if item["read_only"] else "writes"]
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


def web_capabilities_payload() -> dict[str, Any]:
    payload: dict[str, Any] = copy.deepcopy(WEB_UI_CAPABILITIES)
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
            "gemini_history",
            "gemini_cleanup_test_artifacts",
            "gemini_list_chats",
            "gemini_scan_chat_history_sources",
            "gemini_search_chats",
            "gemini_read_chat",
            "gemini_export_chat",
            "gemini_delete_chat",
        ],
        "account": [
            "gemini_account_inventory",
            "gemini_inspect_account",
            "gemini_get_tool_manifest",
            "gemini_get_web_capabilities",
            "gemini_probe_web_features",
            "gemini_list_public_links",
            "gemini_get_usage_limits",
            "gemini_notebooks",
            "gemini_list_notebooks",
            "gemini_list_notebook_chats",
            "gemini_move_chat_to_notebook",
            "gemini_list_library_capabilities",
            "gemini_list_scheduled_actions",
            "gemini_get_scheduled_action",
            "gemini_create_scheduled_action",
            "gemini_delete_scheduled_action",
            "gemini_get_tool_mode_status",
            "gemini_list_models",
        ],
        "media": ["gemini_generate_media", "gemini_generate_music"],
        "files": ["gemini_upload_file", "gemini_analyze_url"],
        "research": [
            "gemini_deep_research",
            "gemini_list_research_report_actions",
            "gemini_create_from_research_report",
        ],
        "gems": ["gemini_manage_gems"],
        "cookie": [
            "gemini_doctor",
            "gemini_get_cookie_status",
            "gemini_list_browser_cookie_profiles",
            "gemini_get_cookie_from_browser",
        ],
    }
    return payload


def format_web_capabilities_markdown(payload: dict[str, Any]) -> str:
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
    grouped: dict[str, list[dict[str, Any]]] = {}
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


_tool_manifest_payload = tool_manifest_payload
_format_tool_manifest_markdown = format_tool_manifest_markdown
_web_capabilities_payload = web_capabilities_payload
_format_web_capabilities_markdown = format_web_capabilities_markdown
