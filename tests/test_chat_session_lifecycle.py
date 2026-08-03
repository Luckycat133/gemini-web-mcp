"""chat 模块的会话行为测试。

调研发现以下工具此前仅有注解形状测试或仅 happy path 间接覆盖：

- gemini_reset_session（destructiveHint=True）：共享 reset_session 结果映射零覆盖
- gemini_list_sessions：空/非空渲染零覆盖
- gemini_send_message：temporary / learning_mode / retain_chat / delete_after_seconds
  四参的 "None 时从 session_data 回退、传入则覆盖" 逻辑零分支覆盖——这是多轮会话
  正确性的核心

本文件补充行为断言：

- gemini_reset_session:
  * session_id 不存在 → 显式返回 SESSION_NOT_FOUND
  * session 存在 → 仅委托共享 reset_session 并返回成功
- gemini_list_sessions:
  * 空会话列表 → 返回"暂无活跃会话"
  * 非空会话列表 → 返回"活跃会话:" + 每个会话的模型/保留状态描述
- gemini_send_message:
  * session 不存在 → 早退，不调 send_message / schedule cleanup
  * image_paths 无效 → 早退（在 session 检查前）
  * temporary=None 回退 session_data；显式传入则覆盖
  * learning_mode=None 回退 session_data；显式传入则覆盖；双 None 不写入 kwargs
  * retain_chat=None 回退 session_data；显式传入则覆盖
  * delete_after_seconds=None 回退 session_data；显式传入则覆盖；双 None 传 None
  * thinking_level 从 session_data 取；缺失回退 "standard"
  * schedule_remote_chat_cleanup 用 session.cid 作为第一参
  * 返回 response.text
"""

import asyncio

from src.adapters.mcp_sdk import MCPServer

import src.tools.chat as chat_tools
from src.session_manager import SessionData, SessionOperationResult

# ---------------------------------------------------------------------------
# 辅助：注册工具并返回可直接 await 的工具函数
# ---------------------------------------------------------------------------


def _register_chat_tools():
    """注册 chat 工具并返回工具函数字典。

    MCPServer 的 call_tool 路径较重，这里直接从 mcp._tool_manager 提取
    已注册的工具函数，便于直接 await 调用。
    """
    mcp = MCPServer("test")
    chat_tools.register_chat_tools(mcp)
    return mcp


async def _call_tool(mcp, name, **kwargs):
    """通过 mcp.call_tool 调用工具，返回 TextContent 列表。

    MCP SDK v2 的 call_tool 返回 CallToolResult，这里只取 content。
    """
    return (await mcp.call_tool(name, kwargs)).content


# ---------------------------------------------------------------------------
# gemini_reset_session
# ---------------------------------------------------------------------------


def test_reset_session_unknown_id_does_not_delete(monkeypatch):
    """共享服务报告不存在时，工具返回显式 SESSION_NOT_FOUND。"""
    reset_ids = []

    async def fake_reset(session_id):
        reset_ids.append(session_id)
        return SessionOperationResult.not_found()
    monkeypatch.setattr(chat_tools, "reset_session", fake_reset)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_reset_session", session_id="sess-unknown")

    result = asyncio.run(run())
    assert reset_ids == ["sess-unknown"]
    assert len(result) == 1
    assert "sess-unknown" in result[0].text
    assert "SESSION_NOT_FOUND" in result[0].text
    assert "已重置" not in result[0].text


def test_reset_session_delegates_single_id_to_shared_service(monkeypatch):
    """工具层不维护第二份状态，只把单个 ID 交给共享服务。"""
    reset_ids = []

    async def fake_reset(session_id):
        reset_ids.append(session_id)
        return SessionOperationResult.success(SessionData(session=object(), session_id=session_id))
    monkeypatch.setattr(chat_tools, "reset_session", fake_reset)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_reset_session", session_id="sess-1")

    result = asyncio.run(run())
    assert reset_ids == ["sess-1"]
    assert result[0].text == "✅ 会话 sess-1 已重置"


# ---------------------------------------------------------------------------
# gemini_list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_empty(monkeypatch):
    """空会话列表返回"暂无活跃会话"。"""
    monkeypatch.setattr(chat_tools, "list_sessions", lambda: {})

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_list_sessions")

    result = asyncio.run(run())
    assert len(result) == 1
    assert "暂无活跃会话" in result[0].text


def test_list_sessions_non_empty_renders_each(monkeypatch):
    """非空会话列表渲染每个会话的 id、模型和保留状态。"""
    sessions = {
        "sess-a": {"model": "fast", "retain_chat": False, "session": object()},
        "sess-b": {"model": "pro", "retain_chat": True, "session": object()},
    }
    monkeypatch.setattr(chat_tools, "list_sessions", lambda: sessions)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_list_sessions")

    result = asyncio.run(run())
    text = result[0].text
    assert text.startswith("活跃会话:")
    assert "sess-a" in text
    assert "sess-b" in text
    # retain_chat=False → "自动清理"，retain_chat=True → "保留"
    assert "自动清理" in text
    assert "保留" in text


