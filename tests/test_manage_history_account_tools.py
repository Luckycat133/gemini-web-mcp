"""manage.py 的历史/账号只读工具与依赖 helper 行为契约测试。

调研发现 manage.py 在 Cycle 27 后仍有 396 miss（79% 覆盖率）。本文件覆盖下一批
高 ROI 簇——4 个 MCP handler + 关键 helper，此前仅 ``test_tool_workflows.py``
有 happy path 间接覆盖，关键分支零断言：

1. ``gemini_search_chats`` [2506-2625]：34 行，空 query / scan_turns 无 read_chat
   早退、``_batch_execute`` 路径 vs ``list_chats`` 路径、title/id/turn 三类匹配、
   matched_fields 排序去重、scan_turns 异常吞咽为 snippet error、has_remote_more
   覆盖 has_more、markdown 说明 footer、response_format=json、顶层 except。
2. ``gemini_get_tool_mode_status`` [3707-3770]：24 行，零现有测试。无 _batch_execute
   早退、body 非列表 / 空 body / leading_enabled 三态（True/False/None）/ body[1]
   非列表、空 page 提示、markdown 渲染、has_more 下一页、response_format=json、
   顶层 except。
3. ``gemini_get_usage_limits`` [3022-3084]：24 行。scope → probe_names 映射
   （all=2 / quota=1 / model_state=1）、bodies 结构 4 分支（空 / 非列表 / 空列表 /
   first 非列表）、_parse_usage_entry reset_time 解析、markdown 暂无条目、
   response_format=json、顶层 except。
4. ``gemini_list_notebook_chats`` [3179-3236]：27 行。无 _batch_execute 早退、
   _find_notebook 未命中（空 / 非空可用标题）、命中（by id / exact title /
   casefold title）、items 空、time 渲染、has_more 下一页、response_format=json
   ok 矩阵、顶层 except。

辅助 helper 同步覆盖：``_turn_matches_query`` / ``_read_chat_turns`` /
``_parse_tool_mode_entry`` / ``_parse_usage_entry`` / ``_find_notebook`` /
``_fetch_notebook_chats``。

mock 边界：
- client_wrapper 接缝：``get_gemini_client`` / ``initialize_client``
- tools.manage 内部接缝：``_extract_rpc_bodies`` / ``_fetch_native_notebooks`` /
  ``_fetch_notebook_chats`` / ``_fetch_recent_conversation_metadata``
- 调用方式：MCP handler 经 ``register_manage_tools(mcp, layers=["all"])`` 注册后
  通过 ``mcp.call_tool`` 分发；helper 直接 await 调用。
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
        self.read_calls = []

    async def read_chat(self, chat_id, limit=20):
        self.read_calls.append((chat_id, limit))
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
# Section A: gemini_search_chats + helpers
# ===========================================================================


def test_turn_matches_query_role_match():
    """role 字段包含 query → True。"""
    turn = {"role": "user", "text": "unrelated"}
    assert manage_tools._turn_matches_query(turn, "user") is True


def test_turn_matches_query_text_match():
    """text 字段包含 query → True。"""
    turn = {"role": "model", "text": "find hotel options"}
    assert manage_tools._turn_matches_query(turn, "hotel") is True


def test_turn_matches_query_case_insensitive():
    """大小写不敏感匹配。"""
    turn = {"role": "Model", "text": "Needle found"}
    assert manage_tools._turn_matches_query(turn, "NEEDLE") is True


def test_turn_matches_query_empty_returns_false():
    """空 query（strip 后为空）→ False。"""
    turn = {"role": "user", "text": "anything"}
    assert manage_tools._turn_matches_query(turn, "   ") is False


def test_turn_matches_query_no_match():
    turn = {"role": "user", "text": "nothing here"}
    assert manage_tools._turn_matches_query(turn, "missing") is False


def test_read_chat_turns_without_read_chat_raises():
    """client 无 read_chat → RuntimeError。"""
    client = _ListChatsClient([])
    raised = False
    try:
        _run(manage_tools._read_chat_turns(client, "c_1", 20, 1000))
    except RuntimeError as e:
        raised = True
        assert "read_chat" in str(e)
    assert raised


def test_read_chat_turns_returns_truncated_dicts():
    """read_chat 返回的 turns 经 _turn_to_dict 截断并按 limit 切片。"""
    turns = [_turn("user", "x" * 50), _turn("model", "y" * 50), _turn("user", "z")]
    client = _ReadChatClient([], {"c_1": turns})

    history, parsed = _run(manage_tools._read_chat_turns(client, "c_1", 2, 10))
    assert history is not None
    assert len(parsed) == 2  # limit=2 切片
    assert parsed[0]["role"] == "user"
    assert parsed[0]["text"].endswith("[truncated]")  # 50 字符 → 截断到 10
    assert parsed[1]["role"] == "model"


def test_read_chat_turns_empty_history_returns_empty():
    """history 为 None → 空 turns。"""
    client = _ReadChatClient([], {})
    client._turns_by_id = {}  # read_chat 返回的 turns 由 history 决定

    async def read_chat_none(chat_id, limit=20):
        return None
    client.read_chat = read_chat_none

    history, parsed = _run(manage_tools._read_chat_turns(client, "c_1", 20, 1000))
    assert history is None
    assert parsed == []


def test_search_chats_empty_query_short_circuits(monkeypatch):
    """空 query 在 client init 后立即早退。"""
    client = _ListChatsClient([])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query=""))
    assert "❌ 搜索聊天需要提供 query" in result[0].text


def test_search_chats_whitespace_query_short_circuits(monkeypatch):
    """纯空白 query strip 后为空 → 早退。"""
    client = _ListChatsClient([])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="   \t  "))
    assert "❌ 搜索聊天需要提供 query" in result[0].text


def test_search_chats_scan_turns_without_read_chat_short_circuits(monkeypatch):
    """scan_turns=True 但 client 无 read_chat → 早退。"""
    client = _ListChatsClient([])  # 有 list_chats 但无 read_chat
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="x", scan_turns=True))
    assert "❌ 当前 gemini-webapi 不支持正文搜索需要的 read_chat" in result[0].text


def test_search_chats_list_chats_path_match_by_title(monkeypatch):
    """无 _batch_execute → 走 client_cache 路径；title 匹配。"""
    client = _ListChatsClient([
        _chat("c_1", "Alpha Report", timestamp=1760000000),
        _chat("c_2", "Other", timestamp=1760000100),
    ])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="alpha", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["match_count"] == 1
    assert payload["matches"][0]["id"] == "c_1"
    assert payload["matches"][0]["matched_fields"] == ["title"]
    assert payload["diagnostic"]["source"] == "client_cache"
    assert payload["diagnostic"]["has_remote_more"] is False
    assert "snippets" not in payload["matches"][0]


def test_search_chats_match_by_id_only(monkeypatch):
    """query 只在 id 中出现 → matched_fields=["id"]。"""
    client = _ListChatsClient([
        _chat("alpha_1", "Unrelated Title"),
        _chat("c_2", "Other"),
    ])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="alpha", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["match_count"] == 1
    assert payload["matches"][0]["id"] == "alpha_1"
    assert payload["matches"][0]["matched_fields"] == ["id"]


def test_search_chats_match_by_title_and_id_sorted(monkeypatch):
    """query 同时匹配 title 和 id → matched_fields 排序去重为 ["id", "title"]。"""
    client = _ListChatsClient([_chat("alpha", "alpha doc")])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="alpha", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["matches"][0]["matched_fields"] == ["id", "title"]


def test_search_chats_no_match_renders_no_match_line(monkeypatch):
    """无匹配 → markdown 含 '未在当前页找到匹配项。' 与说明 footer。"""
    client = _ListChatsClient([_chat("c_1", "Completely Unrelated")])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="missing"))
    text = result[0].text
    assert "未在当前页找到匹配项" in text
    assert "说明: 当前只搜索标题/ID" in text  # 非 scan_turns → footer


def test_search_chats_scan_turns_match_in_turn(monkeypatch):
    """scan_turns=True 且 turn 文本匹配 → matched_fields 含 'turn'，snippets 渲染。"""
    client = _ReadChatClient(
        [_chat("c_1", "Work log"), _chat("c_2", "Travel")],
        {"c_1": [_turn("user", "nothing")], "c_2": [_turn("model", "needle found")]},
    )
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats",
                        query="needle", scan_turns=True, response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["match_count"] == 1
    match = payload["matches"][0]
    assert match["id"] == "c_2"
    assert "turn" in match["matched_fields"]
    assert match["snippets"][0]["turn_index"] == 1
    assert match["snippets"][0]["role"] == "model"
    assert "needle found" in match["snippets"][0]["text"]
    assert client.read_calls == [("c_1", 20), ("c_2", 20)]


def test_search_chats_scan_turns_read_error_becomes_snippet_error(monkeypatch):
    """read_chat 抛异常 → snippet 记录 error，turns=[] 不中断循环。"""
    client = _ReadChatClient([_chat("c_1", "Title")], {})

    async def boom(chat_id, limit=20):
        raise ValueError("boom")
    client.read_chat = boom
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats",
                        query="title", scan_turns=True, response_format="json"))
    payload = json.loads(result[0].text)
    # title 仍匹配，read_chat 异常被吞咽为 snippet error
    assert payload["match_count"] == 1
    match = payload["matches"][0]
    assert match["matched_fields"] == ["title"]  # turn 未匹配
    assert match["snippets"][0]["error"] == "ValueError: boom"


def test_search_chats_batch_execute_path_uses_fetch_recent(monkeypatch):
    """有 _batch_execute → 走 _fetch_recent_conversation_metadata 路径。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    captured = {}

    async def fake_fetch(client_arg, target_count):
        captured["client"] = client_arg
        captured["target"] = target_count
        return ([_chat("c_1", "Alpha")], {"has_remote_more": False, "source_rpc": "MaZiqc"})
    monkeypatch.setattr(manage_tools, "_fetch_recent_conversation_metadata", fake_fetch)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="alpha", limit=5, offset=0))
    assert captured["client"] is client
    assert captured["target"] == 5  # safe_limit + safe_offset = 5 + 0
    assert "Alpha" in result[0].text


