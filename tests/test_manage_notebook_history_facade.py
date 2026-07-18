"""manage.py 的 Notebook 移动 / History facade / 深度扫描行为契约测试。

调研发现 manage.py 在 Cycle 28 后仍有 313 miss（83% 覆盖率）。本文件覆盖下一批
高 ROI 簇——3 个 MCP handler + 1 个 payload helper，此前仅 ``test_tool_workflows.py``
有零星 happy path 间接覆盖，关键分支零断言：

1. ``gemini_move_chat_to_notebook`` [3271-3359]：78 行 mutation 工具（MUTATES_REMOTE），
   最大连续未覆盖块。空 chat_id / 缺 notebook 标识 / 无 _batch_execute 早退、
   _find_notebook 未命中（空 / 非空可用标题）、project_type int/非 int 回退、
   MUAZcd RPC + _extract_rpc_bodies 解析 updated_entry（body[1] list/非 list）、
   _fetch_notebook_chats 验证 verified_in_target_notebook、ok 矩阵
   （status+body_present）、3 种 markdown 渲染（✅ ok+verified / ⚠️ ok+not verified /
   ❌ not ok）、response_format=json、顶层 except。
2. ``gemini_history`` [2675-2736]：38 行 read-only facade dispatcher，5 个 action
   （list/scan/search/read/export）+ 未知 action 兜底 + 默认 list。closure 引用无法
   patch 模块属性，通过控制底层 helper 验证 dispatch 正确性。
3. ``gemini_scan_chat_history_sources`` [2285-2462]：~90 行深度扫描，参数 clamp、
   _fetch_conversation_metadata_sources + include_notebook_chats（_fetch_native_notebooks
   + _fetch_notebook_chats 分页循环）+ include_remy_goals（_fetch_remy_goal_conversation_refs）、
   _merge_conversation_source_items 合并、coverage_warnings（stopped_reason in
   {max_items, max_pages}）、markdown 渲染（来源计数 / 覆盖警告 / 当前页 / has_more）、
   response_format=json、顶层 except。
4. ``_move_chat_to_notebook_payload`` [974-983]：payload helper，conversation[0]=chat_id、
   conversation[7]=notebook_id、conversation[13]=[project_type]，默认 project_type=2。

mock 边界：
- client_wrapper 接缝：``get_gemini_client`` / ``initialize_client``
- tools.manage 内部接缝：``_extract_rpc_bodies`` / ``_fetch_native_notebooks`` /
  ``_fetch_notebook_chats`` / ``_fetch_conversation_metadata_sources`` /
  ``_fetch_remy_goal_conversation_refs``
- 调用方式：MCP handler 经 ``register_manage_tools(mcp, layers=["all"])`` 注册后
  通过 ``mcp.call_tool`` 分发；helper 直接调用。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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
    """无 ``_batch_execute`` 但有 ``list_chats`` 的 client。"""

    def __init__(self, chats):
        self._chats = chats

    def list_chats(self):
        return self._chats


class _ReadChatClient(_ListChatsClient):
    """``_ListChatsClient`` + ``read_chat``。"""

    def __init__(self, chats, turns_by_id):
        super().__init__(chats)
        self._turns_by_id = turns_by_id

    async def read_chat(self, chat_id, limit=20):
        turns = self._turns_by_id.get(chat_id, [])
        return SimpleNamespace(cid=chat_id, turns=turns)


def _patch_seams(monkeypatch, client):
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
    return SimpleNamespace(cid=cid, title=title, is_pinned=is_pinned, timestamp=timestamp)


def _turn(role, text):
    return SimpleNamespace(role=role, text=text)


def _notebook(notebook_id, title, project_type=None):
    """构造 _find_notebook 可命中的 notebook dict。"""
    nb = {"id": notebook_id, "title": title}
    if project_type is not None:
        nb["project_type"] = project_type
    return nb


def _conv_entry(chat_id, title="Chat"):
    """构造 _parse_conversation_metadata 可解析的 entry（entry[0]=id, entry[1]=title）。"""
    return [chat_id, title]


# ===========================================================================
# Section A: _move_chat_to_notebook_payload + gemini_move_chat_to_notebook
# ===========================================================================


def test_move_chat_to_notebook_payload_default_project_type():
    """默认 project_type=2，conversation[0]=chat_id, [7]=notebook_id, [13]=[2]。"""
    payload = manage_tools._move_chat_to_notebook_payload("c_1", "n_1")
    parsed = json.loads(payload)
    # payload = [None, [["bot_id", "bot_project_metadata"]], conversation]
    assert parsed[0] is None
    assert parsed[1] == [["bot_id", "bot_project_metadata"]]
    conversation = parsed[2]
    assert conversation[0] == "c_1"
    assert conversation[7] == "n_1"
    assert conversation[13] == [2]


def test_move_chat_to_notebook_payload_custom_project_type():
    """自定义 project_type=5 → conversation[13]=[5]。"""
    payload = manage_tools._move_chat_to_notebook_payload("c_1", "n_1", project_type=5)
    parsed = json.loads(payload)
    assert parsed[2][13] == [5]


def test_move_chat_to_notebook_payload_is_compact_json():
    """payload 使用 compact separators（无多余空格）。"""
    payload = manage_tools._move_chat_to_notebook_payload("c_1", "n_1")
    assert ", " not in payload  # separators=(",", ":")


def test_move_chat_to_notebook_empty_chat_id_short_circuits(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="", notebook_id="n_1"))
    assert "❌ chat_id 不能为空" in result[0].text


def test_move_chat_to_notebook_whitespace_chat_id_short_circuits(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="   ", notebook_id="n_1"))
    assert "❌ chat_id 不能为空" in result[0].text


def test_move_chat_to_notebook_missing_notebook_id_and_title_short_circuits(monkeypatch):
    """notebook_id 和 notebook_title 都空 → 早退。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook", chat_id="c_1"))
    assert "❌ 需要提供 notebook_id 或 notebook_title" in result[0].text


