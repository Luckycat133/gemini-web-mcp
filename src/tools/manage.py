"""
会话和 Gem 管理 MCP 工具
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal, Optional
import logging

from ..client_wrapper import get_gemini_client, initialize_client
from ..constants import MODEL_CONFIG

logger = logging.getLogger(__name__)


def register_manage_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_list_chats(
        limit: int = 10,
    ) -> list[TextContent]:
        """列出历史对话记录"""
        client = get_gemini_client()
        await initialize_client()

        try:
            chats = client.list_chats()
            if not chats:
                return [TextContent(type="text", text="暂无历史对话。")]
            
            chat_list = ["## 📜 历史对话"]
            for i, chat in enumerate(chats[:limit], 1):
                chat_title = getattr(chat, "title", "Untitled")
                chat_id = getattr(chat, "id", "")
                chat_list.append(f"{i}. {chat_title} (ID: {chat_id})")
            
            return [TextContent(type="text", text="\n".join(chat_list))]
            
        except Exception as e:
            logger.error(f"获取聊天列表失败: {e}")
            return [TextContent(type="text", text=f"❌ 获取失败: {str(e)}")]

    @mcp.tool()
    async def gemini_list_models() -> list[TextContent]:
        """列出所有可用模型及其说明"""
        model_info = """🤖 可用模型（2026.5 更新）:

1. fast → gemini-3-flash
   - ✅ 快速响应，适合日常问答
   - ✅ 音乐生成 = Lyria 3 Clip (30秒)
   - 📌 免费可用

2. thinking → gemini-3-flash-thinking
   - ✅ 带推理链，适合复杂问题
   - ✅ 音乐生成 = Lyria 3 Pro (完整歌曲)
   - 📌 免费可用

3. pro → gemini-3.1-pro
   - ✅ 最强能力，适合专业任务
   - ✅ 音乐生成 = Lyria 3 Pro (完整歌曲)
   - 📌 需要 AI Plus 订阅

---

📝 媒体生成功能:
- 所有模型支持图像生成 (Nano Banana 2)
- 所有模型支持视频生成 (Veo 3.1，最长60秒)
- Deep Research: 需要 AI Plus 订阅
"""
        return [TextContent(type="text", text=model_info)]

    @mcp.tool()
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