def test_search_chats_has_remote_more_overrides_pagination(monkeypatch):
    """diagnostic.has_remote_more=True 且 pagination.has_more=False → 强制 has_more=True。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)

    async def fake_fetch(_c, _t):
        # 单条结果，limit=10 → pagination.has_more=False，但 diagnostic 说远端还有
        return ([_chat("c_1", "Alpha")], {"has_remote_more": True})
    monkeypatch.setattr(manage_tools, "_fetch_recent_conversation_metadata", fake_fetch)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="alpha", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["has_more"] is True
    assert payload["next_offset"] == 1  # offset(0) + count(1)


def test_search_chats_markdown_renders_snippet_and_footer(monkeypatch):
    """markdown 输出含 snippet 行与 scan_turns 时不显示 footer。"""
    client = _ReadChatClient(
        [_chat("c_1", "Needle Title")],
        {"c_1": [_turn("user", "needle in text")]},
    )
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="needle", scan_turns=True))
    text = result[0].text
    assert "## Gemini 历史搜索" in text
    # id="c_1" 不含 needle，故只有 title+turn 匹配
    assert "fields=title, turn" in text
    assert "turn 1 user: needle in text" in text
    # scan_turns=True → 不显示 "当前只搜索标题/ID" footer
    assert "当前只搜索标题/ID" not in text


def test_search_chats_snippet_text_newlines_replaced(monkeypatch):
    """snippet text 中的换行被替换为空格（markdown 单行渲染）。"""
    client = _ReadChatClient(
        [_chat("c_1", "Needle")],
        {"c_1": [_turn("user", "line1\nline2\nneedle")]},
    )
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="needle", scan_turns=True))
    text = result[0].text
    assert "turn 1 user: line1 line2 needle" in text


def test_search_chats_top_level_exception_returns_error(monkeypatch):
    """_fetch_recent_conversation_metadata 抛异常 → 顶层 except 返回错误。"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)

    async def boom(_c, _t):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(manage_tools, "_fetch_recent_conversation_metadata", boom)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_search_chats", query="x"))
    assert "❌ 搜索失败" in result[0].text
    assert "rpc down" in result[0].text