def test_move_chat_to_notebook_no_batch_execute_short_circuits(monkeypatch):
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1"))
    assert "❌ 当前客户端不支持 Gemini Notebooks RPC" in result[0].text


def test_move_chat_to_notebook_notebook_not_found_empty(monkeypatch):
    """notebooks 为空 → 未找到，available_titles=[]。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {"source_rpc": "CNgdBe"})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_title="Missing"))
    assert "未找到匹配的 Gemini 原生笔记本" in result[0].text
    assert "可用标题:" in result[0].text


def test_move_chat_to_notebook_notebook_not_found_json_payload(monkeypatch):
    """response_format=json + 未找到 → ok=False payload。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {"source_rpc": "CNgdBe"})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="missing", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["chat_id"] == "c_1"
    assert payload["notebook_id"] == "missing"
    assert payload["available_titles"] == []


def test_move_chat_to_notebook_ok_and_verified_renders_success(monkeypatch):
    """ok=True + verified=True → '✅ 已移动聊天...'。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    # bodies 非空 → body_present=True；body[1] 为 list → updated_entry 解析
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, _conv_entry("c_1", "Moved Chat")]])
    # verify_items 含 c_1 → verified=True
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([{"id": "c_1", "title": "Moved Chat", "time": ""}], {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1"))
    text = result[0].text
    assert "✅ 已移动聊天 c_1 到笔记本: Math (n_1)" in text
    # 验证 MUAZcd payload 被捕获
    assert client.call_count == 1
    rpc = client.captured_payloads[0][0]
    assert rpc.rpcid == "MUAZcd"


def test_move_chat_to_notebook_ok_but_not_verified_renders_warning(monkeypatch):
    """ok=True + verified=False → '⚠️ Gemini 接受了移动请求...'。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, _conv_entry("c_1")]])
    # verify_items 不含 c_1 → verified=False
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([{"id": "other", "title": "Other", "time": ""}], {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1"))
    text = result[0].text
    assert "⚠️ Gemini 接受了移动请求" in text
    assert "未验证到 c_1" in text


def test_move_chat_to_notebook_not_ok_renders_failure(monkeypatch):
    """ok=False（status_code != 200）→ '❌ 移动聊天失败'。"""
    client = _FakeBatchClient(responses=["resp"], status_code=500)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, _conv_entry("c_1")]])
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([], {
                            "total_count": 0, "count": 0, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 0, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1"))
    assert "❌ 移动聊天失败: c_1" in result[0].text


def test_move_chat_to_notebook_empty_bodies_makes_not_ok(monkeypatch):
    """bodies 为空 → body_present=False → ok=False。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([], {
                            "total_count": 0, "count": 0, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 0, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1"))
    assert "❌ 移动聊天失败: c_1" in result[0].text


def test_move_chat_to_notebook_json_payload_ok_verified(monkeypatch):
    """response_format=json + ok + verified → 完整 payload。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, _conv_entry("c_1", "Moved")]])
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([{"id": "c_1", "title": "Moved", "time": ""}], {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["chat_id"] == "c_1"
    assert payload["notebook"]["id"] == "n_1"
    assert payload["source_rpc"] == "MUAZcd"
    assert payload["status_code"] == 200
    assert payload["body_present"] is True
    assert payload["verified_in_target_notebook"] is True
    assert payload["updated_entry"]["id"] == "c_1"


def test_move_chat_to_notebook_updated_entry_none_when_body1_not_list(monkeypatch):
    """body[1] 非 list → updated_entry=None。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    # body=[None, "not_a_list"] → body[1] 非 list → updated_entry=None
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, "not_a_list"]])
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([{"id": "c_1", "title": "t", "time": ""}], {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["updated_entry"] is None
    assert payload["ok"] is True  # bodies 非空 + status 200


def test_move_chat_to_notebook_project_type_from_notebook(monkeypatch):
    """notebook.project_type 为 int → 用于 payload。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math", project_type=7)]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, _conv_entry("c_1")]])
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([{"id": "c_1", "title": "t", "time": ""}], {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    _run(_call(mcp, "gemini_move_chat_to_notebook", chat_id="c_1", notebook_id="n_1"))
    # 验证捕获的 MUAZcd payload 使用 project_type=7
    rpc = client.captured_payloads[0][0]
    parsed = json.loads(rpc.payload)
    assert parsed[2][13] == [7]


def test_move_chat_to_notebook_project_type_non_int_defaults_to_2(monkeypatch):
    """notebook.project_type 非 int → 默认 2。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math", project_type="not_int")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, _conv_entry("c_1")]])
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([{"id": "c_1", "title": "t", "time": ""}], {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    _run(_call(mcp, "gemini_move_chat_to_notebook", chat_id="c_1", notebook_id="n_1"))
    rpc = client.captured_payloads[0][0]
    parsed = json.loads(rpc.payload)
    assert parsed[2][13] == [2]  # 默认


def test_move_chat_to_notebook_top_level_exception(monkeypatch):
    """_fetch_native_notebooks 抛异常 → 顶层 except。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(side_effect=RuntimeError("rpc down")))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="c_1", notebook_id="n_1"))
    assert "❌ 移动 Gemini Notebook 聊天失败" in result[0].text
    assert "rpc down" in result[0].text


def test_move_chat_to_notebook_strips_chat_id(monkeypatch):
    """chat_id 含空白 → strip 后用于移动与验证。"""
    client = _FakeBatchClient(responses=["resp"], status_code=200)
    _patch_seams(monkeypatch, client)
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[None, _conv_entry("c_1")]])
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([{"id": "c_1", "title": "t", "time": ""}], {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_move_chat_to_notebook",
                        chat_id="  c_1  ", notebook_id="n_1"))
    assert "✅ 已移动聊天 c_1" in result[0].text  # strip 后的 id


