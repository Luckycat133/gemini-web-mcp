"""client_manager 的单元测试。

调研发现以下纯函数和 ClientManager 生命周期方法此前仅通过 client_wrapper
间接覆盖，缺少直接断言：
- validate_config（缺 GEMINI_PSID 抛认证错误）
- get_configured_proxy（无 env / 本地不可达 / 透传 三条路径）
- get_default_chat_retention_seconds（默认 / 有效 / 无效回退 / 0 / 负数边界）
- get_extra_cookies（无 cookie_manager / 有 cookie 两条路径）
- prepare_browser_cookie_cache（force=False 早退 / force=True 跳过检查 /
  GEMINI_COOKIE_PATH 不一致早退 / 正常路径清空 cache 文件 + 设置 env）
- ClientManager.get_client / reset / initialize 并发短路

测试隔离：monkeypatch 环境变量；monkeypatch tempfile.gettempdir 指向 tmp_path，
避免污染真实临时目录。
"""

import asyncio
import logging
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src import client_manager
from src.client_manager import (
    ClientInitializationResetError,
    ClientAuthenticationRequired,
    ClientManager,
    get_configured_proxy,
    get_default_chat_retention_seconds,
    prepare_browser_cookie_cache,
    validate_config,
)


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def test_validate_config_raises_when_psid_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    with pytest.raises(ClientAuthenticationRequired, match="GEMINI_PSID"):
        validate_config()


def test_validate_config_passes_when_psid_set(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "psid-x")
    validate_config()  # 不抛


# ---------------------------------------------------------------------------
# get_configured_proxy
# ---------------------------------------------------------------------------


def test_get_configured_proxy_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("GEMINI_PROXY", raising=False)
    assert get_configured_proxy() is None


def test_get_configured_proxy_returns_none_when_empty(monkeypatch):
    monkeypatch.setenv("GEMINI_PROXY", "   ")
    assert get_configured_proxy() is None


def test_get_configured_proxy_passes_through_remote(monkeypatch):
    monkeypatch.setenv("GEMINI_PROXY", "http://proxy.example.com:8080")
    assert get_configured_proxy() == "http://proxy.example.com:8080"


def test_get_configured_proxy_returns_none_when_local_unreachable(monkeypatch):
    """本地 proxy 端口不可达时返回 None 并记录 warning。"""
    monkeypatch.setenv("GEMINI_PROXY", "http://127.0.0.1:1")  # 端口 1 通常不可达
    # 用极短 timeout 避免测试慢
    assert get_configured_proxy() is None


def test_unreachable_proxy_warning_does_not_log_proxy_credentials(monkeypatch, caplog):
    secret = "private-proxy-password"
    monkeypatch.setenv("GEMINI_PROXY", f"http://user:{secret}@127.0.0.1:7890")
    monkeypatch.setattr("src.client_manager.socket.create_connection", MagicMock(side_effect=OSError("offline")))

    assert get_configured_proxy() is None
    assert "not reachable" in caplog.text
    assert secret not in caplog.text


def test_get_configured_proxy_normalizes_schemeless(monkeypatch):
    """无 scheme 的 proxy 被规范化为 http://。"""
    monkeypatch.setenv("GEMINI_PROXY", "proxy.example.com:8080")
    assert get_configured_proxy() == "proxy.example.com:8080"


# ---------------------------------------------------------------------------
# get_default_chat_retention_seconds
# ---------------------------------------------------------------------------


def test_retention_seconds_default_when_unset(monkeypatch):
    from src.constants import DEFAULT_CHAT_RETENTION_SECONDS
    monkeypatch.delenv("GEMINI_CHAT_RETENTION_SECONDS", raising=False)
    assert get_default_chat_retention_seconds() == DEFAULT_CHAT_RETENTION_SECONDS


def test_retention_seconds_custom_value(monkeypatch):
    monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "600")
    assert get_default_chat_retention_seconds() == 600


def test_retention_seconds_invalid_falls_back(monkeypatch):
    from src.constants import DEFAULT_CHAT_RETENTION_SECONDS
    monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "not-a-number")
    assert get_default_chat_retention_seconds() == DEFAULT_CHAT_RETENTION_SECONDS


