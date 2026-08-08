"""
客户端管理器 - 负责 Gemini 客户端的初始化、生命周期管理和配置验证
"""

import asyncio
import inspect
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


class ClientInitializationResetError(RuntimeError):
    """Raised when a reset supersedes an in-flight client initialization."""


class ClientManager:
    """Gemini 客户端管理器 - 线程和异步任务安全的客户端生命周期管理"""

    def __init__(self):
        self._client: Optional[Any] = None
        self._initialized: bool = False
        self._lock = threading.Lock()
        self._generation = 0
        self._init_task: Optional[asyncio.Task[Any]] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._retirement_tasks: set[asyncio.Task[None]] = set()

    def get_client(self) -> Any:
        """获取或初始化 GeminiClient 实例"""
        with self._lock:
            if self._client is None:
                self._create_client()
        return self._client

    async def initialize(self) -> Any:
        """Initialize one current client and share the attempt across callers."""
        loop = asyncio.get_running_loop()

        while True:
            client = self.get_client()
            with self._lock:
                if self._client is not client:
                    continue
                if self._initialized:
                    return client

                generation = self._generation
                init_task = self._init_task
                if init_task is not None and not init_task.done():
                    if init_task.get_loop() is not loop:
                        raise RuntimeError("client initialization is already running on another event loop")
                else:
                    init_task = loop.create_task(
                        self._initialize_generation(client, generation),
                        name=f"gemini-client-init-{generation}",
                    )
                    self._init_task = init_task

            # A cancelled caller must not cancel the shared initialization used by
            # other requests. Reset explicitly cancels the underlying task.
            return await asyncio.shield(init_task)

    async def _initialize_generation(self, client: Any, generation: int) -> Any:
        """Initialize one client generation without publishing stale state."""
        current_task = asyncio.current_task()
        try:
            logger.info("正在调用 client.init()...")
            await client.init(
                timeout=30,
                auto_close=False,
                auto_refresh=os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true",
            )
            with self._lock:
                if self._generation != generation or self._client is not client:
                    raise ClientInitializationResetError("client reset during initialization")
                self._initialized = True
                self._client_loop = asyncio.get_running_loop()
            logger.info("✅ GeminiClient 初始化完成！")
            return client
        except asyncio.CancelledError:
            with self._lock:
                reset_superseded = self._generation != generation or self._client is not client
            if reset_superseded:
                raise ClientInitializationResetError("client reset during initialization") from None
            raise
        finally:
            with self._lock:
                if self._init_task is current_task:
                    self._init_task = None

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

    def reset(self) -> None:
        """Invalidate the current client and retire it without blocking the caller."""
        client, init_task, owner_loop = self._detach_current_client()
        self._schedule_retirement(client, init_task, owner_loop)
        logger.info("✅ 客户端已重置")

    async def reset_async(self) -> None:
        """Invalidate the current client and wait until it is retired."""
        client, init_task, owner_loop = self._detach_current_client()
        if client is not None:
            current_loop = asyncio.get_running_loop()
            if owner_loop is not None and owner_loop is not current_loop and owner_loop.is_running():
                retirement = asyncio.run_coroutine_threadsafe(
                    self._retire_client(client, init_task),
                    owner_loop,
                )
                await asyncio.wrap_future(retirement)
            else:
                await self._retire_client(client, init_task)
        logger.info("✅ 客户端已重置")

    def _detach_current_client(
        self,
    ) -> tuple[Optional[Any], Optional[asyncio.Task[Any]], Optional[asyncio.AbstractEventLoop]]:
        """Atomically detach one generation so stale initialization cannot publish."""
        with self._lock:
            client = self._client
            init_task = self._init_task
            owner_loop = init_task.get_loop() if init_task is not None else self._client_loop
            self._generation += 1
            self._client = None
            self._initialized = False
            self._init_task = None
            self._client_loop = None
        return client, init_task, owner_loop

    def _schedule_retirement(
        self,
        client: Optional[Any],
        init_task: Optional[asyncio.Task[Any]],
        owner_loop: Optional[asyncio.AbstractEventLoop],
    ) -> None:
        if client is None:
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        target_loop = owner_loop if owner_loop is not None and owner_loop.is_running() else current_loop
        if target_loop is None:
            asyncio.run(self._retire_client(client, init_task))
            return

        if target_loop is current_loop:
            self._start_retirement_task(client, init_task)
        else:
            target_loop.call_soon_threadsafe(self._start_retirement_task, client, init_task)

    def _start_retirement_task(self, client: Any, init_task: Optional[asyncio.Task[Any]]) -> None:
        task = asyncio.create_task(self._retire_client(client, init_task), name="gemini-client-retire")
        with self._lock:
            self._retirement_tasks.add(task)
        task.add_done_callback(self._retirement_done)

    def _retirement_done(self, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._retirement_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("关闭旧 GeminiClient 失败: %s", error)

    async def _retire_client(self, client: Any, init_task: Optional[asyncio.Task[Any]]) -> None:
        if init_task is not None and init_task is not asyncio.current_task():
            if not init_task.done():
                init_task.cancel()
                try:
                    await init_task
                except (asyncio.CancelledError, ClientInitializationResetError):
                    pass
                except Exception as error:
                    logger.debug("被重置的 GeminiClient 初始化已失败: %s", error)
            else:
                try:
                    init_task.result()
                except (asyncio.CancelledError, ClientInitializationResetError):
                    pass
                except Exception as error:
                    logger.debug("被重置的 GeminiClient 初始化已失败: %s", error)

        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            logger.warning("关闭旧 GeminiClient 失败: %s", error)