# ===========================================================================
# Section B: gemini_history facade dispatcher
# ===========================================================================


def test_history_facade_default_action_is_list(monkeypatch):
    """默认 action（不传）→ list，渲染历史对话列表头。"""
    client = _ListChatsClient([_chat("c_1", "Alpha")])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_history"))
    text = result[0].text
    assert "## 📜 历史对话" in text  # list 的 header
    assert "Alpha" in text


def test_history_facade_action_list_dispatches(monkeypatch):
    client = _ListChatsClient([_chat("c_1", "Alpha")])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_history", action="list"))
    assert "## 📜 历史对话" in result[0].text


def test_history_facade_action_search_dispatches(monkeypatch):
    """action=search → 走 gemini_search_chats，渲染搜索头。"""
    client = _ListChatsClient([_chat("c_1", "Alpha Report")])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_history", action="search", query="alpha"))
    text = result[0].text
    assert "## Gemini 历史搜索" in text  # search 的 header
    assert "Query: alpha" in text


def test_history_facade_action_read_dispatches(monkeypatch):
    """action=read → 走 gemini_read_chat，渲染聊天内容。"""
    client = _ReadChatClient([_chat("c_1", "Alpha")],
                             {"c_1": [_turn("user", "hello"), _turn("model", "world")]})
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_history", action="read", chat_id="c_1"))
    text = result[0].text
    assert "hello" in text
    assert "world" in text


def test_history_facade_action_export_dispatches(monkeypatch):
    """action=export → 走 gemini_export_chat，渲染 markdown 导出。"""
    client = _ReadChatClient([_chat("c_1", "Alpha")],
                             {"c_1": [_turn("user", "export me")]})
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_history", action="export", chat_id="c_1"))
    text = result[0].text
    assert "export me" in text
    # export markdown 含角色分节
    assert "user" in text