# ---------------------------------------------------------------------------
# gemini_send_message — 参数回退与早退逻辑
# ---------------------------------------------------------------------------


class _FakeSession:
    """模拟 Gemini chat session，捕获 send_message 的 kwargs 与返回值。"""

    def __init__(self, *, cid="c_test123", response_text="reply"):
        self.cid = cid
        self._response_text = response_text
        self.captured_kwargs = None

    async def send_message(self, **kwargs):
        self.captured_kwargs = dict(kwargs)
        from types import SimpleNamespace
        return SimpleNamespace(text=self._response_text)


def _patch_send_message_env(monkeypatch, session_data, *, captured_schedule=None):
    """统一 patch gemini_send_message 的共享服务接缝。

    - chat_tools.lookup_session → 返回显式查找结果
    - chat_tools.send_session_message → 通过同一 SessionData 发送并返回结果
    - chat_tools.schedule_remote_chat_cleanup → 捕获入参到 captured_schedule 列表

    注意：gemini_send_message 不调 get_gemini_client / initialize_client /
    cleanup_due_remote_chats，故无需 patch。
    """
    if session_data is None:
        data = None
    else:
        data = SessionData(
            session=session_data["session"],
            session_id="s1",
            model=session_data.get("model", "flash"),
            thinking_level=session_data.get("thinking_level", "standard"),
            learning_mode=session_data.get("learning_mode"),
            temporary=session_data.get("temporary", False),
            retain_chat=session_data.get("retain_chat", False),
            delete_after_seconds=session_data.get("delete_after_seconds"),
        )

    def fake_lookup(_sid):
        if data is None:
            return SessionOperationResult.not_found()
        return SessionOperationResult.success(data)

    async def fake_send(_sid, **kwargs):
        if data is None:
            return SessionOperationResult.not_found()
        response = await data.session.send_message(**kwargs)
        return SessionOperationResult.success(data, response)

    monkeypatch.setattr(chat_tools, "lookup_session", fake_lookup)
    monkeypatch.setattr(chat_tools, "send_session_message", fake_send)

    def fake_schedule(cid, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append({
                "cid": cid,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })

    monkeypatch.setattr(chat_tools, "schedule_remote_chat_cleanup", fake_schedule)


def test_send_message_unknown_session_returns_early(monkeypatch):
    """session 不存在 → 返回 '❌ 会话 xxx 不存在'，不调 send_message / schedule。"""
    fake_session = _FakeSession()
    schedule_calls = []
    _patch_send_message_env(monkeypatch, None, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="nope", message="hi")

    result = asyncio.run(run())
    assert len(result) == 1
    assert "会话 nope 不存在" in result[0].text
    assert fake_session.captured_kwargs is None
    assert schedule_calls == []


def test_send_message_invalid_images_short_circuits_before_session_check(monkeypatch):
    """image_paths 无效 → 在 lookup_session 调用前早退。

    用一个会抛错的 lookup_session 证明它没被调用。
    """
    def explode(sid):
        raise AssertionError("lookup_session should not be called on invalid images")
    monkeypatch.setattr(chat_tools, "lookup_session", explode)
    monkeypatch.setattr(chat_tools, "schedule_remote_chat_cleanup",
                        lambda *a, **kw: None)

    mcp = _register_chat_tools()

    async def run():
        # 用一个不存在的文件路径触发 validate_image_paths 失败
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="x", message="hi",
                                image_paths=["/nonexistent/missing.png"])

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0].text.startswith("❌")


def test_send_message_temporary_none_falls_back_to_session_data(monkeypatch):
    """temporary=None + session temporary=True → request_kwargs['temporary']=True。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "temporary": True, "thinking_level": "standard"}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")  # temporary 默认 None

    asyncio.run(run())
    assert fake_session.captured_kwargs["temporary"] is True


def test_send_message_temporary_explicit_overrides_session_data(monkeypatch):
    """temporary=False 显式传入 + session temporary=True → request_kwargs['temporary']=False。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "temporary": True, "thinking_level": "standard"}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi", temporary=False)

    asyncio.run(run())
    assert fake_session.captured_kwargs["temporary"] is False


def test_send_message_temporary_none_defaults_false_when_session_missing(monkeypatch):
    """temporary=None + session 无 temporary key → 回退到 False。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "thinking_level": "standard"}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert fake_session.captured_kwargs["temporary"] is False


def test_send_message_learning_mode_none_falls_back_to_session_data(monkeypatch):
    """learning_mode=None + session learning_mode='flashcards' → kwargs 含 flashcards。"""
    fake_session = _FakeSession()
    session_data = {
        "session": fake_session, "thinking_level": "standard",
        "learning_mode": "flashcards",
    }
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert fake_session.captured_kwargs["learning_mode"] == "flashcards"


def test_send_message_learning_mode_explicit_overrides_session_data(monkeypatch):
    """learning_mode='quiz' 显式 + session learning_mode='flashcards' → kwargs 含 quiz。"""
    fake_session = _FakeSession()
    session_data = {
        "session": fake_session, "thinking_level": "standard",
        "learning_mode": "flashcards",
    }
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi", learning_mode="quiz")

    asyncio.run(run())
    assert fake_session.captured_kwargs["learning_mode"] == "quiz"


def test_send_message_learning_mode_double_none_omits_from_kwargs(monkeypatch):
    """learning_mode=None + session 无 learning_mode → kwargs 不含 learning_mode 键。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "thinking_level": "standard"}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert "learning_mode" not in fake_session.captured_kwargs