def test_search_chats_clamps_invalid_limit_offset(monkeypatch):
    """limit/offset 非法 → _clamp_int 收敛到 [1,50] / [0,5000]。"""
    client = _ListChatsClient([_chat("c_1", "Alpha")])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    # limit=-5 → clamp 到 1；offset=99999 → clamp 到 len(chats)=1
    result = _run(_call(mcp, "gemini_search_chats",
                        query="alpha", limit=-5, offset=99999, response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["offset"] == 1  # clamp 到 max(len(items), 0)=1
    assert payload["limit"] == 1


# ===========================================================================
# Section B: gemini_get_tool_mode_status + _parse_tool_mode_entry
# ===========================================================================


def test_parse_tool_mode_entry_non_list_returns_raw_type():
    """非 list entry → {'raw_type': ...}。"""
    result = manage_tools._parse_tool_mode_entry("not a list")
    assert result == {"raw_type": "str"}


def test_parse_tool_mode_entry_full_list():
    """完整 6 字段 entry → 全字段解析。"""
    entry = [42, True, 100, 30, "extra", "active"]
    result = manage_tools._parse_tool_mode_entry(entry)
    assert result == {
        "mode_id": 42,
        "available": True,
        "quota_value": 100,
        "used_value": 30,
        "reset_or_extra": "extra",
        "state": "active",
        "field_count": 6,
    }


def test_parse_tool_mode_entry_partial_list():
    """部分字段 entry → 缺失字段为 None。"""
    result = manage_tools._parse_tool_mode_entry([42])
    assert result["mode_id"] == 42
    assert result["available"] is None  # entry[1] 不存在
    assert result["field_count"] == 1


def test_parse_tool_mode_entry_available_not_bool_becomes_none():
    """entry[1] 非 bool → available=None。"""
    result = manage_tools._parse_tool_mode_entry([1, "yes", 10])
    assert result["mode_id"] == 1
    assert result["available"] is None  # "yes" 非 bool
    assert result["quota_value"] == 10


def test_get_tool_mode_status_no_batch_execute_short_circuits(monkeypatch):
    """无 _batch_execute → 早退。"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status"))
    assert "❌ 当前客户端不支持工具模式状态 RPC" in result[0].text


def test_get_tool_mode_status_empty_bodies_renders_empty_hint(monkeypatch):
    """bodies 为空 → body=[]，entries=[]，page 空 → '暂无工具模式状态条目。'"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status"))
    assert "暂无工具模式状态条目" in result[0].text