def test_retention_seconds_zero_allowed(monkeypatch):
    """0 表示尽快删除，是合法值。"""
    monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "0")
    assert get_default_chat_retention_seconds() == 0


def test_retention_seconds_negative_clamped_to_zero(monkeypatch):
    """负数被 max(0, ...) 截断为 0。"""
    monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "-100")
    assert get_default_chat_retention_seconds() == 0


# ---------------------------------------------------------------------------
# prepare_browser_cookie_cache
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_tempdir(monkeypatch, tmp_path):
    """把 tempfile.gettempdir 重定向到 tmp_path，避免污染真实临时目录。"""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.delenv("GEMINI_COOKIE_PATH", raising=False)
    return tmp_path


def _make_cookie_data(source: str = "manual"):
    """构造一个最小可用的 cookie_data 替身。"""
    from src.cookie_manager import CookieData, CookieStatus
    return CookieData(psid="x", source=source, status=CookieStatus.VALID)


def test_prepare_cache_noop_when_force_false_and_no_cookie(isolated_tempdir, monkeypatch):
    """force=False 且无 cookie 时早退，不创建 cache 目录。"""
    if client_manager.COOKIE_MANAGER_AVAILABLE:
        monkeypatch.setattr(
            client_manager, "get_cookie_manager",
            lambda: MagicMock(get_cookie=lambda: None),
        )
    prepare_browser_cookie_cache(force=False)
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    assert not cache_dir.exists()


def test_prepare_cache_noop_when_source_not_browser(isolated_tempdir, monkeypatch):
    """force=False 且 source 非 browser_ 前缀时早退。"""
    if not client_manager.COOKIE_MANAGER_AVAILABLE:
        pytest.skip("cookie_manager not available")
    cookie_data = _make_cookie_data(source="manual")
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: cookie_data),
    )
    prepare_browser_cookie_cache(force=False)
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    assert not cache_dir.exists()


def test_prepare_cache_creates_when_source_is_browser(isolated_tempdir, monkeypatch):
    """force=False 且 source 以 browser_ 开头时创建 cache 目录并设置 env。"""
    if not client_manager.COOKIE_MANAGER_AVAILABLE:
        pytest.skip("cookie_manager not available")
    cookie_data = _make_cookie_data(source="browser_chrome")
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: cookie_data),
    )
    prepare_browser_cookie_cache(force=False)
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    assert cache_dir.exists()
    assert os.environ["GEMINI_COOKIE_PATH"] == str(cache_dir)


def test_prepare_cache_force_bypasses_source_check(isolated_tempdir, monkeypatch):
    """force=True 时跳过 source 检查，直接创建 cache 目录。"""
    if not client_manager.COOKIE_MANAGER_AVAILABLE:
        pytest.skip("cookie_manager not available")
    # 即使 get_cookie 返回 None，force=True 也应进入清理
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: None),
    )
    prepare_browser_cookie_cache(force=True)
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    assert cache_dir.exists()
    assert os.environ["GEMINI_COOKIE_PATH"] == str(cache_dir)


def test_prepare_cache_noop_when_cookie_path_mismatch(isolated_tempdir, monkeypatch):
    """GEMINI_COOKIE_PATH 已配置但指向其他路径时早退，不覆盖用户配置。"""
    if not client_manager.COOKIE_MANAGER_AVAILABLE:
        pytest.skip("cookie_manager not available")
    cookie_data = _make_cookie_data(source="browser_chrome")
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: cookie_data),
    )
    # 用户已配置一个不同的 cache 路径
    custom_path = isolated_tempdir / "custom_cache"
    custom_path.mkdir()
    monkeypatch.setenv("GEMINI_COOKIE_PATH", str(custom_path))
    prepare_browser_cookie_cache(force=False)
    # 默认 cache 目录不应被创建（早退）
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    assert not cache_dir.exists()


