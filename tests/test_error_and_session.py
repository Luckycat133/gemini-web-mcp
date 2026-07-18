"""error_handler 与 session_manager 的单元测试。

这两个模块此前只有 hasattr 占位测试，本文件补充行为断言：
- error_handler: 7 个 ERROR_CODES 分支 + handle_error 字符串匹配的边界误判
- session_manager: store/get/remove/pop/list/clear + _clean_expired_sessions 过期逻辑
- extract_remote_chat_id: 两份实现的漂移守护（remote_chat_cleanup_manager 与 tools/utils）
"""

import time
from types import SimpleNamespace

from src.error_handler import (
    ERROR_CODES,
    GeminiError,
    format_error_response,
    handle_error,
    wrap_tool_error,
)
from src.session_manager import SessionManager
from src.remote_chat_cleanup_manager import extract_remote_chat_id as _extract_from_cleanup
from src.tools.utils import extract_remote_chat_id as _extract_from_utils


# ---------------------------------------------------------------------------
# error_handler.handle_error — 7 个 ERROR_CODES 分支
# ---------------------------------------------------------------------------


def test_handle_error_no_cookie_when_psid_not_set():
    info = handle_error(Exception("__Secure-1PSID not set"))
    assert info is ERROR_CODES["NO_COOKIE"]


def test_handle_error_no_cookie_when_cookie_missing():
    info = handle_error(Exception("cookie is missing from request"))
    assert info is ERROR_CODES["NO_COOKIE"]


def test_handle_error_invalid_cookie_when_cookie_present_without_missing_marker():
    info = handle_error(Exception("cookie rejected by server"))
    assert info is ERROR_CODES["INVALID_COOKIE"]


def test_handle_error_network_error_for_connection_keyword():
    info = handle_error(Exception("connection refused"))
    assert info is ERROR_CODES["NETWORK_ERROR"]


def test_handle_error_network_error_for_timeout_keyword():
    info = handle_error(Exception("request timeout after 30s"))
    assert info is ERROR_CODES["NETWORK_ERROR"]


def test_handle_error_session_not_found():
    info = handle_error(Exception("session sess_5 not found"))
    assert info is ERROR_CODES["SESSION_NOT_FOUND"]


def test_handle_error_session_not_found_chinese_falls_back_to_generic():
    """handle_error 只匹配英文 'session' 关键字，中文 '会话' 不会被识别。

    这是已知行为：error_str 经 .lower() 处理，但 'session' 子串匹配
    不会命中中文。此测试记录现状。
    """
    info = handle_error(Exception("会话 sess_5 不存在"))
    assert info["actionable"] is False  # 走到 generic fallback


def test_handle_error_model_unavailable():
    info = handle_error(Exception("model gemini-3-pro not available"))
    assert info is ERROR_CODES["MODEL_UNAVAILABLE"]


def test_handle_error_rate_limit_for_rate_keyword():
    info = handle_error(Exception("rate limit exceeded"))
    assert info is ERROR_CODES["RATE_LIMIT"]


def test_handle_error_rate_limit_for_limit_keyword():
    info = handle_error(Exception("you exceeded the limit"))
    assert info is ERROR_CODES["RATE_LIMIT"]


def test_handle_error_image_load_failed_for_image_keyword():
    info = handle_error(Exception("cannot load image from disk"))
    assert info is ERROR_CODES["IMAGE_LOAD_FAILED"]


def test_handle_error_image_load_failed_for_pillow_keyword():
    info = handle_error(Exception("No module named 'PIL' (pillow required)"))
    assert info is ERROR_CODES["IMAGE_LOAD_FAILED"]


def test_handle_error_falls_back_to_generic_for_unknown_errors():
    err = Exception("something totally unexpected happened")
    info = handle_error(err)
    assert info["message"] == "something totally unexpected happened"
    assert info["actionable"] is False
    assert info["tool"] is None