def test_get_tool_mode_status_body_non_list_renders_empty(monkeypatch):
    """body 非 list → entries=[]，page 空。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: ["not a list"])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status"))
    assert "暂无工具模式状态条目" in result[0].text


def test_get_tool_mode_status_leading_enabled_true(monkeypatch):
    """body[0]=True → leading_enabled=True，body[1] 为 entries 列表。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    entries = [[1, True, 100, 30, None, "active"]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[True, entries]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["leading_enabled"] is True
    assert payload["count"] == 1
    assert payload["items"][0]["mode_id"] == 1
    assert payload["source_rpc"]  # probe rpcid


def test_get_tool_mode_status_leading_enabled_false(monkeypatch):
    """body[0]=False → leading_enabled=False。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[False, []]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["leading_enabled"] is False
    assert payload["items"] == []


def test_get_tool_mode_status_leading_enabled_none_when_not_bool(monkeypatch):
    """body[0] 非 bool → leading_enabled=None。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [["not_bool", [[1, True]]]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["leading_enabled"] is None
    assert payload["items"][0]["mode_id"] == 1


def test_get_tool_mode_status_body1_not_list_entries_empty(monkeypatch):
    """body[1] 非 list → entries=[]，但仍渲染 leading_enabled。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[True, "not_a_list"]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["leading_enabled"] is True
    assert payload["items"] == []


def test_get_tool_mode_status_markdown_renders_entries_and_has_more(monkeypatch):
    """markdown 输出含 mode_id 行与 has_more 下一页提示。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    entries = [[1, True, 100, 30, None, "active"], [2, False, 50, 10, None, "idle"]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[True, entries]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status", limit=1))
    text = result[0].text
    assert "## Gemini 工具/模式状态" in text
    assert "leading_enabled=True" in text
    assert "mode_id=1" in text
    assert "available=True" in text
    assert "下一页: offset=1" in text  # has_more=True
    assert "mode_id 是 Gemini Web 内部枚举" in text  # 说明行


def test_get_tool_mode_status_top_level_exception(monkeypatch):
    """_execute_observed_rpc 抛异常 → 顶层 except 返回错误。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: (_ for _ in ()).throw(RuntimeError("parse fail")))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_tool_mode_status"))
    assert "❌ 读取工具模式状态失败" in result[0].text
    assert "parse fail" in result[0].text


# ===========================================================================
# Section C: gemini_get_usage_limits + _parse_usage_entry
# ===========================================================================


def test_parse_usage_entry_non_list_returns_raw_type():
    assert manage_tools._parse_usage_entry("nope") == {"raw_type": "str"}


def test_parse_usage_entry_full_with_reset_time():
    """完整 entry 含 reset=[seconds, nanos] → reset_time 解析。"""
    entry = ["quota_key", "ok", "free", [1000, 500000000], 100, 80]
    result = manage_tools._parse_usage_entry(entry)
    assert result["key"] == "quota_key"
    assert result["status"] == "ok"
    assert result["tier"] == "free"
    assert result["limit_value"] == 100
    assert result["remaining_value"] == 80
    assert result["reset_timestamp"] == 1000.5  # 1000 + 0.5e9/1e9
    assert result["reset_time"]  # _format_timestamp 返回非空
    assert result["field_count"] == 6


def test_parse_usage_entry_without_reset():
    """entry 无 reset 字段（len<=3）→ reset_time=""，reset_timestamp=None。"""
    entry = ["key", "status", "tier"]
    result = manage_tools._parse_usage_entry(entry)
    assert result["reset_timestamp"] is None
    assert result["reset_time"] == ""
    assert result["limit_value"] is None
    assert result["remaining_value"] is None
    assert result["field_count"] == 3


def test_parse_usage_entry_reset_not_list():
    """reset 非 list → reset_time=""，reset_timestamp=None。"""
    entry = ["key", "ok", "free", "not_a_list", 100, 80]
    result = manage_tools._parse_usage_entry(entry)
    assert result["reset_timestamp"] is None
    assert result["reset_time"] == ""
    assert result["limit_value"] == 100


def test_parse_usage_entry_reset_seconds_not_number():
    """reset=[seconds_non_number, ...] → reset_timestamp=None。"""
    entry = ["key", "ok", "free", ["not_a_number", 0], 100, 80]
    result = manage_tools._parse_usage_entry(entry)
    assert result["reset_timestamp"] is None
    assert result["reset_time"] == ""


def test_parse_usage_entry_empty_list():
    result = manage_tools._parse_usage_entry([])
    assert result["key"] is None
    assert result["field_count"] == 0


def test_get_usage_limits_no_batch_execute_short_circuits(monkeypatch):
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits"))
    assert "❌ 当前客户端不支持用量限额 RPC" in result[0].text


def test_get_usage_limits_scope_all_calls_two_probes(monkeypatch):
    """scope=all → probe_names=[usage_quota, usage_model_state]，2 次 _batch_execute。"""
    client = _FakeBatchClient(responses=["r1", "r2"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[[["key1", "ok", "free", None, 100, 80]]]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="all", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["scope"] == "all"
    assert payload["count"] == 2
    assert payload["results"][0]["name"] == "usage_quota"
    assert payload["results"][1]["name"] == "usage_model_state"
    assert client.call_count == 2


def test_get_usage_limits_scope_quota_calls_one_probe(monkeypatch):
    """scope=quota → 仅 usage_quota，1 次 _batch_execute。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="quota", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["count"] == 1
    assert payload["results"][0]["name"] == "usage_quota"
    assert payload["results"][0]["entries"] == []  # bodies 空


def test_get_usage_limits_scope_model_state_calls_one_probe(monkeypatch):
    """scope=model_state → 仅 usage_model_state。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits",
                        scope="model_state", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["results"][0]["name"] == "usage_model_state"


def test_get_usage_limits_bodies0_not_list_entries_empty(monkeypatch):
    """bodies[0] 非 list → entries=[]。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: ["not_a_list"])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="quota", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["results"][0]["entries"] == []


def test_get_usage_limits_bodies0_empty_list_entries_empty(monkeypatch):
    """bodies[0]=[] → 空列表短路，entries=[]。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [[]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="quota", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["results"][0]["entries"] == []


def test_get_usage_limits_first_not_list_entries_empty(monkeypatch):
    """bodies[0][0] 非 list → entries=[]（first 不是 entries 列表）。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [["not_a_list_of_entries"]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="quota", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["results"][0]["entries"] == []


def test_get_usage_limits_markdown_renders_no_entries_hint(monkeypatch):
    """markdown：entries 为空 → '- 暂无条目'。"""
    client = _FakeBatchClient(responses=["r1", "r2"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="all"))
    text = result[0].text
    assert "## Gemini 用量限额" in text
    assert "范围: all" in text
    assert "### usage_quota" in text
    assert "- 暂无条目" in text


def test_get_usage_limits_markdown_renders_entries_with_reset(monkeypatch):
    """markdown：entries 含 reset_time → 渲染 reset=... 字段。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    entry = ["key1", "ok", "free", [1000, 0], 100, 80]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[[entry]]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="quota"))
    text = result[0].text
    assert "key=key1" in text
    assert "status=ok" in text
    assert "tier=free" in text
    assert "limit=100" in text
    assert "remaining=80" in text
    assert "reset=" in text


def test_get_usage_limits_markdown_omits_reset_when_absent(monkeypatch):
    """markdown：entry 无 reset_time → 不渲染 reset= 字段。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    entry = ["key1", "ok", "free", None, 100, 80]  # reset=None
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [[[entry]]])
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="quota"))
    text = result[0].text
    assert "key=key1" in text
    assert "reset=" not in text


