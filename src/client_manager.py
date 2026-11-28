"""
客户端管理器 - 负责 Gemini 客户端的初始化、生命周期管理和配置验证
"""

import asyncio
import inspect
import json
import math
import os
import socket
import tempfile
import threading
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Optional, Any, Dict, TypedDict
from urllib.parse import urlparse

from .sdk_auth_logging import suppress_sdk_auth_logs

logger = logging.getLogger(__name__)

try:
    from .cookie_manager import gemini_cookie_jar, get_cookie_manager
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False
    logger.warning("cookie_manager 模块不可用")

from .constants import DEFAULT_CHAT_RETENTION_SECONDS  # noqa: E402  (follows optional try/except import)
from .domain.results import classify_exception  # noqa: E402


def validate_config() -> None:
    """验证必需的环境变量"""
    required = ["GEMINI_PSID"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise ClientAuthenticationRequired(f"缺少必需的环境变量: {', '.join(missing)}")


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
            logger.warning("Configured local Gemini proxy is not reachable; continuing without proxy")
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
            "无效的 GEMINI_CHAT_RETENTION_SECONDS，"
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


_VERIFIED_BROWSER_SESSION_TTL_SECONDS = 900.0
_VERIFIED_BROWSER_SESSION_FILENAME = ".cli_browser_session.json"


def _verified_browser_session_cache_path() -> Optional[Path]:
    """Return shared verified-browser-session cache path when persistent cache dir is configured."""
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("GEMINI_WEB_CLI_NO_CONFIG") == "1":
        return None
    configured = os.environ.get("GEMINI_COOKIE_PATH", "").strip()
    if not configured:
        return None
    cache_dir = Path(configured).expanduser()
    if cache_dir.name.startswith("gemini-web-worker-cookies-"):
        return None
    return cache_dir / _VERIFIED_BROWSER_SESSION_FILENAME


def _load_verified_browser_session_cache() -> Optional[tuple[Dict[str, str], str]]:
    cache_path = _verified_browser_session_cache_path()
    if cache_path is None or not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    verified_at = payload.get("verified_at")
    cookies = payload.get("cookies")
    source = payload.get("source")
    if (
        isinstance(verified_at, bool)
        or not isinstance(verified_at, (int, float))
        or time.time() - float(verified_at) >= _VERIFIED_BROWSER_SESSION_TTL_SECONDS
        or not isinstance(cookies, dict)
        or not isinstance(source, str)
        or not source.strip()
    ):
        return None
    normalized_cookies = {
        k: v for k, v in cookies.items()
        if isinstance(k, str) and isinstance(v, str) and v
    }
    if not normalized_cookies.get("__Secure-1PSID"):
        return None
    expected_profile = os.environ.get("GEMINI_BROWSER_COOKIE_PROFILE", "").strip()
    if expected_profile:
        cached_profile = payload.get("profile")
        profile_matches = isinstance(cached_profile, str) and cached_profile.strip().casefold() == expected_profile.casefold()
        source_matches = source.casefold().endswith(f":{expected_profile.casefold()}")
        if not profile_matches and not source_matches:
            return None
    return normalized_cookies, source


def _save_verified_browser_session_cache(cookies: Dict[str, str], source: str) -> None:
    cache_path = _verified_browser_session_cache_path()
    if cache_path is None or not source.startswith("browser_") or not cookies.get("__Secure-1PSID"):
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "verified_at": time.time(),
                "profile": os.environ.get("GEMINI_BROWSER_COOKIE_PROFILE", "").strip(),
                "source": source,
                "cookies": cookies,
            },
            ensure_ascii=False,
        )
        fd = os.open(cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except OSError:
        pass


def invalidate_verified_browser_session_cache() -> None:
    """Remove any cached verified browser session when cookies become unauthenticated."""
    cache_path = _verified_browser_session_cache_path()
    if cache_path is not None:
        with suppress(OSError):
            cache_path.unlink()


def prepare_browser_cookie_cache(force: bool = False) -> None:
    """Avoid stale gemini_webapi cache when cookies were refreshed from a browser."""
    if force:
        invalidate_verified_browser_session_cache()
    if not COOKIE_MANAGER_AVAILABLE:
        return
    if not force:
        cookie_data = get_cookie_manager().get_cookie()
        if not cookie_data or not str(getattr(cookie_data, "source", "")).startswith("browser_"):
            return
    cache_dir = Path(tempfile.gettempdir()) / "gemini_web_mcp_webapi_cookie_cache"
    configured_cache = os.environ.get("GEMINI_COOKIE_PATH")
    if configured_cache and Path(configured_cache) != cache_dir:
        configured_dir = Path(configured_cache)
        # Never touch a detached worker's ephemeral cache directory.
        if configured_dir.name.startswith("gemini-web-worker-cookies-"):
            return
        if configured_dir.is_dir():
            for cache_file in configured_dir.glob(".cached_cookies_*.json"):
                try:
                    cache_file.unlink()
                except OSError as e:
                    logger.debug("无法删除 Gemini WebAPI cookie cache error_type=%s", type(e).__name__)
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
            logger.debug("无法删除 Gemini WebAPI cookie cache error_type=%s", type(e).__name__)

    os.environ["GEMINI_COOKIE_PATH"] = str(cache_dir)


@contextmanager
def isolated_worker_cookie_cache() -> Iterator[Path]:
    """Own a Web worker's SDK cache until its clients have been closed.

    Use only at the detached process entrypoint: GEMINI_COOKIE_PATH is
    process-wide. Never clear or reuse the submitting process's cache.
    """
    previous = os.environ.get("GEMINI_COOKIE_PATH")
    with tempfile.TemporaryDirectory(prefix="gemini-web-worker-cookies-") as directory:
        cache_dir = Path(directory)
        cache_dir.chmod(0o700)
        try:
            os.environ["GEMINI_COOKIE_PATH"] = str(cache_dir)
            yield cache_dir
        finally:
            if previous is None:
                os.environ.pop("GEMINI_COOKIE_PATH", None)
            else:
                os.environ["GEMINI_COOKIE_PATH"] = previous


class ClientInitializationResetError(RuntimeError):
    """Raised when a reset supersedes an in-flight client initialization."""


class ClientAuthenticationRequired(PermissionError):
    """Authentication could not be restored before request dispatch."""

    def __init__(
        self, message: str = "Gemini authentication is required.", *,
        auth_reason: str | None = None, browser_auth_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.auth_reason = auth_reason
        self.browser_auth_reason = browser_auth_reason


class ClientRateLimited(RuntimeError):
    """A temporary upstream limit is not a failed account credential."""

    code = "RATE_LIMITED"

    def __init__(
        self,
        *,
        retry_after_seconds: int | None = None,
        reset_at: str | None = None,
        reset_timestamp: float | None = None,
        quota_window: str | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        self.reset_at = reset_at
        self.reset_timestamp = reset_timestamp
        self.quota_window = quota_window
        message = "Gemini temporarily limited requests"
        if reset_at:
            message += f"; cooldown resets at {reset_at}"
        elif retry_after_seconds:
            message += f"; wait ~{retry_after_seconds}s"
        else:
            message += "; wait before retrying"
        super().__init__(message + ".")


def browser_cookie_auto_load_enabled() -> bool:
    return os.environ.get("GEMINI_BROWSER_AUTO_LOAD", "true").lower() == "true"


class _ConfiguredBrowserProfile(TypedDict, total=False):
    profile: str


def _configured_browser_profile() -> _ConfiguredBrowserProfile:
    profile = os.environ.get("GEMINI_BROWSER_COOKIE_PROFILE", "").strip()
    return {"profile": profile} if profile else {}


def _explicitly_unauthenticated(client: Any) -> bool:
    from gemini_webapi.constants import AccountStatus
    return bool(getattr(client, "account_status", None) == AccountStatus.UNAUTHENTICATED)


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
        self._browser_auth_lock = threading.Lock()
        self._browser_retry_at = 0.0
        self._rate_limit_reset_timestamp = 0.0
        self.browser_auth_reason: str | None = None

    def _remember_rate_limit(self) -> None:
        self.browser_auth_reason = "BROWSER_COOKIE_RATE_LIMITED"
        # Reuse the existing local browser-probe debounce, not a fabricated Retry-After.
        self._browser_retry_at = time.monotonic() + 30
        self._rate_limit_reset_timestamp = time.time() + 30

    def _rate_limit_error(self) -> ClientRateLimited:
        remaining = max(1, math.ceil(self._browser_retry_at - time.monotonic()))
        reset_ts = self._rate_limit_reset_timestamp or (time.time() + remaining)
        from datetime import datetime, timezone
        reset_at = datetime.fromtimestamp(reset_ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        return ClientRateLimited(
            retry_after_seconds=remaining,
            reset_at=reset_at,
            reset_timestamp=reset_ts,
            quota_window="browser_probe_cooldown",
        )

    def is_rate_limited(self) -> bool:
        """Return True while the local browser-probe rate-limit cooldown is active."""
        return self.browser_auth_reason == "BROWSER_COOKIE_RATE_LIMITED" and time.monotonic() < self._browser_retry_at

    def _check_rate_limit(self) -> None:
        if self.is_rate_limited():
            raise self._rate_limit_error()

    def ensure_browser_cookie(self) -> None:
        """Bind a verified browser session when no credential is configured."""
        self._check_rate_limit()
        if os.environ.get("GEMINI_PSID"):
            return
        if not browser_cookie_auto_load_enabled():
            raise ClientAuthenticationRequired(auth_reason="BROWSER_COOKIE_AUTO_LOAD_DISABLED")
        if not COOKIE_MANAGER_AVAILABLE:
            raise ClientAuthenticationRequired(auth_reason="BROWSER_COOKIE_DEPENDENCY_MISSING")

        with self._browser_auth_lock:
            self._check_rate_limit()
            if os.environ.get("GEMINI_PSID"):
                return
            if self.browser_auth_reason and time.monotonic() < self._browser_retry_at:
                raise ClientAuthenticationRequired(auth_reason=self.browser_auth_reason)
            manager = get_cookie_manager()
            cached = _load_verified_browser_session_cache()
            if cached is not None:
                cached_cookies, cached_source = cached
                cached_psid = cached_cookies["__Secure-1PSID"]
                cached_psidts = cached_cookies.get("__Secure-1PSIDTS", "")
                if manager.update_cookie(
                    cached_psid, cached_psidts, source=cached_source, extra_cookies=cached_cookies,
                    notify=False, guard=lambda: not os.environ.get("GEMINI_PSID"),
                ):
                    os.environ["GEMINI_PSID"] = cached_psid
                    if cached_psidts:
                        os.environ["GEMINI_PSIDTS"] = cached_psidts
                    else:
                        os.environ.pop("GEMINI_PSIDTS", None)
                    self.browser_auth_reason = None
                    logger.info("Browser cookie loaded from verified session cache source=%s", cached_source)
                    return
            try:
                discovery = manager.discover_browser_cookies(**_configured_browser_profile())
            except Exception as error:
                if classify_exception(error).error_code == "RATE_LIMITED":
                    self._remember_rate_limit()
                    raise self._rate_limit_error() from None
                raise
            if discovery.reason == "BROWSER_COOKIE_RATE_LIMITED":
                self._remember_rate_limit()
                raise self._rate_limit_error()
            psid = discovery.cookies.get("__Secure-1PSID")
            if not psid:
                self.browser_auth_reason = discovery.reason or "BROWSER_COOKIE_NOT_FOUND"
                self._browser_retry_at = time.monotonic() + 30
                raise ClientAuthenticationRequired(auth_reason=self.browser_auth_reason)
            psidts = discovery.cookies.get("__Secure-1PSIDTS", "")
            resolved_source = discovery.source or "browser_chrome"
            if manager.update_cookie(
                psid, psidts, source=resolved_source, extra_cookies=discovery.cookies,
                notify=False, guard=lambda: not os.environ.get("GEMINI_PSID"),
            ):
                os.environ["GEMINI_PSID"] = psid
                if psidts:
                    os.environ["GEMINI_PSIDTS"] = psidts
                else:
                    os.environ.pop("GEMINI_PSIDTS", None)
                self.browser_auth_reason = None
                _save_verified_browser_session_cache(dict(discovery.cookies), resolved_source)
                logger.info("Browser cookie loaded from a verified Gemini session source=%s", discovery.source)
            elif not os.environ.get("GEMINI_PSID"):
                raise ClientAuthenticationRequired(auth_reason="BROWSER_COOKIE_AUTH_UNVERIFIED")

    def get_client(self) -> Any:
        """获取或初始化 GeminiClient 实例"""
        self._check_rate_limit()
        if self._client is None and not os.environ.get("GEMINI_PSID"):
            self.ensure_browser_cookie()
        with self._lock:
            if self._client is None:
                self._create_client()
        return self._client

    async def initialize(self) -> Any:
        """Initialize one current client and share the attempt across callers."""
        loop = asyncio.get_running_loop()
        retried_browser_reload = False

        while True:
            client = self.get_client()
            with self._lock:
                if self._client is not client:
                    continue
                if self._initialized:
                    if _explicitly_unauthenticated(client):
                        cookie_source = ""
                        if COOKIE_MANAGER_AVAILABLE:
                            try:
                                cookie_info = get_cookie_manager().get_cookie()
                                cookie_source = str(getattr(cookie_info, "source", "") or "")
                            except Exception:
                                cookie_source = ""
                        if (
                            not retried_browser_reload
                            and COOKIE_MANAGER_AVAILABLE
                            and browser_cookie_auto_load_enabled()
                            and cookie_source.startswith(("browser_", "browser_worker"))
                        ):
                            retried_browser_reload = True
                        else:
                            raise ClientAuthenticationRequired(auth_reason="GEMINI_SESSION_REJECTED")
                    else:
                        return client
                else:
                    retried = False
                if self._initialized and retried_browser_reload:
                    retried = True
                else:
                    retried = False
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
            if retried:
                await self.reset_async()
                os.environ.pop("GEMINI_PSID", None)
                os.environ.pop("GEMINI_PSIDTS", None)
                invalidate_verified_browser_session_cache()
                prepare_browser_cookie_cache(force=True)
                self.browser_auth_reason = None
                self._browser_retry_at = 0.0
                continue

            # A cancelled caller must not cancel the shared initialization used by
            # other requests. Reset explicitly cancels the underlying task.
            assert init_task is not None
            return await asyncio.shield(init_task)

    async def _initialize_generation(self, client: Any, generation: int) -> Any:
        """Initialize one client generation without publishing stale state."""
        current_task = asyncio.current_task()
        try:
            suppress_sdk_auth_logs()
            logger.info("正在调用 client.init()...")
            from gemini_webapi.exceptions import AuthError

            raw_auto_refresh = os.environ.get("GEMINI_AUTO_REFRESH")
            if raw_auto_refresh is None:
                cookie_source = ""
                if COOKIE_MANAGER_AVAILABLE:
                    try:
                        cookie_info = get_cookie_manager().get_cookie()
                        cookie_source = str(getattr(cookie_info, "source", "") or "")
                    except Exception:
                        cookie_source = ""
                using_browser_session = cookie_source.startswith(("browser_", "browser_worker"))
                auto_refresh_enabled = not using_browser_session
            else:
                auto_refresh_enabled = raw_auto_refresh.lower() == "true"

            init_options = dict(timeout=30, verbose=False, auto_close=False,
                                auto_refresh=auto_refresh_enabled)
            auth_reason = None
            try:
                await client.init(**init_options)
                if _explicitly_unauthenticated(client):
                    auth_reason = "GEMINI_SESSION_REJECTED"
            except AuthError as error:
                if classify_exception(error).error_code == "RATE_LIMITED":
                    raise
                auth_reason = "GEMINI_SESSION_UNVERIFIED"
            if auth_reason:
                await self._restore_browser_authentication(client, generation, init_options, auth_reason=auth_reason)
            with self._lock:
                if self._generation != generation or self._client is not client:
                    raise ClientInitializationResetError("client reset during initialization")
                self._initialized = True
                self._client_loop = asyncio.get_running_loop()
            if self.browser_auth_reason == "BROWSER_COOKIE_RATE_LIMITED":
                self.browser_auth_reason = None
                self._browser_retry_at = 0
            logger.info("✅ GeminiClient 初始化完成！")
            return client
        except ClientAuthenticationRequired:
            with self._lock:
                current = self._generation == generation and self._client is client
            if current:
                with suppress(Exception):
                    await client.close()
            raise
        except Exception as error:
            if classify_exception(error).error_code == "RATE_LIMITED":
                self._remember_rate_limit()
                with suppress(Exception):
                    await client.close()
                raise self._rate_limit_error() from None
            raise
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

    async def _restore_browser_authentication(
        self, client: Any, generation: int, options: dict[str, Any], *, auth_reason: str,
    ) -> None:
        """Reinitialize the same undispatched client once using the bound account."""
        from gemini_webapi.exceptions import AuthError

        if not COOKIE_MANAGER_AVAILABLE or not browser_cookie_auto_load_enabled():
            reason = "BROWSER_COOKIE_AUTO_LOAD_DISABLED" if COOKIE_MANAGER_AVAILABLE else "BROWSER_COOKIE_DEPENDENCY_MISSING"
            raise ClientAuthenticationRequired(auth_reason=auth_reason, browser_auth_reason=reason)
        manager = get_cookie_manager()
        old_psid = os.environ.get("GEMINI_PSID", "")
        if not old_psid:
            raise ClientAuthenticationRequired(auth_reason=auth_reason)
        try:
            discovery = await asyncio.wait_for(
                asyncio.to_thread(
                    manager.discover_browser_cookies, expected_psid=old_psid, **_configured_browser_profile(),
                ),
                timeout=60,
            )
        except Exception as error:
            if classify_exception(error).error_code == "RATE_LIMITED":
                raise ClientRateLimited() from None
            self.browser_auth_reason = "BROWSER_COOKIE_AUTH_UNVERIFIED"
            raise ClientAuthenticationRequired(
                auth_reason=auth_reason, browser_auth_reason=self.browser_auth_reason,
            ) from None
        if discovery.reason == "BROWSER_COOKIE_RATE_LIMITED":
            raise ClientRateLimited()
        cookies = discovery.cookies
        psid = cookies.get("__Secure-1PSID")
        if psid != old_psid or os.environ.get("GEMINI_PSID", "") != old_psid:
            self.browser_auth_reason = discovery.reason or "BROWSER_COOKIE_ACCOUNT_MISMATCH"
            raise ClientAuthenticationRequired(
                auth_reason=auth_reason, browser_auth_reason=self.browser_auth_reason,
            )
        with self._lock:
            if (self._generation != generation or self._client is not client
                    or os.environ.get("GEMINI_PSID", "") != old_psid):
                raise ClientInitializationResetError("client reset during initialization")
        assert client is not None
        await client.close()
        with self._lock:
            if (self._generation != generation or self._client is not client
                    or os.environ.get("GEMINI_PSID", "") != old_psid):
                raise ClientInitializationResetError("client reset during initialization")
        client._cookies.clear()
        client.cookies = gemini_cookie_jar(cookies)
        prepare_browser_cookie_cache(force=True)
        try:
            await client.init(**options)
        except AuthError as error:
            if classify_exception(error).error_code == "RATE_LIMITED":
                raise
            raise ClientAuthenticationRequired(auth_reason="GEMINI_SESSION_UNVERIFIED") from None
        if _explicitly_unauthenticated(client):
            await client.close()
            raise ClientAuthenticationRequired(auth_reason="GEMINI_SESSION_REJECTED")
        def generation_current() -> bool:
            with self._lock:
                return (self._generation == generation and self._client is client
                        and os.environ.get("GEMINI_PSID", "") == old_psid)

        if not manager.update_cookie(psid, cookies.get("__Secure-1PSIDTS", ""), source=discovery.source or "browser_chrome",
                                     extra_cookies=cookies, notify=False, guard=generation_current):
            raise ClientInitializationResetError("client reset during initialization")
        self.browser_auth_reason = None

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
            self._client.cookies = gemini_cookie_jar(extra_cookies)
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
            logger.warning("关闭旧 GeminiClient 失败 error_type=%s", type(error).__name__)

    async def _retire_client(self, client: Any, init_task: Optional[asyncio.Task[Any]]) -> None:
        if init_task is not None and init_task is not asyncio.current_task():
            if not init_task.done():
                init_task.cancel()
                try:
                    await init_task
                except (asyncio.CancelledError, ClientInitializationResetError):
                    pass
                except Exception as error:
                    logger.debug("被重置的 GeminiClient 初始化已失败 error_type=%s", type(error).__name__)
            else:
                try:
                    init_task.result()
                except (asyncio.CancelledError, ClientInitializationResetError):
                    pass
                except Exception as error:
                    logger.debug("被重置的 GeminiClient 初始化已失败 error_type=%s", type(error).__name__)

        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            logger.warning("关闭旧 GeminiClient 失败 error_type=%s", type(error).__name__)
