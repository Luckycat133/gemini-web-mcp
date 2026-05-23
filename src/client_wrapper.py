"""
Gemini 客户端封装 - 线程安全版本
"""

import asyncio
import os
import time
import threading
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

try:
    from .cookie_manager import get_cookie_manager, init_cookie_manager, CookieData
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False
    logger.warning("cookie_manager 模块不可用")

_client: Optional[Any] = None
_initialized: bool = False
_client_lock = threading.Lock()
_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()
_remote_chat_cleanup_lock = threading.Lock()
_pending_remote_chat_cleanup: Dict[str, Dict[str, Any]] = {}


def validate_config() -> None:
    """验证必需的环境变量"""
    required = ["GEMINI_PSID"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise ValueError(f"缺少必需的环境变量: {', '.join(missing)}")


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


def get_cookie_from_browser(browser: str = "chrome") -> bool:
    """从浏览器获取 Cookie"""
    if not COOKIE_MANAGER_AVAILABLE:
        logger.error("❌ Cookie Manager 不可用")
        return False
    cm = get_cookie_manager()
    cookies = cm.get_cookies_from_browser(browser)
    psid = cookies.get("__Secure-1PSID")
    psidts = cookies.get("__Secure-1PSIDTS", "")
    if psid:
        success = cm.update_cookie(
            psid,
            psidts,
            source=f"browser_{browser}",
            extra_cookies=cookies,
        )
        if success:
            os.environ["GEMINI_PSID"] = psid
            if psidts:
                os.environ["GEMINI_PSIDTS"] = psidts
            logger.info("✅ 已从浏览器获取 Cookie 并更新")
        return success
    return False


def _get_extra_cookies() -> Dict[str, str]:
    """获取当前 Cookie Manager 中的完整认证 Cookie。"""
    if not COOKIE_MANAGER_AVAILABLE:
        return {}
    cookie_data = get_cookie_manager().get_cookie()
    if not cookie_data:
        return {}
    return cookie_data.extra_cookies


def get_cookie_status() -> Dict[str, Any]:
    """获取 Cookie 状态"""
    if not COOKIE_MANAGER_AVAILABLE:
        return {"available": False, "message": "Cookie Manager 不可用"}
    status, info = get_cookie_manager().get_cookie_status()
    return {"available": True, "status": status.value, **info}


def get_default_chat_retention_seconds() -> int:
    """远端 Gemini 对话默认保留时间。"""
    raw_value = os.environ.get("GEMINI_CHAT_RETENTION_SECONDS", "1800")
    try:
        return max(0, int(raw_value))
    except ValueError:
        logger.warning(f"无效的 GEMINI_CHAT_RETENTION_SECONDS={raw_value!r}，使用 1800 秒")
        return 1800


def get_gemini_client() -> Any:
    """获取或初始化 GeminiClient 实例"""
    global _client
    with _client_lock:
        if _client is None:
            validate_config()
            try:
                from .thinking_client import ThinkingLevelGeminiClient
            except ImportError:
                raise ImportError("请先安装 gemini-webapi")
            psid = os.environ.get("GEMINI_PSID")
            psidts = os.environ.get("GEMINI_PSIDTS", "")
            proxy = os.environ.get("GEMINI_PROXY")
            logger.info("正在初始化 GeminiClient...")
            _client = ThinkingLevelGeminiClient(psid, psidts, proxy=proxy)
            extra_cookies = _get_extra_cookies()
            if extra_cookies:
                _client.cookies = extra_cookies
                logger.info(f"已加载 {len(extra_cookies)} 个完整认证 Cookie")
    return _client


async def initialize_client() -> Any:
    """完成客户端初始化"""
    global _client, _initialized
    client = get_gemini_client()
    if not _initialized:
        logger.info("正在调用 client.init()...")
        await client.init(
            timeout=30,
            auto_close=False,
            auto_refresh=os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
        )
        with _client_lock:
            _initialized = True
        logger.info("✅ GeminiClient 初始化完成！")
    return client


def _clean_expired_sessions() -> None:
    """清理过期会话（内部函数，需在锁内调用）"""
    now = time.time()
    max_age = 1800
    expired = [sid for sid, data in _sessions.items() if now - data["created_at"] > max_age]
    for sid in expired:
        del _sessions[sid]
    if expired:
        logger.info(f"清理了 {len(expired)} 个过期会话")


def cleanup_expired_sessions() -> None:
    """清理过期会话。"""
    with _sessions_lock:
        _clean_expired_sessions()


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """获取存储的会话"""
    with _sessions_lock:
        _clean_expired_sessions()
        return _sessions.get(session_id)


def remove_session(session_id: str) -> None:
    """移除会话"""
    with _sessions_lock:
        if session_id in _sessions:
            del _sessions[session_id]


def pop_session(session_id: str) -> Optional[Dict[str, Any]]:
    """移除并返回会话数据。"""
    with _sessions_lock:
        return _sessions.pop(session_id, None)


def clear_sessions() -> None:
    """清空所有会话"""
    with _sessions_lock:
        _sessions.clear()


def reset_client() -> None:
    """重置客户端"""
    global _client, _initialized
    with _client_lock:
        _client = None
        _initialized = False
    clear_sessions()
    logger.info("✅ 客户端已重置")


def list_sessions() -> Dict[str, Dict[str, Any]]:
    """获取所有会话"""
    with _sessions_lock:
        _clean_expired_sessions()
        return _sessions.copy()


def load_images(image_paths: List[str]) -> List[Any]:
    """安全加载图片"""
    images = []
    if not image_paths:
        return images
    try:
        from PIL import Image
        for path in image_paths:
            if path and path.strip():
                try:
                    images.append(Image.open(path))
                except Exception as e:
                    logger.warning(f"加载图片失败: {path}, 错误: {e}")
    except ImportError:
        logger.warning("未安装 PIL/Pillow")
    except Exception as e:
        logger.error(f"加载图片出错: {e}")
    return images


def store_session(
    session_id: str,
    session: Any,
    model: str = "flash",
    thinking_level: str = "standard",
    temporary: bool = False,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
) -> None:
    """存储会话"""
    with _sessions_lock:
        _sessions[session_id] = {
            "session": session,
            "model": model,
            "thinking_level": thinking_level,
            "temporary": temporary,
            "created_at": time.time(),
            "retain_chat": retain_chat,
            "delete_after_seconds": delete_after_seconds,
        }


def _extract_remote_chat_id(obj: Any) -> Optional[str]:
    """从 Gemini response/chat/session 对象中提取远端 chat id。"""
    cid = getattr(obj, "cid", None)
    if isinstance(cid, str) and cid.startswith("c_"):
        return cid

    metadata = getattr(obj, "metadata", None)
    if isinstance(metadata, list) and metadata:
        cid = metadata[0]
        if isinstance(cid, str) and cid.startswith("c_"):
            return cid

    return None


def schedule_remote_chat_cleanup_from_response(
    response: Any,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
) -> Optional[str]:
    """登记 response 产生的远端 chat，默认稍后自动删除。"""
    cid = _extract_remote_chat_id(response)
    if cid:
        schedule_remote_chat_cleanup(
            cid,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=source,
        )
    return cid


def schedule_remote_chat_cleanup(
    cid: Optional[str],
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
) -> None:
    """登记远端 Gemini chat 的自动删除任务。"""
    if not cid or retain_chat:
        return

    ttl = get_default_chat_retention_seconds() if delete_after_seconds is None else max(0, delete_after_seconds)
    delete_at = time.time() + ttl
    with _remote_chat_cleanup_lock:
        _pending_remote_chat_cleanup[cid] = {
            "delete_at": delete_at,
            "source": source,
        }

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_delete_remote_chat_after_delay(cid, delete_at))