def test_get_usage_limits_top_level_exception(monkeypatch):
    """_extract_rpc_bodies 抛异常 → 顶层 except 返回错误。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: (_ for _ in ()).throw(RuntimeError("parse fail")))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_usage_limits", scope="quota"))
    assert "❌ 读取用量限额失败" in result[0].text
    assert "parse fail" in result[0].text


# ===========================================================================
# Section D: gemini_list_notebook_chats + _find_notebook + _fetch_notebook_chats
# ===========================================================================


def test_find_notebook_by_id():
    notebooks = [{"id": "n_1", "title": "Math"}, {"id": "n_2", "title": "Science"}]
    assert manage_tools._find_notebook(notebooks, notebook_id="n_2") == {"id": "n_2", "title": "Science"}


def test_find_notebook_by_id_no_match_returns_none():
    notebooks = [{"id": "n_1", "title": "Math"}]
    assert manage_tools._find_notebook(notebooks, notebook_id="missing") is None


def test_find_notebook_by_exact_title():
    notebooks = [{"id": "n_1", "title": "Math"}, {"id": "n_2", "title": "Science"}]
    assert manage_tools._find_notebook(notebooks, notebook_title="Math") == {"id": "n_1", "title": "Math"}


def test_find_notebook_by_casefold_title():
    """exact title 无匹配 → casefold 匹配。"""
    notebooks = [{"id": "n_1", "title": "Math"}]
    result = manage_tools._find_notebook(notebooks, notebook_title="MATH")
    assert result == {"id": "n_1", "title": "Math"}


def test_find_notebook_ambiguous_exact_returns_none():
    """exact title 多匹配 → 不走 casefold，返回 None。"""
    notebooks = [{"id": "n_1", "title": "Math"}, {"id": "n_2", "title": "Math"}]
    assert manage_tools._find_notebook(notebooks, notebook_title="Math") is None


def test_find_notebook_ambiguous_folded_returns_none():
    """casefold title 多匹配 → 返回 None。"""
    notebooks = [{"id": "n_1", "title": "Math"}, {"id": "n_2", "title": "MATH"}]
    # exact 无匹配（"math" != "Math" != "MATH"），casefold 多匹配 → None
    assert manage_tools._find_notebook(notebooks, notebook_title="math") is None


def test_find_notebook_empty_inputs_returns_none():
    assert manage_tools._find_notebook([], notebook_id="", notebook_title="") is None


def test_find_notebook_strips_whitespace():
    notebooks = [{"id": "n_1", "title": "Math"}]
    assert manage_tools._find_notebook(notebooks, notebook_id="  n_1  ") == notebooks[0]
    assert manage_tools._find_notebook(notebooks, notebook_title="  Math  ") == notebooks[0]


def test_fetch_notebook_chats_single_page(monkeypatch):
    """单页：body 有 entries 且无 next_page_token → 一次 _batch_execute 即止。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    entries = [["c_1", "Chat 1", False, None, None, None, None, None, None, None, None, None, None, None]]
    # body=[next_page_token, raw_entries, raw_entries_dup?] —— 实际 body[1]=token, body[2]=entries
    body = [None, None, entries]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [body])
    items, page_payload = _run(manage_tools._fetch_notebook_chats(client, "n_1", 20, 0))
    assert client.call_count == 1
    assert len(items) == 1
    assert items[0]["id"] == "c_1"
    assert items[0]["title"] == "Chat 1"
    assert page_payload["count"] == 1
    assert page_payload["has_more"] is False
    assert page_payload["diagnostic"]["page_count"] == 1
    assert page_payload["diagnostic"]["has_remote_more"] is False


