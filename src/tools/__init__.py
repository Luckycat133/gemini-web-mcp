"""
工具模块初始化 - 支持分层加载
"""

from .chat import register_chat_tools
from .media import register_media_tools
from .image import register_image_tools
from .prompts import register_prompts_tools
from .manage import register_manage_tools
from .file import register_file_tools
from .research import register_research_tools

TOOL_GROUPS = {
    # Intent-focused profiles for agent configuration.
    "model": ["chat"],
    "chat": ["chat"],
    "invoke": ["chat"],
    "history": ["manage:history-read"],
    "history-read": ["manage:history-read"],
    "history-organize": ["manage:history-read", "manage:notebooks-read", "manage:notebooks-write"],
    "account-read": ["manage:account-read"],
    "scheduled-read": ["manage:scheduled-read"],
    "scheduled-admin": ["manage:scheduled-read", "manage:scheduled-write"],
    "admin": ["manage:all"],
    # Backward-compatible module groups.
    "core": ["chat", "media", "file", "research"],
    "basic": ["chat"],
    "media": ["media", "image"],
    "advanced": ["prompts", "research"],
    "manage": ["manage:all"],
    "file": ["file"],
    "files": ["file"],
    "research": ["research"],
    "prompts": ["prompts"],
    "all": ["chat", "media", "file", "research", "manage:all"],
}


def _resolve_tool_selection(groups: list | None = None) -> tuple[set[str], set[str]]:
    if not groups:
        groups = ["core"]

    selected: set[str] = set()
    manage_layers: set[str] = set()
    for raw_group in groups:
        group = str(raw_group).strip()
        if not group:
            continue
        for item in TOOL_GROUPS.get(group, [group]):
            if item.startswith("manage:"):
                selected.add("manage")
                manage_layers.add(item.split(":", 1)[1])
            else:
                selected.add(item)

    return selected, manage_layers


def groups_enable_manage(groups: list | None = None) -> bool:
    selected, _manage_layers = _resolve_tool_selection(groups)
    return "manage" in selected

def register_tools(mcp, groups: list = None):
    """
    选择性注册工具组
    
    Args:
        mcp: FastMCP 实例
        groups: 要加载的工具组列表

    Example:
        register_tools(mcp, ["model"])         # 只调用 Gemini 模型
        register_tools(mcp, ["history"])       # 只读历史整理入口
        register_tools(mcp, ["core"])          # 聊天 + 媒体 + 文件 + 研究
        register_tools(mcp, ["basic", "media"])
        register_tools(mcp, ["all"])
    """
    selected, manage_layers = _resolve_tool_selection(groups)

    if "chat" in selected:
        register_chat_tools(mcp)

    if "media" in selected:
        register_media_tools(mcp)

    if "image" in selected and "media" not in selected:
        register_image_tools(mcp)

    if "prompts" in selected:
        register_prompts_tools(mcp)

    if "manage" in selected:
        register_manage_tools(mcp, sorted(manage_layers) or None)

    if "file" in selected:
        register_file_tools(mcp)

    if "research" in selected:
        register_research_tools(mcp)
