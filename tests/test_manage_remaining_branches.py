"""manage.py 剩余 markdown 渲染分支与 notebook 分页/导出/搜索 edge case 行为契约测试。

覆盖 manage.py 中以下此前零直接覆盖的分支：

1. **has_more footer 渲染**（5 个 handler × 1 miss = 5 miss）：
   - ``gemini_list_public_links`` [3016]：limit 截断触发 has_more → "下一页: offset=..."
   - ``gemini_list_library_capabilities`` [3128]：同上
   - ``gemini_list_notebooks`` [3173]：同上
   - ``gemini_list_notebook_chats`` [3232]：page_payload.has_more → 同上
   - ``gemini_list_scheduled_actions`` [3412]：result.has_more → "- 下一页: offset=..."
     需用 ``limit=1`` + 2 条 entries 触发 has_more=True。

2. **gemini_scan_chat_history_sources notebook 分页** [2326, 2347-2350]（4 miss）：
   - 2326：notebook 无 "id" key → ``continue`` 跳过
   - 2347-2350：``new_offset = page_payload.get("next_offset")`` / 非 int 或
     ``new_offset <= next_offset`` → break / 否则 ``next_offset = new_offset`` 推进循环

3. **gemini_export_chat _batch_execute 路径 + 顶层 except** [2656, 2671-2673]（3 miss）：
   - 2656：``hasattr(client, "_batch_execute")`` 为 True → 走 ``_fetch_recent_conversation_metadata`` 路径
   - 2671-2673：顶层 except（read_chat 抛异常）

4. **gemini_search_chats markdown 渲染** [2614, 2619]（2 miss）：
   - 2614：snippet.error 在 markdown 渲染中输出 ``- read error: {error}``
   - 2619：``payload["has_more"]`` 在 markdown 渲染中输出 ``下一页: offset=...``

mock 边界：
- client_wrapper 接缝：``get_gemini_client`` / ``initialize_client``
- tools.manage 内部接缝：``_extract_rpc_bodies`` / ``_fetch_native_notebooks`` /
  ``_fetch_notebook_chats`` / ``_fetch_scheduled_registry`` /
  ``_fetch_conversation_metadata_sources`` / ``_fetch_recent_conversation_metadata``
- 调用方式：MCP handler 经 ``register_manage_tools(mcp, layers=["all"])`` 注册后
  通过 ``mcp.call_tool`` 分发。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

import src.tools.manage as manage_tools


# ---------------------------------------------------------------------------
# 共享辅助
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


class _FakeBatchClient:
    """带 ``_batch_execute`` 的 client，按队列返回响应文本。"""

    def __init__(self, responses=None, status_code=200, raise_exc=None):
        self._responses = list(responses) if responses else []
        self._status_code = status_code
        self._raise_exc = raise_exc
        self.call_count = 0
        self.captured_payloads = []

    async def _batch_execute(self, raw_rpc_list, *, source_path=None, close_on_error=False):
        self.call_count += 1
        self.captured_payloads.append(raw_rpc_list)
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._responses:
            text = self._responses.pop(0)
        else:
            text = ""
        return SimpleNamespace(text=text, status_code=self._status_code)


class _NoBatchClient:
    """无 ``_batch_execute`` 属性的 client，触发能力缺失早退。"""

    pass


class _ListChatsClient:
    """无 ``_batch_execute`` 但有 ``list_chats`` 的 client，走 client_cache 路径。"""

    def __init__(self, chats):
        self._chats = chats

    def list_chats(self):
        return self._chats


class _ReadChatClient(_ListChatsClient):
    """``_ListChatsClient`` + ``read_chat``，支持 scan_turns 路径。"""

    def __init__(self, chats, turns_by_id):
        super().__init__(chats)
        self._turns_by_id = turns_by_id

    async def read_chat(self, chat_id, limit=20):
        turns = self._turns_by_id.get(chat_id, [])
        return SimpleNamespace(cid=chat_id, turns=turns)


class _BatchReadChatClient:
    """同时带 ``_batch_execute`` + ``read_chat`` 的 client。"""

    def __init__(self, turns_by_id=None):
        self._turns_by_id = turns_by_id or {}
        self.batch_call_count = 0

    async def _batch_execute(self, raw_rpc_list, *, source_path=None, close_on_error=False):
        self.batch_call_count += 1
        return SimpleNamespace(text="", status_code=200)

    async def read_chat(self, chat_id, limit=20):
        turns = self._turns_by_id.get(chat_id, [])
        return SimpleNamespace(cid=chat_id, turns=turns)


def _patch_seams(monkeypatch, client):
    """统一 patch manage 模块的 client_wrapper 接缝。"""
    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None

    monkeypatch.setattr(manage_tools, "initialize_client", fake_init)


def _make_mcp():
    mcp = FastMCP("test")
    manage_tools.register_manage_tools(mcp, layers=["all"])
    return mcp


async def _call(mcp, name, **kwargs):
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


def _chat(cid, title="t", is_pinned=False, timestamp=None):
    """构造可被 ``_chat_to_dict`` 解析的 chat 对象。"""
    return SimpleNamespace(cid=cid, title=title, is_pinned=is_pinned, timestamp=timestamp)


def _turn(role, text):
    return SimpleNamespace(role=role, text=text)


# ===========================================================================
# Section A: has_more footer 渲染（5 个 handler）
# ===========================================================================


def test_list_public_links_has_more_footer(monkeypatch):
    """limit=1 + 2 条 entries → has_more=True → "下一页: offset=1"。"""
    entries = [
        ["id_1", "Link One", False, "", "https://example.com/1"],
        ["id_2", "Link Two", True, "", "https://example.com/2"],
    ]
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [entries])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_public_links", limit=1, offset=0))
    text = result[0].text
    assert "共 2 条；当前 offset=0 count=1" in text
    assert "Link Two" not in text  # 第二条不在当前页
    assert "下一页: offset=1" in text


def test_list_library_capabilities_has_more_footer(monkeypatch):
    """limit=1 + 2 条 entries → has_more=True → "下一页: offset=1"。"""
    entries = [
        [["alias_a"], "Cap One", "desc 一", "details 一"],
        [[], "Cap Two", "desc 二", ""],
    ]
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [[entries]])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_library_capabilities", limit=1, offset=0))
    text = result[0].text
    assert "共 2 条；当前 offset=0 count=1" in text
    assert "Cap Two" not in text
    assert "下一页: offset=1" in text


def test_list_notebooks_has_more_footer(monkeypatch):
    """limit=1 + 2 个 notebooks → has_more=True → "下一页: offset=1"。"""
    notebooks = [
        {"id": "n_1", "title": "Math Notes", "emoji": "📚", "source_count": 3},
        {"id": "n_2", "title": "Science", "emoji": "", "source_count": 0},
    ]
    diagnostic = {"source_rpc": "CNgdBe", "observed": "observed"}
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=(notebooks, diagnostic)),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebooks", limit=1, offset=0))
    text = result[0].text
    assert "共 2 个；当前 offset=0 count=1" in text
    assert "Science" not in text
    assert "下一页: offset=1" in text


def test_list_notebook_chats_has_more_footer(monkeypatch):
    """page_payload.has_more=True → "下一页: offset=1"。"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=(notebooks, {"source_rpc": "CNgdBe"})),
    )
    items = [{"id": "c_1", "title": "Chat 1", "time": ""}]
    monkeypatch.setattr(
        manage_tools, "_fetch_notebook_chats",
        AsyncMock(return_value=(items, {
            "total_count": 2, "count": 1, "offset": 0, "limit": 1,
            "has_more": True, "next_offset": 1,
            "diagnostic": {"fetched_count": 2, "page_count": 1, "has_remote_more": True},
        })),
    )
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_id="n_1", limit=1))
    assert "下一页: offset=1" in result[0].text


