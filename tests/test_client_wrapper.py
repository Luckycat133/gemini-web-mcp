"""client_wrapper 门面模块的行为测试。

调研发现该模块覆盖率仅 45%（104 stmts, 57 miss），是全仓最低覆盖模块。
此前零直接测试——所有覆盖均来自 tools 层间接调用。缺失行集中在：

- `_session_data_to_dict`：None 短路 + 字典构造（lines 40-42）
- 客户端/会话/清理门面委托：`initialize_client` / `reset_client` /
  `store_session` / `get_session` / `remove_session` / `pop_session` /
  `clear_sessions` / `cleanup_expired_sessions` / `list_sessions`
  （lines 63, 68-69, 85, 99, 104, 109, 114, 119, 124-125）
- 异步清理函数 client=None 分支（自取 client + 初始化）：
  `delete_remote_chat` / `cleanup_due_remote_chats`（lines 166-169, 175-176）
- Cookie 集成层：
  - `_on_cookie_update` 回调（reset + 写 env，psidts 真假两分支）（lines 196-200）
  - `init_cookie_manager_integration`（unavailable 短路 / auto_refresh env 解析 /
    init + start_monitor）（lines 205-210）
  - `get_cookie_from_browser`（unavailable / 无 psid / update 成功 / update 失败 /
    psidts 空 / profile 拼接 source）（lines 215-239）
  - `list_browser_cookie_profiles`（unavailable / validate=True 调 cache /
    validate=False 跳过 cache）（lines 245, 247）
  - `get_cookie_status`（unavailable / available 展开状态）（line 254）

测试策略：用 `monkeypatch.setattr(cw, "_session_manager", fake)` 等替换模块级
单例，避免触碰真实 ClientManager/SessionManager/RemoteChatCleanupManager 状态。
Cookie 函数通过 patch `cw.COOKIE_MANAGER_AVAILABLE` + `cw.get_cookie_manager` +
`cw.init_cookie_manager` + `cw._prepare_browser_cookie_cache` + `cw.reset_client`
隔离真实浏览器/单例副作用。
"""

import asyncio
import os
from types import SimpleNamespace

import src.client_wrapper as cw
from src.remote_chat_cleanup_manager import CleanupTask
from src.session_manager import SessionData


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _run(coro):
    """同步运行协程的薄包装，便于在同步测试中调用 async 门面。"""
    return asyncio.run(coro)


class _RecordingFake:
    """记录所有方法调用的假管理器。

    用 keyword 参数指定方法的返回值；未列出的方法返回 None。
    _async_methods 中列出的方法返回协程（供 facade await）。
    每次调用追加 (method_name, args, kwargs) 到 self.calls。
    """

    def __init__(self, _async_methods=(), **return_values):
        self._return_values = return_values
        self._async_methods = set(_async_methods)
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            rv = self._return_values.get(name)
            if name in self._async_methods:
                async def _coro():
                    return rv
                return _coro()
            return rv

        return _method


# ---------------------------------------------------------------------------
# _session_data_to_dict
# ---------------------------------------------------------------------------


def test_session_data_to_dict_returns_none_for_none():
    assert cw._session_data_to_dict(None) is None


def test_session_data_to_dict_maps_all_eight_fields():
    data = SessionData(
        session="sess-obj",
        model="pro",
        thinking_level="extended",
        learning_mode="quiz",
        temporary=True,
        retain_chat=True,
        delete_after_seconds=120,
    )
    result = cw._session_data_to_dict(data)
    assert result == {
        "session": "sess-obj",
        "model": "pro",
        "thinking_level": "extended",
        "learning_mode": "quiz",
        "temporary": True,
        "created_at": data.created_at,
        "retain_chat": True,
        "delete_after_seconds": 120,
    }


# ---------------------------------------------------------------------------
# 客户端管理门面
# ---------------------------------------------------------------------------


def test_get_gemini_client_delegates_to_client_manager(monkeypatch):
    fake = _RecordingFake(get_client="the-client")
    monkeypatch.setattr(cw, "_client_manager", fake)
    assert cw.get_gemini_client() == "the-client"
    assert fake.calls == [("get_client", (), {})]


