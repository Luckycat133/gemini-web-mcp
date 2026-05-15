#!/usr/bin/env python3
"""
Gemini Web 逆向 MCP 服务器
支持: 文本对话、Deep Research、媒体生成、文件分析
版本: 2.0 (2026.5)
"""

import logging
import os
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from .tools import register_tools
from .client_wrapper import (
    reset_client,
    get_cookie_from_browser,
    get_cookie_status,
    init_cookie_manager_integration
)
from .error_handler import handle_error, format_error_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 根据环境变量选择加载的工具组
TOOL_GROUPS = os.environ.get("GEMINI_TOOLS", "basic").split(",")

mcp = FastMCP(
    "Gemini Web MCP Server",
    instructions="""
# Gemini Web MCP Server (v2.0)

## 可用模型
- fast → gemini-3-flash (快速，免费)
- thinking → gemini-3-flash-thinking (推理链，免费)
- pro → gemini-3.1-pro (最强，AI Plus)

## 工具组（可配置）
- basic: 基础对话功能
- media: 图像/视频/音乐生成
- advanced: 提示词管理等高级功能

## 主要功能
- 💬 对话: 单次对话、多轮会话
- 🎨 媒体生成: 图像、视频、音乐
- 🖼️ 图像编辑: 编辑、变体生成
- 📝 提示词管理（高级）

## 错误处理
遇到问题时会自动提供解决方案和可使用的工具
""",
)

# 选择性注册工具组
register_tools(mcp, TOOL_GROUPS)


@mcp.tool()
async def gemini_reset() -> list[TextContent]:
    """重置客户端"""
    reset_client()
    return [TextContent(type="text", text="✅ 客户端已重置")]


@mcp.tool()
async def gemini_get_cookie_status() -> list[TextContent]:
    """获取 Cookie 状态"""
    status = get_cookie_status()
    if not status.get("available", False):
        return [TextContent(type="text", text="⚠️ Cookie Manager 不可用")]
    
    return [TextContent(type="text", text=f"""📊 Cookie 状态
状态: {status.get('status', 'unknown')}
已设置: {'✅' if status.get('has_cookie') else '❌'}
来源: {status.get('source', 'unknown')}
需要刷新: {'✅' if status.get('needs_refresh', False) else '❌'}""")]


@mcp.tool()
async def gemini_get_cookie_from_browser(browser: str = "chrome") -> list[TextContent]:
    """从浏览器获取 Cookie"""
    try:
        success = get_cookie_from_browser(browser)
        if success:
            return [TextContent(type="text", text=f"✅ 已从 {browser} 获取 Cookie")]
        else:
            return [TextContent(type="text", text=f"❌ 获取失败，请确保已登录 gemini.google.com")]
    except Exception as e:
        error_info = handle_error(e)
        return [format_error_response(error_info)]


@mcp.tool()
async def gemini_list_features() -> list[TextContent]:
    """列出可用功能"""
    features = """✨ Gemini MCP 服务器功能
    
📜 对话工具
- gemini_chat: 单次对话
- gemini_chat_stream: 流式对话
- gemini_start_chat: 创建会话
- gemini_send_message: 会话消息
- gemini_send_message_stream: 会话流式消息
- gemini_list_sessions: 列会话
- gemini_reset_session: 重置会话

🎨 媒体生成
- gemini_generate_media: 图像/视频/音乐
- gemini_generate_music: 音乐生成

🖼️ 图像编辑
- gemini_edit_image: 图像编辑
- gemini_variations: 图像变体

📝 提示词管理（高级）
- gemini_manage_prompts: 提示词 CRUD

🍪 Cookie 管理
- gemini_get_cookie_status: Cookie 状态
- gemini_get_cookie_from_browser: 从浏览器获取

🔧 管理工具
- gemini_reset: 重置客户端
- gemini_list_features: 功能列表

---
💡 使用提示:
- 设置 GEMINI_PSID 环境变量
- 通过 GEMINI_TOOLS 配置加载的工具组
- 例如: GEMINI_TOOLS=basic,media
"""
    return [TextContent(type="text", text=features)]


def main():
    """启动服务器"""
    logger.info(f"🚀 启动 Gemini Web MCP Server (v2.0)")
    logger.info(f"🔧 加载工具组: {', '.join(TOOL_GROUPS)}")
    init_cookie_manager_integration()
    mcp.run()


if __name__ == "__main__":
    main()
