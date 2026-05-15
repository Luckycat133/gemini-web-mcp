"""
工具模块初始化 - 支持分层加载
"""

from .chat import register_chat_tools
from .media import register_media_tools
from .image import register_image_tools
from .prompts import register_prompts_tools

TOOL_GROUPS = {
    "basic": ["chat"],
    "media": ["media", "image"],
    "advanced": ["prompts"],
    "all": ["chat", "media", "image", "prompts"],
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
    
    if "chat" in groups or "basic" in groups or "all" in groups:
        register_chat_tools(mcp)
    
    if "media" in groups or "all" in groups:
        register_media_tools(mcp)
    
    if "image" in groups or "all" in groups:
        register_image_tools(mcp)
    
    if "prompts" in groups or "advanced" in groups or "all" in groups:
        register_prompts_tools(mcp)