def test_initialize_client_delegates_to_client_manager(monkeypatch):
    fake = _RecordingFake(initialize="inited", _async_methods={"initialize"})
    monkeypatch.setattr(cw, "_client_manager", fake)
    result = _run(cw.initialize_client())
    assert result == "inited"
    assert fake.calls == [("initialize", (), {})]


def test_reset_client_resets_client_manager_and_clears_sessions(monkeypatch):
    client_fake = _RecordingFake()
    session_fake = _RecordingFake()
    monkeypatch.setattr(cw, "_client_manager", client_fake)
    monkeypatch.setattr(cw, "_session_manager", session_fake)
    cw.reset_client()
    assert client_fake.calls == [("reset", (), {})]
    assert session_fake.calls == [("clear_sessions", (), {})]


# ---------------------------------------------------------------------------
# 会话管理门面
# ---------------------------------------------------------------------------


def test_store_session_forwards_all_args_to_session_manager(monkeypatch):
    fake = _RecordingFake()
    monkeypatch.setattr(cw, "_session_manager", fake)
    cw.store_session(
        "sid-1",
        "session-obj",
        "pro",
        thinking_level="extended",
        learning_mode="quiz",
        temporary=True,
        retain_chat=True,
        delete_after_seconds=300,
    )
    name, args, kwargs = fake.calls[0]
    assert name == "store_session"
    assert args == ("sid-1", "session-obj", "pro")
    assert kwargs == {
        "thinking_level": "extended",
        "learning_mode": "quiz",
        "temporary": True,
        "retain_chat": True,
        "delete_after_seconds": 300,
    }


def test_get_session_returns_dict_via_converter(monkeypatch):
    data = SessionData(session="s", model="flash")
    fake = _RecordingFake(get_session=data)
    monkeypatch.setattr(cw, "_session_manager", fake)
    result = cw.get_session("sid-1")
    assert result["session"] == "s"
    assert result["model"] == "flash"
    assert fake.calls == [("get_session", ("sid-1",), {})]


def test_get_session_returns_none_when_underlying_returns_none(monkeypatch):
    fake = _RecordingFake(get_session=None)
    monkeypatch.setattr(cw, "_session_manager", fake)
    assert cw.get_session("missing") is None


def test_remove_session_delegates(monkeypatch):
    fake = _RecordingFake()
    monkeypatch.setattr(cw, "_session_manager", fake)
    cw.remove_session("sid-1")
    assert fake.calls == [("remove_session", ("sid-1",), {})]


def test_pop_session_returns_dict_via_converter(monkeypatch):
    data = SessionData(session="s", model="pro")
    fake = _RecordingFake(pop_session=data)
    monkeypatch.setattr(cw, "_session_manager", fake)
    result = cw.pop_session("sid-1")
    assert result["session"] == "s"
    assert result["model"] == "pro"
    assert fake.calls == [("pop_session", ("sid-1",), {})]


def test_pop_session_returns_none_when_underlying_returns_none(monkeypatch):
    fake = _RecordingFake(pop_session=None)
    monkeypatch.setattr(cw, "_session_manager", fake)
    assert cw.pop_session("missing") is None


def test_clear_sessions_delegates(monkeypatch):
    fake = _RecordingFake()
    monkeypatch.setattr(cw, "_session_manager", fake)
    cw.clear_sessions()
    assert fake.calls == [("clear_sessions", (), {})]


def test_cleanup_expired_sessions_delegates(monkeypatch):
    fake = _RecordingFake()
    monkeypatch.setattr(cw, "_session_manager", fake)
    cw.cleanup_expired_sessions()
    assert fake.calls == [("cleanup_expired_sessions", (), {})]


def test_list_sessions_filters_none_and_converts(monkeypatch):
    kept = SessionData(session="s1", model="flash")
    fake = _RecordingFake(list_sessions={"a": kept, "b": None})
    monkeypatch.setattr(cw, "_session_manager", fake)
    result = cw.list_sessions()
    assert set(result.keys()) == {"a"}
    assert result["a"]["session"] == "s1"


def test_list_sessions_empty_when_underlying_empty(monkeypatch):
    fake = _RecordingFake(list_sessions={})
    monkeypatch.setattr(cw, "_session_manager", fake)
    assert cw.list_sessions() == {}


# ---------------------------------------------------------------------------
# 远程聊天清理门面
# ---------------------------------------------------------------------------


