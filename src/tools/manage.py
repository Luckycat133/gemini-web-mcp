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
                chat_id = getattr(chat, "cid", "") or getattr(chat, "id", "")
                chat_list.append(f"{i}. {chat_title} (ID: {chat_id})")
            
            return [TextContent(type="text", text="\n".join(chat_list))]
            
        except Exception as e:
            logger.error(f"获取聊天列表失败: {e}")
            return [TextContent(type="text", text=f"❌ 获取失败: {str(e)}")]

    @mcp.tool()
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
