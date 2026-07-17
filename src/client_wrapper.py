"""
Gemini 客户端封装 - 门面模式
提供统一的向后兼容接口，内部委托给专门的管理类
"""

import os
import logging
from typing import Optional, Dict, Any

from .client_manager import (
    ClientManager,
    validate_config,
    get_configured_proxy,
    get_default_chat_retention_seconds,
    prepare_browser_cookie_cache as _prepare_browser_cookie_cache,
)
from .session_manager import SessionManager, SessionData
from .remote_chat_cleanup_manager import RemoteChatCleanupManager

logger = logging.getLogger(__name__)

try:
    from .cookie_manager import get_cookie_manager, init_cookie_manager, CookieData
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False
    logger.warning("cookie_manager 模块不可用")

# 全局管理器实例
_client_manager = ClientManager()
_session_manager = SessionManager()
_cleanup_manager = RemoteChatCleanupManager(
    client_provider=lambda: _client_manager.get_client(),
    retention_provider=get_default_chat_retention_seconds,
)


def _session_data_to_dict(data: Optional[SessionData]) -> Optional[Dict[str, Any]]:
    """将 SessionData 转换为字典以保持向后兼容"""
    if data is None:
        return None
    return {
        "session": data.session,
        "model": data.model,
        "thinking_level": data.thinking_level,
        "learning_mode": data.learning_mode,
        "temporary": data.temporary,
        "created_at": data.created_at,
        "retain_chat": data.retain_chat,
        "delete_after_seconds": data.delete_after_seconds,
    }


# ============ 客户端管理接口 ============

def get_gemini_client() -> Any:
    """获取或初始化 GeminiClient 实例"""
    return _client_manager.get_client()


async def initialize_client() -> Any:
    """完成客户端初始化"""
    return await _client_manager.initialize()


def reset_client() -> None:
    """重置客户端"""
    _client_manager.reset()
    _session_manager.clear_sessions()


# ============ 会话管理接口 ============

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
    _session_manager.store_session(
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
    return _session_data_to_dict(_session_manager.get_session(session_id))


def remove_session(session_id: str) -> None:
    """移除会话"""
    _session_manager.remove_session(session_id)


def pop_session(session_id: str) -> Optional[Dict[str, Any]]:
    """移除并返回会话数据。"""
    return _session_data_to_dict(_session_manager.pop_session(session_id))


def clear_sessions() -> None:
    """清空所有会话"""
    _session_manager.clear_sessions()


def cleanup_expired_sessions() -> None:
    """清理过期会话。"""
    _session_manager.cleanup_expired_sessions()


def list_sessions() -> Dict[str, Dict[str, Any]]:
    """获取所有会话"""
    sessions = _session_manager.list_sessions()
    return {
        sid: _session_data_to_dict(data)
        for sid, data in sessions.items()
    }


# ============ 远程聊天清理接口 ============

def schedule_remote_chat_cleanup_from_response(
    response: Any,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
) -> Optional[str]:
    """登记 response 产生的远端 chat，默认稍后自动删除。"""
    return _cleanup_manager.schedule_cleanup_from_response(
        response,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
    )


def schedule_remote_chat_cleanup(
    cid: Optional[str],
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
) -> None:
    """登记远端 Gemini chat 的自动删除任务。"""
    _cleanup_manager.schedule_cleanup(
        cid,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
    )


async def delete_remote_chat(cid: Optional[str], client: Any = None) -> bool:
    """立即删除远端 Gemini chat。"""
    if client is None:
        client = get_gemini_client()
        await initialize_client()
    return await _cleanup_manager.delete_chat(cid, client=client)


async def cleanup_due_remote_chats(client: Any = None) -> int:
    """清理已经到期的远端 Gemini chat。"""
    if client is None:
        client = get_gemini_client()
        await initialize_client()
    return await _cleanup_manager.cleanup_due_chats(client=client)


def list_pending_remote_chat_cleanup() -> Dict[str, Dict[str, Any]]:
    """返回待自动删除的远端 chat。"""
    pending = _cleanup_manager.list_pending_cleanup()
    return {
        cid: {
            "delete_at": data.delete_at,
            "source": data.source,
        }
        for cid, data in pending.items()
    }


# ============ Cookie 管理接口 ============

def _on_cookie_update(cookie_data: CookieData) -> None:
    """Cookie 更新回调"""
    logger.info("🔄 Cookie 已更新，重置客户端...")
    reset_client()
    os.environ["GEMINI_PSID"] = cookie_data.psid
    if cookie_data.psidts:
        os.environ["GEMINI_PSIDTS"] = cookie_data.psidts


def init_cookie_manager_integration() -> None:
    """初始化 Cookie Manager 集成"""
    if not COOKIE_MANAGER_AVAILABLE:
        return
    auto_refresh = os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
    init_cookie_manager(auto_refresh=auto_refresh, on_cookie_update=_on_cookie_update)
    get_cookie_manager().start_monitor()
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