def test_history_facade_action_scan_dispatches(monkeypatch):
    """action=scan → 走 gemini_scan_chat_history_sources，渲染深度扫描头。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    # patch 底层 helper 使 scan 不真正发 RPC
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_remy_goal_conversation_refs",
                        AsyncMock(return_value={"name": "remy_goals", "rpcid": "XPSWpd",
                                                "items": [], "diagnostic": {}}))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_history", action="scan",
                        include_notebook_chats=False, include_remy_goals=False))
    text = result[0].text
    assert "## Gemini 历史对话深度扫描" in text  # scan 的 header


def test_history_facade_unknown_action_rejected_by_pydantic(monkeypatch):
    """action 非 Literal 值 → pydantic 在 dispatcher 前拒绝（ToolError 包 ValidationError）。

    action 类型为 ``Literal["list", "scan", "search", "read", "export"]``，
    非法值在 MCP 入口校验阶段即被拒，dispatcher 的 ``❌ 不支持的 history action``
    兜底分支是 Literal 类型保护下的防御性死代码，无法经 mcp.call_tool 触达。
    mcp.server.fastmcp.exceptions.ToolError 是 Exception 子类，用 Exception 捕获
    避免新增 mypy import-not-found 基线。
    """
    client = _ListChatsClient([])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    with pytest.raises(Exception):
        _run(_call(mcp, "gemini_history", action="delete"))


def test_history_facade_search_passes_query_through(monkeypatch):
    """facade 正确转发 query 参数到 search。"""
    client = _ListChatsClient([_chat("c_1", "Needle"), _chat("c_2", "Other")])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_history", action="search",
                        query="needle", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["query"] == "needle"
    assert payload["match_count"] == 1
    assert payload["matches"][0]["id"] == "c_1"


# ===========================================================================
# Section C: gemini_scan_chat_history_sources
# ===========================================================================


def _source_block(name, items, stopped_reason=None):
    """构造 _fetch_conversation_metadata_sources 返回的 source_block。"""
    diagnostic = {"source_rpc": "MaZiqc", "fetched_count": len(items)}
    if stopped_reason:
        diagnostic["stopped_reason"] = stopped_reason
    return {"name": name, "rpcid": "MaZiqc", "items": items, "diagnostic": diagnostic}


def test_scan_chat_history_sources_no_batch_execute_short_circuits(monkeypatch):
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources"))
    assert "❌ 当前客户端不支持 Gemini Web RPC 深度扫描" in result[0].text


def test_scan_chat_history_sources_empty_sources_renders_empty(monkeypatch):
    """所有 source 为空 → markdown 仍渲染结构，items=[]。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=False, include_remy_goals=False))
    text = result[0].text
    assert "## Gemini 历史对话深度扫描" in text
    assert "合并唯一对话: 0" in text
    assert "### 来源计数" in text


def test_scan_chat_history_sources_renders_source_counts(monkeypatch):
    """source_counts 渲染每个 source 的 name + count。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    sources = [
        _source_block("ui_pinned", [{"id": "c_1", "title": "Pinned", "time": ""}]),
        _source_block("ui_recent", [{"id": "c_2", "title": "Recent", "time": ""}]),
    ]
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=sources))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=False, include_remy_goals=False))
    text = result[0].text
    assert "### 来源计数" in text
    assert "ui_pinned: 1" in text
    assert "ui_recent: 1" in text


def test_scan_chat_history_sources_coverage_warnings_rendered(monkeypatch):
    """stopped_reason in {max_items, max_pages} → coverage_warnings 段渲染。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    sources = [
        _source_block("ui_pinned", [{"id": "c_1", "title": "P", "time": ""}], "max_items"),
        _source_block("ui_recent", [{"id": "c_2", "title": "R", "time": ""}], "max_pages"),
    ]
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=sources))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=False, include_remy_goals=False))
    text = result[0].text
    assert "### 覆盖警告" in text
    assert "ui_pinned: max_items" in text
    assert "ui_recent: max_pages" in text