async def _delete_remote_chat_after_delay(cid: str, delete_at: float) -> None:
    await asyncio.sleep(max(0, delete_at - time.time()))
    with _remote_chat_cleanup_lock:
        pending = _pending_remote_chat_cleanup.get(cid)
        if not pending or pending.get("delete_at") != delete_at:
            return
    await delete_remote_chat(cid)


async def delete_remote_chat(cid: Optional[str], client: Any = None) -> bool:
    """立即删除远端 Gemini chat。"""
    if not cid:
        return False
    client = client or get_gemini_client()
    await initialize_client()
    if not hasattr(client, "delete_chat"):
        logger.warning("当前 GeminiClient 不支持 delete_chat")
        return False
    try:
        await client.delete_chat(cid)
    except Exception as e:
        logger.warning(f"删除远端 Gemini 对话失败 {cid}: {e}")
        return False
    with _remote_chat_cleanup_lock:
        _pending_remote_chat_cleanup.pop(cid, None)
    logger.info(f"已删除远端 Gemini 对话: {cid}")
    return True


async def cleanup_due_remote_chats(client: Any = None) -> int:
    """清理已经到期的远端 Gemini chat。"""
    now = time.time()
    with _remote_chat_cleanup_lock:
        due_cids = [
            cid
            for cid, data in _pending_remote_chat_cleanup.items()
            if data.get("delete_at", 0) <= now
        ]
    deleted = 0
    for cid in due_cids:
        if await delete_remote_chat(cid, client=client):
            deleted += 1
    return deleted


def list_pending_remote_chat_cleanup() -> Dict[str, Dict[str, Any]]:
    """返回待自动删除的远端 chat。"""
    with _remote_chat_cleanup_lock:
        return dict(_pending_remote_chat_cleanup)
