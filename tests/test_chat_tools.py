"""chat 模块的 gemini_chat 与 gemini_start_chat 入口工具行为测试。

调研发现这两个工具此前仅有 happy path 间接覆盖（test_tool_workflows.py
用 FakeClient 走过），但关键的行为契约零断言：

- gemini_chat：request_kwargs 字段注入（prompt/files/model/thinking_level/
  gem/temporary/learning_mode 条件注入）、schedule_remote_chat_cleanup_from_response
  入参（retain_chat/delete_after_seconds/source）、parse_response 用 model 解析
- gemini_start_chat：store_session 入参（session_id 长度/model/thinking_level/
  learning_mode/temporary/retain_chat/delete_after_seconds）、client.start_chat
  接收 model_name 与 gem、返回文本含 session_id 与 model_name

mock 边界：gemini_chat / gemini_start_chat 都调用 get_gemini_client /
initialize_client / cleanup_due_remote_chats，需 patch 这 3 个接缝 + 工具特有
的 schedule_remote_chat_cleanup_from_response / store_session。parse_response
走真实实现（不 mock），用 SimpleNamespace 构造 response。
"""

import asyncio
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

import src.tools.chat as chat_tools


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _call_tool(mcp, name, **kwargs):
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


class _FakeChatClient:
    """模拟 Gemini 客户端，捕获 generate_content / start_chat 入参。"""

    def __init__(self, *, response_text="model reply", response_cid="c_chat1"):
        self._response_text = response_text
        self._response_cid = response_cid
        self.captured_generate_kwargs = None
        self.captured_start_chat_kwargs = None
        self.last_response = None

    async def generate_content(self, **kwargs):
        self.captured_generate_kwargs = dict(kwargs)
        response = SimpleNamespace(
            text=self._response_text,
            images=[],
            videos=[],
            media=[],
            metadata=[self._response_cid, "r_response"],
        )
        self.last_response = response
        return response

    def start_chat(self, model=None, gem=None):
        self.captured_start_chat_kwargs = {"model": model, "gem": gem}
        return SimpleNamespace(cid="c_session1")


def _patch_chat_client_env(monkeypatch, client, *, captured_schedule=None,
                           captured_store=None, captured_cleanup=None):
    """统一 patch gemini_chat / gemini_start_chat 的外部接缝。"""
    monkeypatch.setattr(chat_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None
    monkeypatch.setattr(chat_tools, "initialize_client", fake_init)

    async def fake_cleanup(client_arg):
        if captured_cleanup is not None:
            captured_cleanup.append(client_arg)
    monkeypatch.setattr(chat_tools, "cleanup_due_remote_chats", fake_cleanup)

    def fake_schedule_from_response(response, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append({
                "response": response,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })
    monkeypatch.setattr(chat_tools, "schedule_remote_chat_cleanup_from_response",
                        fake_schedule_from_response)

    def fake_store(session_id, session, model, **kwargs):
        if captured_store is not None:
            captured_store.append({
                "session_id": session_id,
                "session": session,
                "model": model,
                **kwargs,
            })
    monkeypatch.setattr(chat_tools, "store_session", fake_store)


def _make_mcp():
    mcp = FastMCP("test")
    chat_tools.register_chat_tools(mcp)
    return mcp


# ---------------------------------------------------------------------------
# gemini_chat — request_kwargs 字段注入
# ---------------------------------------------------------------------------


def test_chat_invalid_images_returns_error_before_client_init(monkeypatch):
    """image_paths 无效 → 在 get_gemini_client 调用前早退。"""
    def explode():
        raise AssertionError("get_gemini_client should not be called on invalid images")
    monkeypatch.setattr(chat_tools, "get_gemini_client", explode)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat",
                                message="hi", image_paths=["/nonexistent/x.png"])

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0].text.startswith("❌")