def test_schedule_remote_chat_cleanup_from_response_delegates(monkeypatch):
    fake = _RecordingFake(schedule_cleanup_from_response="c_123")
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    result = cw.schedule_remote_chat_cleanup_from_response(
        SimpleNamespace(cid="c_123"),
        retain_chat=True,
        delete_after_seconds=60,
        source="test",
    )
    assert result == "c_123"
    name, args, kwargs = fake.calls[0]
    assert name == "schedule_cleanup_from_response"
    assert args[0].cid == "c_123"
    assert kwargs == {
        "retain_chat": True,
        "delete_after_seconds": 60,
        "source": "test",
    }


def test_schedule_remote_chat_cleanup_delegates(monkeypatch):
    fake = _RecordingFake()
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    cw.schedule_remote_chat_cleanup("c_1", retain_chat=False, delete_after_seconds=10, source="src")
    name, args, kwargs = fake.calls[0]
    assert name == "schedule_cleanup"
    assert args == ("c_1",)
    assert kwargs == {"retain_chat": False, "delete_after_seconds": 10, "source": "src"}


def test_delete_remote_chat_with_explicit_client_skips_init(monkeypatch):
    fake = _RecordingFake(delete_chat=True, _async_methods={"delete_chat"})
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    client = SimpleNamespace()
    result = _run(cw.delete_remote_chat("c_1", client=client))
    assert result is True
    name, args, kwargs = fake.calls[0]
    assert name == "delete_chat"
    assert args == ("c_1",)
    assert kwargs == {"client": client}


def test_delete_remote_chat_with_none_client_fetches_and_inits(monkeypatch):
    fake = _RecordingFake(delete_chat=True, _async_methods={"delete_chat"})
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    init_calls = []

    async def fake_initialize():
        init_calls.append("init")
        return "inited"

    monkeypatch.setattr(cw, "initialize_client", fake_initialize)
    monkeypatch.setattr(cw, "get_gemini_client", lambda: "fetched-client")
    result = _run(cw.delete_remote_chat("c_1"))
    assert result is True
    assert init_calls == ["init"]
    name, args, kwargs = fake.calls[0]
    assert kwargs["client"] == "fetched-client"


def test_cleanup_due_remote_chats_with_explicit_client_skips_init(monkeypatch):
    fake = _RecordingFake(cleanup_due_chats=3, _async_methods={"cleanup_due_chats"})
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    client = SimpleNamespace()
    result = _run(cw.cleanup_due_remote_chats(client=client))
    assert result == 3
    name, args, kwargs = fake.calls[0]
    assert name == "cleanup_due_chats"
    assert kwargs == {"client": client}


def test_cleanup_due_remote_chats_with_none_client_fetches_and_inits(monkeypatch):
    fake = _RecordingFake(cleanup_due_chats=2, _async_methods={"cleanup_due_chats"})
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    init_calls = []

    async def fake_initialize():
        init_calls.append("init")
        return "inited"

    monkeypatch.setattr(cw, "initialize_client", fake_initialize)
    monkeypatch.setattr(cw, "get_gemini_client", lambda: "fetched-client")
    result = _run(cw.cleanup_due_remote_chats())
    assert result == 2
    assert init_calls == ["init"]
    name, args, kwargs = fake.calls[0]
    assert kwargs["client"] == "fetched-client"


def test_list_pending_remote_chat_cleanup_maps_cleanup_tasks(monkeypatch):
    now = 1234.5
    fake = _RecordingFake(
        list_pending_cleanup={
            "c_1": CleanupTask(delete_at=now, source="src1"),
            "c_2": CleanupTask(delete_at=now + 10, source="src2"),
        }
    )
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    result = cw.list_pending_remote_chat_cleanup()
    assert result == {
        "c_1": {"delete_at": now, "source": "src1"},
        "c_2": {"delete_at": now + 10, "source": "src2"},
    }


def test_list_pending_remote_chat_cleanup_empty(monkeypatch):
    fake = _RecordingFake(list_pending_cleanup={})
    monkeypatch.setattr(cw, "_cleanup_manager", fake)
    assert cw.list_pending_remote_chat_cleanup() == {}


# ---------------------------------------------------------------------------
# _on_cookie_update
# ---------------------------------------------------------------------------