def test_scan_chat_history_sources_no_coverage_warning_for_other_stopped_reasons(monkeypatch):
    """stopped_reason 非 {max_items, max_pages} → 不渲染覆盖警告。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    sources = [
        _source_block("ui_pinned", [{"id": "c_1", "title": "P", "time": ""}], "no_next_page_token"),
    ]
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=sources))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=False, include_remy_goals=False))
    text = result[0].text
    assert "### 覆盖警告" not in text


def test_scan_chat_history_sources_json_payload(monkeypatch):
    """response_format=json → 完整 payload 含 source_counts/coverage_warnings/scan_parameters。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    sources = [
        _source_block("ui_pinned", [{"id": "c_1", "title": "P", "time": ""}], "max_items"),
    ]
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=sources))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=False, include_remy_goals=False,
                        response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["source_rpc"] == "MaZiqc"
    assert payload["source_counts"]["ui_pinned"] == 1
    assert len(payload["coverage_warnings"]) == 1
    assert payload["coverage_warnings"][0]["source"] == "ui_pinned"
    assert payload["coverage_warnings"][0]["stopped_reason"] == "max_items"
    assert payload["scan_parameters"]["include_notebook_chats"] is False
    assert payload["scan_parameters"]["include_remy_goals"] is False
    assert payload["notebooks"]["included"] is False


def test_scan_chat_history_sources_includes_notebook_chats(monkeypatch):
    """include_notebook_chats=True → _fetch_native_notebooks + _fetch_notebook_chats 被调用。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=[]))
    notebooks = [_notebook("n_1", "Math")]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {"source_rpc": "CNgdBe"})))
    notebook_items = [{"id": "c_nb", "title": "Notebook Chat", "time": ""}]
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=(notebook_items, {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 100,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=True, include_remy_goals=False,
                        response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["notebooks"]["included"] is True
    assert len(payload["notebooks"]["items"]) == 1
    assert payload["notebooks"]["items"][0]["title"] == "Math"
    assert payload["source_counts"]["notebook:Math"] == 1


def test_scan_chat_history_sources_includes_remy_goals(monkeypatch):
    """include_remy_goals=True → _fetch_remy_goal_conversation_refs 被调用。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    remy_block = {"name": "remy_goals", "rpcid": "XPSWpd",
                  "items": [{"id": "c_remy", "title": "Remy Goal", "time": ""}],
                  "diagnostic": {"source_rpc": "XPSWpd", "fetched_count": 1}}
    monkeypatch.setattr(manage_tools, "_fetch_remy_goal_conversation_refs",
                        AsyncMock(return_value=remy_block))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=False, include_remy_goals=True,
                        response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["source_counts"]["remy_goals"] == 1


def test_scan_chat_history_sources_clamps_parameters(monkeypatch):
    """max_items_per_source / page_size / max_pages_per_source 非法 → clamp。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    captured = {}

    async def fake_fetch_sources(_c, _filters, *, max_items_per_source, page_size, max_pages_per_source):
        captured["max_items"] = max_items_per_source
        captured["page_size"] = page_size
        captured["max_pages"] = max_pages_per_source
        return []
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources", fake_fetch_sources)
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    _run(_call(mcp, "gemini_scan_chat_history_sources",
               max_items_per_source=-5, page_size=99999, max_pages_per_source=0,
               include_notebook_chats=False, include_remy_goals=False,
               response_format="json"))
    # max_items -5 → clamp 1；page_size 99999 → clamp 100；max_pages 0 → clamp 1
    assert captured["max_items"] == 1
    assert captured["page_size"] == 100
    assert captured["max_pages"] == 1


def test_scan_chat_history_sources_markdown_renders_items_and_has_more(monkeypatch):
    """markdown 渲染当前页 items + has_more 下一页。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    # 2 个 item，limit=1 → has_more=True。
    # _merge_conversation_source_items 按 (timestamp, id) reverse=True 排序，
    # 给 c_1 较高 timestamp 使其排在当前页，便于断言 📌 渲染。
    sources = [
        _source_block("ui_recent", [
            {"id": "c_1", "title": "First", "time": "", "is_pinned": True,
             "sources": ["ui_recent"], "timestamp": 1000},
            {"id": "c_2", "title": "Second", "time": "", "is_pinned": False,
             "sources": ["ui_recent"], "timestamp": 500},
        ]),
    ]
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(return_value=sources))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        limit=1, include_notebook_chats=False, include_remy_goals=False))
    text = result[0].text
    assert "### 当前页" in text
    assert "1. First" in text
    assert "📌" in text  # is_pinned 渲染
    assert "下一页: offset=1" in text  # has_more


def test_scan_chat_history_sources_top_level_exception(monkeypatch):
    """_fetch_conversation_metadata_sources 抛异常 → 顶层 except。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_conversation_metadata_sources",
                        AsyncMock(side_effect=RuntimeError("rpc down")))
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_scan_chat_history_sources",
                        include_notebook_chats=False, include_remy_goals=False))
    assert "❌ 深度扫描失败" in result[0].text
    assert "rpc down" in result[0].text