def test_chat_happy_path_passes_all_fields_to_generate_content(monkeypatch):
    """request_kwargs 含 prompt/files/model/thinking_level/gem/temporary。"""
    client = _FakeChatClient()
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat",
                                message="hello", model="flash", thinking_level="extended",
                                gem_id="g_123", temporary=True)

    asyncio.run(run())
    kwargs = client.captured_generate_kwargs
    assert kwargs["prompt"] == "hello"
    assert kwargs["model"] == "gemini-3-flash"  # resolve_model_name("flash")
    assert kwargs["thinking_level"] == "extended"
    assert kwargs["gem"] == "g_123"
    assert kwargs["temporary"] is True
    # image_paths=None → safe_image_paths=[] → files=None
    assert kwargs["files"] is None


def test_chat_resolves_model_alias_to_runtime_name(monkeypatch):
    """model alias 经 resolve_model_name 解析后传给 client。"""
    client = _FakeChatClient()
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat", message="hi", model="pro")

    asyncio.run(run())
    assert client.captured_generate_kwargs["model"] == "gemini-3-pro"


def test_chat_omits_learning_mode_when_none(monkeypatch):
    """learning_mode=None → request_kwargs 不含 learning_mode 键。"""
    client = _FakeChatClient()
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat", message="hi")  # learning_mode 默认 None

    asyncio.run(run())
    assert "learning_mode" not in client.captured_generate_kwargs


def test_chat_includes_learning_mode_when_truthy(monkeypatch):
    """learning_mode='flashcards' → request_kwargs 含 flashcards。"""
    client = _FakeChatClient()
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat", message="hi", learning_mode="flashcards")

    asyncio.run(run())
    assert client.captured_generate_kwargs["learning_mode"] == "flashcards"


def test_chat_calls_cleanup_due_remote_chats_with_client(monkeypatch):
    """cleanup_due_remote_chats 接收 get_gemini_client 返回的 client 对象。"""
    client = _FakeChatClient()
    captured_cleanup = []
    _patch_chat_client_env(monkeypatch, client, captured_cleanup=captured_cleanup)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat", message="hi")

    asyncio.run(run())
    assert captured_cleanup == [client]


def test_chat_schedules_cleanup_with_correct_args(monkeypatch):
    """schedule_remote_chat_cleanup_from_response 接收 response + retain/delete/source。"""
    client = _FakeChatClient()
    schedule_calls = []
    _patch_chat_client_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat", message="hi",
                                retain_chat=True, delete_after_seconds=300)

    asyncio.run(run())
    assert len(schedule_calls) == 1
    call = schedule_calls[0]
    assert call["response"] is client.last_response  # generate_content 返回的同一对象
    assert call["retain_chat"] is True
    assert call["delete_after_seconds"] == 300
    assert call["source"] == "gemini_chat"


def test_chat_returns_parsed_response_with_model(monkeypatch):
    """返回值经 parse_response(response, model) 处理，含 response.text 与 remote_chat_id。"""
    client = _FakeChatClient(response_text="answer from model", response_cid="c_parsed1")
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat", message="hi", model="pro")

    result = asyncio.run(run())
    text = result[0].text
    assert "answer from model" in text
    # parse_response 会从 metadata[0] 提取 remote_chat_id 并追加
    assert "Remote chat ID: c_parsed1" in text


# ---------------------------------------------------------------------------
# gemini_start_chat — session 创建与 store_session 入参
# ---------------------------------------------------------------------------


def test_start_chat_passes_model_name_and_gem_to_client(monkeypatch):
    """client.start_chat 接收 resolve_model_name 后的 model_name 与 gem_id。"""
    client = _FakeChatClient()
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_start_chat", model="pro", gem_id="g_abc")

    asyncio.run(run())
    assert client.captured_start_chat_kwargs["model"] == "gemini-3-pro"
    assert client.captured_start_chat_kwargs["gem"] == "g_abc"


def test_start_chat_stores_session_with_all_params(monkeypatch):
    """store_session 接收 session_id / session / model + 所有会话配置参数。"""
    client = _FakeChatClient()
    store_calls = []
    _patch_chat_client_env(monkeypatch, client, captured_store=store_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_start_chat",
                                model="flash", thinking_level="extended",
                                learning_mode="quiz", temporary=True,
                                retain_chat=True, delete_after_seconds=600)

    asyncio.run(run())
    assert len(store_calls) == 1
    call = store_calls[0]
    assert len(call["session_id"]) == 8  # uuid[:8]
    assert call["session"] is not None  # 由 client.start_chat 返回
    assert call["model"] == "flash"  # store_session 收到的是原始 alias（不是 model_name）
    assert call["thinking_level"] == "extended"
    assert call["learning_mode"] == "quiz"
    assert call["temporary"] is True
    assert call["retain_chat"] is True
    assert call["delete_after_seconds"] == 600