def test_on_cookie_update_resets_client_and_sets_env(monkeypatch):
    reset_calls = []
    monkeypatch.setattr(cw, "reset_client", lambda: reset_calls.append("reset"))
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)

    cookie_data = SimpleNamespace(psid="psid-val", psidts="psidts-val")
    cw._on_cookie_update(cookie_data)

    assert reset_calls == ["reset"]
    assert os.environ["GEMINI_PSID"] == "psid-val"
    assert os.environ["GEMINI_PSIDTS"] == "psidts-val"


def test_on_cookie_update_skips_psidts_when_falsy(monkeypatch):
    reset_calls = []
    monkeypatch.setattr(cw, "reset_client", lambda: reset_calls.append("reset"))
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)

    cookie_data = SimpleNamespace(psid="psid-val", psidts="")
    cw._on_cookie_update(cookie_data)

    assert reset_calls == ["reset"]
    assert os.environ["GEMINI_PSID"] == "psid-val"
    assert "GEMINI_PSIDTS" not in os.environ


# ---------------------------------------------------------------------------
# init_cookie_manager_integration
# ---------------------------------------------------------------------------


def test_init_cookie_manager_integration_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", False)
    init_calls = []
    monkeypatch.setattr(cw, "init_cookie_manager", lambda **kw: init_calls.append(kw))
    cw.init_cookie_manager_integration()
    assert init_calls == []


