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
    "basic": ["chat"],
    "media": ["media", "image"],
    "advanced": ["prompts", "research"],
    "manage": ["manage"],
    "file": ["file"],
    "research": ["research"],
    "all": ["chat", "media", "image", "prompts", "manage", "file", "research"],
}

def register_tools(mcp, groups: list = None):
    """
    选择性注册工具组
    
    Args:
        mcp: FastMCP 实例
        groups: 要加载的工具组列表
                - basic: 基础对话功能
                - media: 媒体生成功能
                - advanced: 高级功能（提示词管理等）
                - all: 加载所有功能
    
    Example:
        register_tools(mcp, ["basic"])        # 仅基础对话
        register_tools(mcp, ["basic", "media"]) # 基础+媒体
        register_tools(mcp, ["all"])           # 全部加载
    """
    if not groups:
        groups = ["basic"]

    selected = set()
    for group in groups:
        group = group.strip()
        selected.update(TOOL_GROUPS.get(group, [group]))

    if "chat" in selected:
        register_chat_tools(mcp)

    if "media" in selected:
        register_media_tools(mcp)

    if "image" in selected and "media" not in selected:
        register_image_tools(mcp)

    if "prompts" in selected:
        register_prompts_tools(mcp)

    if "manage" in selected:
        register_manage_tools(mcp)

    if "file" in selected:
        register_file_tools(mcp)

    if "research" in selected:
        register_research_tools(mcp)
