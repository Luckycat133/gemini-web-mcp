"""
对话相关 MCP 工具
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Optional, Literal
import uuid
import logging

from ..client_wrapper import (
    get_gemini_client,
    initialize_client,
    store_session,
    get_session,
    remove_session,
    list_sessions,
    load_images,
)
from ..constants import MODEL_CONFIG
from .utils import parse_response

logger = logging.getLogger(__name__)


def register_chat_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_chat(
        message: str,
        model: Literal["fast", "thinking", "pro"] = "fast",
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """单次对话"""
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]
        contents = [message]
        if image_paths:
            contents.extend(load_images(image_paths))
        logger.info(f"正在使用 {config['name']} 生成响应...")
        response = await client.generate_content(contents, model=config["name"])
        return parse_response(response, model)

    @mcp.tool()
    async def gemini_start_chat(
        system_instruction: str = "",
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """创建多轮会话"""
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]
        session = client.start_chat(system_instruction=system_instruction, model=config["name"])
        session_id = str(uuid.uuid4())[:8]
        store_session(session_id, session, model)
        return [TextContent(
            type="text",
            text=f"✅ 会话创建成功！\nID: {session_id}\n模型: {config['name']}\n使用 gemini_send_message 继续对话"
        )]

    @mcp.tool()
    async def gemini_send_message(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """会话消息"""
        session_data = get_session(session_id)
        if not session_data:
            return [TextContent(type="text", text=f"❌ 会话 {session_id} 不存在")]
        contents = [message]
        if image_paths:
            contents.extend(load_images(image_paths))
        response = await session_data["session"].send_message(contents)
        return [TextContent(type="text", text=response.text)]

    @mcp.tool()
    async def gemini_reset_session(session_id: str) -> list[TextContent]:
        """重置会话"""
        remove_session(session_id)
        return [TextContent(type="text", text=f"✅ 会话 {session_id} 已重置")]

    @mcp.tool()
    async def gemini_list_sessions() -> list[TextContent]:
        """列出会话"""
        sessions = list_sessions()
        if not sessions:
            return [TextContent(type="text", text="暂无活跃会话")]
        session_list = ["活跃会话:"]
        for i, (sid, data) in enumerate(sessions.items(), 1):
            session_list.append(f"{i}. {sid} - {MODEL_CONFIG[data['model']]['name']}")
        return [TextContent(type="text", text="\n".join(session_list))]

    @mcp.tool()
    async def gemini_chat_stream(
        message: str,
        model: Literal["fast", "thinking", "pro"] = "fast",
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """流式对话"""
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]
        contents = [message]
        if image_paths:
            contents.extend(load_images(image_paths))
        full_text = ""
        final_response = None
        async for response in client.generate_content_stream(contents, model=config["name"]):
            if response.text:
                full_text += response.text
            final_response = response
        if final_response:
            final_response.text = full_text or final_response.text
            return parse_response(final_response, model)
        return [TextContent(type="text", text=full_text)]

    @mcp.tool()
    async def gemini_send_message_stream(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """会话流式消息"""
        session_data = get_session(session_id)
        if not session_data:
            return [TextContent(type="text", text=f"❌ 会话 {session_id} 不存在")]
        contents = [message]
        if image_paths:
            contents.extend(load_images(image_paths))
        full_text = ""
        async for response in session_data["session"].send_message_stream(contents):
            if response.text:
                full_text += response.text
        return [TextContent(type="text", text=full_text)]