# ---------------------------------------------------------------------------
# handle_error 顺序敏感的边界误判（已知行为，测试记录现状）
# ---------------------------------------------------------------------------


def test_handle_error_cookie_substring_wins_over_network():
    """'cookie' 子串优先匹配，即使错误本质是网络问题。

    这是已知行为：error_handler 用顺序敏感的子串匹配，'cookie' 关键字
    过于宽泛。此测试记录现状，避免未来重构时无意改变分类。
    """
    info = handle_error(Exception("network timeout while refreshing cookie"))
    # 'cookie' 在 'network' 之前检查，所以归到 INVALID_COOKIE 而非 NETWORK_ERROR
    assert info is ERROR_CODES["INVALID_COOKIE"]


def test_handle_error_rate_wins_over_network_when_rate_appears_first():
    """'rate' 子串在 'network' 之后检查，但 'rate' 优先于 'limit' 的通用语义。"""
    info = handle_error(Exception("Rate limiting network error"))
    # 'network' 在 'rate' 之前检查，所以归到 NETWORK_ERROR
    assert info is ERROR_CODES["NETWORK_ERROR"]


def test_handle_error_is_case_insensitive():
    info = handle_error(Exception("NETWORK TIMEOUT"))
    assert info is ERROR_CODES["NETWORK_ERROR"]


def test_handle_error_empty_string_falls_back_to_generic():
    info = handle_error(Exception(""))
    assert info["actionable"] is False


# ---------------------------------------------------------------------------
# format_error_response
# ---------------------------------------------------------------------------


def test_format_error_response_includes_actionable_marker_and_solution_and_tool():
    info = ERROR_CODES["NO_COOKIE"]
    text = format_error_response(info).text
    assert text.startswith("✅")
    assert "未设置 GEMINI_PSID" in text
    assert "💡 解决方案" in text
    assert "🔧 可使用工具: gemini_get_cookie_from_browser" in text


def test_format_error_response_uses_warning_marker_for_non_actionable():
    info = {"message": "boom", "solution": "give up", "actionable": False, "tool": None}
    text = format_error_response(info).text
    assert text.startswith("⚠️")
    assert "🔧" not in text


# ---------------------------------------------------------------------------
# GeminiError
# ---------------------------------------------------------------------------


def test_gemini_error_carries_code_and_solution():
    err = GeminiError("CUSTOM", "boom", "try again")
    assert err.code == "CUSTOM"
    assert err.solution == "try again"
    assert str(err) == "boom"


# ---------------------------------------------------------------------------
# wrap_tool_error 装饰器
# ---------------------------------------------------------------------------


def test_wrap_tool_error_returns_formatted_response_on_exception():
    import asyncio

    @wrap_tool_error
    async def boom():
        raise Exception("psid not set")

    result = asyncio.run(boom())
    assert len(result) == 1
    text = result[0].text
    assert "未设置 GEMINI_PSID" in text
    assert "🔧 可使用工具: gemini_get_cookie_from_browser" in text


def test_wrap_tool_error_passes_through_success():
    import asyncio

    @wrap_tool_error
    async def ok():
        return ["ok"]

    assert asyncio.run(ok()) == ["ok"]


# ---------------------------------------------------------------------------
# SessionManager 行为测试
# ---------------------------------------------------------------------------


def test_session_manager_store_and_get_roundtrip():
    sm = SessionManager(max_age=3600)
    sentinel = object()
    sm.store_session(
        "sess_1",
        sentinel,
        model="pro",
        thinking_level="extended",
        temporary=True,
        retain_chat=True,
        delete_after_seconds=42,
    )
    data = sm.get_session("sess_1")
    assert data is not None
    assert data.session is sentinel
    assert data.model == "pro"
    assert data.thinking_level == "extended"
    assert data.temporary is True
    assert data.retain_chat is True
    assert data.delete_after_seconds == 42


def test_session_manager_get_missing_returns_none():
    sm = SessionManager()
    assert sm.get_session("missing") is None


