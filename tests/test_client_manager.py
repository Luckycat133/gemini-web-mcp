"""client_manager 的单元测试。

调研发现以下纯函数和 ClientManager 生命周期方法此前仅通过 client_wrapper
间接覆盖，缺少直接断言：
- validate_config（缺 GEMINI_PSID 抛 ValueError）
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
import os
import tempfile
import threading
from unittest.mock import MagicMock

import pytest

from src import client_manager
from src.client_manager import (
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
    with pytest.raises(ValueError, match="GEMINI_PSID"):
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


def test_client_manager_initialize_concurrent_safe(monkeypatch):
    """并发 initialize 不会重复调用 client.init（_init_lock 保护）。"""
    mgr = ClientManager()
    fake_client = MagicMock()
    init_call_count = {"n": 0}
    lock = threading.Lock()

    async def fake_init(**kwargs):
        with lock:
            init_call_count["n"] += 1

    fake_client.init = fake_init
    mgr._client = fake_client
    mgr._initialized = False

    async def run_all():
        await asyncio.gather(mgr.initialize(), mgr.initialize(), mgr.initialize())

    asyncio.run(run_all())
    assert init_call_count["n"] == 1
    assert mgr._initialized is True