def test_start_chat_stores_none_learning_mode_when_omitted(monkeypatch):
    """learning_mode 默认 None → store_session 收到 learning_mode=None。"""
    client = _FakeChatClient()
    store_calls = []
    _patch_chat_client_env(monkeypatch, client, captured_store=store_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_start_chat", model="flash")

    asyncio.run(run())
    assert store_calls[0]["learning_mode"] is None
    assert store_calls[0]["temporary"] is False  # 默认
    assert store_calls[0]["retain_chat"] is False  # 默认
    assert store_calls[0]["delete_after_seconds"] is None  # 默认


def test_start_chat_returns_text_with_session_id_and_model_name(monkeypatch):
    """返回文本含 '会话创建成功'、session_id、model_name。"""
    client = _FakeChatClient()
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_start_chat", model="pro")

    result = asyncio.run(run())
    text = result[0].text
    assert "会话创建成功" in text
    assert "gemini-3-pro" in text
    assert "使用 gemini_send_message 继续对话" in text
    # session_id 是 8 字符 hex
    assert "ID:" in text
    id_line = [line for line in text.split("\n") if line.startswith("ID:")][0]
    session_id = id_line.split(":", 1)[1].strip()
    assert len(session_id) == 8
    assert all(c in "0123456789abcdef" for c in session_id)


def test_start_chat_calls_cleanup_due_remote_chats_with_client(monkeypatch):
    """start_chat 也调用 cleanup_due_remote_chats(client)。"""
    client = _FakeChatClient()
    captured_cleanup = []
    _patch_chat_client_env(monkeypatch, client, captured_cleanup=captured_cleanup)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_start_chat", model="flash")

    asyncio.run(run())
    assert captured_cleanup == [client]


def test_start_chat_does_not_schedule_remote_chat_cleanup(monkeypatch):
    """start_chat 不调 schedule_remote_chat_cleanup_from_response（无 response）。"""
    client = _FakeChatClient()
    schedule_calls = []
    _patch_chat_client_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_start_chat", model="flash")

    asyncio.run(run())
    assert schedule_calls == []


# ---------------------------------------------------------------------------
# gemini_chat_stream — 流式对话
# ---------------------------------------------------------------------------


class _FakeStreamClient(_FakeChatClient):
    """扩展 _FakeChatClient 支持流式生成（async iter）。"""

    def __init__(self, *, chunks=None, response_text="model reply", response_cid="c_chat1"):
        super().__init__(response_text=response_text, response_cid=response_cid)
        self._chunks = chunks if chunks is not None else []

    async def generate_content_stream(self, **kwargs):
        self.captured_generate_kwargs = dict(kwargs)
        for chunk in self._chunks:
            yield chunk


def _stream_chunk(text_delta="", text=""):
    """构造流式 chunk：优先用 text_delta，缺失时 get_stream_text_piece 回退到 text。"""
    from types import SimpleNamespace
    if text_delta:
        return SimpleNamespace(text_delta=text_delta, text=text)
    return SimpleNamespace(text=text)


def test_chat_stream_invalid_images_returns_error_before_client(monkeypatch):
    """image_paths 无效 → 在 get_gemini_client 调用前早退。"""
    def explode():
        raise AssertionError("get_gemini_client should not be called on invalid images")
    monkeypatch.setattr(chat_tools, "get_gemini_client", explode)
    monkeypatch.setattr(chat_tools, "schedule_remote_chat_cleanup_from_response",
                        lambda *a, **kw: None)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat_stream",
                                message="hi", image_paths=["/nonexistent/x.png"])

    result = asyncio.run(run())
    assert result[0].text.startswith("❌")


