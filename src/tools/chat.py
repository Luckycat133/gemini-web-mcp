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
from ..services import (
    ChatRequest,
    ChatService,
    ChatServiceDependencies,
    CleanupStrategy,
    SessionMessageRequest,
    StartSessionRequest,
)
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


def _build_chat_service() -> ChatService:
    """Bind the shared service to this adapter's patchable compatibility seams."""
    return ChatService(
        ChatServiceDependencies(
            client_provider=lambda: get_gemini_client(),
            client_initializer=lambda: initialize_client(),
            cleanup_due_remote_chats=lambda client: cleanup_due_remote_chats(client),
            create_session=lambda *args, **kwargs: create_session(*args, **kwargs),
            lookup_session=lambda session_id: lookup_session(session_id),
            send_session_message=lambda *args, **kwargs: send_session_message(
                *args,
                **kwargs,
            ),
            send_session_message_stream=lambda *args, **kwargs: send_session_message_stream(*args, **kwargs),
            schedule_response_cleanup=lambda *args, **kwargs: schedule_remote_chat_cleanup_from_response(
                *args, **kwargs
            ),
            schedule_chat_cleanup=lambda *args, **kwargs: schedule_remote_chat_cleanup(*args, **kwargs),
            normalize_model=lambda model: model,
            resolve_model=lambda model: resolve_model_name(model),
        )
    )


def register_chat_tools(mcp: FastMCP):
    chat_service = _build_chat_service()

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

        result = await chat_service.generate(
            ChatRequest(
                message=message,
                model=model,
                thinking_level=thinking_level,
                learning_mode=learning_mode,
                files=tuple(safe_image_paths or ()),
                gem_id=gem_id,
                temporary=temporary,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                cleanup_source="gemini_chat",
                include_gem_argument=True,
            )
        )
        assert result.data is not None
        logger.info(f"正在使用 {result.data.effective_model} 生成响应...")
        return attach_domain_result(
            parse_response(result.data.response, model),
            result,
            data={
                "model": result.data.requested_model,
                "resolved_model": result.data.effective_model,
                "temporary": result.data.temporary,
                "lifecycle": result.data.lifecycle,
            },
        )

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
        result = await chat_service.start_session(
            StartSessionRequest(
                model=model,
                thinking_level=thinking_level,
                learning_mode=learning_mode,
                gem_id=gem_id,
                temporary=temporary,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                include_gem_argument=True,
            )
        )
        if not result.ok or result.data is None:
            return domain_text(result, "❌ 会话创建失败")
        session_id = result.data.session_id or ""
        return domain_text(
            result,
            f"✅ 会话创建成功！\nID: {session_id}\n模型: {result.data.effective_model}\n使用 gemini_send_message 继续对话",
            data={
                "session_id": session_id,
                "model": result.data.requested_model,
                "resolved_model": result.data.effective_model,
                "lifecycle": result.data.lifecycle,
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

        result = await chat_service.send_session(
            SessionMessageRequest(
                session_id=session_id,
                message=message,
                files=tuple(safe_image_paths or ()),
                learning_mode=learning_mode,
                temporary=temporary,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                prepare_client=False,
                include_temporary=True,
                fallback_empty_thinking_level=False,
                cleanup_strategy=CleanupStrategy.SESSION,
                cleanup_source="gemini_send_message",
            )
        )
        if not result.ok or result.data is None:
            return domain_text(
                result,
                f"❌ SESSION_NOT_FOUND: 会话 {session_id} 不存在",
            )
        return domain_text(
            result,
            result.data.response.text,
            data={
                "session_id": session_id,
                "model": result.data.requested_model,
                "lifecycle": result.data.lifecycle,
            },
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
            data={
                "session_id": session_id,
                "lifecycle": result.meta.details.get("lifecycle"),
            },
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
                "lifecycle_state": data.get("lifecycle_state", "active"),
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

        result = await chat_service.generate_stream(
            ChatRequest(
                message=message,
                model=model,
                thinking_level=thinking_level,
                learning_mode=learning_mode,
                files=tuple(safe_image_paths or ()),
                gem_id=gem_id,
                temporary=temporary,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                cleanup_source="gemini_chat_stream",
                include_gem_argument=True,
            ),
            text_piece=get_stream_text_piece,
        )
        assert result.data is not None
        if result.data.response is not None:
            return attach_domain_result(
                parse_response(
                    result.data.response,
                    model,
                    text_override=(result.data.stream_text or getattr(result.data.response, "text", "")),
                ),
                result,
                data={
                    "model": result.data.requested_model,
                    "resolved_model": result.data.effective_model,
                    "temporary": result.data.temporary,
                    "streamed": True,
                    "lifecycle": result.data.lifecycle,
                },
            )
        return domain_text(
            result,
            result.data.stream_text,
            data={
                "model": result.data.requested_model,
                "resolved_model": result.data.effective_model,
                "temporary": result.data.temporary,
                "streamed": True,
                "lifecycle": result.data.lifecycle,
            },
        )

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

        result = await chat_service.send_session_stream(
            SessionMessageRequest(
                session_id=session_id,
                message=message,
                files=tuple(safe_image_paths or ()),
                learning_mode=learning_mode,
                temporary=temporary,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                prepare_client=False,
                include_temporary=True,
                fallback_empty_thinking_level=False,
                cleanup_strategy=CleanupStrategy.RESPONSE,
                cleanup_source="gemini_send_message_stream",
            ),
            text_piece=get_stream_text_piece,
        )
        if not result.ok or result.data is None:
            return domain_text(
                result,
                f"❌ SESSION_NOT_FOUND: 会话 {session_id} 不存在",
            )
        return domain_text(
            result,
            result.data.stream_text,
            data={
                "session_id": session_id,
                "model": result.data.requested_model,
                "streamed": True,
                "lifecycle": result.data.lifecycle,
            },
        )
