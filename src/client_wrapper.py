"""
Gemini 客户端封装 - 线程安全版本
"""

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
    psid, psidts = cm.get_cookie_from_browser(browser)
    if psid:
        success = cm.update_cookie(psid, psidts, source=f"browser_{browser}")
        if success:
            os.environ["GEMINI_PSID"] = psid
            if psidts:
                os.environ["GEMINI_PSIDTS"] = psidts
            logger.info("✅ 已从浏览器获取 Cookie 并更新")
        return success
    return False


def get_cookie_status() -> Dict[str, Any]:
    """获取 Cookie 状态"""
    if not COOKIE_MANAGER_AVAILABLE:
        return {"available": False, "message": "Cookie Manager 不可用"}
    status, info = get_cookie_manager().get_cookie_status()
    return {"available": True, "status": status.value, **info}


def get_gemini_client() -> Any:
    """获取或初始化 GeminiClient 实例"""
    global _client
    with _client_lock:
        if _client is None:
            validate_config()
            try:
                from gemini_webapi import GeminiClient
            except ImportError:
                raise ImportError("请先安装 gemini-webapi")
            psid = os.environ.get("GEMINI_PSID")
            psidts = os.environ.get("GEMINI_PSIDTS", "")
            proxy = os.environ.get("GEMINI_PROXY")
            logger.info("正在初始化 GeminiClient...")
            _client = GeminiClient(psid, psidts, proxy=proxy)
    return _client


async def initialize_client() -> Any:
    """完成客户端初始化"""
    global _client, _initialized
    with _client_lock:
        if _client is None:
            _client = get_gemini_client()
        if not _initialized:
            logger.info("正在调用 client.init()...")
            await _client.init(
                timeout=30,
                auto_close=False,
                auto_refresh=os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
            )
            _initialized = True
            logger.info("✅ GeminiClient 初始化完成！")
    return _client


def _clean_expired_sessions() -> None:
    """清理过期会话（内部函数，需在锁内调用）"""
    now = time.time()
    max_age = 1800
    expired = [sid for sid, data in _sessions.items() if now - data["created_at"] > max_age]
    for sid in expired:
        del _sessions[sid]
    if expired:
        logger.info(f"清理了 {len(expired)} 个过期会话")


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


def store_session(session_id: str, session: Any, model: str = "fast") -> None:
    """存储会话"""
    with _sessions_lock:
        _sessions[session_id] = {
            "session": session,
            "model": model,
            "created_at": time.time()
        }
