"""cookie_manager 的单元测试。

调研发现 CookieManager 的核心生命周期（update_cookie 回调链、get_cookie_status
状态机、refresh_cookie、to_env_vars、start/stop_monitor）此前零行为测试，仅有
list_browser_cookie_profiles / get_cookies_from_browser 的浏览器候选探测覆盖。
本文件补充行为断言，不依赖真实浏览器或网络。

测试隔离策略：
- 直接构造 CookieManager(...) 实例，不走模块级单例
- monkeypatch.setenv/unsetenv 隔离环境变量
- monkeypatch 替换 get_cookie_from_browser 避免真实浏览器调用
- start_monitor 用极短 interval + 立即 stop，避免长循环
"""

import time
import threading

import pytest

from src.cookie_manager import (
    CookieData,
    CookieManager,
    CookieStatus,
)


# ---------------------------------------------------------------------------
# 辅助 fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    """清空 Cookie 相关环境变量，确保测试隔离。"""
    for var in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def manager(clean_env):
    """无初始 cookie 的 CookieManager 实例。"""
    return CookieManager(refresh_threshold_hours=24, auto_refresh=False)


# ---------------------------------------------------------------------------
# __init__ + _load_initial_cookie
# ---------------------------------------------------------------------------


def test_init_loads_cookie_from_env(clean_env):
    """环境变量有 PSID 时，__init__ 加载为 VALID 状态。"""
    clean_env.setenv("GEMINI_PSID", "psid-abc")
    clean_env.setenv("GEMINI_PSIDTS", "psidts-def")
    mgr = CookieManager()
    cookie = mgr.get_cookie()
    assert cookie is not None
    assert cookie.psid == "psid-abc"
    assert cookie.psidts == "psidts-def"
    assert cookie.source == "manual"
    assert cookie.status == CookieStatus.VALID


def test_init_without_env_has_no_cookie(clean_env):
    """环境变量无 PSID 时，cookie_data 为 None。"""
    mgr = CookieManager()
    assert mgr.get_cookie() is None


def test_load_extra_cookies_from_env_includes_psidcc(clean_env):
    """_load_extra_cookies_from_env 在有 PSIDCC 时附加 extra_cookies。"""
    clean_env.setenv("GEMINI_PSIDCC", "psidcc-xyz")
    cookies = CookieManager._load_extra_cookies_from_env("psid", "psidts")
    assert cookies["__Secure-1PSID"] == "psid"
    assert cookies["__Secure-1PSIDTS"] == "psidts"
    assert cookies["__Secure-1PSIDCC"] == "psidcc-xyz"


def test_load_extra_cookies_from_env_omits_empty_values(clean_env):
    """_load_extra_cookies_from_env 不附加空值。"""
    cookies = CookieManager._load_extra_cookies_from_env("", "")
    assert cookies == {}


# ---------------------------------------------------------------------------
# update_cookie + on_cookie_update 回调链
# ---------------------------------------------------------------------------


def test_update_cookie_sets_valid_status_and_source(manager):
    """update_cookie 成功后 status=VALID、source 透传。"""
    ok = manager.update_cookie("new-psid", "new-psidts", source="browser_chrome")
    assert ok is True
    cookie = manager.get_cookie()
    assert cookie.psid == "new-psid"
    assert cookie.psidts == "new-psidts"
    assert cookie.source == "browser_chrome"
    assert cookie.status == CookieStatus.VALID


def test_update_cookie_rejects_empty_psid(manager):
    """空 psid 返回 False，不修改状态。"""
    ok = manager.update_cookie("")
    assert ok is False
    assert manager.get_cookie() is None


def test_update_cookie_invokes_callback(manager):
    """update_cookie 成功时触发 on_cookie_update 回调，传入 CookieData。"""
    received: list[CookieData] = []
    manager.on_cookie_update = received.append
    manager.update_cookie("psid-1", "psidts-1")
    assert len(received) == 1
    assert received[0].psid == "psid-1"


def test_update_cookie_callback_exception_does_not_break_update(manager):
    """回调抛异常时 update_cookie 仍返回 True（异常被吞并记录）。"""
    def boom(_):
        raise RuntimeError("callback boom")
    manager.on_cookie_update = boom
    ok = manager.update_cookie("psid-1")
    assert ok is True
    assert manager.get_cookie().psid == "psid-1"


def test_update_cookie_overwrites_previous(manager):
    """二次 update_cookie 覆盖旧 cookie_data。"""
    manager.update_cookie("old-psid")
    manager.update_cookie("new-psid", "new-psidts")
    cookie = manager.get_cookie()
    assert cookie.psid == "new-psid"
    assert cookie.psidts == "new-psidts"


# ---------------------------------------------------------------------------
# get_cookie_status 状态机
# ---------------------------------------------------------------------------


def test_get_cookie_status_unknown_when_no_cookie(manager):
    """无 cookie 时返回 UNKNOWN + has_cookie=False。"""
    status, info = manager.get_cookie_status()
    assert status == CookieStatus.UNKNOWN
    assert info["has_cookie"] is False


def test_get_cookie_status_valid_for_fresh_cookie(manager):
    """新 cookie 返回 VALID + needs_refresh=False。"""
    manager.update_cookie("psid")
    status, info = manager.get_cookie_status()
    assert status == CookieStatus.VALID
    assert info["has_cookie"] is True
    assert info["needs_refresh"] is False
    assert info["source"] == "manual"


def test_get_cookie_status_expired_for_old_cookie(manager):
    """acquired_at 早于 refresh_threshold 时返回 EXPIRED + needs_refresh=True。"""
    manager.update_cookie("psid")
    cookie = manager.get_cookie()
    cookie.acquired_at = time.time() - 25 * 3600  # 25 小时前，超过 24h 阈值
    status, info = manager.get_cookie_status()
    assert status == CookieStatus.EXPIRED
    assert info["needs_refresh"] is True