def test_fetch_notebook_chats_multi_page_until_no_token(monkeypatch):
    """多页：首页有 token + entries，次页无 token → 2 次 _batch_execute 后停止。"""
    client = _FakeBatchClient(responses=["r1", "r2"])
    _patch_seams(monkeypatch, client)
    entries1 = [["c_1", "Chat 1"]]
    entries2 = [["c_2", "Chat 2"]]
    # body[1]=next_page_token（str），body[2]=raw_entries
    body1 = [None, "tok_next", entries1]  # body[1]=token（str）→ 继续
    body2 = [None, None, entries2]  # body[1]=None → 停止
    pages = [body1, body2]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [pages.pop(0)])
    items, page_payload = _run(manage_tools._fetch_notebook_chats(client, "n_1", 20, 0))
    assert client.call_count == 2
    assert len(items) == 2
    assert page_payload["diagnostic"]["page_count"] == 2
    assert page_payload["diagnostic"]["has_remote_more"] is False


def test_fetch_notebook_chats_breaks_on_empty_entries(monkeypatch):
    """raw_entries 为空 → 即使有 token 也 break。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    # body[1]=token（str），body[2]=[] 空 entries → break via `not raw_entries`
    body = [None, "tok_next", []]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [body])
    items, page_payload = _run(manage_tools._fetch_notebook_chats(client, "n_1", 20, 0))
    assert client.call_count == 1
    assert items == []
    assert page_payload["count"] == 0


def test_fetch_notebook_chats_clamps_limit_and_offset(monkeypatch):
    """limit/offset 非法 → clamp 到 [1,100] / [0,10000]。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    entries = [["c_1", "Chat 1"]]
    body = [None, None, entries]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [body])
    items, page_payload = _run(manage_tools._fetch_notebook_chats(
        client, "n_1", limit=-5, offset=99999))
    # limit -5 → clamp 1；offset 99999 → clamp 10000；但 items 只有 1 条 → page 从 offset 10000 切片为空
    assert page_payload["limit"] == 1
    assert page_payload["offset"] == 10000
    assert page_payload["count"] == 0
    assert items == []