def test_prepare_cache_clears_stale_cache_files(isolated_tempdir, monkeypatch):
    """正常路径清空已存在的 .cached_cookies_*.json 文件。"""
    if not client_manager.COOKIE_MANAGER_AVAILABLE:
        pytest.skip("cookie_manager not available")
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    cache_dir.mkdir()
    stale_file = cache_dir / ".cached_cookies_old.json"
    stale_file.write_text("{}")

    cookie_data = _make_cookie_data(source="browser_chrome")
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: cookie_data),
    )
    prepare_browser_cookie_cache(force=False)
    assert not stale_file.exists()


# ---------------------------------------------------------------------------
# ClientManager 生命周期
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_psid_env(monkeypatch):
    """提供最小可用的 GEMINI_PSID 环境，让 _create_client 能过 validate_config。

    实际 ThinkingLevelGeminiClient 实例化通过 monkeypatch 替换避免真实依赖。
    """
    monkeypatch.setenv("GEMINI_PSID", "test-psid")
    return monkeypatch


def test_client_manager_get_client_creates_once(clean_psid_env, monkeypatch):
    """get_client 第一次调用创建客户端，第二次返回同一实例。"""
    fake_client = MagicMock()
    init_call_count = {"n": 0}

    def fake_factory(*args, **kwargs):
        init_call_count["n"] += 1
        return fake_client

    monkeypatch.setattr(
        "src.thinking_client.ThinkingLevelGeminiClient", fake_factory, raising=False
    )
    # 避免 _create_client 调用 prepare_browser_cookie_cache 走真实路径
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", False)

    mgr = ClientManager()
    c1 = mgr.get_client()
    c2 = mgr.get_client()
    assert c1 is c2
    assert init_call_count["n"] == 1


def test_client_manager_reset_clears_state(clean_psid_env, monkeypatch):
    """reset 后 get_client 会创建新实例。"""
    instances = []

    def fake_factory(*args, **kwargs):
        instance = MagicMock()
        instance.close = AsyncMock()
        instances.append(instance)
        return instance

    monkeypatch.setattr(
        "src.thinking_client.ThinkingLevelGeminiClient", fake_factory, raising=False
    )
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", False)

    mgr = ClientManager()
    c1 = mgr.get_client()
    assert mgr._initialized is False  # 仅 get_client 不会标记 initialized
    mgr.reset()
    assert mgr._client is None
    instances[0].close.assert_awaited_once_with()
    c2 = mgr.get_client()
    assert c2 is not c1
    assert len(instances) == 2


def test_client_manager_initialize_short_circuits_when_initialized(monkeypatch):
    """initialize 在 _initialized=True 时直接返回 client，不调用 client.init。"""
    mgr = ClientManager()
    fake_client = MagicMock()
    init_call_count = {"n": 0}

    async def fake_init(**kwargs):
        init_call_count["n"] += 1

    fake_client.init = fake_init
    mgr._client = fake_client
    mgr._initialized = True

    result = asyncio.run(mgr.initialize())
    assert result is fake_client
    assert init_call_count["n"] == 0  # 已初始化，不重复调用


def test_client_manager_initialize_calls_init_when_not_initialized(monkeypatch):
    """initialize 在 _initialized=False 时调用 client.init 并标记为已初始化。"""
    mgr = ClientManager()
    fake_client = MagicMock()
    init_call_count = {"n": 0}

    async def fake_init(**kwargs):
        init_call_count["n"] += 1

    fake_client.init = fake_init
    mgr._client = fake_client
    mgr._initialized = False

    asyncio.run(mgr.initialize())
    assert init_call_count["n"] == 1
    assert mgr._initialized is True


def test_authentication_deadline_does_not_shorten_later_generation(monkeypatch):
    """SDK init's timeout persists as the generation request budget."""
    from gemini_webapi import GeminiClient
    from src.thinking_client import ThinkingLevelGeminiClient

    client = ThinkingLevelGeminiClient("fixture-psid")
    sdk_default = client.timeout

    async def initialize_without_network(self, *, timeout, **kwargs):
        # This assignment is the SDK's real init contract; no HTTP is needed.
        self.timeout = timeout

    monkeypatch.setattr(GeminiClient, "init", initialize_without_network)
    manager = ClientManager()
    manager._client = client
    assert asyncio.run(manager.initialize()) is client
    assert client.timeout == sdk_default
    assert client.timeout > client_manager.INITIALIZATION_TIMEOUT_SECONDS


