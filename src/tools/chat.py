"""
对话相关 MCP 工具
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Optional
import uuid
import logging

from ..client_wrapper import (
    cleanup_due_remote_chats,
    delete_remote_chat,
    get_gemini_client,
    initialize_client,
    store_session,
    get_session,
    pop_session,
    list_sessions,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import describe_model_name, resolve_model_name
from .utils import get_stream_text_piece, parse_response

logger = logging.getLogger(__name__)


def register_chat_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_chat(
        message: str,
        model: str = "fast",
        thinking_level: str = "standard",
        image_paths: Optional[list[str]] = None,
        gem_id: Optional[str] = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """单次对话"""
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        logger.info(f"正在使用 {model_name} 生成响应...")
        response = await client.generate_content(
            prompt=message,
            files=image_paths,
            model=model_name,
            thinking_level=thinking_level,
            gem=gem_id,
            temporary=temporary,
        )
        schedule_remote_chat_cleanup_from_response(
            response,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source="gemini_chat",
        )
        return parse_response(response, model)

    @mcp.tool()
    async def gemini_start_chat(
        model: str = "fast",
        thinking_level: str = "standard",
        gem_id: Optional[str] = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """创建多轮会话"""
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        session = client.start_chat(model=model_name, gem=gem_id)
        session_id = str(uuid.uuid4())[:8]
        store_session(
            session_id,
            session,
            model,
            thinking_level=thinking_level,
            temporary=temporary,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
        )
        return [TextContent(
            type="text",
            text=f"✅ 会话创建成功！\nID: {session_id}\n模型: {model_name}\n使用 gemini_send_message 继续对话"
        )]

    @mcp.tool()
    async def gemini_send_message(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
        temporary: Optional[bool] = None,
        retain_chat: Optional[bool] = None,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """会话消息"""
        session_data = get_session(session_id)
        if not session_data:
            return [TextContent(type="text", text=f"❌ 会话 {session_id} 不存在")]
        use_temporary = session_data.get("temporary", False) if temporary is None else temporary
        response = await session_data["session"].send_message(
            prompt=message,
            files=image_paths,
            temporary=use_temporary,
            thinking_level=session_data.get("thinking_level", "standard"),
        )
        keep_chat = session_data.get("retain_chat", False) if retain_chat is None else retain_chat
        ttl = delete_after_seconds
        if ttl is None:
            ttl = session_data.get("delete_after_seconds")
        schedule_remote_chat_cleanup(
            getattr(session_data["session"], "cid", None),
            retain_chat=keep_chat,
            delete_after_seconds=ttl,
            source="gemini_send_message",
        )
        return [TextContent(type="text", text=response.text)]

    @mcp.tool()
    async def gemini_reset_session(session_id: str) -> list[TextContent]:
        """重置会话"""
        session_data = pop_session(session_id)
        if session_data and not session_data.get("retain_chat", False):
            await delete_remote_chat(getattr(session_data["session"], "cid", None))
        return [TextContent(type="text", text=f"✅ 会话 {session_id} 已重置")]

    @mcp.tool()
    async def gemini_list_sessions() -> list[TextContent]:
        """列出会话"""
        sessions = list_sessions()
        if not sessions:
            return [TextContent(type="text", text="暂无活跃会话")]
        session_list = ["活跃会话:"]
        for i, (sid, data) in enumerate(sessions.items(), 1):
            retain_text = "保留" if data.get("retain_chat", False) else "自动清理"
            session_list.append(f"{i}. {sid} - {describe_model_name(data['model'])} ({retain_text})")
        return [TextContent(type="text", text="\n".join(session_list))]

    @mcp.tool()
    async def gemini_chat_stream(
        message: str,
        model: str = "fast",
        thinking_level: str = "standard",
        image_paths: Optional[list[str]] = None,
        gem_id: Optional[str] = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """流式对话"""
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        full_text = ""
        final_response = None
        async for response in client.generate_content_stream(
            prompt=message,
            files=image_paths,
            model=model_name,
            thinking_level=thinking_level,
            gem=gem_id,
            temporary=temporary,
        ):
            full_text += get_stream_text_piece(response)
            final_response = response
        if final_response:
            schedule_remote_chat_cleanup_from_response(
                final_response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source="gemini_chat_stream",
            )
            return parse_response(
                final_response,
                model,
                text_override=full_text or getattr(final_response, "text", ""),
            )
        return [TextContent(type="text", text=full_text)]

    @mcp.tool()
    async def gemini_send_message_stream(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
        temporary: Optional[bool] = None,
        retain_chat: Optional[bool] = None,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """会话流式消息"""
        session_data = get_session(session_id)
        if not session_data:
            return [TextContent(type="text", text=f"❌ 会话 {session_id} 不存在")]
        full_text = ""
        final_response = None
        use_temporary = session_data.get("temporary", False) if temporary is None else temporary
        async for response in session_data["session"].send_message_stream(
            prompt=message,
            files=image_paths,
            temporary=use_temporary,
            thinking_level=session_data.get("thinking_level", "standard"),
        ):
            full_text += get_stream_text_piece(response)
            final_response = response
        keep_chat = session_data.get("retain_chat", False) if retain_chat is None else retain_chat
        ttl = delete_after_seconds
        if ttl is None:
            ttl = session_data.get("delete_after_seconds")
        schedule_remote_chat_cleanup_from_response(
            final_response,
            retain_chat=keep_chat,
            delete_after_seconds=ttl,
            source="gemini_send_message_stream",
        )
        return [TextContent(type="text", text=full_text)]