def test_list_scheduled_actions_has_more_footer(monkeypatch):
    """limit=1 + 2 条 entries → result.has_more=True → "- 下一页: offset=1"。"""
    entries = [
        {
            "id": "task_1", "title": "Daily Report", "enabled": True,
            "schedule_label": "每日 09:00", "hour": 9, "timezone_name": "Asia/Shanghai",
        },
        {
            "id": "task_2", "title": "Weekly Report", "enabled": False,
            "schedule_label": "", "hour": None, "timezone_name": "",
        },
    ]
    diagnostic = {"source_rpc": "CNgdBe", "observed": "observed"}
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=(entries, diagnostic)),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_scheduled_actions", limit=1, offset=0))
    text = result[0].text
    assert "Weekly Report" not in text  # 第二条不在当前页
    assert "- 下一页: offset=1" in text


# ===========================================================================
# Section B: gemini_scan_chat_history_sources notebook 分页
# ===========================================================================


def test_scan_chat_history_sources_notebook_without_id_skipped(monkeypatch):
    """notebook 无 "id" key → continue 跳过，notebook_summary 为空。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_conversation_metadata_sources",
        AsyncMock(return_value=[]),
    )
    # notebook 无 "id" 字段 → notebook.get("id", "")="" → continue
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=([{"title": "No ID Notebook"}], {"source_rpc": "CNgdBe"})),
    )
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=True, include_remy_goals=False,
                        response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["total_count"] == 0
    # notebook 被跳过 → notebook_summary 为空
    assert payload["notebooks"]["items"] == []


def test_scan_chat_history_sources_notebook_next_offset_non_int_breaks(monkeypatch):
    """page_payload.next_offset 非 int → break，notebook_summary 只有 1 个 page。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_conversation_metadata_sources",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=([{"id": "n_1", "title": "Math"}], {"source_rpc": "CNgdBe"})),
    )
    # has_more=True + next_offset="not_an_int" → break via `not isinstance(new_offset, int)`
    items = [{"id": "c_1", "title": "Chat 1", "is_pinned": False, "timestamp": 1000}]
    monkeypatch.setattr(
        manage_tools, "_fetch_notebook_chats",
        AsyncMock(return_value=(items, {"has_more": True, "next_offset": "not_an_int"})),
    )
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=True, include_remy_goals=False,
                        response_format="json"))
    payload = json.loads(result[0].text)
    # 1 条 item 被收集，但循环因 next_offset 非 int 而 break
    assert payload["total_count"] == 1
    nb = payload["notebooks"]["items"][0]
    assert nb["fetched_count"] == 1
    assert len(nb["pages"]) == 1
    assert nb["pages"][0]["next_offset"] == "not_an_int"