def test_list_notebook_chats_no_batch_execute_short_circuits(monkeypatch):
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats"))
    assert "❌ 当前客户端不支持 Gemini Notebooks RPC" in result[0].text


def test_list_notebook_chats_notebook_not_found_empty(monkeypatch):
    """notebooks 为空 → 未找到，available_titles=[]。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {"source_rpc": "CNgdBe"})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_title="Missing"))
    assert "未找到匹配的 Gemini 原生笔记本" in result[0].text
    assert "可用标题:" in result[0].text


def test_list_notebook_chats_notebook_not_found_lists_available_titles(monkeypatch):
    """非空 notebooks 但 title 不匹配 → markdown 列出可用标题。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}, {"id": "n_2", "title": "Science"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {"source_rpc": "CNgdBe"})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_title="Missing"))
    text = result[0].text
    assert "Math" in text
    assert "Science" in text


def test_list_notebook_chats_not_found_json_payload(monkeypatch):
    """response_format=json + 未找到 → ok=False payload。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=([], {"source_rpc": "CNgdBe"})))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats",
                        notebook_id="missing", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["notebook_id"] == "missing"
    assert payload["available_titles"] == []
    assert payload["diagnostic"]["source_rpc"] == "CNgdBe"


def test_list_notebook_chats_found_by_id_renders_items(monkeypatch):
    """by id 命中 → items 渲染。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {"source_rpc": "CNgdBe"})))
    items = [{"id": "c_1", "title": "Chat 1", "time": "2025-01-01"}]
    page_payload = {
        "total_count": 1, "count": 1, "offset": 0, "limit": 20,
        "has_more": False, "next_offset": None,
        "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
    }
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=(items, page_payload)))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_id="n_1"))
    text = result[0].text
    assert "## Notebook Chats: Math" in text
    assert "offset=0 count=1" in text
    assert "fetched=1" in text
    assert "1. Chat 1 (ID: c_1)" in text
    assert "2025-01-01" in text  # time_text


