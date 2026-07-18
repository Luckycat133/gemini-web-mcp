"""
客户端管理器 - 负责 Gemini 客户端的初始化、生命周期管理和配置验证
"""

import os
import socket
import tempfile
import threading
import logging
from pathlib import Path
from typing import Optional, Any, Dict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    from .cookie_manager import get_cookie_manager
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False
    logger.warning("cookie_manager 模块不可用")

from .constants import DEFAULT_CHAT_RETENTION_SECONDS  # noqa: E402  (follows optional try/except import)


def validate_config() -> None:
    """验证必需的环境变量"""
    required = ["GEMINI_PSID"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise ValueError(f"缺少必需的环境变量: {', '.join(missing)}")


def get_configured_proxy() -> Optional[str]:
    """Return a usable proxy, ignoring stale local proxy endpoints."""
    proxy = os.environ.get("GEMINI_PROXY", "").strip()
    if not proxy:
        return None

    parsed = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    host = parsed.hostname
    port = parsed.port
    if host in {"127.0.0.1", "localhost", "::1"} and port:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                pass
        except OSError:
            logger.warning("GEMINI_PROXY=%s is not reachable; continuing without proxy", proxy)
            return None
    return proxy


def get_default_chat_retention_seconds() -> int:
    """远端 Gemini 对话默认保留时间。"""
    raw_value = os.environ.get(
        "GEMINI_CHAT_RETENTION_SECONDS",
        str(DEFAULT_CHAT_RETENTION_SECONDS),
    )
    try:
        return max(0, int(raw_value))
    except ValueError:
        logger.warning(
            f"无效的 GEMINI_CHAT_RETENTION_SECONDS={raw_value!r}，"
            f"使用 {DEFAULT_CHAT_RETENTION_SECONDS} 秒"
        )
        return DEFAULT_CHAT_RETENTION_SECONDS


def get_extra_cookies() -> Dict[str, str]:
    """获取当前 Cookie Manager 中的完整认证 Cookie。"""
    if not COOKIE_MANAGER_AVAILABLE:
        return {}
    cookie_data = get_cookie_manager().get_cookie()
    if not cookie_data:
        return {}
    return cookie_data.extra_cookies


def prepare_browser_cookie_cache(force: bool = False) -> None:
    """Avoid stale gemini_webapi cache when cookies were refreshed from a browser."""
    if not COOKIE_MANAGER_AVAILABLE:
        return
    if not force:
        cookie_data = get_cookie_manager().get_cookie()
        if not cookie_data or not str(getattr(cookie_data, "source", "")).startswith("browser_"):
            return
    cache_dir = Path(tempfile.gettempdir()) / "gemini_web_mcp_webapi_cookie_cache"
    configured_cache = os.environ.get("GEMINI_COOKIE_PATH")
    if configured_cache and Path(configured_cache) != cache_dir:
        return

    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        cache_dir.chmod(0o700)
    except OSError:
        pass

    for cache_file in cache_dir.glob(".cached_cookies_*.json"):
        try:
            cache_file.unlink()
        except OSError as e:
            logger.debug("无法删除 Gemini WebAPI cookie cache %s: %s", cache_file.name, e)

    os.environ["GEMINI_COOKIE_PATH"] = str(cache_dir)


class ClientManager:
    """Gemini 客户端管理器 - 线程安全的客户端生命周期管理"""

    def __init__(self):
        self._client: Optional[Any] = None
        self._initialized: bool = False
        self._lock = threading.Lock()
        self._init_lock = threading.Lock()

    def get_client(self) -> Any:
        """获取或初始化 GeminiClient 实例"""
        with self._lock:
            if self._client is None:
                self._create_client()
        return self._client

    async def initialize(self) -> Any:
        """完成客户端初始化（线程安全：防止并发重复 init）"""
        client = self.get_client()
        with self._init_lock:
            if self._initialized:
                return client
            logger.info("正在调用 client.init()...")
            await client.init(
                timeout=30,
                auto_close=False,
                auto_refresh=os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
            )
            with self._lock:
                self._initialized = True
        return client

    def _create_client(self) -> None:
        """创建新的客户端实例"""
        validate_config()
        try:
            from .thinking_client import ThinkingLevelGeminiClient
        except ImportError:
            raise ImportError("请先安装 gemini-webapi")

        psid = os.environ.get("GEMINI_PSID")
        psidts = os.environ.get("GEMINI_PSIDTS", "")
        proxy = get_configured_proxy()

        logger.info("正在初始化 GeminiClient...")
        self._client = ThinkingLevelGeminiClient(psid, psidts, proxy=proxy)

        extra_cookies = get_extra_cookies()
        if extra_cookies:
            prepare_browser_cookie_cache()
            self._client.cookies = extra_cookies
            logger.info(f"已加载 {len(extra_cookies)} 个完整认证 Cookie")

    # initialize 方法定义在上方（带 _init_lock 的线程安全版本）

    def reset(self) -> None:
        """重置客户端"""
        with self._lock:
            self._client = None
            self._initialized = False
        logger.info("✅ 客户端已重置")
