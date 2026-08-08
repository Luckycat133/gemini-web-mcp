"""
Gemini 客户端封装 - 门面模式
提供统一的向后兼容接口，内部委托给专门的管理类
"""

import inspect
import logging
import os
from collections.abc import Mapping
from functools import wraps
from typing import Any, Dict, Optional, Self

from .client_manager import (
    ClientManager,
    get_configured_proxy,  # noqa: F401  (re-exported as public facade API)
    get_default_chat_retention_seconds,
    validate_config,  # noqa: F401  (re-exported as public facade API)
)
from .client_manager import (
    prepare_browser_cookie_cache as _prepare_browser_cookie_cache,
)
from .domain import (
    CleanupObservation,
    CleanupState,
    DomainResult,
    is_valid_remote_chat_id,
)
from .remote_chat_cleanup_manager import RemoteChatCleanupManager
from .services.lifecycle import (
    ConversationLifecycleService,
    LifecycleResetAllData,
)
from .session_manager import SessionData, SessionOperationResult, SessionService

logger = logging.getLogger(__name__)

try:
    from .cookie_manager import CookieData, get_cookie_manager, init_cookie_manager
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False
    logger.warning("cookie_manager 模块不可用")

# 全局管理器实例
_client_manager = ClientManager()
_session_manager = SessionService()
_cleanup_manager = RemoteChatCleanupManager(
    client_provider=lambda: _client_manager.get_client(),
    retention_provider=get_default_chat_retention_seconds,
)
_lifecycle_service = ConversationLifecycleService(
    session_provider=lambda: _session_manager,
    cleanup_provider=lambda: _cleanup_manager,
)


