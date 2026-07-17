"""chat 模块的 gemini_reset_session 和 gemini_list_sessions 行为测试。

调研发现 gemini_reset_session 标注 destructiveHint=True（DESTRUCTIVE_REMOTE），
但仅有注解形状测试，没有覆盖 delete_remote_chat 调用决策路径。gemini_list_sessions
同样仅有注解测试。本文件补充行为断言：

- gemini_reset_session:
  * session_id 不存在 → 不调 delete_remote_chat，返回成功
  * session 存在 + retain_chat=False → 调用 delete_remote_chat(cid)
  * session 存在 + retain_chat=True → 不调用 delete_remote_chat
  * session 存在但 session 对象无 cid → delete_remote_chat(None)
- gemini_list_sessions:
  * 空会话列表 → 返回"暂无活跃会话"
  * 非空会话列表 → 返回"活跃会话:" + 每个会话的模型/保留状态描述
"""

import asyncio
import pytest

from mcp.server.fastmcp import FastMCP

import src.tools.chat as chat_tools


# ---------------------------------------------------------------------------
# 辅助：注册工具并返回可直接 await 的工具函数
# ---------------------------------------------------------------------------


def _register_chat_tools():
    """注册 chat 工具并返回工具函数字典。

    FastMCP 的 call_tool 路径较重，这里直接从 mcp._tool_manager 提取
    已注册的工具函数，便于直接 await 调用。
    """
    mcp = FastMCP("test")
    chat_tools.register_chat_tools(mcp)
    return mcp


async def _call_tool(mcp, name, **kwargs):
    """通过 mcp.call_tool 调用工具，返回 TextContent 列表。

    mcp.call_tool 返回 (content_list, structured_dict) 元组，这里只取 content_list。
    """
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


# ---------------------------------------------------------------------------
# gemini_reset_session
# ---------------------------------------------------------------------------


def test_reset_session_unknown_id_does_not_delete(monkeypatch):
    """session_id 不存在时 pop_session 返回 None，不调 delete_remote_chat。"""
    deleted_cids = []

    monkeypatch.setattr(chat_tools, "pop_session", lambda session_id: None)

    async def fake_delete(cid):
        deleted_cids.append(cid)
    monkeypatch.setattr(chat_tools, "delete_remote_chat", fake_delete)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_reset_session", session_id="sess-unknown")

    result = asyncio.run(run())
    assert deleted_cids == []
    assert len(result) == 1
    assert "sess-unknown" in result[0].text
    assert "已重置" in result[0].text


def test_reset_session_retain_false_triggers_delete(monkeypatch):
    """session 存在且 retain_chat=False 时调用 delete_remote_chat(cid)。"""
    deleted_cids = []

    class FakeSession:
        cid = "c_abc123"

    monkeypatch.setattr(
        chat_tools, "pop_session",
        lambda session_id: {"session": FakeSession(), "retain_chat": False, "model": "fast"},
    )

    async def fake_delete(cid):
        deleted_cids.append(cid)
    monkeypatch.setattr(chat_tools, "delete_remote_chat", fake_delete)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_reset_session", session_id="sess-1")

    asyncio.run(run())
    assert deleted_cids == ["c_abc123"]


def test_reset_session_retain_true_skips_delete(monkeypatch):
    """session 存在且 retain_chat=True 时不调用 delete_remote_chat。"""
    deleted_cids = []

    class FakeSession:
        cid = "c_retained"

    monkeypatch.setattr(
        chat_tools, "pop_session",
        lambda session_id: {"session": FakeSession(), "retain_chat": True, "model": "fast"},
    )

    async def fake_delete(cid):
        deleted_cids.append(cid)
    monkeypatch.setattr(chat_tools, "delete_remote_chat", fake_delete)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_reset_session", session_id="sess-2")

    asyncio.run(run())
    assert deleted_cids == []


def test_reset_session_without_cid_deletes_none(monkeypatch):
    """session 对象无 cid 属性时 delete_remote_chat(None)。"""
    deleted_cids = []

    class FakeSessionNoCid:
        pass  # 无 cid 属性

    monkeypatch.setattr(
        chat_tools, "pop_session",
        lambda session_id: {"session": FakeSessionNoCid(), "retain_chat": False, "model": "fast"},
    )

    async def fake_delete(cid):
        deleted_cids.append(cid)
    monkeypatch.setattr(chat_tools, "delete_remote_chat", fake_delete)

    mcp = _register_chat_tools()

    async def run():
        return await _call_tool(mcp, "gemini_reset_session", session_id="sess-3")

    asyncio.run(run())
    assert deleted_cids == [None]


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