def test_chat_stream_empty_stream_returns_empty_text_and_skips_cleanup(monkeypatch):
    """空流（无 chunk）→ final_response=None → 不调 cleanup，返回空文本。"""
    client = _FakeStreamClient(chunks=[])
    schedule_calls = []
    _patch_chat_client_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat_stream", message="hi")

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0].text == ""  # full_text 为空
    assert schedule_calls == []  # final_response is None → 跳过 cleanup


def test_chat_stream_accumulates_text_delta_across_chunks(monkeypatch):
    """多 chunk → full_text 累加 text_delta，返回拼合文本。"""
    client = _FakeStreamClient(chunks=[
        _stream_chunk(text_delta="Hello"),
        _stream_chunk(text_delta=", "),
        _stream_chunk(text_delta="world!"),
    ])
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat_stream", message="hi")

    result = asyncio.run(run())
    assert result[0].text == "Hello, world!"


def test_chat_stream_schedules_cleanup_with_final_response(monkeypatch):
    """有 chunk → schedule cleanup 用最后一个 response + retain/delete/source。"""
    final_chunk = _stream_chunk(text_delta="last")
    client = _FakeStreamClient(chunks=[_stream_chunk(text_delta="first"), final_chunk])
    schedule_calls = []
    _patch_chat_client_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat_stream", message="hi",
                                retain_chat=True, delete_after_seconds=120)

    asyncio.run(run())
    assert len(schedule_calls) == 1
    call = schedule_calls[0]
    assert call["response"] is final_chunk
    assert call["retain_chat"] is True
    assert call["delete_after_seconds"] == 120
    assert call["source"] == "gemini_chat_stream"


def test_chat_stream_passes_all_fields_to_generate_content_stream(monkeypatch):
    """request_kwargs 含 prompt/files/model/thinking_level/gem/temporary。"""
    client = _FakeStreamClient(chunks=[_stream_chunk(text_delta="x")])
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat_stream",
                                message="hi", model="pro", thinking_level="extended",
                                gem_id="g_1", temporary=True)

    asyncio.run(run())
    kwargs = client.captured_generate_kwargs
    assert kwargs["prompt"] == "hi"
    assert kwargs["model"] == "gemini-3-pro"
    assert kwargs["thinking_level"] == "extended"
    assert kwargs["gem"] == "g_1"
    assert kwargs["temporary"] is True
    assert kwargs["files"] is None


def test_chat_stream_omits_learning_mode_when_none(monkeypatch):
    """learning_mode=None → request_kwargs 不含 learning_mode 键。"""
    client = _FakeStreamClient(chunks=[_stream_chunk(text_delta="x")])
    _patch_chat_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_chat_stream", message="hi")

    asyncio.run(run())
    assert "learning_mode" not in client.captured_generate_kwargs


# ---------------------------------------------------------------------------
# gemini_send_message_stream — 会话流式消息
# ---------------------------------------------------------------------------


class _FakeStreamSession:
    """模拟 chat session 的流式 send_message_stream（async iter）。"""

    def __init__(self, *, cid="c_stream1", chunks=None):
        self.cid = cid
        self._chunks = chunks if chunks is not None else []
        self.captured_kwargs = None

    async def send_message_stream(self, **kwargs):
        self.captured_kwargs = dict(kwargs)
        for chunk in self._chunks:
            yield chunk


def _patch_stream_session_env(monkeypatch, session_data, *, captured_schedule=None):
    """patch gemini_send_message_stream 的 2 个接缝（与 send_message 同构）。"""
    monkeypatch.setattr(chat_tools, "get_session", lambda sid: session_data)

    def fake_schedule(response, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append({
                "response": response,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })
    monkeypatch.setattr(chat_tools, "schedule_remote_chat_cleanup_from_response", fake_schedule)


def test_send_message_stream_unknown_session_returns_early(monkeypatch):
    """session 不存在 → 早退，不调 send_message_stream / schedule。"""
    fake_session = _FakeStreamSession()
    schedule_calls = []
    _patch_stream_session_env(monkeypatch, None, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_send_message_stream",
                                session_id="nope", message="hi")

    result = asyncio.run(run())
    assert "会话 nope 不存在" in result[0].text
    assert fake_session.captured_kwargs is None
    assert schedule_calls == []


def test_send_message_stream_invalid_images_short_circuits_before_session(monkeypatch):
    """image_paths 无效 → 在 get_session 调用前早退。"""
    def explode(sid):
        raise AssertionError("get_session should not be called on invalid images")
    monkeypatch.setattr(chat_tools, "get_session", explode)
    monkeypatch.setattr(chat_tools, "schedule_remote_chat_cleanup_from_response",
                        lambda *a, **kw: None)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_send_message_stream",
                                session_id="x", message="hi",
                                image_paths=["/nonexistent/missing.png"])

    result = asyncio.run(run())
    assert result[0].text.startswith("❌")


