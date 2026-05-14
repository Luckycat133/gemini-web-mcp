#!/usr/bin/env python3
"""
Gemini Web 逆向 MCP 服务器
支持: 文本对话、Deep Research、媒体生成、文件分析
版本: 2.0 (2026.5)
"""

import logging
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

# 工具注册
from .tools.chat import register_chat_tools
from .tools.research import register_research_tools
from .tools.media import register_media_tools
from .tools.file import register_file_tools
from .tools.manage import register_manage_tools
from .tools.image import register_image_tools
from .tools.prompts import register_prompts_tools

# 客户端封装
from .client_wrapper import (
    reset_client,
    get_cookie_from_browser,
    get_cookie_status,
    init_cookie_manager_integration
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Gemini Web MCP Server",
    instructions="""
# Gemini Web MCP Server (v2.0)

## 可用模型

1. fast → gemini-3-flash (快速，免费)
2. thinking → gemini-3-flash-thinking (推理链，免费)
3. pro → gemini-3.1-pro (最强，AI Plus)

## 媒体生成功能

- 图像: Nano Banana 2（所有模型）
- 视频: Veo 3.1（所有模型，最长60秒）
- 音乐:
  - fast → Lyria 3 Clip (30秒)
  - thinking/pro → Lyria 3 Pro (完整歌曲)

## 主要功能

- 💬 对话: 单次对话、多轮会话
- 📚 Deep Research: 深度研究（需 AI Plus）
- 🎨 媒体生成: 图像、视频、音乐
- 📁 文件分析: 上传文件、分析 URL
- 🔧 管理: 历史对话、Gem 管理
""",
)

# 注册所有工具
register_chat_tools(mcp)
register_research_tools(mcp)
register_media_tools(mcp)
register_file_tools(mcp)
register_manage_tools(mcp)
register_image_tools(mcp)
register_prompts_tools(mcp)


@mcp.tool()
async def gemini_reset() -> list[TextContent]:
    """重置客户端并清除所有会话"""
    reset_client()
    return [
        TextContent(
            type="text",
            text="✅ Gemini 客户端已重置，所有会话已清除。"
        )
    ]


@mcp.tool()
async def gemini_get_cookie_status() -> list[TextContent]:
    """获取 Cookie 状态信息"""
    status = get_cookie_status()
    
    if not status.get("available", False):
        return [
            TextContent(
                type="text",
                text="⚠️ Cookie Manager 不可用"
            )
        ]
    
    status_text = f"""📊 Cookie 状态
状态: {status.get('status', 'unknown')}
已设置: {'✅' if status.get('has_cookie') else '❌'}
来源: {status.get('source', 'unknown')}
使用时长: {status.get('age_hours', 0)} 小时
需要刷新: {'✅' if status.get('needs_refresh', False) else '❌'}"""
    
    return [
        TextContent(
            type="text",
            text=status_text
        )
    ]


@mcp.tool()
async def gemini_get_cookie_from_browser(browser: str = "chrome") -> list[TextContent]:
    """
    从浏览器自动获取 Cookie
    
    Args:
        browser: 浏览器类型 (chrome, firefox, edge, opera, brave)
    """
    try:
        success = get_cookie_from_browser(browser)
        
        if success:
            return [
                TextContent(
                    type="text",
                    text=f"✅ 已从 {browser} 浏览器获取 Cookie！客户端已自动重置。"
                )
            ]
        else:
            return [
                TextContent(
                    type="text",
                    text=f"❌ 从 {browser} 浏览器获取 Cookie 失败\n\n"
                    "请确保：\n"
                    "1. 已登录 gemini.google.com\n"
                    "2. 浏览器已关闭或 Cookie 未被锁定\n"
                    "3. browser-cookie3 已安装: pip install browser-cookie3"
                )
            ]
    except Exception as e:
        logger.error(f"获取浏览器 Cookie 失败: {e}")
        return [
            TextContent(
                type="text",
                text=f"❌ 获取 Cookie 时出错: {str(e)}"
            )
        ]


@mcp.tool()
async def gemini_health_check() -> list[TextContent]:
    """检查连接健康状态"""
    from .client_wrapper import get_gemini_client, initialize_client
    
    try:
        client = get_gemini_client()
        await initialize_client()
        
        # 发送测试信息
        response = await client.generate_content("Hello, quick check!")
        
        return [
            TextContent(
                type="text",
                text=f"✅ Gemini 连接正常！\n\n响应预览: {response.text[:100]}..."
            )
        ]
        
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return [
            TextContent(
                type="text",
                text=f"❌ 连接检查失败: {str(e)}\n\n"
                "请检查: \n"
                "1. GEMINI_PSID 环境变量是否正确设置？\n"
                "2. 网络连接是否正常？\n"
                "3. Cookie 是否过期？"
            )
        ]


@mcp.tool()
async def gemini_list_features() -> list[TextContent]:
    """列出所有可用功能和特性"""
    features = """✨ Gemini MCP 服务器功能大全（v3.0）

📜 对话工具
- gemini_chat: 单次对话（支持图片输入）
- gemini_chat_stream: 单次流式对话
- gemini_start_chat: 创建多轮会话
- gemini_send_message: 会话消息
- gemini_send_message_stream: 会话流式消息
- gemini_list_sessions: 列会话
- gemini_reset_session: 重置会话

📚 深度研究
- gemini_deep_research: 深度研究（需 AI Plus）

🎨 媒体生成
- gemini_generate_media: 图像/视频/音乐生成
- gemini_generate_music: 音乐生成（便捷工具）

🖼️ 图像编辑
- gemini_edit_image: 使用提示词编辑图像
- gemini_variations: 生成图像变体

📁 文件分析
- gemini_upload_file: 上传并分析文件
- gemini_analyze_url: 分析网址（YouTube、网页等）

📝 预设提示词库
- gemini_manage_prompts: 提示词管理（CRUD）

🍪 Cookie 管理
- gemini_get_cookie_status: 查看 Cookie 状态
- gemini_get_cookie_from_browser: 从浏览器自动获取 Cookie

🔧 管理工具
- gemini_list_chats: 历史对话
- gemini_manage_gems: Gem 管理（CRUD）
- gemini_list_models: 模型列表
- gemini_list_features: 功能列表
- gemini_health_check: 健康检查
- gemini_reset: 重置客户端

---
✨ v3.0 新特性:
- ✅ 会话持久化（自动保存会话）
- ✅ Cookie 自动刷新（后台监控）
- ✅ 流式对话支持
- ✅ 图像编辑功能
- ✅ 预设提示词库

⚠️ 使用提示:
- 设置 GEMINI_PSID 环境变量
- 从 gemini.google.com 获取 Cookie
- 部分功能需要 AI Plus 订阅
"""
    return [TextContent(type="text", text=features)]


def main():
    """启动服务器"""
    logger.info("🚀 启动 Gemini Web MCP Server (v2.0)...")
    init_cookie_manager_integration()
    mcp.run()


if __name__ == "__main__":
    main()