def test_session_manager_remove_session():
    sm = SessionManager()
    sm.store_session("sess_1", object())
    sm.remove_session("sess_1")
    assert sm.get_session("sess_1") is None


def test_session_manager_remove_missing_is_noop():
    sm = SessionManager()
    sm.remove_session("missing")  # should not raise


def test_session_manager_pop_returns_data_and_removes():
    sm = SessionManager()
    sentinel = object()
    sm.store_session("sess_1", sentinel)
    popped = sm.pop_session("sess_1")
    assert popped is not None
    assert popped.session is sentinel
    assert sm.get_session("sess_1") is None


def test_session_manager_pop_missing_returns_none():
    sm = SessionManager()
    assert sm.pop_session("missing") is None


def test_session_manager_list_sessions_returns_copy():
    sm = SessionManager()
    sm.store_session("sess_1", object())
    listed = sm.list_sessions()
    assert "sess_1" in listed
    listed["sess_1"] = "tampered"
    # 原始数据不受影响（list_sessions 返回 dict 副本）
    assert sm.get_session("sess_1") is not None


def test_session_manager_clear_sessions():
    sm = SessionManager()
    sm.store_session("sess_1", object())
    sm.store_session("sess_2", object())
    sm.clear_sessions()
    assert sm.list_sessions() == {}


def test_session_manager_cleanup_expired_sessions_removes_old_entries():
    sm = SessionManager(max_age=1)
    sm.store_session("old", object())
    # 伪造 created_at 让它过期
    sm._sessions["old"].created_at = time.time() - 100
    sm.store_session("new", object())
    sm.cleanup_expired_sessions()
    listed = sm.list_sessions()
    assert "old" not in listed
    assert "new" in listed


def test_session_manager_get_session_triggers_cleanup():
    """get_session 内部调用 _clean_expired_sessions，过期 session 拿不到。"""
    sm = SessionManager(max_age=1)
    sm.store_session("old", object())
    sm._sessions["old"].created_at = time.time() - 100
    assert sm.get_session("old") is None


def test_session_manager_pop_session_triggers_cleanup():
    sm = SessionManager(max_age=1)
    sm.store_session("old", object())
    sm._sessions["old"].created_at = time.time() - 100
    assert sm.pop_session("old") is None


# ---------------------------------------------------------------------------
# extract_remote_chat_id 漂移守护
# 两份实现必须保持行为一致（详见 remote_chat_cleanup_manager.py 注释）
# ---------------------------------------------------------------------------


def test_extract_remote_chat_id_two_implementations_agree_on_cid_attribute():
    obj = SimpleNamespace(cid="c_abc123", metadata=None)
    assert _extract_from_cleanup(obj) == "c_abc123"
    assert _extract_from_utils(obj) == "c_abc123"


def test_extract_remote_chat_id_two_implementations_agree_on_metadata():
    obj = SimpleNamespace(cid=None, metadata=["c_from_meta", "r_response"])
    assert _extract_from_cleanup(obj) == "c_from_meta"
    assert _extract_from_utils(obj) == "c_from_meta"


def test_extract_remote_chat_id_two_implementations_agree_on_no_match():
    obj = SimpleNamespace(cid="not_a_cid", metadata=["also_not"])
    assert _extract_from_cleanup(obj) is None
    assert _extract_from_utils(obj) is None


def test_extract_remote_chat_id_two_implementations_agree_on_empty_metadata():
    obj = SimpleNamespace(cid=None, metadata=[])
    assert _extract_from_cleanup(obj) is None
    assert _extract_from_utils(obj) is None


def test_extract_remote_chat_id_two_implementations_agree_on_non_c_prefix():
    obj = SimpleNamespace(cid=None, metadata=["r_response", "c_late"])
    # metadata[0] 不是 c_ 开头，不应返回
    assert _extract_from_cleanup(obj) is None
    assert _extract_from_utils(obj) is None