def test_list_notebook_chats_found_by_exact_title(monkeypatch):
    """by exact title 命中。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([], {
                            "total_count": 0, "count": 0, "offset": 0, "limit": 20,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 0, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_title="Math"))
    assert "## Notebook Chats: Math" in result[0].text


def test_list_notebook_chats_found_by_casefold_title(monkeypatch):
    """by casefold title 命中。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([], {
                            "total_count": 0, "count": 0, "offset": 0, "limit": 20,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 0, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_title="math"))
    assert "## Notebook Chats: Math" in result[0].text


def test_list_notebook_chats_empty_items_renders_hint(monkeypatch):
    """items 为空 → '- 暂无最近对话。'"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=([], {
                            "total_count": 0, "count": 0, "offset": 0, "limit": 20,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 0, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_id="n_1"))
    assert "- 暂无最近对话" in result[0].text


def test_list_notebook_chats_has_more_renders_next_page(monkeypatch):
    """has_more=True → '下一页: offset=...'"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    items = [{"id": "c_1", "title": "Chat 1", "time": ""}]
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=(items, {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 1,
                            "has_more": True, "next_offset": 1,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": True},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_id="n_1", limit=1))
    assert "下一页: offset=1" in result[0].text


def test_list_notebook_chats_found_json_payload(monkeypatch):
    """response_format=json + 命中 → ok=True payload。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    items = [{"id": "c_1", "title": "Chat 1", "time": ""}]
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=(items, {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 20,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats",
                        notebook_id="n_1", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["notebook"]["id"] == "n_1"
    assert payload["items"][0]["id"] == "c_1"
    assert payload["source_rpc"] == "MaZiqc"


def test_list_notebook_chats_top_level_exception(monkeypatch):
    """_fetch_native_notebooks 抛异常 → 顶层 except 返回错误。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(side_effect=RuntimeError("rpc down")))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_id="n_1"))
    assert "❌ 读取 Gemini Notebook 对话失败" in result[0].text
    assert "rpc down" in result[0].text


def test_list_notebook_chats_untitled_when_title_missing(monkeypatch):
    """item 无 title → 渲染 '(untitled)'。"""
    client = _FakeBatchClient(responses=["r1"])
    _patch_seams(monkeypatch, client)
    notebooks = [{"id": "n_1", "title": "Math"}]
    monkeypatch.setattr(manage_tools, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    items = [{"id": "c_1", "title": "", "time": ""}]
    monkeypatch.setattr(manage_tools, "_fetch_notebook_chats",
                        AsyncMock(return_value=(items, {
                            "total_count": 1, "count": 1, "offset": 0, "limit": 20,
                            "has_more": False, "next_offset": None,
                            "diagnostic": {"fetched_count": 1, "page_count": 1, "has_remote_more": False},
                        })))
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebook_chats", notebook_id="n_1"))
    assert "(untitled)" in result[0].text
