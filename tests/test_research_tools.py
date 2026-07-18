"""research 模块的 gemini_deep_research 行为契约测试。

调研发现 gemini_deep_research 此前在 test_tool_workflows.py 有 9 个间接用例
（覆盖 native happy path / fallback / chat-history 轮询 / immersive report 提取），
但关键集成契约零断言：

- schedule cleanup 的 source 字符串（native="gemini_deep_research" /
  fallback="gemini_deep_research:fallback"）
- cleanup_due_remote_chats 接收 client 对象
- retain_chat / delete_after_seconds 转发
- native vs fallback 路径选择（基于 client 是否有 3 个方法）
- thinking_scope 在非 default transport 时调用
- 错误分支（TimeoutError / RuntimeError）的返回文本契约
- 异常分支不调 schedule cleanup
- poll_interval 的 max(3, ...) clamp
- fallback 返回文本结构（"Deep Research 计划" + 模型信息 + 警告）

mock 边界：4 个 client_wrapper 接缝 + FakeClient 控制 native/fallback 路径。
_format_deep_research_result 走真实实现。
"""

import asyncio
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

import src.tools.research as research_tools
from src.tools.research import _null_scope


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _call_tool(mcp, name, **kwargs):
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


class _FakeFallbackResearchClient:
    """缺 native API 的 client，触发 fallback 路径。

    关键：不提供 create_deep_research_plan / start_deep_research /
    wait_for_deep_research 任一，使 has_native_api=False。
    """

    def __init__(self, *, response_text="research plan draft",
                 response_cid="c_fb1", raise_exc=None):
        self._response_text = response_text
        self._response_cid = response_cid
        self._raise_exc = raise_exc
        self.captured_generate_kwargs = None
        self.captured_generate_prompt = None
        self.last_response = None

    async def generate_content(self, prompt, **kwargs):
        self.captured_generate_prompt = prompt
        self.captured_generate_kwargs = dict(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        response = SimpleNamespace(
            text=self._response_text,
            metadata=[self._response_cid, "r_fb"],
        )
        self.last_response = response
        return response


class _FakeNativeResearchClient:
    """提供 native plan/start/wait API 的 client，触发 native 路径。"""

    def __init__(self, *, research_id="r_123", plan_cid="c_plan1",
                 plan_title="Research Plan",
                 plan_response_text="plan response text",
                 result_text="final report body",
                 result_done=True,
                 plan_exc=None, start_exc=None, wait_exc=None,
                 thinking_scope_obj=None):
        self._research_id = research_id
        self._plan_cid = plan_cid
        self._plan_title = plan_title
        self._plan_response_text = plan_response_text
        self._result_text = result_text
        self._result_done = result_done
        self._plan_exc = plan_exc
        self._start_exc = start_exc
        self._wait_exc = wait_exc
        self._thinking_scope_obj = thinking_scope_obj
        self.captured_start_chat_model = None
        self.captured_plan_query = None
        self.captured_plan_chat = None
        self.captured_plan_model = None
        self.captured_start_plan = None
        self.captured_start_chat = None
        self.captured_wait_plan = None
        self.captured_wait_poll = None
        self.captured_wait_timeout = None
        self.captured_scope_model = None
        self.captured_scope_thinking = None
        self.scope_called = False

    def start_chat(self, model=None):
        self.captured_start_chat_model = model
        return SimpleNamespace(cid="c_chat_initial", rid="r_1", rcid="rc_1")

    async def create_deep_research_plan(self, query, chat=None, model=None):
        if self._plan_exc is not None:
            raise self._plan_exc
        self.captured_plan_query = query
        self.captured_plan_chat = chat
        self.captured_plan_model = model
        return SimpleNamespace(
            research_id=self._research_id,
            title=self._plan_title,
            response_text=self._plan_response_text,
            confirm_prompt="Start research",
            cid=self._plan_cid,
        )

    async def start_deep_research(self, plan, chat=None):
        if self._start_exc is not None:
            raise self._start_exc
        self.captured_start_plan = plan
        self.captured_start_chat = chat
        return SimpleNamespace(text="Deep Research started")

    async def wait_for_deep_research(self, plan, poll_interval=None, timeout=None):
        if self._wait_exc is not None:
            raise self._wait_exc
        self.captured_wait_plan = plan
        self.captured_wait_poll = poll_interval
        self.captured_wait_timeout = timeout
        return SimpleNamespace(
            plan=plan,
            final_output=SimpleNamespace(text=self._result_text),
            statuses=[SimpleNamespace(state="completed", done=True, notes=["research done"])],
            done=self._result_done,
        )

    def thinking_scope(self, model, thinking_level):
        self.scope_called = True
        self.captured_scope_model = model
        self.captured_scope_thinking = thinking_level
        return self._thinking_scope_obj if self._thinking_scope_obj is not None else _null_scope()


def _patch_research_env(monkeypatch, client, *, captured_schedule=None,
                        captured_schedule_response=None, captured_cleanup=None):
    """统一 patch research 工具的外部接缝。"""
    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None
    monkeypatch.setattr(research_tools, "initialize_client", fake_init)

    async def fake_cleanup(client_arg):
        if captured_cleanup is not None:
            captured_cleanup.append(client_arg)
    monkeypatch.setattr(research_tools, "cleanup_due_remote_chats", fake_cleanup)

    def fake_schedule(cid, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append({
                "cid": cid,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })
    monkeypatch.setattr(research_tools, "schedule_remote_chat_cleanup", fake_schedule)

    def fake_schedule_response(response, *, retain_chat, delete_after_seconds, source):
        if captured_schedule_response is not None:
            captured_schedule_response.append({
                "response": response,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })
    monkeypatch.setattr(research_tools, "schedule_remote_chat_cleanup_from_response",
                        fake_schedule_response)


def _make_mcp():
    mcp = FastMCP("test")
    research_tools.register_research_tools(mcp)
    return mcp


# ---------------------------------------------------------------------------
# A. 入口共享契约
# ---------------------------------------------------------------------------


def test_deep_research_calls_cleanup_due_remote_chats_with_client(monkeypatch):
    """cleanup_due_remote_chats 接收 get_gemini_client 返回的 client 对象。"""
    client = _FakeFallbackResearchClient()
    captured_cleanup = []
    _patch_research_env(monkeypatch, client, captured_cleanup=captured_cleanup)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="quantum computing", timeout_seconds=5)

    asyncio.run(run())
    assert captured_cleanup == [client]


def test_deep_research_default_thinking_level_is_extended(monkeypatch):
    """默认 thinking_level='extended'（与 gemini_chat 默认 'standard' 不同）。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)  # 不传 thinking_level

    asyncio.run(run())
    assert client.captured_generate_kwargs["thinking_level"] == "extended"


def test_deep_research_default_timeout_is_600(monkeypatch):
    """默认 timeout_seconds=600。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research", query="x")  # 不传 timeout

    asyncio.run(run())
    assert client.captured_generate_kwargs["timeout"] == 600


# ---------------------------------------------------------------------------
# B. Fallback 路径（client 缺 native API）
# ---------------------------------------------------------------------------


def test_fallback_passes_deep_research_true_to_generate_content(monkeypatch):
    """fallback 路径 generate_content 收到 deep_research=True。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)

    asyncio.run(run())
    assert client.captured_generate_kwargs["deep_research"] is True


def test_fallback_passes_resolved_model_name(monkeypatch):
    """model='flash' → generate_content 收到 model='gemini-3-flash'。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", model="flash", timeout_seconds=5)

    asyncio.run(run())
    assert client.captured_generate_kwargs["model"] == "gemini-3-flash"


def test_fallback_passes_timeout_seconds_unchanged(monkeypatch):
    """fallback 路径 timeout 用原始 timeout_seconds（不走 _phase_timeout 的 max(30, ...) 底线）。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)  # 小于 30 底线

    asyncio.run(run())
    assert client.captured_generate_kwargs["timeout"] == 5


def test_fallback_prompt_contains_model_metadata(monkeypatch):
    """generate_content 收到的 prompt 含 'Requested MCP model alias' 与 'Transport model selection'。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="climate impacts", model="flash",
                                timeout_seconds=5)

    asyncio.run(run())
    prompt = client.captured_generate_prompt
    assert "climate impacts" in prompt
    assert "Requested MCP model alias: flash" in prompt
    assert "Transport model selection:" in prompt