def test_send_message_stream_accumulates_text_delta(monkeypatch):
    """多 chunk → full_text 累加 text_delta，返回拼合文本。"""
    fake_session = _FakeStreamSession(chunks=[
        _stream_chunk(text_delta="partial "),
        _stream_chunk(text_delta="reply"),
    ])
    _patch_stream_session_env(monkeypatch, {"session": fake_session, "thinking_level": "standard"})

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_send_message_stream",
                                session_id="s1", message="hi")

    result = asyncio.run(run())
    assert result[0].text == "partial reply"


def test_send_message_stream_empty_stream_still_schedules_cleanup_with_none(monkeypatch):
    """空流（无 chunk）→ final_response=None → 仍调 cleanup（传 None，文档化当前行为）。

    注意：这与 gemini_chat_stream 不同——chat_stream 用 `if final_response:` 守卫，
    send_message_stream 无守卫，总是调用。这是一个潜在的不一致，本测试锁定当前行为。
    """
    fake_session = _FakeStreamSession(chunks=[])
    schedule_calls = []
    _patch_stream_session_env(
        monkeypatch,
        {"session": fake_session, "thinking_level": "standard"},
        captured_schedule=schedule_calls,
    )

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_send_message_stream",
                                session_id="s1", message="hi")

    result = asyncio.run(run())
    assert result[0].text == ""  # full_text 为空
    assert len(schedule_calls) == 1
    assert schedule_calls[0]["response"] is None  # 文档化：空流传 None
    assert schedule_calls[0]["source"] == "gemini_send_message_stream"


def test_send_message_stream_schedules_cleanup_with_final_response(monkeypatch):
    """有 chunk → schedule cleanup 用最后一个 response。"""
    final_chunk = _stream_chunk(text_delta="end")
    fake_session = _FakeStreamSession(chunks=[_stream_chunk(text_delta="start"), final_chunk])
    schedule_calls = []
    _patch_stream_session_env(
        monkeypatch,
        {"session": fake_session, "thinking_level": "standard"},
        captured_schedule=schedule_calls,
    )

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_send_message_stream",
                                session_id="s1", message="hi",
                                retain_chat=True, delete_after_seconds=60)

    asyncio.run(run())
    assert schedule_calls[0]["response"] is final_chunk
    assert schedule_calls[0]["retain_chat"] is True
    assert schedule_calls[0]["delete_after_seconds"] == 60


def test_send_message_stream_temporary_falls_back_to_session(monkeypatch):
    """temporary=None + session temporary=True → kwargs['temporary']=True。"""
    fake_session = _FakeStreamSession(chunks=[_stream_chunk(text_delta="x")])
    _patch_stream_session_env(
        monkeypatch,
        {"session": fake_session, "temporary": True, "thinking_level": "standard"},
    )

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_send_message_stream",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert fake_session.captured_kwargs["temporary"] is True


def test_send_message_stream_learning_mode_double_none_omits(monkeypatch):
    """learning_mode=None + session 无 → kwargs 不含 learning_mode。"""
    fake_session = _FakeStreamSession(chunks=[_stream_chunk(text_delta="x")])
    _patch_stream_session_env(
        monkeypatch,
        {"session": fake_session, "thinking_level": "standard"},
    )

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_send_message_stream",
                                session_id="s1", message="hi")

    asyncio.run(run())
    assert "learning_mode" not in fake_session.captured_kwargs