def test_scan_chat_history_sources_notebook_next_offset_le_current_breaks(monkeypatch):
    """page_payload.next_offset <= current → break。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_conversation_metadata_sources",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=([{"id": "n_1", "title": "Math"}], {"source_rpc": "CNgdBe"})),
    )
    # has_more=True + next_offset=0 (== current 0) → break via `new_offset <= next_offset`
    items = [{"id": "c_1", "title": "Chat 1", "is_pinned": False, "timestamp": 1000}]
    monkeypatch.setattr(
        manage_tools, "_fetch_notebook_chats",
        AsyncMock(return_value=(items, {"has_more": True, "next_offset": 0})),
    )
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=True, include_remy_goals=False,
                        response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["total_count"] == 1
    nb = payload["notebooks"]["items"][0]
    assert nb["fetched_count"] == 1
    assert len(nb["pages"]) == 1  # 只 1 页，循环 break


def test_scan_chat_history_sources_notebook_next_offset_advances_loop(monkeypatch):
    """page_payload.next_offset > current → next_offset=new_offset，循环推进。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_conversation_metadata_sources",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=([{"id": "n_1", "title": "Math"}], {"source_rpc": "CNgdBe"})),
    )
    item1 = {"id": "c_1", "title": "Chat 1", "is_pinned": False, "timestamp": 1000}
    item2 = {"id": "c_2", "title": "Chat 2", "is_pinned": False, "timestamp": 2000}
    # 第一次：has_more=True, next_offset=5 (>0) → 推进；第二次：has_more=False → break
    monkeypatch.setattr(
        manage_tools, "_fetch_notebook_chats",
        AsyncMock(side_effect=[
            ([item1], {"has_more": True, "next_offset": 5}),
            ([item2], {"has_more": False, "next_offset": None}),
        ]),
    )
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=True, include_remy_goals=False,
                        response_format="json"))
    payload = json.loads(result[0].text)
    # 2 条 item 被收集，循环推进后 break
    assert payload["total_count"] == 2
    nb = payload["notebooks"]["items"][0]
    assert nb["fetched_count"] == 2
    assert len(nb["pages"]) == 2  # 2 页
    assert nb["pages"][0]["offset"] == 0
    assert nb["pages"][1]["offset"] == 5