def test_stalled_authentication_is_still_bounded_and_not_published(monkeypatch):
    monkeypatch.setattr(client_manager, "INITIALIZATION_TIMEOUT_SECONDS", 0.005)
    monkeypatch.setattr(client_manager, "INITIALIZATION_RETRY_DELAY_SECONDS", 0)
    manager = ClientManager()
    attempts = []

    async def stalled_init(**kwargs):
        attempts.append(kwargs["timeout"])
        await asyncio.Event().wait()

    manager._client = MagicMock(init=stalled_init)
    with pytest.raises(TimeoutError):
        asyncio.run(manager.initialize())
    assert len(attempts) == client_manager.INITIALIZATION_ATTEMPTS
    assert not manager._initialized
    assert manager._init_task is None


def test_client_manager_initialize_concurrent_safe(monkeypatch):
    """真实挂起期间的并发 initialize 共享同一个初始化任务。"""
    mgr = ClientManager()
    fake_client = MagicMock()
    init_call_count = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_init(**kwargs):
        init_call_count["n"] += 1
        started.set()
        await release.wait()

    fake_client.init = fake_init
    mgr._client = fake_client
    mgr._initialized = False

    async def run_all():
        calls = [asyncio.create_task(mgr.initialize()) for _ in range(4)]
        await started.wait()
        await asyncio.sleep(0)
        assert init_call_count["n"] == 1
        release.set()
        results = await asyncio.gather(*calls)
        assert results == [fake_client] * 4

    asyncio.run(run_all())
    assert init_call_count["n"] == 1
    assert mgr._initialized is True


