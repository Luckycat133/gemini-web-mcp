"""
工具模块初始化 — 支持按需加载与默认底座。

默认底座（mode="default"，始终对 AI 可见）：chat、media、session、research。
按需加载（mode 需要显式开启对应 GEMINI_TOOLS 子集）：history、account、scheduled、notebooks、prompts。
基础设施（cookie、doctor、manifest、cleanup、reset）：下沉到服务器后台自动化，不注册为 MCP 工具。
"""

from ..adapters.mcp_sdk import MCPServer

# 默认底座工具组 — 始终加载，AI 自动化操控 Gemini 的核心能力
DEFAULT_TOOL_GROUPS: set[str] = {"chat", "media", "file", "research"}

# 按需加载工具组 — 需要显式开启，AI 不默认暴露
ON_DEMAND_TOOL_GROUPS: dict[str, list[str]] = {
    "history": ["manage:history-read", "manage:notebooks-read", "manage:notebooks-write"],
    "account": ["manage:account-read"],
    "scheduled": ["manage:scheduled-read", "manage:scheduled-write"],
    "notebooks": ["manage:notebooks-read", "manage:notebooks-write"],
    "prompts": ["prompts"],
    "gems": ["manage:gems"],
}

# 向后兼容的分组别名
TOOL_GROUP_ALIASES: dict[str, list[str]] = {
    "model": ["chat"],
    "chat": ["chat"],
    "invoke": ["chat"],
    "basic": ["chat"],
    "media": ["media"],
    "image": ["media"],
    "file": ["file"],
    "files": ["file"],
    "research": ["research"],
    "advanced": ["prompts", "research"],
    "core": ["chat", "media", "file", "research"],
    "history-organize": ["history", "notebooks"],
    "history-read": ["history"],
    "account-read": ["account"],
    "scheduled-read": ["scheduled"],
    "scheduled-admin": ["scheduled"],
    "admin": ["history", "account", "scheduled", "notebooks", "prompts", "gems"],
    "all": ["chat", "media", "file", "research", "history", "account", "scheduled", "notebooks", "prompts", "gems"],
}

# 按需工具组 -> manage 层工具名映射（供 register_tools 过滤，模块级常量避免重复创建）
MANAGE_TOOL_NAMES: dict[str, set[str]] = {
    "history": {
        "gemini_list_chats", "gemini_scan_chat_history_sources",
        "gemini_read_chat", "gemini_search_chats", "gemini_export_chat",
        "gemini_history", "gemini_delete_chat",
    },
    "account": {
        "gemini_inspect_account", "gemini_probe_web_features",
        "gemini_get_web_capabilities",
        "gemini_account_inventory", "gemini_list_public_links",
        "gemini_get_usage_limits", "gemini_list_library_capabilities",
        "gemini_get_tool_mode_status", "gemini_list_models",
    },
    "scheduled": {
        "gemini_list_scheduled_actions", "gemini_get_scheduled_action",
        "gemini_create_scheduled_action", "gemini_delete_scheduled_action",
    },
    "notebooks": {
        "gemini_list_notebooks", "gemini_list_notebook_chats",
        "gemini_notebooks", "gemini_move_chat_to_notebook",
    },
    "prompts": {
        "gemini_manage_prompts",
    },
}


def groups_enable_manage(groups: list | None = None) -> bool:
    """向后兼容：检查是否启用了任何按需（原 manage）工具组。"""
    if not groups:
        return False
    for raw_group in groups:
        group = str(raw_group).strip()
        if not group:
            continue
        expanded = TOOL_GROUP_ALIASES.get(group, [group])
        for item in expanded:
            if item.startswith("manage:"):
                return True
            if item in ON_DEMAND_TOOL_GROUPS:
                return True
    return False


def _resolve_tool_selection(groups: list | None = None, mode: str = "default") -> set[str]:
    """解析要加载的工具组。

    Args:
        groups: 用户指定的工具组列表
        mode: "default" 只加载默认底座；"full" 加载所有工具组
    """
    if not groups:
        groups = ["core"] if mode == "default" else ["all"]

    selected: set[str] = set()
    for raw_group in groups:
        group = str(raw_group).strip()
        if not group:
            continue
        # 先匹配别名
        expanded = TOOL_GROUP_ALIASES.get(group, [group])
        for item in expanded:
            if item.startswith("manage:"):
                # 映射到按需工具组名
                layer = item.split(":", 1)[1]
                group_name = {
                    "history-read": "history",
                    "notebooks-read": "notebooks",
                    "notebooks-write": "notebooks",
                    "account-read": "account",
                    "scheduled-read": "scheduled",
                    "scheduled-write": "scheduled",
                    "gems": "gems",
                }.get(layer)
                if group_name:
                    selected.add(group_name)
            else:
                selected.add(item)

    # default 模式下，若用户未显式指定 groups（即使用默认 "core"），只保留默认底座。
    # 用户显式指定的按需组（如 ["history"]）始终会被加载。
    if groups is None and mode == "default":
        selected &= DEFAULT_TOOL_GROUPS

    return selected


def register_tools(mcp: MCPServer, groups: list[str] | None = None, mode: str = "default") -> None:
    """
    选择性注册工具组。

    Args:
        mcp: MCPServer 实例
        groups: 要加载的工具组列表（可用别名，如 "history", "account", "scheduled"）
        mode: "default" 只加载默认底座（chat/media/session/research）；
              "full" 加载所有工具组（维护/管理员用）

    Example:
        register_tools(mcp)                                  # 默认底座：AI 核心能力
        register_tools(mcp, ["history"], mode="full")        # 默认底座 + 历史
        register_tools(mcp, ["all"], mode="full")            # 全部工具（管理员）
    """
    selected = _resolve_tool_selection(groups, mode)

    if "chat" in selected:
        from .chat import register_chat_tools

        register_chat_tools(mcp)

    if "media" in selected:
        from .media import register_media_tools

        register_media_tools(mcp)

    if "file" in selected:
        from .file import register_file_tools

        register_file_tools(mcp)

    if "research" in selected:
        from .research import register_research_tools

        register_research_tools(mcp)

    if selected & {"research", "media"}:
        from .operations import register_operation_tools

        register_operation_tools(mcp)

    if "history" in selected:
        from .manage import register_history_tools

        register_history_tools(mcp, MANAGE_TOOL_NAMES["history"])

    if "account" in selected:
        from .manage import register_account_tools

        register_account_tools(mcp, MANAGE_TOOL_NAMES["account"])

    if "scheduled" in selected:
        from .manage import register_scheduled_tools

        register_scheduled_tools(mcp, MANAGE_TOOL_NAMES["scheduled"])

    if "notebooks" in selected:
        from .manage import register_notebook_tools

        register_notebook_tools(mcp, MANAGE_TOOL_NAMES["notebooks"])

    if "prompts" in selected:
        from .prompts import register_prompts_tools

        register_prompts_tools(mcp)

    if "gems" in selected:
        from .manage import _register_gem_tools

        _register_gem_tools(mcp, {"gemini_manage_gems"})