def test_fallback_schedule_source_is_gemini_deep_research_fallback(monkeypatch):
    """fallback 路径 schedule source = 'gemini_deep_research:fallback'。"""
    client = _FakeFallbackResearchClient()
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule_response=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)

    asyncio.run(run())
    assert schedule_calls[0]["source"] == "gemini_deep_research:fallback"


def test_fallback_schedule_receives_response_object(monkeypatch):
    """schedule_remote_chat_cleanup_from_response 接收 generate_content 返回的同一 response。"""
    client = _FakeFallbackResearchClient()
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule_response=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)

    asyncio.run(run())
    assert schedule_calls[0]["response"] is client.last_response


def test_fallback_forwards_retain_chat_and_delete_after_seconds(monkeypatch):
    """retain_chat=True/delete_after_seconds=300 转发到 schedule。"""
    client = _FakeFallbackResearchClient()
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule_response=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5,
                                retain_chat=True, delete_after_seconds=300)

    asyncio.run(run())
    call = schedule_calls[0]
    assert call["retain_chat"] is True
    assert call["delete_after_seconds"] == 300


def test_fallback_default_retain_chat_false_delete_none(monkeypatch):
    """默认 retain_chat=False/delete_after_seconds=None 也转发。"""
    client = _FakeFallbackResearchClient()
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule_response=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)  # 默认值

    asyncio.run(run())
    assert schedule_calls[0]["retain_chat"] is False
    assert schedule_calls[0]["delete_after_seconds"] is None