# ===========================================================================
# Section C: gemini_export_chat _batch_execute 路径 + 顶层 except
# ===========================================================================


def test_export_chat_batch_execute_path_uses_fetch_recent_metadata(monkeypatch):
    """hasattr(client, "_batch_execute") True → 走 _fetch_recent_conversation_metadata 路径。

    metadata 从 _fetch_recent_conversation_metadata 返回的 chats 中匹配 chat_id。
    """
    client = _BatchReadChatClient(turns_by_id={"c_1": [_turn("user", "hello")]})
    _patch_seams(monkeypatch, client)
    captured = {}

    async def fake_fetch(client_arg, target_count):
        captured["client"] = client_arg
        captured["target"] = target_count
        return (
            [{"id": "c_1", "title": "Found Title", "time": "2025-01-01 00:00:00 UTC"}],
            {"has_remote_more": False},
        )

    monkeypatch.setattr(manage_tools, "_fetch_recent_conversation_metadata", fake_fetch)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_export_chat", chat_id="c_1"))
    text = result[0].text
    assert captured["client"] is client
    assert captured["target"] == 500
    # metadata.title="Found Title" → 渲染在标题行
    assert "## Gemini Chat Export: Found Title" in text
    assert "Time: 2025-01-01 00:00:00 UTC" in text
    assert "### 1. user" in text
    assert "hello" in text


def test_export_chat_top_level_exception_returns_error(monkeypatch):
    """read_chat 抛异常 → 顶层 except '❌ 导出失败: {e}'。"""
    client = _ReadChatClient([], {})

    async def boom(chat_id, limit=20):
        raise RuntimeError("read failed")

    client.read_chat = boom
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_export_chat", chat_id="c_1"))
    assert "❌ 导出失败" in result[0].text
    assert "read failed" in result[0].text


# ===========================================================================
# Section D: gemini_search_chats markdown 渲染
# ===========================================================================


def test_search_chats_markdown_renders_snippet_error(monkeypatch):
    """scan_turns=True + read_chat 抛异常 → markdown 含 '- read error: {error}'。"""
    client = _ReadChatClient([_chat("c_1", "Needle Title")], {})

    async def boom(chat_id, limit=20):
        raise ValueError("boom")

    client.read_chat = boom
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="needle", scan_turns=True))
    text = result[0].text
    assert "## Gemini 历史搜索" in text
    assert "fields=title" in text  # title 匹配（read error 不影响 title 匹配）
    assert "read error: ValueError: boom" in text


def test_search_chats_markdown_renders_has_more_footer(monkeypatch):
    """2 chats + limit=1 → has_more=True → markdown 含 '下一页: offset=1'。"""
    client = _ListChatsClient([
        _chat("c_1", "Alpha Report"),
        _chat("c_2", "Beta Report"),
    ])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="alpha", limit=1))
    text = result[0].text
    assert "Scanned: 1/2" in text
    assert "Matches: 1" in text
    assert "下一页: offset=1" in text
