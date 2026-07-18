"""cookie_manager 浏览器/静态方法与异步验证的行为测试。

调研发现 cookie_manager.py 覆盖率 62%（404 stmts, 155 miss），缺失行集中在
浏览器 Cookie 提取的静态方法与异步 Gemini 账号验证函数。已有 test_cookie_manager.py
覆盖 CookieManager 生命周期，本文件补充：

静态方法（纯逻辑，无需浏览器/网络）：
- `_read_cookie_jar`：cookie 名不在集合 / 空值 / 域名不匹配 → 过滤
- `_browser_cookie_candidates`：非 chrome 浏览器 → 空列表
- `_chrome_selected_profile_directory`：Local State 读取的各种边界
- `_select_valid_cookie_candidate`：单候选直接返回
- `_select_named_cookie_candidate`：未找到 → 空 dict
- `get_cookies_from_browser`：不支持的浏览器 → 空 dict
- `list_browser_cookie_profiles`：不支持的浏览器 → error dict
- `get_cookie_from_browser`：委托 get_cookies_from_browser

异步验证（mock GeminiClient，无需真实网络）：
- `_validate_cookie_candidates_async`：账号可用 + scheduled > 0 / 不可用 / init 抛异常 / 首个可用回退
- `_validate_cookie_candidate_profiles_async`：可用 / 不可用 / init 抛异常
- `_probe_scheduled_registry_count`：_batch_execute 抛异常 → 0

不可测试的分支：`except ImportError`（browser_cookie3 与 gemini_webapi 在 venv 中恒可用）。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cookie_manager import CookieManager


def _run(coro):
    return asyncio.run(coro)


def _fake_cookie(name, value, domain="google.com"):
    """构造一个最小可用的 cookie 替身。"""
    return SimpleNamespace(name=name, value=value, domain=domain)


# ---------------------------------------------------------------------------
# _read_cookie_jar
# ---------------------------------------------------------------------------


def test_read_cookie_jar_filters_unwanted_names():
    """cookie 名不在 cookie_names 集合中 → 跳过（line 249）。"""
    names = {"__Secure-1PSID"}
    jar = [_fake_cookie("unwanted", "v"), _fake_cookie("__Secure-1PSID", "psid")]
    result = CookieManager._read_cookie_jar(jar, names)
    assert result == {"__Secure-1PSID": "psid"}


def test_read_cookie_jar_filters_empty_values():
    """cookie 值为空 → 跳过（line 249 的 not cookie.value）。"""
    names = {"__Secure-1PSID"}
    jar = [_fake_cookie("__Secure-1PSID", "")]
    result = CookieManager._read_cookie_jar(jar, names)
    assert result == {}


def test_read_cookie_jar_filters_non_google_domains():
    """cookie 域名不是 google.com 或 .google.com → 跳过（line 251）。"""
    names = {"__Secure-1PSID"}
    jar = [
        _fake_cookie("__Secure-1PSID", "v1", domain="evil.com"),
        _fake_cookie("__Secure-1PSID", "v2", domain=".google.com"),
        _fake_cookie("__Secure-1PSID", "v3", domain="google.com"),
    ]
    result = CookieManager._read_cookie_jar(jar, names)
    assert result == {"__Secure-1PSID": "v3"}  # v2 和 v3 都通过过滤，后者覆盖


# ---------------------------------------------------------------------------
# _chrome_base_path（平台分支）
# ---------------------------------------------------------------------------


def test_chrome_base_path_darwin(monkeypatch):
    """sys.platform == darwin → macOS Chrome 路径（line 258-259）。"""
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    path = CookieManager._chrome_base_path()
    assert path is not None
    assert path.name == "Chrome"
    assert "Application Support" in path.parts


def test_chrome_base_path_win32(monkeypatch):
    """sys.platform == win32 → Windows Chrome 路径（line 260-261）。"""
    import sys
    monkeypatch.setattr(sys, "platform", "win32")
    path = CookieManager._chrome_base_path()
    assert path is not None
    assert "User Data" in path.parts


def test_chrome_base_path_linux(monkeypatch):
    """sys.platform == linux → Linux Chrome 路径（line 262-263）。"""
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    path = CookieManager._chrome_base_path()
    assert path is not None
    assert path.name == "google-chrome"


# ---------------------------------------------------------------------------
# _browser_cookie_candidates
# ---------------------------------------------------------------------------


def test_browser_cookie_candidates_returns_empty_for_non_chrome():
    """非 chrome 浏览器 → 返回空列表（line 274）。"""
    result = CookieManager._browser_cookie_candidates(
        MagicMock(), "firefox", MagicMock(), {"__Secure-1PSID"},
    )
    assert result == []


def test_browser_cookie_candidates_chrome_reads_auto_and_profiles(monkeypatch, tmp_path):
    """chrome 路径：auto cookie 有 PSID + Profile 目录有 cookie 文件 → 收集候选（lines 276-304）。"""
    names = {"__Secure-1PSID", "__Secure-1PSIDTS"}
    # 仅创建 Profile 1/Cookies（Default/Cookies 不存在 → 跳过），避免与 auto 候选重复计数
    (tmp_path / "Profile 1").mkdir()
    (tmp_path / "Profile 1" / "Cookies").write_text("")
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))

    auto_jar = [_fake_cookie("__Secure-1PSID", "auto-psid")]
    profile_jar = [_fake_cookie("__Secure-1PSID", "prof1-psid")]

    cookie_function = MagicMock(return_value=auto_jar)
    browser_cookie3 = MagicMock()
    browser_cookie3.chrome = MagicMock(return_value=profile_jar)

    candidates = CookieManager._browser_cookie_candidates(
        browser_cookie3, "chrome", cookie_function, names,
    )
    # auto 候选 + Profile 1 候选
    assert len(candidates) == 2
    profile_names = [name for name, _ in candidates]
    assert "auto" in profile_names
    assert "Profile 1" in profile_names


def test_browser_cookie_candidates_skips_auto_on_exception(monkeypatch, tmp_path):
    """auto cookie 读取抛异常 → 跳过 auto，仅返回 profile 候选（lines 285-286）。"""
    names = {"__Secure-1PSID"}
    (tmp_path / "Default").mkdir()
    (tmp_path / "Default" / "Cookies").write_text("")
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))

    cookie_function = MagicMock(side_effect=RuntimeError("locked"))
    profile_jar = [_fake_cookie("__Secure-1PSID", "prof-psid")]
    browser_cookie3 = MagicMock()
    browser_cookie3.chrome = MagicMock(return_value=profile_jar)

    candidates = CookieManager._browser_cookie_candidates(
        browser_cookie3, "chrome", cookie_function, names,
    )
    assert len(candidates) == 1
    assert candidates[0][0] == "Default"


def test_browser_cookie_candidates_require_psid_false_keeps_empty(monkeypatch, tmp_path):
    """require_psid=False → 无 PSID 的候选也保留（line 299）。"""
    names = {"__Secure-1PSID"}
    (tmp_path / "Default").mkdir()
    (tmp_path / "Default" / "Cookies").write_text("")
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))

    # auto 无 PSID，profile 也无 PSID
    cookie_function = MagicMock(return_value=[])
    profile_jar = [_fake_cookie("SID", "other")]  # 非 PSID
    browser_cookie3 = MagicMock()
    browser_cookie3.chrome = MagicMock(return_value=profile_jar)

    candidates = CookieManager._browser_cookie_candidates(
        browser_cookie3, "chrome", cookie_function, names, require_psid=False,
    )
    # auto 无 PSID 不加入（即使 require_psid=False，auto 分支仍需 PSID，line 283）
    # profile 无 PSID 但 require_psid=False → 加入（line 299）
    assert len(candidates) == 1
    assert candidates[0][0] == "Default"


def test_browser_cookie_candidates_returns_empty_when_base_none(monkeypatch):
    """_chrome_base_path 返回 None → 空列表（lines 277-278）。"""
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: None))
    candidates = CookieManager._browser_cookie_candidates(
        MagicMock(), "chrome", MagicMock(), {"__Secure-1PSID"},
    )
    assert candidates == []


def test_browser_cookie_candidates_skips_profile_on_exception(monkeypatch, tmp_path):
    """profile cookie 读取抛异常 → 跳过该 profile（lines 296-298）。"""
    names = {"__Secure-1PSID"}
    (tmp_path / "Default").mkdir()
    (tmp_path / "Default" / "Cookies").write_text("")
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))

    cookie_function = MagicMock(return_value=[])  # auto 无 PSID
    browser_cookie3 = MagicMock()
    browser_cookie3.chrome = MagicMock(side_effect=RuntimeError("locked"))

    candidates = CookieManager._browser_cookie_candidates(
        browser_cookie3, "chrome", cookie_function, names,
    )
    assert candidates == []


def test_browser_cookie_candidates_returns_empty_when_no_candidates(monkeypatch, tmp_path):
    """无任何候选 → 返回空列表（lines 302-303）。"""
    names = {"__Secure-1PSID"}
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))
    # auto 无 PSID，无 profile 文件
    cookie_function = MagicMock(return_value=[])
    candidates = CookieManager._browser_cookie_candidates(
        MagicMock(), "chrome", cookie_function, names,
    )
    assert candidates == []


# ---------------------------------------------------------------------------
# _chrome_selected_profile_directory
# ---------------------------------------------------------------------------


def test_chrome_selected_profile_returns_empty_when_base_none(monkeypatch):
    """_chrome_base_path 返回 None → 空串（lines 309-310）。"""
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: None))
    assert CookieManager._chrome_selected_profile_directory() == ""


@pytest.fixture
def mock_chrome_base(monkeypatch, tmp_path):
    """将 _chrome_base_path 重定向到 tmp_path，隔离真实 Chrome 目录。"""
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))
    return tmp_path


def test_chrome_selected_profile_returns_empty_when_no_local_state(mock_chrome_base):
    """Local State 文件不存在 → 返回空串（lines 314-316）。"""
    assert CookieManager._chrome_selected_profile_directory() == ""


def test_chrome_selected_profile_returns_empty_when_invalid_json(mock_chrome_base):
    """Local State 非合法 JSON → 返回空串（lines 314-316）。"""
    (mock_chrome_base / "Local State").write_text("not json{")
    assert CookieManager._chrome_selected_profile_directory() == ""


def test_chrome_selected_profile_returns_last_used_string(mock_chrome_base):
    """profile.last_used 为字符串 → 返回该字符串（line 321-322）。"""
    (mock_chrome_base / "Local State").write_text(json.dumps(
        {"profile": {"last_used": "Profile 5"}},
    ))
    assert CookieManager._chrome_selected_profile_directory() == "Profile 5"


def test_chrome_selected_profile_returns_first_of_last_active_list(mock_chrome_base):
    """profile.last_active_profiles 为列表 → 返回首元素（lines 323-324）。"""
    (mock_chrome_base / "Local State").write_text(json.dumps(
        {"profile": {"last_active_profiles": ["Profile 3", "Default"]}},
    ))
    assert CookieManager._chrome_selected_profile_directory() == "Profile 3"


def test_chrome_selected_profile_returns_empty_when_profile_not_dict(mock_chrome_base):
    """profile 键非 dict → 返回空串（line 319）。"""
    (mock_chrome_base / "Local State").write_text(json.dumps({"profile": "not-a-dict"}))
    assert CookieManager._chrome_selected_profile_directory() == ""


def test_chrome_selected_profile_returns_empty_when_no_selected_key(mock_chrome_base):
    """profile 无 last_used / last_active_profiles → 返回空串（line 325）。"""
    (mock_chrome_base / "Local State").write_text(json.dumps({"profile": {}}))
    assert CookieManager._chrome_selected_profile_directory() == ""


# ---------------------------------------------------------------------------
# _select_valid_cookie_candidate
# ---------------------------------------------------------------------------


def test_select_valid_cookie_candidate_returns_directly_for_single():
    """单候选 → 直接返回其 cookies，不启动验证线程（lines 329-330）。"""
    cookies = {"__Secure-1PSID": "x"}
    result = CookieManager._select_valid_cookie_candidate([("Default", cookies)])
    assert result is cookies


def test_select_valid_cookie_candidate_falls_back_to_first_on_timeout(monkeypatch):
    """多候选 + 验证返回空 → 回退到第一个候选（lines 344-345）。"""
    cookies_a = {"__Secure-1PSID": "a"}
    cookies_b = {"__Secure-1PSID": "b"}
    # mock 异步验证返回空 dict（模拟"无可用候选"），AsyncMock 返回协程，
    # asyncio.run 才能 await；用 lambda 返回 dict 会让 asyncio.run 抛 TypeError
    monkeypatch.setattr(
        CookieManager, "_validate_cookie_candidates_async",
        staticmethod(AsyncMock(return_value={})),
    )
    result = CookieManager._select_valid_cookie_candidate([("A", cookies_a), ("B", cookies_b)])
    assert result is cookies_a


def test_select_valid_cookie_candidate_returns_validated_cookies(monkeypatch):
    """多候选 + 验证成功 → 返回验证后的 cookies（line 342）。"""
    cookies_a = {"__Secure-1PSID": "a"}
    cookies_b = {"__Secure-1PSID": "b"}
    validated = {"__Secure-1PSID": "validated"}
    monkeypatch.setattr(
        CookieManager, "_validate_cookie_candidates_async",
        staticmethod(AsyncMock(return_value=validated)),
    )
    result = CookieManager._select_valid_cookie_candidate([("A", cookies_a), ("B", cookies_b)])
    assert result == validated


# ---------------------------------------------------------------------------
# _select_named_cookie_candidate
# ---------------------------------------------------------------------------


def test_select_named_cookie_candidate_returns_empty_when_not_found():
    """指定 profile 未找到 → 返回空 dict（lines 354-355）。"""
    result = CookieManager._select_named_cookie_candidate(
        [("Default", {"__Secure-1PSID": "x"})], "NonExistent",
    )
    assert result == {}


def test_select_named_cookie_candidate_matches_case_insensitively():
    """profile 名匹配大小写不敏感。"""
    cookies = {"__Secure-1PSID": "x"}
    result = CookieManager._select_named_cookie_candidate(
        [("Profile 1", cookies)], "profile 1",
    )
    assert result is cookies


# ---------------------------------------------------------------------------
# get_cookies_from_browser / list_browser_cookie_profiles / get_cookie_from_browser
# ---------------------------------------------------------------------------


def test_get_cookies_from_browser_returns_empty_for_unsupported_browser():
    """不支持的浏览器 → 返回空 dict（lines 152-153）。"""
    assert CookieManager.get_cookies_from_browser("safari") == {}


def test_list_browser_cookie_profiles_returns_error_for_unsupported_browser():
    """不支持的浏览器 → 返回含 error 的列表（line 204）。"""
    result = CookieManager.list_browser_cookie_profiles("safari")
    assert len(result) == 1
    assert "error" in result[0]
    assert "safari" in result[0]["error"]


def test_get_cookie_from_browser_delegates_to_get_cookies(monkeypatch):
    """get_cookie_from_browser 委托 get_cookies_from_browser 并提取 psid/psidts（lines 485-486）。"""
    monkeypatch.setattr(
        CookieManager, "get_cookies_from_browser",
        staticmethod(lambda browser, profile="": {"__Secure-1PSID": "p", "__Secure-1PSIDTS": "t"}),
    )
    psid, psidts = CookieManager.get_cookie_from_browser("chrome")
    assert psid == "p"
    assert psidts == "t"


def test_get_cookie_from_browser_returns_none_when_no_psid(monkeypatch):
    """无 __Secure-1PSID → (None, None)（lines 485-486）。"""
    monkeypatch.setattr(
        CookieManager, "get_cookies_from_browser",
        staticmethod(lambda browser, profile="": {}),
    )
    psid, psidts = CookieManager.get_cookie_from_browser("chrome")
    assert psid is None
    assert psidts is None


def test_get_cookies_from_browser_with_profile_selects_named(monkeypatch):
    """profile + candidates → _select_named_cookie_candidate（lines 163-164, 177-181）。"""
    candidates = [("Default", {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "ts"})]
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: candidates),
    )
    cookies = CookieManager.get_cookies_from_browser("chrome", profile="Default")
    assert cookies["__Secure-1PSID"] == "psid"


def test_get_cookies_from_browser_no_profile_single_candidate(monkeypatch):
    """candidates 但无 profile → _select_valid_cookie_candidate 单候选直返（line 171）。"""
    candidates = [("Default", {"__Secure-1PSID": "psid"})]
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: candidates),
    )
    cookies = CookieManager.get_cookies_from_browser("chrome")
    assert cookies["__Secure-1PSID"] == "psid"


def test_get_cookies_from_browser_no_candidates_falls_back_to_read_jar(monkeypatch):
    """无 candidates → _read_cookie_jar fallback（lines 165-169）。"""
    import browser_cookie3
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: []),
    )
    jar = [_fake_cookie("__Secure-1PSID", "fallback-psid")]
    monkeypatch.setattr(browser_cookie3, "chrome", lambda domain_name="": jar)
    cookies = CookieManager.get_cookies_from_browser("chrome")
    assert cookies["__Secure-1PSID"] == "fallback-psid"


def test_get_cookies_from_browser_no_psid_in_candidates_returns_empty(monkeypatch):
    """candidates 无 PSID → 返回空 dict（lines 173-175）。"""
    candidates = [("Default", {})]
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: candidates),
    )
    cookies = CookieManager.get_cookies_from_browser("chrome")
    assert cookies == {}


def test_get_cookies_from_browser_handles_exception(monkeypatch):
    """_browser_cookie_candidates 抛异常 → 返回空 dict（lines 182-184）。"""
    def _raise(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(CookieManager, "_browser_cookie_candidates", staticmethod(_raise))
    cookies = CookieManager.get_cookies_from_browser("chrome")
    assert cookies == {}


def test_list_browser_cookie_profiles_with_candidates(monkeypatch):
    """有 candidates → 渲染 profile 信息（lines 206-232）。"""
    candidates = [("Default", {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "ts"})]
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: candidates),
    )
    monkeypatch.setattr(CookieManager, "_chrome_selected_profile_directory", staticmethod(lambda: ""))
    profiles = CookieManager.list_browser_cookie_profiles("chrome", validate=False)
    assert len(profiles) == 1
    assert profiles[0]["profile"] == "Default"
    assert profiles[0]["has_psid"] is True
    assert profiles[0]["has_psidts"] is True
    assert profiles[0]["cookie_count"] == 2


def test_list_browser_cookie_profiles_no_candidates_falls_back(monkeypatch):
    """无 candidates → fallback _read_cookie_jar（lines 210-218）。"""
    import browser_cookie3
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: []),
    )
    jar = [_fake_cookie("__Secure-1PSID", "psid")]
    monkeypatch.setattr(browser_cookie3, "chrome", lambda domain_name="": jar)
    monkeypatch.setattr(CookieManager, "_chrome_selected_profile_directory", staticmethod(lambda: ""))
    profiles = CookieManager.list_browser_cookie_profiles("chrome", validate=False)
    assert len(profiles) == 1
    assert profiles[0]["profile"] == "auto"
    assert profiles[0]["has_psid"] is True


def test_list_browser_cookie_profiles_no_candidates_read_error(monkeypatch):
    """无 candidates + cookie 读取抛异常 → error dict（lines 216-217）。"""
    import browser_cookie3
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: []),
    )
    def _raise(domain_name=""):
        raise RuntimeError("locked")
    monkeypatch.setattr(browser_cookie3, "chrome", _raise)
    profiles = CookieManager.list_browser_cookie_profiles("chrome", validate=False)
    assert len(profiles) == 1
    assert "error" in profiles[0]


def test_list_browser_cookie_profiles_validate_merges_validation(monkeypatch):
    """validate=True → 合并验证结果到 profiles（lines 233-238）。"""
    candidates = [("Default", {"__Secure-1PSID": "psid"})]
    monkeypatch.setattr(
        CookieManager, "_browser_cookie_candidates",
        staticmethod(lambda *a, **k: candidates),
    )
    validation = [{"profile": "Default", "account_available": True, "scheduled_registry_count": 2}]
    monkeypatch.setattr(
        CookieManager, "_validate_cookie_candidate_profiles",
        staticmethod(lambda c: validation),
    )
    monkeypatch.setattr(CookieManager, "_chrome_selected_profile_directory", staticmethod(lambda: ""))
    profiles = CookieManager.list_browser_cookie_profiles("chrome", validate=True)
    assert profiles[0]["account_available"] is True
    assert profiles[0]["scheduled_registry_count"] == 2


# ---------------------------------------------------------------------------
# _validate_cookie_candidates_async（mock GeminiClient）
# ---------------------------------------------------------------------------


def _make_fake_gemini_client_class(*, init_side_effect=None, account_status=None, close_side_effect=None):
    """构造一个假的 GeminiClient 类。"""
    class FakeGeminiClient:
        def __init__(self, psid, psidts="", **kwargs):
            self.psid = psid
            self.psidts = psidts
            self.cookies = {}
            self.account_status = account_status
            self.language = "en"
            self.init = AsyncMock(side_effect=init_side_effect)
            self.close = AsyncMock(side_effect=close_side_effect)

    return FakeGeminiClient


def _make_async_probe(return_value):
    """构造一个返回固定值的 async _probe_scheduled_registry_count 替身。

    必须返回协程而非在 lambda 里调 asyncio.run——后者会与
    _validate_cookie_candidates_async / _validate_cookie_candidate_profiles_async
    已运行的事件循环冲突（asyncio.run 不能嵌套调用）。
    """
    async def _probe(client):
        return return_value
    return _probe


def test_validate_cookie_candidates_async_returns_cookies_when_available_with_scheduled(monkeypatch):
    """账号可用 + scheduled_count > 0 → 返回该候选 cookies（lines 384-392）。"""
    from gemini_webapi.constants import AccountStatus

    fake_cls = _make_fake_gemini_client_class(account_status=AccountStatus.AVAILABLE)
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)
    # mock _probe_scheduled_registry_count 返回 > 0；必须直接返回协程，
    # 不能在 lambda 里调 asyncio.run（会与 _validate_cookie_candidates_async
    # 已运行的事件循环冲突，抛 RuntimeError 被 except 吞掉）
    monkeypatch.setattr(
        CookieManager, "_probe_scheduled_registry_count",
        staticmethod(_make_async_probe(3)),
    )

    cookies = {"__Secure-1PSID": "x", "__Secure-1PSIDTS": "t"}
    result = _run(CookieManager._validate_cookie_candidates_async([("Default", cookies)]))
    assert result == cookies


def test_validate_cookie_candidates_async_skips_unavailable_account(monkeypatch):
    """账号不可用 → 跳过，返回空（line 385-386）。"""
    fake_cls = _make_fake_gemini_client_class(account_status=None)
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)

    result = _run(CookieManager._validate_cookie_candidates_async(
        [("Default", {"__Secure-1PSID": "x"})],
    ))
    assert result == {}


def test_validate_cookie_candidates_async_returns_first_available_when_no_scheduled(monkeypatch):
    """账号可用但 scheduled == 0 → 返回 first_available（lines 387-388, 401-403）。"""
    from gemini_webapi.constants import AccountStatus

    fake_cls = _make_fake_gemini_client_class(account_status=AccountStatus.AVAILABLE)
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)
    monkeypatch.setattr(
        CookieManager, "_probe_scheduled_registry_count",
        staticmethod(_make_async_probe(0)),
    )

    cookies = {"__Secure-1PSID": "x"}
    result = _run(CookieManager._validate_cookie_candidates_async([("Default", cookies)]))
    assert result == cookies


def test_validate_cookie_candidates_async_handles_init_exception(monkeypatch):
    """client.init 抛异常 → 跳过该候选（lines 394-395）。"""
    fake_cls = _make_fake_gemini_client_class(init_side_effect=RuntimeError("network"))
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)

    result = _run(CookieManager._validate_cookie_candidates_async(
        [("Default", {"__Secure-1PSID": "x"})],
    ))
    assert result == {}


def test_validate_cookie_candidates_async_swallows_close_exception(monkeypatch):
    """client.close 抛异常 → 不影响结果（lines 399-400）。"""
    fake_cls = _make_fake_gemini_client_class(
        account_status=None,
        close_side_effect=RuntimeError("close boom"),
    )
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)

    # 账号不可用 → continue 触发 finally；close 抛异常被 finally 内 try/except 吞掉
    result = _run(CookieManager._validate_cookie_candidates_async(
        [("Default", {"__Secure-1PSID": "x"})],
    ))
    assert result == {}


# ---------------------------------------------------------------------------
# _validate_cookie_candidate_profiles_async（mock GeminiClient）
# ---------------------------------------------------------------------------


def test_validate_cookie_candidate_profiles_async_marks_available(monkeypatch):
    """账号可用 → account_available=True + scheduled_registry_count（lines 423-430）。"""
    from gemini_webapi.constants import AccountStatus

    fake_cls = _make_fake_gemini_client_class(account_status=AccountStatus.AVAILABLE)
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)
    monkeypatch.setattr(
        CookieManager, "_probe_scheduled_registry_count",
        staticmethod(_make_async_probe(5)),
    )

    profiles = _run(CookieManager._validate_cookie_candidate_profiles_async(
        [("Default", {"__Secure-1PSID": "x"})],
    ))
    assert len(profiles) == 1
    assert profiles[0]["account_available"] is True
    assert profiles[0]["scheduled_registry_count"] == 5


def test_validate_cookie_candidate_profiles_async_records_init_error(monkeypatch):
    """client.init 抛异常 → validation_error（lines 431-432）。"""
    fake_cls = _make_fake_gemini_client_class(init_side_effect=RuntimeError("fail"))
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)

    profiles = _run(CookieManager._validate_cookie_candidate_profiles_async(
        [("Default", {"__Secure-1PSID": "x"})],
    ))
    assert len(profiles) == 1
    assert "validation_error" in profiles[0]
    assert profiles[0]["account_available"] is False


def test_validate_cookie_candidate_profiles_async_swallows_close_exception(monkeypatch):
    """client.close 抛异常 → 不影响 profile 结果（lines 436-437）。"""
    from gemini_webapi.constants import AccountStatus

    fake_cls = _make_fake_gemini_client_class(
        account_status=AccountStatus.AVAILABLE,
        close_side_effect=RuntimeError("close boom"),
    )
    monkeypatch.setattr("gemini_webapi.GeminiClient", fake_cls)
    monkeypatch.setattr(
        CookieManager, "_probe_scheduled_registry_count",
        staticmethod(_make_async_probe(2)),
    )

    profiles = _run(CookieManager._validate_cookie_candidate_profiles_async(
        [("Default", {"__Secure-1PSID": "x"})],
    ))
    assert len(profiles) == 1
    assert profiles[0]["account_available"] is True


# ---------------------------------------------------------------------------
# _validate_cookie_candidate_profiles（同步线程包装器，lines 359-367）
# ---------------------------------------------------------------------------


def test_validate_cookie_candidate_profiles_thread_wrapper_returns_profiles(monkeypatch):
    """同步包装器启动线程运行 async 版本，返回 profile 列表（lines 359-367）。"""
    expected = [{"profile": "Default", "account_available": True}]
    monkeypatch.setattr(
        CookieManager, "_validate_cookie_candidate_profiles_async",
        staticmethod(AsyncMock(return_value=expected)),
    )
    result = CookieManager._validate_cookie_candidate_profiles(
        [("Default", {"__Secure-1PSID": "x"})],
    )
    assert result == expected


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_validate_cookie_candidate_profiles_thread_wrapper_returns_empty_on_timeout(monkeypatch):
    """线程超时/异常 → 返回空列表（line 367 的 result.get 默认值）。"""
    # mock async 版本抛异常 → 线程内 asyncio.run 失败 → result 为空 → get 默认 []
    async def _raise(candidates):
        raise RuntimeError("async boom")
    monkeypatch.setattr(
        CookieManager, "_validate_cookie_candidate_profiles_async",
        staticmethod(_raise),
    )
    result = CookieManager._validate_cookie_candidate_profiles(
        [("Default", {"__Secure-1PSID": "x"})],
    )
    assert result == []


# ---------------------------------------------------------------------------
# _probe_scheduled_registry_count（mock client）
# ---------------------------------------------------------------------------


def test_probe_scheduled_registry_count_returns_zero_on_exception(monkeypatch):
    """_batch_execute 抛异常 → 返回 0（lines 467-468, 472）。"""
    client = MagicMock()
    client.language = "en"
    client._batch_execute = AsyncMock(side_effect=RuntimeError("rpc fail"))
    result = _run(CookieManager._probe_scheduled_registry_count(client))
    assert result == 0


def test_probe_scheduled_registry_count_restores_language(monkeypatch):
    """探测后恢复 client.language（lines 470-471）。"""
    client = MagicMock()
    client.language = "en"
    client._batch_execute = AsyncMock(side_effect=RuntimeError("fail"))
    _run(CookieManager._probe_scheduled_registry_count(client))
    assert client.language == "en"


def test_probe_scheduled_registry_count_returns_count_on_happy_path(monkeypatch):
    """_batch_execute 返回有效响应 → 解析 JSON 返回 parsed[0] 长度（lines 458-466）。

    注意：生产代码 RPCData("XPSWpd", "[]") 在当前 gemini_webapi 版本会失败
   （pydantic 不接受位置参数 + XPSWpd 非有效 GRPC 枚举），这里 monkeypatch RPCData
    以隔离测试解析逻辑。RPCData 构造本身是已知生产 bug。
    """
    import gemini_webapi.types as gw_types
    import gemini_webapi.utils as gw_utils

    class _FakeRPCData:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(gw_types, "RPCData", _FakeRPCData)

    # 构造 part: [0]="wrb.fr", [1]="XPSWpd", [2]=JSON 字符串（parsed[0] 是 3 元素列表）
    body_json = json.dumps([["a", "b", "c"], ["d"]])
    parts = [["wrb.fr", "XPSWpd", body_json]]
    monkeypatch.setattr(gw_utils, "extract_json_from_response", lambda text: parts)

    client = MagicMock()
    client.language = "en"
    response = MagicMock()
    response.text = "dummy"
    client._batch_execute = AsyncMock(return_value=response)

    result = _run(CookieManager._probe_scheduled_registry_count(client))
    assert result == 3


def test_probe_scheduled_registry_count_returns_zero_when_body_not_string(monkeypatch):
    """body 非 str → 不解析，继续循环，最终返回 0（line 464 False 分支）。"""
    import gemini_webapi.types as gw_types
    import gemini_webapi.utils as gw_utils

    class _FakeRPCData:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(gw_types, "RPCData", _FakeRPCData)

    # part[2] 是 list 而非 str → isinstance(body, str) False
    parts = [["wrb.fr", "XPSWpd", ["not", "a", "string"]]]
    monkeypatch.setattr(gw_utils, "extract_json_from_response", lambda text: parts)

    client = MagicMock()
    client.language = "en"
    response = MagicMock()
    response.text = "dummy"
    client._batch_execute = AsyncMock(return_value=response)

    result = _run(CookieManager._probe_scheduled_registry_count(client))
    assert result == 0


def test_probe_scheduled_registry_count_returns_zero_when_parsed0_not_list(monkeypatch):
    """parsed[0] 非 list → 返回 0（line 466 的 else 0）。"""
    import gemini_webapi.types as gw_types
    import gemini_webapi.utils as gw_utils

    class _FakeRPCData:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(gw_types, "RPCData", _FakeRPCData)

    # parsed[0] 是字符串而非 list → ternary 走 else 0
    body_json = json.dumps(["not-a-list"])
    parts = [["wrb.fr", "XPSWpd", body_json]]
    monkeypatch.setattr(gw_utils, "extract_json_from_response", lambda text: parts)

    client = MagicMock()
    client.language = "en"
    response = MagicMock()
    response.text = "dummy"
    client._batch_execute = AsyncMock(return_value=response)

    result = _run(CookieManager._probe_scheduled_registry_count(client))
    assert result == 0


def test_probe_scheduled_registry_count_skips_non_matching_parts(monkeypatch):
    """part[0] != 'wrb.fr' 或 part[1] != 'XPSWpd' → continue 跳过（lines 459-462）。"""
    import gemini_webapi.types as gw_types
    import gemini_webapi.utils as gw_utils

    class _FakeRPCData:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(gw_types, "RPCData", _FakeRPCData)

    body_json = json.dumps([["a", "b"]])
    parts = [
        ["other", "XPSWpd", body_json],  # [0] != wrb.fr → skip
        ["wrb.fr", "other", body_json],   # [1] != XPSWpd → skip
    ]
    monkeypatch.setattr(gw_utils, "extract_json_from_response", lambda text: parts)

    client = MagicMock()
    client.language = "en"
    response = MagicMock()
    response.text = "dummy"
    client._batch_execute = AsyncMock(return_value=response)

    result = _run(CookieManager._probe_scheduled_registry_count(client))
    assert result == 0