def test_init_cookie_manager_integration_auto_refresh_true_default(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setenv("GEMINI_AUTO_REFRESH", "true")
    init_calls = []
    monitor = SimpleNamespace(start_monitor=lambda: init_calls.append("monitor"))
    monkeypatch.setattr(
        cw, "init_cookie_manager", lambda **kw: (init_calls.append(("init", kw)) or monitor)
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: monitor)
    cw.init_cookie_manager_integration()
    assert ("init", {"auto_refresh": True, "on_cookie_update": cw._on_cookie_update}) in init_calls
    assert "monitor" in init_calls


def test_init_cookie_manager_integration_auto_refresh_false_when_env_disabled(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setenv("GEMINI_AUTO_REFRESH", "false")
    init_calls = []
    monitor = SimpleNamespace(start_monitor=lambda: init_calls.append("monitor"))
    monkeypatch.setattr(
        cw, "init_cookie_manager", lambda **kw: (init_calls.append(("init", kw)) or monitor)
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: monitor)
    cw.init_cookie_manager_integration()
    assert ("init", {"auto_refresh": False, "on_cookie_update": cw._on_cookie_update}) in init_calls


# ---------------------------------------------------------------------------
# get_cookie_from_browser
# ---------------------------------------------------------------------------


def test_get_cookie_from_browser_returns_false_when_unavailable(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", False)
    assert cw.get_cookie_from_browser("chrome") is False


def test_get_cookie_from_browser_returns_false_when_no_psid(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    cache_calls = []
    monkeypatch.setattr(cw, "_prepare_browser_cookie_cache", lambda **kw: cache_calls.append(kw))
    cm = SimpleNamespace(
        get_cookies_from_browser=lambda browser, profile="": {"__Secure-1PSIDTS": "ts"},
        update_cookie=lambda *a, **kw: True,
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    result = cw.get_cookie_from_browser("chrome")
    assert result is False
    assert cache_calls == [{"force": True}]


def test_get_cookie_from_browser_updates_and_sets_env_on_success(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(cw, "_prepare_browser_cookie_cache", lambda **kw: None)
    update_calls = []

    def fake_update(psid, psidts, source="", extra_cookies=None):
        update_calls.append(
            {"psid": psid, "psidts": psidts, "source": source, "extra": extra_cookies}
        )
        return True

    cm = SimpleNamespace(
        get_cookies_from_browser=lambda browser, profile="": {
            "__Secure-1PSID": "psid-val",
            "__Secure-1PSIDTS": "psidts-val",
            "extra": "cookie",
        },
        update_cookie=fake_update,
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)

    result = cw.get_cookie_from_browser("chrome")
    assert result is True
    assert update_calls[0]["psid"] == "psid-val"
    assert update_calls[0]["psidts"] == "psidts-val"
    assert update_calls[0]["source"] == "browser_chrome"
    assert update_calls[0]["extra"] == {
        "__Secure-1PSID": "psid-val",
        "__Secure-1PSIDTS": "psidts-val",
        "extra": "cookie",
    }
    assert os.environ["GEMINI_PSID"] == "psid-val"
    assert os.environ["GEMINI_PSIDTS"] == "psidts-val"


def test_get_cookie_from_browser_returns_false_when_update_fails(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(cw, "_prepare_browser_cookie_cache", lambda **kw: None)
    cm = SimpleNamespace(
        get_cookies_from_browser=lambda browser, profile="": {
            "__Secure-1PSID": "psid-val",
            "__Secure-1PSIDTS": "psidts-val",
        },
        update_cookie=lambda *a, **kw: False,
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    result = cw.get_cookie_from_browser("chrome")
    assert result is False
    assert "GEMINI_PSID" not in os.environ


def test_get_cookie_from_browser_skips_psidts_env_when_empty(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(cw, "_prepare_browser_cookie_cache", lambda **kw: None)
    cm = SimpleNamespace(
        get_cookies_from_browser=lambda browser, profile="": {
            "__Secure-1PSID": "psid-val",
            "__Secure-1PSIDTS": "",
        },
        update_cookie=lambda *a, **kw: True,
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)
    result = cw.get_cookie_from_browser("chrome")
    assert result is True
    assert os.environ["GEMINI_PSID"] == "psid-val"
    assert "GEMINI_PSIDTS" not in os.environ


def test_get_cookie_from_browser_appends_profile_to_source(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(cw, "_prepare_browser_cookie_cache", lambda **kw: None)
    update_calls = []
    cm = SimpleNamespace(
        get_cookies_from_browser=lambda browser, profile="": {
            "__Secure-1PSID": "psid-val",
        },
        update_cookie=lambda *a, **kw: update_calls.append(kw) or True,
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)
    cw.get_cookie_from_browser("chrome", profile="Profile 1")
    assert update_calls[0]["source"] == "browser_chrome:Profile 1"


# ---------------------------------------------------------------------------
# list_browser_cookie_profiles
# ---------------------------------------------------------------------------


def test_list_browser_cookie_profiles_unavailable_returns_error_entry(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", False)
    result = cw.list_browser_cookie_profiles("firefox")
    assert result == [{"browser": "firefox", "error": "Cookie Manager unavailable"}]


def test_list_browser_cookie_profiles_validate_true_calls_cache(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    cache_calls = []
    monkeypatch.setattr(cw, "_prepare_browser_cookie_cache", lambda **kw: cache_calls.append(kw))
    expected = [{"browser": "chrome", "account_available": True}]
    cm = SimpleNamespace(list_browser_cookie_profiles=lambda browser, validate=True: expected)
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    result = cw.list_browser_cookie_profiles("chrome", validate=True)
    assert result is expected
    assert cache_calls == [{"force": True}]


def test_list_browser_cookie_profiles_validate_false_skips_cache(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    cache_calls = []
    monkeypatch.setattr(cw, "_prepare_browser_cookie_cache", lambda **kw: cache_calls.append(kw))
    expected = [{"browser": "chrome"}]
    cm = SimpleNamespace(list_browser_cookie_profiles=lambda browser, validate=True: expected)
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    result = cw.list_browser_cookie_profiles("chrome", validate=False)
    assert result is expected
    assert cache_calls == []


# ---------------------------------------------------------------------------
# get_cookie_status
# ---------------------------------------------------------------------------


def test_get_cookie_status_unavailable_returns_unavailable_dict(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", False)
    result = cw.get_cookie_status()
    assert result == {"available": False, "message": "Cookie Manager 不可用"}


def test_get_cookie_status_available_expands_status_and_info(monkeypatch):
    monkeypatch.setattr(cw, "COOKIE_MANAGER_AVAILABLE", True)
    status_obj = SimpleNamespace(value="VALID")
    cm = SimpleNamespace(
        get_cookie_status=lambda: (status_obj, {"source": "browser", "acquired_at": 99})
    )
    monkeypatch.setattr(cw, "get_cookie_manager", lambda: cm)
    result = cw.get_cookie_status()
    assert result == {
        "available": True,
        "status": "VALID",
        "source": "browser",
        "acquired_at": 99,
    }