def test_needs_refresh_reflects_status(manager):
    """needs_refresh 基于 status==EXPIRED。"""
    assert manager.needs_refresh() is False
    manager.update_cookie("psid")
    assert manager.needs_refresh() is False
    manager.get_cookie().acquired_at = time.time() - 25 * 3600
    assert manager.needs_refresh() is True


# ---------------------------------------------------------------------------
# refresh_cookie
# ---------------------------------------------------------------------------


def test_refresh_cookie_returns_false_when_no_cookie(manager):
    """无 cookie 时 refresh_cookie 返回 False 并记录错误。"""
    assert manager.refresh_cookie() is False


def test_refresh_cookie_without_browser_returns_false(manager):
    """有 cookie 但无 browser 参数时返回 False（需用户手动交互）。"""
    manager.update_cookie("psid")
    assert manager.refresh_cookie() is False
    # 失败后 status 应该被 finally 块重置为 VALID（不是 REFRESHING）
    assert manager.get_cookie().status == CookieStatus.VALID


def test_refresh_cookie_with_browser_calls_update_on_success(manager, monkeypatch):
    """refresh_cookie(browser) 成功获取后调用 update_cookie，source 带 browser 前缀。"""
    manager.update_cookie("old-psid")
    monkeypatch.setattr(
        CookieManager, "get_cookie_from_browser",
        lambda self, browser="chrome", profile="": ("fresh-psid", "fresh-psidts"),
    )
    ok = manager.refresh_cookie(browser="chrome")
    assert ok is True
    cookie = manager.get_cookie()
    assert cookie.psid == "fresh-psid"
    assert cookie.source == "browser_chrome"


def test_refresh_cookie_with_browser_failure_returns_false(manager, monkeypatch):
    """refresh_cookie(browser) 未获取到 cookie 时返回 False。"""
    manager.update_cookie("old-psid")
    monkeypatch.setattr(
        CookieManager, "get_cookie_from_browser",
        lambda self, browser="chrome", profile="": (None, None),
    )
    ok = manager.refresh_cookie(browser="chrome")
    assert ok is False
    # 原 cookie 仍保留，status 被 finally 重置为 VALID
    assert manager.get_cookie().psid == "old-psid"
    assert manager.get_cookie().status == CookieStatus.VALID


# ---------------------------------------------------------------------------
# to_env_vars
# ---------------------------------------------------------------------------


def test_to_env_vars_empty_when_no_cookie(manager):
    assert manager.to_env_vars() == {}


def test_to_env_vars_only_psid(manager):
    manager.update_cookie("psid-1")
    env = manager.to_env_vars()
    assert env == {"GEMINI_PSID": "psid-1"}


def test_to_env_vars_includes_psidts(manager):
    manager.update_cookie("psid-1", "psidts-1")
    env = manager.to_env_vars()
    assert env == {"GEMINI_PSID": "psid-1", "GEMINI_PSIDTS": "psidts-1"}


# ---------------------------------------------------------------------------
# start_monitor / stop_monitor
# ---------------------------------------------------------------------------


def test_start_stop_monitor_idempotent(manager):
    """start → stop → stop 不报错；重复 start 不重启线程。"""
    manager.start_monitor(interval=3600)
    assert manager._monitor_running is True
    assert manager._monitor_thread is not None

    # 重复 start 不重启
    first_thread = manager._monitor_thread
    manager.start_monitor(interval=3600)
    assert manager._monitor_thread is first_thread

    manager.stop_monitor()
    assert manager._monitor_running is False

    # 重复 stop 安全
    manager.stop_monitor()
    assert manager._monitor_running is False


def test_monitor_loop_short_interval_does_not_crash(manager):
    """启动极短间隔的监控循环，立即停止，验证 _monitor_loop 不抛异常。

    不验证循环行为本身（依赖时间），只验证启停协议健壮。
    """
    manager.update_cookie("psid")  # 有 cookie 才进入正常分支
    manager.start_monitor(interval=0)  # 立即触发第一次检查
    # 让监控线程有机会跑一轮
    time.sleep(0.05)
    manager.stop_monitor()
    # 监控线程停止后 _monitor_running=False，原 cookie 仍在
    assert manager.get_cookie() is not None


# ---------------------------------------------------------------------------
# CookieData dataclass
# ---------------------------------------------------------------------------


def test_cookie_data_defaults():
    """CookieData 默认值：source=manual、status=UNKNOWN、extra_cookies={}。"""
    data = CookieData(psid="x")
    assert data.psidts == ""
    assert data.extra_cookies == {}
    assert data.source == "manual"
    assert data.status == CookieStatus.UNKNOWN
    assert data.expires_at is None


def test_cookie_status_enum_values():
    """CookieStatus 4 个枚举值稳定（外部字符串匹配依赖）。"""
    assert CookieStatus.VALID.value == "valid"
    assert CookieStatus.EXPIRED.value == "expired"
    assert CookieStatus.UNKNOWN.value == "unknown"
    assert CookieStatus.REFRESHING.value == "refreshing"


# ---------------------------------------------------------------------------
# 并发：update_cookie + get_cookie 在锁下不竞争
# ---------------------------------------------------------------------------


def test_concurrent_update_cookie_is_safe(manager):
    """多线程并发 update_cookie 不破坏 cookie_data 完整性。"""
    errors: list[Exception] = []

    def writer(idx: int):
        try:
            for i in range(20):
                manager.update_cookie(f"psid-{idx}-{i}", source=f"thread-{idx}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    cookie = manager.get_cookie()
    assert cookie is not None
    assert cookie.psid.startswith("psid-")
