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

# 客户端封装
from .client_wrapper import reset_client

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
    features = """✨ Gemini MCP 服务器功能大全（v2.0）

📜 对话工具
- gemini_chat: 单次对话（支持图片输入）
- gemini_start_chat: 创建多轮会话
- gemini_send_message: 会话消息
- gemini_list_sessions: 列会话
- gemini_reset_session: 重置会话

📚 深度研究
- gemini_deep_research: 深度研究（需 AI Plus）

🎨 媒体生成
- gemini_generate_media: 图像/视频/音乐生成
- gemini_generate_music: 音乐生成（便捷工具）

📁 文件分析
- gemini_upload_file: 上传并分析文件
- gemini_analyze_url: 分析网址（YouTube、网页等）

🔧 管理工具
- gemini_list_chats: 历史对话
- gemini_manage_gems: Gem 管理（CRUD）
- gemini_list_models: 模型列表
- gemini_list_features: 功能列表
- gemini_health_check: 健康检查
- gemini_reset: 重置客户端

---
⚠️ 使用提示:
- 设置 GEMINI_PSID 环境变量
- 从 gemini.google.com 获取 Cookie
- 部分功能需要 AI Plus 订阅
"""
    return [TextContent(type="text", text=features)]


def main():
    """启动服务器"""
    logger.info("🚀 启动 Gemini Web MCP Server (v2.0)...")
    mcp.run()


if __name__ == "__main__":
    main()