def test_send_message_retain_chat_none_falls_back_to_session_data(monkeypatch):
    """retain_chat=None + session retain_chat=True → schedule cleanup retain_chat=True。"""
    fake_session = _FakeSession()
    session_data = {
        "session": fake_session, "thinking_level": "standard",
        "retain_chat": True,
    }
    schedule_calls = []
    _patch_send_message_env(monkeypatch, session_data, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert schedule_calls[0]["retain_chat"] is True


def test_send_message_retain_chat_explicit_overrides_session_data(monkeypatch):
    """retain_chat=False 显式 + session retain_chat=True → schedule cleanup retain_chat=False。"""
    fake_session = _FakeSession()
    session_data = {
        "session": fake_session, "thinking_level": "standard",
        "retain_chat": True,
    }
    schedule_calls = []
    _patch_send_message_env(monkeypatch, session_data, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi", retain_chat=False)

    asyncio.run(run())
    assert schedule_calls[0]["retain_chat"] is False


def test_send_message_retain_chat_none_defaults_false_when_session_missing(monkeypatch):
    """retain_chat=None + session 无 retain_chat key → schedule cleanup retain_chat=False。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "thinking_level": "standard"}
    schedule_calls = []
    _patch_send_message_env(monkeypatch, session_data, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert schedule_calls[0]["retain_chat"] is False


def test_send_message_delete_after_seconds_none_falls_back_to_session_data(monkeypatch):
    """delete_after_seconds=None + session delete_after_seconds=600 → schedule cleanup 600。"""
    fake_session = _FakeSession()
    session_data = {
        "session": fake_session, "thinking_level": "standard",
        "delete_after_seconds": 600,
    }
    schedule_calls = []
    _patch_send_message_env(monkeypatch, session_data, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert schedule_calls[0]["delete_after_seconds"] == 600


def test_send_message_delete_after_seconds_explicit_overrides_session_data(monkeypatch):
    """delete_after_seconds=300 显式 + session delete_after_seconds=600 → schedule cleanup 300。"""
    fake_session = _FakeSession()
    session_data = {
        "session": fake_session, "thinking_level": "standard",
        "delete_after_seconds": 600,
    }
    schedule_calls = []
    _patch_send_message_env(monkeypatch, session_data, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi", delete_after_seconds=300)

    asyncio.run(run())
    assert schedule_calls[0]["delete_after_seconds"] == 300


def test_send_message_delete_after_seconds_double_none_passes_none(monkeypatch):
    """delete_after_seconds=None + session 无该 key → schedule cleanup delete_after_seconds=None。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "thinking_level": "standard"}
    schedule_calls = []
    _patch_send_message_env(monkeypatch, session_data, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert schedule_calls[0]["delete_after_seconds"] is None


def test_send_message_thinking_level_taken_from_session_data(monkeypatch):
    """thinking_level 不接收参数，始终从 session_data 取。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "thinking_level": "extended"}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert fake_session.captured_kwargs["thinking_level"] == "extended"


def test_send_message_thinking_level_defaults_standard_when_session_missing(monkeypatch):
    """session 无 thinking_level key → 回退到 'standard'。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert fake_session.captured_kwargs["thinking_level"] == "standard"


def test_send_message_schedule_uses_session_cid(monkeypatch):
    """schedule_remote_chat_cleanup 第一参取自 session.cid 属性。"""
    fake_session = _FakeSession(cid="c_unique456")
    session_data = {"session": fake_session, "thinking_level": "standard"}
    schedule_calls = []
    _patch_send_message_env(monkeypatch, session_data, captured_schedule=schedule_calls)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert schedule_calls[0]["cid"] == "c_unique456"
    assert schedule_calls[0]["source"] == "gemini_send_message"


def test_send_message_returns_response_text(monkeypatch):
    """工具返回值为 session.send_message 返回的 response.text。"""
    fake_session = _FakeSession(response_text="hello from model")
    session_data = {"session": fake_session, "thinking_level": "standard"}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="hi")

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0].text == "hello from model"


def test_send_message_passes_prompt_and_files_to_send_message(monkeypatch):
    """request_kwargs 含 prompt 与 files 字段。"""
    fake_session = _FakeSession()
    session_data = {"session": fake_session, "thinking_level": "standard"}
    _patch_send_message_env(monkeypatch, session_data)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_send_message",
                                session_id="s1", message="what is 2+2")

    asyncio.run(run())
    assert fake_session.captured_kwargs["prompt"] == "what is 2+2"
    # image_paths=None → safe_image_paths=[] → files=None
    assert fake_session.captured_kwargs["files"] is None