class _AttributeMapping(dict[str, Any]):
    """Keep mapping semantics while exposing fields to legacy attribute readers."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _adapt_history_value(value: Any) -> Any:
    """Recursively adapt mapping-backed history values without changing objects."""

    if isinstance(value, _AttributeMapping):
        return value
    if isinstance(value, Mapping):
        return _AttributeMapping({key: _adapt_history_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_adapt_history_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_adapt_history_value(item) for item in value)
    return value


def _install_history_compatibility(client: Any) -> Any:
    """Normalize only history-return values while preserving the client identity."""

    if getattr(client, "_gemini_mcp_history_compatible", False):
        return client

    list_chats = getattr(client, "list_chats", None)
    if callable(list_chats):
        @wraps(list_chats)
        def list_chats_compatible(*args: Any, **kwargs: Any) -> Any:
            return _adapt_history_value(list_chats(*args, **kwargs))

        try:
            setattr(client, "list_chats", list_chats_compatible)
        except (AttributeError, TypeError):
            logger.debug("Could not install list_chats mapping compatibility on %s", type(client).__name__)

    read_chat = getattr(client, "read_chat", None)
    if callable(read_chat):
        @wraps(read_chat)
        async def read_chat_compatible(*args: Any, **kwargs: Any) -> Any:
            result = read_chat(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return _adapt_history_value(result)

        try:
            setattr(client, "read_chat", read_chat_compatible)
        except (AttributeError, TypeError):
            logger.debug("Could not install read_chat mapping compatibility on %s", type(client).__name__)

    try:
        setattr(client, "_gemini_mcp_history_compatible", True)
    except (AttributeError, TypeError):
        pass
    return client


class ScheduledCleanupChatId(str):
    """String-compatible chat ID carrying its structured scheduling evidence."""

    cleanup_observation: CleanupObservation

    def __new__(
        cls,
        value: str,
        observation: CleanupObservation,
    ) -> Self:
        instance = super().__new__(cls, value)
        instance.cleanup_observation = observation
        return instance


def _session_data_to_dict(data: Optional[SessionData]) -> Optional[Dict[str, Any]]:
    """将 SessionData 转换为字典以保持向后兼容"""
    if data is None:
        return None
    return {
        "session": data.session,
        "session_id": data.session_id,
        "model": data.model,
        "thinking_level": data.thinking_level,
        "learning_mode": data.learning_mode,
        "temporary": data.temporary,
        "created_at": data.created_at,
        "updated_at": data.updated_at,
        "upstream_chat_id": data.upstream_chat_id,
        "retain_chat": data.retain_chat,
        "delete_after_seconds": data.delete_after_seconds,
        "lifecycle_state": data.lifecycle_state.value,
    }


# ============ 客户端管理接口 ============

def get_gemini_client() -> Any:
    """获取 GeminiClient，并规范化混合对象/映射历史返回值。"""
    return _install_history_compatibility(_client_manager.get_client())


async def initialize_client() -> Any:
    """完成客户端初始化"""
    return await _client_manager.initialize()


def reset_client() -> None:
    """重置客户端"""
    _client_manager.reset()
    _lifecycle_service.clear_sessions()


async def reset_client_async() -> Optional[DomainResult[LifecycleResetAllData]]:
    """重置客户端并等待旧连接关闭。"""
    reset_result: Optional[DomainResult[LifecycleResetAllData]] = None
    if isinstance(_session_manager, SessionService):
        reset_result = await _lifecycle_service.reset_all(
            delete_callback=lambda cid: delete_remote_chat(cid),
        )
    else:
        _lifecycle_service.clear_sessions()
    await _client_manager.reset_async()
    return reset_result


# ============ 会话管理接口 ============

def create_session(
    session: Any,
    model: str = "flash",
    thinking_level: str = "standard",
    learning_mode: Optional[str] = None,
    temporary: bool = False,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
) -> SessionOperationResult:
    """创建带不可碰撞本地 ID 的共享会话。"""
    return _lifecycle_service.create_session(
        session,
        model,
        thinking_level=thinking_level,
        learning_mode=learning_mode,
        temporary=temporary,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
    )


def store_session(
    session_id: str,
    session: Any,
    model: str = "flash",
    thinking_level: str = "standard",
    learning_mode: Optional[str] = None,
    temporary: bool = False,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
) -> None:
    """存储会话"""
    _lifecycle_service.store_session(
        session_id,
        session,
        model,
        thinking_level=thinking_level,
        learning_mode=learning_mode,
        temporary=temporary,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
    )


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """获取存储的会话"""
    return _session_data_to_dict(_lifecycle_service.get_session(session_id))


def lookup_session(session_id: str) -> SessionOperationResult:
    """查找共享会话并保留显式 SESSION_NOT_FOUND 结果。"""
    return _lifecycle_service.lookup_session(session_id)


async def send_session_message(session_id: str, **request_kwargs: Any) -> SessionOperationResult:
    """通过共享会话服务串行发送消息。"""
    return await _lifecycle_service.send_message(session_id, **request_kwargs)


async def send_session_message_stream(session_id: str, **request_kwargs: Any) -> SessionOperationResult:
    """通过共享会话服务串行消费一次流式消息。"""
    return await _lifecycle_service.send_message_stream(session_id, **request_kwargs)


async def reset_session(session_id: str) -> SessionOperationResult:
    """只重置指定会话，并按保留策略清理其远端聊天。"""
    return await _lifecycle_service.reset_one(
        session_id,
        delete_callback=lambda cid: delete_remote_chat(cid),
    )


def remove_session(session_id: str) -> None:
    """移除会话"""
    _lifecycle_service.remove_session(session_id)


def pop_session(session_id: str) -> Optional[Dict[str, Any]]:
    """移除并返回会话数据。"""
    return _session_data_to_dict(_lifecycle_service.pop_session(session_id))


def clear_sessions() -> None:
    """清空所有会话"""
    _lifecycle_service.clear_sessions()


def cleanup_expired_sessions() -> None:
    """清理过期会话。"""
    _lifecycle_service.cleanup_expired_sessions()


def list_sessions() -> Dict[str, Dict[str, Any]]:
    """获取所有会话"""
    sessions = _lifecycle_service.list_sessions()
    return {
        sid: _session_data_to_dict(data)  # type: ignore[misc]  # data 非 None 时返回 Dict
        for sid, data in sessions.items()
        if data is not None
    }


# ============ 远程聊天清理接口 ============

def schedule_remote_chat_cleanup_from_response(
    response: Any,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
) -> Optional[str]:
    """登记 response 产生的远端 chat，默认稍后自动删除。"""
    observation = _lifecycle_service.schedule_cleanup_from_response(
        response,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
    )
    if observation.upstream_chat_id is None:
        return None
    return ScheduledCleanupChatId(
        observation.upstream_chat_id,
        observation,
    )


def schedule_remote_chat_cleanup(
    cid: Optional[str],
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
) -> CleanupObservation:
    """登记远端 Gemini chat 的自动删除任务。"""
    return _lifecycle_service.schedule_cleanup(
        cid,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
    )


async def delete_remote_chat(cid: Optional[str], client: Any = None) -> bool:
    """立即删除远端 Gemini chat。"""
    if not is_valid_remote_chat_id(cid):
        return False
    if client is None:
        client = get_gemini_client()
        await initialize_client()
    observation = await _lifecycle_service.delete_chat_result(cid, client=client)
    return observation.state in {
        CleanupState.COMPLETED,
        CleanupState.ALREADY_COMPLETED,
    }


async def delete_remote_chat_result(
    cid: Optional[str],
    client: Any = None,
) -> CleanupObservation:
    """Immediately delete a chat and retain structured lifecycle evidence."""
    if not is_valid_remote_chat_id(cid):
        return CleanupObservation(
            state=(CleanupState.INVALID_ID if cid is not None else CleanupState.NOT_APPLICABLE),
        )
    if client is None:
        client = get_gemini_client()
        await initialize_client()
    return await _lifecycle_service.delete_chat_result(cid, client=client)


async def cleanup_due_remote_chats(client: Any = None) -> int:
    """清理已经到期的远端 Gemini chat。"""
    if client is None:
        client = get_gemini_client()
        await initialize_client()
    return await _lifecycle_service.cleanup_due_chats(client=client)


def list_pending_remote_chat_cleanup() -> Dict[str, Dict[str, Any]]:
    """返回待自动删除的远端 chat。"""
    pending = _cleanup_manager.list_pending_cleanup()
    return {
        cid: {
            "delete_at": data.delete_at,
            "source": data.source,
            "attempts": data.attempts,
            "diagnostic_id": data.last_diagnostic_id,
        }
        for cid, data in pending.items()
    }


# ============ Cookie 管理接口 ============

def _on_cookie_update(cookie_data: CookieData) -> None:
    """Cookie 更新回调"""
    logger.info("🔄 Cookie 已更新，重置客户端...")
    os.environ["GEMINI_PSID"] = cookie_data.psid
    if cookie_data.psidts:
        os.environ["GEMINI_PSIDTS"] = cookie_data.psidts
    else:
        os.environ.pop("GEMINI_PSIDTS", None)
    reset_client()


def init_cookie_manager_integration() -> None:
    """初始化 Cookie Manager 集成"""
    if not COOKIE_MANAGER_AVAILABLE:
        return
    auto_refresh = os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
    cookie_manager = init_cookie_manager(auto_refresh=auto_refresh, on_cookie_update=_on_cookie_update)
    if auto_refresh:
        cookie_manager.start_monitor()
    logger.info("✅ Cookie Manager 集成已初始化")


def get_cookie_from_browser(browser: str = "chrome", profile: str = "") -> bool:
    """从浏览器获取 Cookie"""
    if not COOKIE_MANAGER_AVAILABLE:
        logger.error("❌ Cookie Manager 不可用")
        return False
    _prepare_browser_cookie_cache(force=True)
    cm = get_cookie_manager()
    cookies = cm.get_cookies_from_browser(browser, profile=profile)
    psid = cookies.get("__Secure-1PSID")
    psidts = cookies.get("__Secure-1PSIDTS", "")
    if psid:
        source = f"browser_{browser}"
        if profile:
            source += f":{profile}"
        success = cm.update_cookie(
            psid,
            psidts,
            source=source,
            extra_cookies=cookies,
        )
        if success:
            os.environ["GEMINI_PSID"] = psid
            if psidts:
                os.environ["GEMINI_PSIDTS"] = psidts
            logger.info("✅ 已从浏览器获取 Cookie 并更新")
        return success
    return False


def list_browser_cookie_profiles(browser: str = "chrome", validate: bool = True) -> list[dict[str, Any]]:
    """List local browser cookie profile diagnostics without exposing cookie values."""
    if not COOKIE_MANAGER_AVAILABLE:
        return [{"browser": browser, "error": "Cookie Manager unavailable"}]
    if validate:
        _prepare_browser_cookie_cache(force=True)
    return get_cookie_manager().list_browser_cookie_profiles(browser, validate=validate)


def get_cookie_status() -> Dict[str, Any]:
    """获取 Cookie 状态"""
    if not COOKIE_MANAGER_AVAILABLE:
        return {"available": False, "message": "Cookie Manager 不可用"}
    status, info = get_cookie_manager().get_cookie_status()
    return {"available": True, "status": status.value, **info}