def test_client_manager_initialize_failure_can_retry(monkeypatch):
    """失败的共享初始化会清理任务状态，后续调用可重试。"""
    mgr = ClientManager()
    fake_client = MagicMock()
    attempts = 0

    async def fake_init(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary upstream failure")

    fake_client.init = fake_init
    mgr._client = fake_client

    async def run():
        with pytest.raises(OSError, match="temporary upstream failure"):
            await mgr.initialize()
        assert mgr._initialized is False
        assert mgr._init_task is None
        assert await mgr.initialize() is fake_client

    asyncio.run(run())
    assert attempts == 2
    assert mgr._initialized is True


@pytest.mark.parametrize("error_type", [ConnectionError, TimeoutError])
def test_initialization_transport_retry_is_shared_by_concurrent_callers(monkeypatch, error_type):
    monkeypatch.setattr(client_manager, "INITIALIZATION_RETRY_DELAY_SECONDS", 0)
    mgr = ClientManager()
    fake_client = MagicMock()
    calls = 0

    async def init(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error_type("private transport detail")

    fake_client.init = init
    mgr._client = fake_client

    async def run():
        assert await asyncio.gather(*(mgr.initialize() for _ in range(4))) == [fake_client] * 4

    asyncio.run(run())
    assert calls == 2
    assert mgr._initialized is True


def test_initialization_curl_tls_retry_is_bounded(monkeypatch):
    from curl_cffi.requests.exceptions import SSLError

    monkeypatch.setattr(client_manager, "INITIALIZATION_RETRY_DELAY_SECONDS", 0)
    mgr = ClientManager()
    client = MagicMock()
    client.init = AsyncMock(side_effect=SSLError("private connection detail"))
    mgr._client = client
    with pytest.raises(SSLError):
        asyncio.run(mgr.initialize())
    assert client.init.await_count == 3
    assert mgr._initialized is False
    assert mgr._init_task is None


@pytest.mark.parametrize("error", [PermissionError("timeout in private cookie text"), ValueError("network detail")])
def test_initialization_does_not_retry_authentication_or_argument_failures(monkeypatch, error):
    mgr = ClientManager()
    client = MagicMock()
    client.init = AsyncMock(side_effect=error)
    mgr._client = client
    with pytest.raises(type(error)):
        asyncio.run(mgr.initialize())
    assert client.init.await_count == 1


def test_reset_withdraws_initialization_retry_before_another_attempt(monkeypatch):
    monkeypatch.setattr(client_manager, "INITIALIZATION_RETRY_DELAY_SECONDS", 10)
    mgr = ClientManager()
    old = MagicMock()
    old.close = AsyncMock()
    replacement = MagicMock()
    replacement.init = AsyncMock()
    started = asyncio.Event()

    async def init(**kwargs):
        started.set()
        raise ConnectionError("private detail")

    old.init = AsyncMock(side_effect=init)
    mgr._client = old

    async def run():
        pending = asyncio.create_task(mgr.initialize())
        await started.wait()
        await mgr.reset_async()
        mgr._client = replacement
        assert await mgr.initialize() is replacement
        with pytest.raises(ClientInitializationResetError):
            await pending

    asyncio.run(run())
    old.init.assert_awaited_once()
    old.close.assert_awaited_once()
    assert mgr._client is replacement
    assert mgr._initialized is True


def test_client_manager_reset_during_initialize_cannot_publish_stale_state(monkeypatch):
    """旧初始化即使吞掉取消并晚完成，也不能覆盖 reset 后的新一代状态。"""
    mgr = ClientManager()
    old_client = MagicMock()
    old_client.close = AsyncMock()
    replacement_client = MagicMock()
    replacement_client.close = AsyncMock()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def old_init(**kwargs):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    async def replacement_init(**kwargs):
        return None

    old_client.init = old_init
    replacement_client.init = replacement_init
    mgr._client = old_client

    async def run():
        stale_initialize = asyncio.create_task(mgr.initialize())
        await started.wait()

        reset = asyncio.create_task(mgr.reset_async())
        await cancelled.wait()
        assert mgr._client is None
        assert mgr._initialized is False

        with mgr._lock:
            mgr._client = replacement_client
        assert await mgr.initialize() is replacement_client
        assert mgr._initialized is True

        release.set()
        await reset
        with pytest.raises(ClientInitializationResetError):
            await stale_initialize

        assert mgr._client is replacement_client
        assert mgr._initialized is True

    asyncio.run(run())
    old_client.close.assert_awaited_once_with()
    replacement_client.close.assert_not_awaited()


def test_client_manager_create_client_loads_extra_cookies(clean_psid_env, monkeypatch):
    """_create_client 在 extra_cookies 非空时加载 cookies 并调 prepare_browser_cookie_cache（lines 156-158）。"""
    fake_client = MagicMock()
    monkeypatch.setattr(
        "src.thinking_client.ThinkingLevelGeminiClient", lambda *a, **k: fake_client, raising=False
    )
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(client_manager, "get_extra_cookies", lambda: {"__Secure-1PSID": "x"})
    prepare_calls = []
    monkeypatch.setattr(client_manager, "prepare_browser_cookie_cache", lambda: prepare_calls.append(1))

    mgr = ClientManager()
    client = mgr.get_client()
    assert client is fake_client
    assert client.cookies == {"__Secure-1PSID": "x"}
    assert prepare_calls == [1]


# ---------------------------------------------------------------------------
# get_configured_proxy — 本地端口可达 happy path（line 46）
# ---------------------------------------------------------------------------


def test_get_configured_proxy_returns_proxy_when_local_reachable(monkeypatch):
    """本地 proxy 端口可达时返回 proxy 值（line 46 happy path）。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        monkeypatch.setenv("GEMINI_PROXY", f"http://127.0.0.1:{port}")
        assert get_configured_proxy() == f"http://127.0.0.1:{port}"
    finally:
        server.close()


# ---------------------------------------------------------------------------
# get_extra_cookies — 直接覆盖（lines 72, 75, 76）
# ---------------------------------------------------------------------------


def test_get_extra_cookies_returns_empty_when_unavailable(monkeypatch):
    """COOKIE_MANAGER_AVAILABLE=False 时返回空 dict（line 72 直接覆盖）。"""
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", False)
    assert client_manager.get_extra_cookies() == {}


def test_get_extra_cookies_returns_empty_when_no_cookie_data(monkeypatch):
    """cookie_data 为 None 时返回空 dict（line 75）。"""
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: None),
    )
    assert client_manager.get_extra_cookies() == {}


def test_get_extra_cookies_returns_extra_cookies(monkeypatch):
    """cookie_data 存在时返回 extra_cookies（line 76 直接覆盖）。"""
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setenv("GEMINI_PSID", "x")
    cookie_data = _make_cookie_data(source="manual")
    cookie_data.extra_cookies = {"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: cookie_data),
    )
    result = client_manager.get_extra_cookies()
    assert result == {"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}


# ---------------------------------------------------------------------------
# prepare_browser_cookie_cache — 异常吞咽与 unavailable 早退（lines 82, 95-96, 101-102）
# ---------------------------------------------------------------------------


def test_prepare_cache_noop_when_cookie_manager_unavailable(monkeypatch, isolated_tempdir):
    """COOKIE_MANAGER_AVAILABLE=False 时直接早退，不创建 cache 目录（line 82）。"""
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", False)
    prepare_browser_cookie_cache(force=True)
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    assert not cache_dir.exists()


def test_prepare_cache_chmod_error_swallowed(isolated_tempdir, monkeypatch):
    """cache_dir.chmod 抛 OSError 时静默吞咽，cache 目录仍创建（lines 95-96）。"""
    if not client_manager.COOKIE_MANAGER_AVAILABLE:
        pytest.skip("cookie_manager not available")
    cookie_data = _make_cookie_data(source="browser_chrome")
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: cookie_data),
    )

    def raising_chmod(self, mode):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "chmod", raising_chmod)
    prepare_browser_cookie_cache(force=False)
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    assert cache_dir.exists()  # 目录创建成功，chmod 错误被吞咽
    assert os.environ["GEMINI_COOKIE_PATH"] == str(cache_dir)


def test_prepare_cache_unlink_error_logged(isolated_tempdir, monkeypatch, caplog):
    """stale cache 文件 unlink 抛 OSError 时记录 debug 日志但不中断（lines 101-102）。"""
    if not client_manager.COOKIE_MANAGER_AVAILABLE:
        pytest.skip("cookie_manager not available")
    cache_dir = isolated_tempdir / "gemini_web_mcp_webapi_cookie_cache"
    cache_dir.mkdir()
    stale_file = cache_dir / ".cached_cookies_old.json"
    stale_file.write_text("{}")

    cookie_data = _make_cookie_data(source="browser_chrome")
    monkeypatch.setattr(
        client_manager, "get_cookie_manager",
        lambda: MagicMock(get_cookie=lambda: cookie_data),
    )

    def raising_unlink(self):
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", raising_unlink)

    with caplog.at_level(logging.DEBUG, logger="src.client_manager"):
        prepare_browser_cookie_cache(force=False)
    # unlink 失败，文件仍存在
    assert stale_file.exists()
    assert any("无法删除" in r.message for r in caplog.records)


def test_initialize_disables_sdk_verbose_logging(caplog):
    manager = ClientManager()
    fake_client = MagicMock()
    secret = "synthetic-psid-for-log-test"

    async def init(*, verbose=True, **kwargs):
        if verbose:
            logging.getLogger("synthetic_sdk").warning(".cached_cookies_%s.json", secret)

    fake_client.init = init
    manager._client = fake_client
    with caplog.at_level(logging.DEBUG):
        asyncio.run(manager.initialize())
    assert secret not in caplog.text
    assert manager._initialized


def _synthetic_auth_client(statuses):
    from gemini_webapi.constants import AccountStatus
    client = MagicMock()
    client.account_status = AccountStatus.AVAILABLE
    client._cookies = MagicMock()
    client.close = AsyncMock()
    states = iter(statuses)
    async def init(**_kwargs):
        client.account_status = next(states)
    client.init = AsyncMock(side_effect=init)
    return client


def test_explicit_unauthenticated_refreshes_same_bound_browser_account_once(monkeypatch):
    from gemini_webapi.constants import AccountStatus
    from src.cookie_manager import BrowserCookieDiscovery, CookieManager
    monkeypatch.setenv("GEMINI_PSID", "synthetic-bound")
    monkeypatch.setenv("GEMINI_PSIDTS", "old")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "true")
    monkeypatch.setattr(client_manager, "prepare_browser_cookie_cache", lambda **_kwargs: None)
    cookies = {"__Secure-1PSID": "synthetic-bound", "__Secure-1PSIDTS": "fresh"}
    cm = CookieManager(auto_refresh=False)
    discover = MagicMock(return_value=BrowserCookieDiscovery(cookies=cookies, source="browser_chrome:Default"))
    monkeypatch.setattr(cm, "discover_browser_cookies", discover)
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: cm)
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    client = _synthetic_auth_client([AccountStatus.UNAUTHENTICATED, AccountStatus.AVAILABLE])
    mgr = ClientManager()
    mgr._client = client
    async def run():
        results = await asyncio.gather(*(mgr.initialize() for _ in range(3)))
        assert all(value is client for value in results)
    asyncio.run(run())
    assert client.init.await_count == 2 and client.close.await_count == 1
    assert client._cookies.clear.call_count == 1 and client.cookies == cookies
    discover.assert_called_once_with(expected_psid="synthetic-bound")
    assert cm.get_cookie().extra_cookies == cookies and cm.get_cookie().source == "browser_chrome:Default"
    assert os.environ["GEMINI_PSIDTS"] == "old"  # No environment/config change.


def test_unauthenticated_different_browser_account_fails_closed(monkeypatch):
    from gemini_webapi.constants import AccountStatus
    from src.cookie_manager import BrowserCookieDiscovery
    monkeypatch.setenv("GEMINI_PSID", "bound")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "true")
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    cm = MagicMock()
    cm.discover_browser_cookies.return_value = BrowserCookieDiscovery(reason="BROWSER_COOKIE_ACCOUNT_MISMATCH")
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: cm)
    client = _synthetic_auth_client([AccountStatus.UNAUTHENTICATED])
    mgr = ClientManager()
    mgr._client = client
    with pytest.raises(client_manager.ClientAuthenticationRequired) as caught:
        asyncio.run(mgr.initialize())
    assert caught.value.auth_reason == "GEMINI_SESSION_REJECTED"
    assert caught.value.browser_auth_reason == "BROWSER_COOKIE_ACCOUNT_MISMATCH"
    cm.discover_browser_cookies.assert_called_once_with(expected_psid="bound")
    assert not mgr._initialized and client.init.await_count == 1
    assert client._cookies.clear.call_count == 0 and cm.update_cookie.call_count == 0


def test_browser_refresh_rejected_status_remains_auth_failure(monkeypatch):
    from gemini_webapi.constants import AccountStatus
    from src.cookie_manager import BrowserCookieDiscovery
    monkeypatch.setenv("GEMINI_PSID", "bound")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "true")
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(client_manager, "prepare_browser_cookie_cache", lambda **_kw: None)
    cm = MagicMock()
    cm.discover_browser_cookies.return_value = BrowserCookieDiscovery(cookies={"__Secure-1PSID": "bound"})
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: cm)
    client = _synthetic_auth_client([AccountStatus.UNAUTHENTICATED, AccountStatus.UNAUTHENTICATED])
    mgr = ClientManager()
    mgr._client = client
    with pytest.raises(PermissionError):
        asyncio.run(mgr.initialize())
    assert client.init.await_count == 2 and not mgr._initialized and cm.update_cookie.call_count == 0


def test_network_failure_does_not_trigger_browser_cookie_recovery(monkeypatch):
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    cm = MagicMock()
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: cm)
    client = MagicMock()
    client.init = AsyncMock(side_effect=OSError("offline"))
    mgr = ClientManager()
    mgr._client = client
    with pytest.raises(OSError):
        asyncio.run(mgr.initialize())
    cm.discover_browser_cookies.assert_not_called()
    assert not mgr._initialized


def test_previously_initialized_explicit_unauthenticated_is_not_returned(monkeypatch):
    from gemini_webapi.constants import AccountStatus
    client = _synthetic_auth_client([])
    client.account_status = AccountStatus.UNAUTHENTICATED
    mgr = ClientManager()
    mgr._client = client
    mgr._initialized = True
    with pytest.raises(PermissionError):
        asyncio.run(mgr.initialize())
    assert client.close.await_count == 0  # Do not interrupt other in-flight operations.


def test_sdk_auth_error_refreshes_before_any_generation(monkeypatch):
    from gemini_webapi.constants import AccountStatus
    from gemini_webapi.exceptions import AuthError
    from src.cookie_manager import BrowserCookieDiscovery

    monkeypatch.setenv("GEMINI_PSID", "bound")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "true")
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(client_manager, "prepare_browser_cookie_cache", lambda **_kw: None)
    cm = MagicMock()
    cm.discover_browser_cookies.return_value = BrowserCookieDiscovery(cookies={"__Secure-1PSID": "bound"})
    cm.update_cookie.return_value = True
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: cm)
    client = _synthetic_auth_client([AccountStatus.AVAILABLE])
    mgr = ClientManager()
    mgr._client = client
    # Use a function, rather than returning a coroutine from an AsyncMock side effect.
    async def init(**_kwargs):
        if client.init.await_count == 1:
            raise AuthError("synthetic")
        client.account_status = AccountStatus.AVAILABLE
    client.init.side_effect = init
    assert asyncio.run(mgr.initialize()) is client
    assert client.init.await_count == 2
    client.generate_content.assert_not_called()


def test_auth_refresh_rejects_account_changed_during_browser_read(monkeypatch):
    from gemini_webapi.constants import AccountStatus
    from src.cookie_manager import BrowserCookieDiscovery

    monkeypatch.setenv("GEMINI_PSID", "bound")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "true")
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    cm = MagicMock()
    def read_browser(**_kwargs):
        os.environ["GEMINI_PSID"] = "changed"
        return BrowserCookieDiscovery(cookies={"__Secure-1PSID": "bound"})
    cm.discover_browser_cookies.side_effect = read_browser
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: cm)
    client = _synthetic_auth_client([AccountStatus.UNAUTHENTICATED])
    mgr = ClientManager()
    mgr._client = client
    with pytest.raises(PermissionError):
        asyncio.run(mgr.initialize())
    assert client.init.await_count == 1 and cm.update_cookie.call_count == 0


def test_browser_autoload_retries_on_next_use_after_a_read_denial(monkeypatch):
    from src.cookie_manager import BrowserCookieDiscovery, CookieManager

    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "true")
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)
    manager = CookieManager(auto_refresh=False)
    cookies = {"__Secure-1PSID": "synthetic-browser-session", "__Secure-1PSIDTS": "fresh"}
    discover = MagicMock(side_effect=[
        BrowserCookieDiscovery(reason="BROWSER_COOKIE_ACCESS_DENIED"),
        BrowserCookieDiscovery(cookies=cookies, source="browser_chrome:Default"),
    ])
    monkeypatch.setattr(manager, "discover_browser_cookies", discover)
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: manager)
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    client = MagicMock()
    monkeypatch.setattr("src.thinking_client.ThinkingLevelGeminiClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(client_manager, "prepare_browser_cookie_cache", lambda: None)
    mgr = ClientManager()

    for _ in range(2):
        with pytest.raises(client_manager.ClientAuthenticationRequired) as caught:
            mgr.get_client()
        assert caught.value.auth_reason == "BROWSER_COOKIE_ACCESS_DENIED"
    assert not manager.get_cookie() and discover.call_count == 1

    mgr._browser_retry_at = 0
    assert mgr.get_client() is client
    assert discover.call_count == 2 and mgr.browser_auth_reason is None
    assert client.cookies == cookies and manager.get_cookie().source == "browser_chrome:Default"
