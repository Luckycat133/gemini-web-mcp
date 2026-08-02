"""
对话相关 MCP 工具
"""

import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..adapters import attach_domain_result, domain_error_boundary, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    create_session,
    get_gemini_client,
    initialize_client,
    list_sessions,
    lookup_session,
    reset_session,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
    send_session_message,
    send_session_message_stream,
)
from ..constants import describe_model_name, resolve_model_name
from ..domain import DomainErrorCode, DomainResult
from .annotations import DESTRUCTIVE_REMOTE, MUTATES_REMOTE, READ_ONLY_LOCAL
from .utils import get_stream_text_piece, parse_response, validate_image_paths

logger = logging.getLogger(__name__)


def _invalid_argument(message: str) -> DomainResult[None]:
    return DomainResult.failure(
        DomainErrorCode.INVALID_ARGUMENT,
        message,
        suggested_action="Correct the arguments and retry.",
        verification_status="input_rejected",
    )


def register_chat_tools(mcp: FastMCP):

    @mcp.tool(annotations=MUTATES_REMOTE)
    @domain_error_boundary("gemini_chat", logger)
    async def gemini_chat(
        message: str,
        model: str = "flash",
        thinking_level: str = "standard",
        learning_mode: Optional[str] = None,
        image_paths: Optional[list[str]] = None,
        gem_id: Optional[str] = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """单次对话"""
        valid_images, safe_image_paths, image_error = validate_image_paths(image_paths)
        if not valid_images:
            return domain_text(
                _invalid_argument(image_error or "Invalid image paths."),
                f"❌ {image_error}",
            )

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        logger.info(f"正在使用 {model_name} 生成响应...")
        request_kwargs = {
            "prompt": message,
            "files": safe_image_paths or None,
            "model": model_name,
            "thinking_level": thinking_level,
            "gem": gem_id,
            "temporary": temporary,
        }
        if learning_mode:
            request_kwargs["learning_mode"] = learning_mode
        response = await client.generate_content(**request_kwargs)
        schedule_remote_chat_cleanup_from_response(
            response,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source="gemini_chat",
        )
        result = DomainResult.success(
            {
                "model": model,
                "resolved_model": model_name,
                "temporary": temporary,
            },
            requested_backend=model,
            effective_backend=model_name,
            verification_status="upstream_response_received",
        )
        return attach_domain_result(parse_response(response, model), result, use_result_data=True)

    @mcp.tool(annotations=MUTATES_REMOTE)
    @domain_error_boundary("gemini_start_chat", logger)
    async def gemini_start_chat(
        model: str = "flash",
        thinking_level: str = "standard",
        learning_mode: Optional[str] = None,
        gem_id: Optional[str] = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """创建共享多轮会话，返回不可预测的 sess_<uuid> 本地 ID。"""
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        session = client.start_chat(model=model_name, gem=gem_id)
        created = create_session(
            session,
            model,
            thinking_level=thinking_level,
            learning_mode=learning_mode,
            temporary=temporary,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
        )
        session_id = created.session.session_id if created.session is not None else ""
        return domain_text(
            created,
            f"✅ 会话创建成功！\nID: {session_id}\n模型: {model_name}\n使用 gemini_send_message 继续对话",
            data={
                "session_id": session_id,
                "model": model,
                "resolved_model": model_name,
            },
        )

    @mcp.tool(annotations=MUTATES_REMOTE)
    @domain_error_boundary("gemini_send_message", logger)
    async def gemini_send_message(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
        learning_mode: Optional[str] = None,
        temporary: Optional[bool] = None,
        retain_chat: Optional[bool] = None,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """向现有共享会话发送消息；未知 ID 明确返回 SESSION_NOT_FOUND。"""
        valid_images, safe_image_paths, image_error = validate_image_paths(image_paths)
        if not valid_images:
            return domain_text(
                _invalid_argument(image_error or "Invalid image paths."),
                f"❌ {image_error}",
            )

        lookup = lookup_session(session_id)
        if not lookup.ok or lookup.session is None:
            return domain_text(
                lookup,
                f"❌ SESSION_NOT_FOUND: 会话 {session_id} 不存在",
            )
        session_data = lookup.session
        use_temporary = session_data.temporary if temporary is None else temporary
        request_kwargs = {
            "prompt": message,
            "files": safe_image_paths or None,
            "temporary": use_temporary,
            "thinking_level": session_data.thinking_level,
        }
        use_learning_mode = learning_mode or session_data.learning_mode
        if use_learning_mode:
            request_kwargs["learning_mode"] = use_learning_mode
        sent = await send_session_message(session_id, **request_kwargs)
        if not sent.ok or sent.session is None:
            return domain_text(
                sent,
                f"❌ SESSION_NOT_FOUND: 会话 {session_id} 不存在",
            )
        response = sent.response
        keep_chat = sent.session.retain_chat if retain_chat is None else retain_chat
        ttl = delete_after_seconds
        if ttl is None:
            ttl = sent.session.delete_after_seconds
        schedule_remote_chat_cleanup(
            getattr(sent.session.session, "cid", None) or sent.session.upstream_chat_id,
            retain_chat=keep_chat,
            delete_after_seconds=ttl,
            source="gemini_send_message",
        )
        return domain_text(
            sent,
            response.text,
            data={"session_id": session_id, "model": sent.session.model},
        )

    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("gemini_reset_session", logger)
    async def gemini_reset_session(session_id: str) -> list[TextContent]:
        """只重置指定会话；未知 ID 不影响其他会话并返回 SESSION_NOT_FOUND。"""
        result = await reset_session(session_id)
        if not result.ok:
            return domain_text(
                result,
                f"❌ SESSION_NOT_FOUND: 会话 {session_id} 不存在",
            )
        return domain_text(
            result,
            f"✅ 会话 {session_id} 已重置",
            data={"session_id": session_id},
        )

    @mcp.tool(annotations=READ_ONLY_LOCAL)
    @domain_error_boundary("gemini_list_sessions", logger)
    async def gemini_list_sessions() -> list[TextContent]:
        """列出 primary 与 compact 入口共享的本地会话。"""
        sessions = list_sessions()
        public_sessions = [
            {
                "session_id": sid,
                "model": data["model"],
                "retain_chat": data.get("retain_chat", False),
            }
            for sid, data in sessions.items()
        ]
        result = DomainResult.success(
            {
                "count": len(public_sessions),
                "sessions": public_sessions,
            }
        )
        if not sessions:
            return domain_text(result, "暂无活跃会话", use_result_data=True)
        session_list = ["活跃会话:"]
        for i, (sid, data) in enumerate(sessions.items(), 1):
            retain_text = "保留" if data.get("retain_chat", False) else "自动清理"
            session_list.append(f"{i}. {sid} - {describe_model_name(data['model'])} ({retain_text})")
        return domain_text(result, "\n".join(session_list), use_result_data=True)

    @mcp.tool(annotations=MUTATES_REMOTE)
    @domain_error_boundary("gemini_chat_stream", logger)
    async def gemini_chat_stream(
        message: str,
        model: str = "flash",
        thinking_level: str = "standard",
        learning_mode: Optional[str] = None,
        image_paths: Optional[list[str]] = None,
        gem_id: Optional[str] = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """流式对话"""
        valid_images, safe_image_paths, image_error = validate_image_paths(image_paths)
        if not valid_images:
            return domain_text(
                _invalid_argument(image_error or "Invalid image paths."),
                f"❌ {image_error}",
            )

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        full_text = ""
        final_response = None
        request_kwargs = {
            "prompt": message,
            "files": safe_image_paths or None,
            "model": model_name,
            "thinking_level": thinking_level,
            "gem": gem_id,
            "temporary": temporary,
        }
        if learning_mode:
            request_kwargs["learning_mode"] = learning_mode
        async for response in client.generate_content_stream(**request_kwargs):
            full_text += get_stream_text_piece(response)
            final_response = response
        result = DomainResult.success(
            {
                "model": model,
                "resolved_model": model_name,
                "temporary": temporary,
                "streamed": True,
            },
            requested_backend=model,
            effective_backend=model_name,
            verification_status="upstream_response_received",
        )
        if final_response:
            schedule_remote_chat_cleanup_from_response(
                final_response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source="gemini_chat_stream",
            )
            return attach_domain_result(
                parse_response(
                    final_response,
                    model,
                    text_override=full_text or getattr(final_response, "text", ""),
                ),
                result,
                use_result_data=True,
            )
        return domain_text(result, full_text, use_result_data=True)

    @mcp.tool(annotations=MUTATES_REMOTE)
    @domain_error_boundary("gemini_send_message_stream", logger)
    async def gemini_send_message_stream(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
        learning_mode: Optional[str] = None,
        temporary: Optional[bool] = None,
        retain_chat: Optional[bool] = None,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """从现有共享会话流式取回消息；未知 ID 返回 SESSION_NOT_FOUND。"""
        valid_images, safe_image_paths, image_error = validate_image_paths(image_paths)
        if not valid_images:
            return domain_text(
                _invalid_argument(image_error or "Invalid image paths."),
                f"❌ {image_error}",
            )

        lookup = lookup_session(session_id)
        if not lookup.ok or lookup.session is None:
            return domain_text(
                lookup,
                f"❌ SESSION_NOT_FOUND: 会话 {session_id} 不存在",
            )
        session_data = lookup.session
        full_text = ""
        final_response = None
        use_temporary = session_data.temporary if temporary is None else temporary
        request_kwargs = {
            "prompt": message,
            "files": safe_image_paths or None,
            "temporary": use_temporary,
            "thinking_level": session_data.thinking_level,
        }
        use_learning_mode = learning_mode or session_data.learning_mode
        if use_learning_mode:
            request_kwargs["learning_mode"] = use_learning_mode
        streamed = await send_session_message_stream(session_id, **request_kwargs)
        if not streamed.ok or streamed.session is None:
            return domain_text(
                streamed,
                f"❌ SESSION_NOT_FOUND: 会话 {session_id} 不存在",
            )
        for response in streamed.response:
            full_text += get_stream_text_piece(response)
            final_response = response
        keep_chat = streamed.session.retain_chat if retain_chat is None else retain_chat
        ttl = delete_after_seconds
        if ttl is None:
            ttl = streamed.session.delete_after_seconds
        schedule_remote_chat_cleanup_from_response(
            final_response,
            retain_chat=keep_chat,
            delete_after_seconds=ttl,
            source="gemini_send_message_stream",
        )
        return domain_text(
            streamed,
            full_text,
            data={
                "session_id": session_id,
                "model": streamed.session.model,
                "streamed": True,
            },
        )