def test_fallback_returns_plan_heading_with_query(monkeypatch):
    """fallback 返回文本前缀 '# 📚 Deep Research 计划: {query}'。"""
    client = _FakeFallbackResearchClient(response_text="report body")
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="machine learning", timeout_seconds=5)

    result = asyncio.run(run())
    text = result[0].text
    assert text.startswith("# 📚 Deep Research 计划: machine learning")


def test_fallback_returns_model_metadata_lines(monkeypatch):
    """fallback 返回文本含 '- 请求模型: {model}' 与 '- 实际研究传输: {model_note}'。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", model="flash", timeout_seconds=5)

    result = asyncio.run(run())
    text = result[0].text
    assert "- 请求模型: flash" in text
    assert "- 实际研究传输:" in text
    assert "Gemini Web default Deep Research mode" in text  # model_note 内容


def test_fallback_returns_warning_about_missing_polling_api(monkeypatch):
    """fallback 返回文本含 '⚠️ 当前 gemini-webapi 客户端没有暴露完整研究轮询 API'。"""
    client = _FakeFallbackResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)

    result = asyncio.run(run())
    assert "⚠️ 当前 gemini-webapi 客户端没有暴露完整研究轮询 API" in result[0].text


def test_fallback_includes_response_text_in_output(monkeypatch):
    """fallback 返回文本含 response.text 内容。"""
    client = _FakeFallbackResearchClient(response_text="unique report content xyz")
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=5)

    result = asyncio.run(run())
    assert "unique report content xyz" in result[0].text


# ---------------------------------------------------------------------------
# C. Native 路径（client 有 3 个方法，plan.research_id 存在）
# ---------------------------------------------------------------------------


def test_native_path_taken_when_client_has_three_methods(monkeypatch):
    """client 同时有 create_deep_research_plan/start_deep_research/wait_for_deep_research → 走 native。"""
    client = _FakeNativeResearchClient()
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    asyncio.run(run())
    # native 路径调 schedule_remote_chat_cleanup（非 _from_response），source 无 :fallback
    assert len(schedule_calls) == 1
    assert schedule_calls[0]["source"] == "gemini_deep_research"


def test_native_calls_start_chat_with_research_model(monkeypatch):
    """start_chat 接收 research_model（默认 model='flash' → Model.UNSPECIFIED）。"""
    client = _FakeNativeResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", model="flash", timeout_seconds=30)

    asyncio.run(run())
    # research_model 是 Model.UNSPECIFIED（enum），model_name == "unspecified"
    assert getattr(client.captured_start_chat_model, "model_name", None) == "unspecified"


def test_native_create_plan_receives_query_with_model_metadata(monkeypatch):
    """create_deep_research_plan 收到含 'Requested MCP model alias' 的 query。"""
    client = _FakeNativeResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="renewable energy", model="flash",
                                timeout_seconds=30)

    asyncio.run(run())
    assert "renewable energy" in client.captured_plan_query
    assert "Requested MCP model alias: flash" in client.captured_plan_query


def test_native_start_deep_research_receives_plan_and_chat(monkeypatch):
    """start_deep_research 接收 create_plan 返回的 plan 与 start_chat 返回的 chat。"""
    client = _FakeNativeResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    asyncio.run(run())
    # plan 是 create_deep_research_plan 的返回值（含 research_id）
    assert client.captured_start_plan is not None
    assert getattr(client.captured_start_plan, "research_id", None) == "r_123"


def test_native_wait_for_deep_research_called_when_research_id_present(monkeypatch):
    """plan.research_id 存在 → 调 wait_for_deep_research(plan, poll_interval=, timeout=)。"""
    client = _FakeNativeResearchClient(research_id="r_abc")
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=45,
                                poll_interval_seconds=7)

    asyncio.run(run())
    assert client.captured_wait_plan is not None
    assert client.captured_wait_poll == 7
    assert client.captured_wait_timeout == 45


def test_native_poll_interval_clamped_to_min_3(monkeypatch):
    """poll_interval_seconds=1 → 实际 poll_interval = max(3, 1) = 3。"""
    client = _FakeNativeResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30,
                                poll_interval_seconds=1)

    asyncio.run(run())
    assert client.captured_wait_poll == 3


def test_native_schedule_cid_from_plan_when_chat_cid_cleared(monkeypatch):
    """chat.cid 被 _start_fresh_research_chat 清空为 '' → schedule cid 回退到 plan.cid。

    _start_fresh_research_chat 把 chat.cid/rid/rcid 设为 ''。
    line 100: getattr(chat, "cid", None) or getattr(plan, "cid", None)
    空字符串 falsy → 回退到 plan.cid。
    """
    client = _FakeNativeResearchClient(plan_cid="c_plan_xyz")
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    asyncio.run(run())
    assert schedule_calls[0]["cid"] == "c_plan_xyz"


def test_native_schedule_forwards_retain_and_delete(monkeypatch):
    """native 路径 retain_chat/delete_after_seconds 转发到 schedule_remote_chat_cleanup。"""
    client = _FakeNativeResearchClient()
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30,
                                retain_chat=True, delete_after_seconds=600)

    asyncio.run(run())
    assert schedule_calls[0]["retain_chat"] is True
    assert schedule_calls[0]["delete_after_seconds"] == 600


def test_native_returns_report_heading_when_done(monkeypatch):
    """done=True → 返回文本含 '# 📚 Deep Research 报告:' 与 '完成: 是'。"""
    client = _FakeNativeResearchClient(result_text="final report", result_done=True)
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="deep query", timeout_seconds=30)

    result = asyncio.run(run())
    text = result[0].text
    assert "# 📚 Deep Research 报告: deep query" in text
    assert "完成: 是" in text
    assert "## 报告" in text
    assert "final report" in text


def test_native_includes_research_id_and_title_in_status(monkeypatch):
    """plan.research_id 与 plan.title 出现在返回文本的 '## 状态' 部分。"""
    client = _FakeNativeResearchClient(research_id="r_unique", plan_title="My Plan")
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    result = asyncio.run(run())
    text = result[0].text
    assert "Research ID: r_unique" in text
    assert "标题: My Plan" in text


def test_native_includes_model_note_in_status(monkeypatch):
    """native 返回文本含 '- 实际研究传输: {model_note}'。"""
    client = _FakeNativeResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", model="flash", timeout_seconds=30)

    result = asyncio.run(run())
    assert "- 实际研究传输:" in result[0].text
    assert "Gemini Web default Deep Research mode" in result[0].text


# ---------------------------------------------------------------------------
# D. thinking_scope（非 default transport）
# ---------------------------------------------------------------------------


def test_thinking_scope_called_for_non_default_transport(monkeypatch):
    """model 非 standard alias（如 'gemini-3-pro' 字面值）→ thinking_scope 被调用。

    _resolve_deep_research_transport_model 对非标准 alias 返回 (resolved, resolved)，
    _is_default_deep_research_transport 返回 False → 调 client.thinking_scope。
    """
    client = _FakeNativeResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", model="gemini-3-pro",  # 非标准 alias
                                thinking_level="extended", timeout_seconds=30)

    asyncio.run(run())
    assert client.scope_called is True
    # thinking_scope 接收 model_name（解析后）与 thinking_level
    assert client.captured_scope_model == "gemini-3-pro"
    assert client.captured_scope_thinking == "extended"


def test_thinking_scope_skipped_for_default_transport(monkeypatch):
    """model='flash'（标准 alias）→ Model.UNSPECIFIED → default transport → 不调 thinking_scope。"""
    client = _FakeNativeResearchClient()
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", model="flash", timeout_seconds=30)

    asyncio.run(run())
    assert client.scope_called is False


# ---------------------------------------------------------------------------
# E. 错误处理
# ---------------------------------------------------------------------------


def test_timeout_returns_timeout_error_with_seconds(monkeypatch):
    """asyncio.TimeoutError → 返回 '❌ Deep Research 超时（{N}秒）' + 'AI Plus 订阅'。"""
    client = _FakeFallbackResearchClient(raise_exc=asyncio.TimeoutError())
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=42)

    result = asyncio.run(run())
    text = result[0].text
    assert "❌ Deep Research 超时（42秒）" in text
    assert "AI Plus 订阅" in text


def test_generic_exception_returns_error_with_message(monkeypatch):
    """RuntimeError → 返回 '❌ Deep Research 失败: {str(e)}' + '该功能在您所在的区域是否可用'。"""
    client = _FakeFallbackResearchClient(raise_exc=RuntimeError("capability missing"))
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    result = asyncio.run(run())
    text = result[0].text
    assert "❌ Deep Research 失败: capability missing" in text
    assert "该功能在您所在的区域是否可用" in text


def test_exception_skips_schedule_cleanup(monkeypatch):
    """异常分支不调 schedule cleanup（schedule 在 try 内 return 前）。"""
    client = _FakeFallbackResearchClient(raise_exc=RuntimeError("boom"))
    schedule_calls = []
    _patch_research_env(monkeypatch, client, captured_schedule_response=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    asyncio.run(run())
    assert schedule_calls == []


def test_native_wait_timeout_returns_timeout_error(monkeypatch):
    """native 路径 wait_for_deep_research 抛 TimeoutError → 外层捕获返回超时错误。"""
    client = _FakeNativeResearchClient(wait_exc=asyncio.TimeoutError())
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    result = asyncio.run(run())
    assert "❌ Deep Research 超时（30秒）" in result[0].text


def test_native_wait_generic_exception_returns_error(monkeypatch):
    """native 路径 wait_for_deep_research 抛 RuntimeError → 外层捕获返回通用错误。"""
    client = _FakeNativeResearchClient(wait_exc=RuntimeError("network down"))
    _patch_research_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_deep_research",
                                query="x", timeout_seconds=30)

    result = asyncio.run(run())
    assert "❌ Deep Research 失败: network down" in result[0].text
