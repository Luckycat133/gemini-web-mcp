"""manage.py 的定时操作与 remy_goals helper 行为契约测试。

调研发现 manage.py 此前 505 miss（73% 覆盖率），TOP 3 最高 ROI 簇零直接覆盖：

1. ``_fetch_remy_goal_conversation_refs`` [809-891]：60 行 async helper，
   stopped_reason 分支（no_next_page_token / max_items / empty_page /
   no_new_unique_items / max_pages）+ 参数 clamp + 去重 + 分页 token 透传。
2. ``gemini_create_scheduled_action`` [3471-3600]：43 行 MCP handler，
   参数校验 + 无 _batch_execute + verification_status 矩阵
   （visible_in_registry / not_visible_in_nonempty_registry /
   registry_empty_unverified / readable_by_id_* / verification_error /
   get_task_error）+ 无 created id + response_format=json + 顶层 except。
3. ``gemini_delete_scheduled_action`` [3602-3705]：40 行 MCP handler，
   空 id + 无 _batch_execute + verification_status 矩阵
   （deleted_state_by_id / still_visible_in_registry /
   not_visible_not_readable_by_id / registry_empty_not_readable_by_id /
   not_visible_active_or_unknown_by_id / registry_empty_active_or_unknown_by_id /
   verification_error / get_task_error）+ 空 bodies + 非 200 + response_format=json
   + 顶层 except。

mock 边界：
- client_wrapper 接缝：``get_gemini_client`` / ``initialize_client``
- tools.manage 内部接缝：``_extract_rpc_bodies`` / ``_fetch_scheduled_registry``
  / ``_fetch_scheduled_task_by_id``
- 调用方式：MCP handler 经 ``register_manage_tools(mcp, layers=["all"])``
  注册后通过 ``mcp.call_tool`` 分发；helper 直接 await 调用。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

import src.tools.manage as manage_tools


# ---------------------------------------------------------------------------
# 辅助
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


def _remy_entry(item_id, title="title"):
    """构造 _parse_remy_goal_entry 可解析的 entry（entry[13]=id, entry[1]=title）。"""
    entry = [None] * 14
    entry[1] = title
    entry[13] = item_id
    return entry


def _created_body(task_id="t_123", title="My Title", instructions="instr",
                  schedule_label="每天 9:00", enabled=None):
    """构造 _parse_scheduled_action_create_body 可解析的 body。"""
    details = [None, [[instructions, schedule_label, title]]]
    if enabled is not None:
        details = [None, [[instructions, schedule_label, title]],
                   None, None, None, [enabled]]
    return [task_id, details]


def _registry_entry(item_id):
    return {"id": item_id, "title": "t", "task_state": "active", "task_state_id": 1}


def _task_entry(item_id, task_state_id=None, task_state=""):
    return {"id": item_id, "task_state": task_state, "task_state_id": task_state_id}


# ---------------------------------------------------------------------------
# A. _fetch_remy_goal_conversation_refs（直接 await 调用）
# ---------------------------------------------------------------------------


# _extract_rpc_bodies 返回 bodies（list），bodies[0]=body，body[0]=raw_entries，
# body[1]=next_page_token（str）。故 bodies 形如 [[[entry,...], token]]（3 层）。
# 以下测试直接构造 bodies 作为 fake_extract 的返回值。


def test_remy_goal_stopped_reason_no_next_page_token(monkeypatch):
    """单页有 entries 但无 next_page_token → stopped_reason='no_next_page_token'。"""
    bodies = [[[_remy_entry("a1"), _remy_entry("a2")]]]  # body=[entries], 无 body[1]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["resp1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    assert result["diagnostic"]["stopped_reason"] == "no_next_page_token"
    assert len(result["items"]) == 2
    assert result["items"][0]["id"] == "a1"
    assert result["items"][0]["history_source"] == "remy_goals"
    assert result["diagnostic"]["page_count"] == 1


def test_remy_goal_stopped_reason_max_items(monkeypatch):
    """items 达到 max_items → stopped_reason='max_items'，提前 break。"""
    bodies = [[[_remy_entry("a1"), _remy_entry("a2"), _remy_entry("a3")], "tok"]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["resp1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=2))

    assert result["diagnostic"]["stopped_reason"] == "max_items"
    assert len(result["items"]) == 2


def test_remy_goal_stopped_reason_empty_page(monkeypatch):
    """页面返回空 raw_entries → stopped_reason='empty_page'。"""
    bodies = [[[]]]  # body=[[]], body[0]=[] → raw_entries=[]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["resp1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    assert result["diagnostic"]["stopped_reason"] == "empty_page"
    assert result["items"] == []


def test_remy_goal_stopped_reason_no_new_unique_items(monkeypatch):
    """第二页全部是已见 id → new_unique_count=0 → stopped_reason='no_new_unique_items'。"""
    pages = [
        [[[_remy_entry("a1")], "tok"]],
        [[[_remy_entry("a1")], "tok2"]],  # 重复 id
    ]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: pages.pop(0))

    client = _FakeBatchClient(responses=["r1", "r2"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    assert result["diagnostic"]["stopped_reason"] == "no_new_unique_items"
    assert len(result["items"]) == 1
    assert result["diagnostic"]["page_count"] == 2


def test_remy_goal_stopped_reason_max_pages(monkeypatch):
    """连续有 next_page_token 但未触发其他 break → 达 max_pages → 'max_pages'。"""
    pages = [
        [[[_remy_entry("a1")], "tok"]],
        [[[_remy_entry("a2")], "tok2"]],
    ]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: pages.pop(0))

    client = _FakeBatchClient(responses=["r1", "r2"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(
        client, max_items=100, max_pages=2))

    assert result["diagnostic"]["stopped_reason"] == "max_pages"
    assert result["diagnostic"]["page_count"] == 2
    assert len(result["items"]) == 2


def test_remy_goal_clamps_page_size_and_max_pages_and_max_items(monkeypatch):
    """非法 page_size/max_pages/max_items 被 _clamp_int 收敛到默认/边界。"""
    bodies = [[[_remy_entry("a1")]]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["r1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(
        client,
        max_items="not-a-number",
        page_size=0,        # → minimum 1
        max_pages=-5,       # → minimum 1
    ))

    diag = result["diagnostic"]
    assert diag["page_size"] == 1
    assert diag["max_pages"] == 1
    # max_items 非法 → default 5000，但受 maximum 10000 限制
    assert diag["max_items"] == 5000


def test_remy_goal_clamps_max_items_to_upper_bound(monkeypatch):
    """max_items 超过 10000 → 收敛到 10000。"""
    bodies = [[[_remy_entry("a1")]]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["r1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(
        client, max_items=99999, max_pages=1))

    assert result["diagnostic"]["max_items"] == 10000


def test_remy_goal_request_payload_includes_next_page_token(monkeypatch):
    """第二页起 request_payload 形如 [page_size, next_page_token]。"""
    pages = [
        [[[_remy_entry("a1")], "tok_next"]],
        [[[_remy_entry("a2")]]],  # 无 next_page_token → 终止
    ]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: pages.pop(0))

    client = _FakeBatchClient(responses=["r1", "r2"])
    _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    # 第二次调用的 raw_rpc payload 应包含 next_page_token
    second_payload = client.captured_payloads[1][0]
    payload_list = json.loads(second_payload.payload)
    assert payload_list == [100, "tok_next"]


def test_remy_goal_first_page_request_payload_omits_token(monkeypatch):
    """第一页 request_payload 形如 [page_size]（无 token）。"""
    bodies = [[[_remy_entry("a1")]]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["r1"])
    _run(manage_tools._fetch_remy_goal_conversation_refs(
        client, max_items=100, page_size=50))

    first_payload = client.captured_payloads[0][0]
    payload_list = json.loads(first_payload.payload)
    assert payload_list == [50]


def test_remy_goal_dedup_skips_duplicate_ids(monkeypatch):
    """同一页内重复 id 被去重，仅保留第一个。"""
    bodies = [[[_remy_entry("dup"), _remy_entry("dup"), _remy_entry("uniq")]]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["r1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    ids = [item["id"] for item in result["items"]]
    assert ids == ["dup", "uniq"]
    # 第一页 new_unique_count=2（dup 出现两次但只计一次）
    assert result["diagnostic"]["pages"][0]["new_unique_count"] == 2


def test_remy_goal_skips_entries_without_id(monkeypatch):
    """entry[13] 非 str（或缺失）→ id='' → 被 not item_id 跳过。"""
    no_id_entry = [None, "title"]
    bodies = [[[no_id_entry, _remy_entry("ok")]]]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: bodies)

    client = _FakeBatchClient(responses=["r1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    ids = [item["id"] for item in result["items"]]
    assert ids == ["ok"]


def test_remy_goal_aggregates_response_length(monkeypatch):
    """response_length 累加每页 response.text 长度。"""
    pages = [
        [[[_remy_entry("a1")], "tok"]],
        [[[_remy_entry("a2")]]],
    ]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: pages.pop(0))

    client = _FakeBatchClient(responses=["aaaa", "bbbbbb"])  # 4 + 6 = 10
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    assert result["diagnostic"]["response_length"] == 10


def test_remy_goal_empty_bodies_falls_back_to_empty_page(monkeypatch):
    """_extract_rpc_bodies 返回空 list → body=[] → raw_entries=[] → 'empty_page'。"""
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])

    client = _FakeBatchClient(responses=["r1"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    assert result["diagnostic"]["stopped_reason"] == "empty_page"
    assert result["items"] == []


def test_remy_goal_pages_diagnostic_records_per_page_metadata(monkeypatch):
    """pages 列表记录每页 page/raw_count/new_unique_count/unique_so_far/next_page_token_present。"""
    pages = [
        [[[_remy_entry("a1"), _remy_entry("a2")], "tok"]],
        [[[_remy_entry("a3")]]],
    ]
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: pages.pop(0))

    client = _FakeBatchClient(responses=["r1", "r2"])
    result = _run(manage_tools._fetch_remy_goal_conversation_refs(client, max_items=100))

    pages_diag = result["diagnostic"]["pages"]
    assert len(pages_diag) == 2
    assert pages_diag[0]["page"] == 1
    assert pages_diag[0]["raw_count"] == 2
    assert pages_diag[0]["new_unique_count"] == 2
    assert pages_diag[0]["unique_so_far"] == 2
    assert pages_diag[0]["next_page_token_present"] is True
    assert pages_diag[1]["page"] == 2
    assert pages_diag[1]["next_page_token_present"] is False


# ---------------------------------------------------------------------------
# B. gemini_create_scheduled_action（MCP handler）
# ---------------------------------------------------------------------------


def test_create_scheduled_action_rejects_empty_title(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="  ", instructions="do stuff", hour=9))
    assert "title 不能为空" in result[0].text
    assert client.call_count == 0


def test_create_scheduled_action_rejects_empty_instructions(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="", hour=9))
    assert "instructions 不能为空" in result[0].text
    assert client.call_count == 0


def test_create_scheduled_action_rejects_invalid_hour_negative(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=-1))
    assert "hour 必须在 0 到 23" in result[0].text


def test_create_scheduled_action_rejects_invalid_hour_over_23(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=24))
    assert "hour 必须在 0 到 23" in result[0].text


def test_create_scheduled_action_rejects_empty_timezone(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9, timezone_name="  "))
    assert "timezone_name 不能为空" in result[0].text


def test_create_scheduled_action_rejects_client_without_batch_execute(monkeypatch):
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    assert "不支持定时操作 RPC" in result[0].text


def test_create_scheduled_action_visible_in_registry(monkeypatch):
    """created.id 在 registry 中 → verification_status='visible_in_registry'。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    created_id = "t_123"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id=created_id)])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry(created_id)], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(created_id, 1), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    text = result[0].text
    assert "✅ 已创建" in text
    assert created_id in text
    assert "每天 9:00" in text  # schedule_label


def test_create_scheduled_action_not_visible_in_nonempty_registry(monkeypatch):
    """registry 非空但无匹配 + task by id None → not_visible_in_nonempty_registry。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id="t_1")])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry("other")], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    # ok=True (有 id) 但 not visible + not readable → ⚠️ visibility 警告
    assert "⚠️" in result[0].text
    assert "cookie/session" in result[0].text


def test_create_scheduled_action_registry_empty_unverified(monkeypatch):
    """registry 空 + task by id None → registry_empty_unverified。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id="t_1")])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    assert "⚠️" in result[0].text
    assert "cookie/session" in result[0].text


def test_create_scheduled_action_readable_by_id_registry_empty(monkeypatch):
    """registry 空 + task by id 存在 → readable_by_id_registry_empty。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    created_id = "t_1"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id=created_id)])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(created_id, 1), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    # readable_by_id_after_create=True → visibility 提示"按 ID 可读取"
    assert "按 ID 可读取" in result[0].text


def test_create_scheduled_action_readable_by_id_not_visible_in_registry(monkeypatch):
    """registry 非空无匹配 + task by id 存在 → readable_by_id_not_visible_in_registry。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    created_id = "t_1"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id=created_id)])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry("other")], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(created_id, 1), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    assert "按 ID 可读取" in result[0].text


def test_create_scheduled_action_verification_error(monkeypatch):
    """_fetch_scheduled_registry 抛异常 → verification_status='verification_error'。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id="t_1")])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(side_effect=RuntimeError("registry boom")))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    # 仍 ok=True（有 id），走 ⚠️ 分支（not visible + not readable）
    assert "⚠️" in result[0].text


def test_create_scheduled_action_get_task_error(monkeypatch):
    """_fetch_scheduled_task_by_id 抛异常 → get_task_error 记录，不阻塞返回。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id="t_1")])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry("t_1")], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(side_effect=RuntimeError("task boom")))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    # visible_in_registry=True（registry 命中），即使 get_task 失败仍走 ✅ 分支
    assert "✅ 已创建" in result[0].text


def test_create_scheduled_action_no_created_id(monkeypatch):
    """响应未解析到 created id → ok=False → ⚠️ 未解析到定时操作 id。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    # body=[] → created.get("id")="" → falsy → 无 verification
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [[]])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    assert "⚠️" in result[0].text
    assert "未在响应中解析到定时操作 id" in result[0].text


def test_create_scheduled_action_response_format_json(monkeypatch):
    """response_format='json' → 返回 JSON payload。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    created_id = "t_json"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id=created_id)])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry(created_id)], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(created_id, 1), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9, response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["id"] == created_id
    assert payload["visible_in_registry"] is True
    assert payload["verification_status"] == "visible_in_registry"
    assert payload["hour"] == 9


def test_create_scheduled_action_json_no_created_id(monkeypatch):
    """response_format='json' + 无 created id → ok=False payload。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [[]])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9, response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["id"] == ""


def test_create_scheduled_action_schedule_label_omitted_when_empty(monkeypatch):
    """created 无 schedule_label → markdown 不包含 label 括号。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    created_id = "t_nolabel"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id=created_id, schedule_label="")])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry(created_id)], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(created_id, 1), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    # 无 schedule_label → 不出现 " (" 后缀
    assert " (" not in result[0].text


def test_create_scheduled_action_batch_execute_exception(monkeypatch):
    """_batch_execute 抛异常 → 顶层 except → '❌ 创建定时操作失败'。"""
    client = _FakeBatchClient(raise_exc=RuntimeError("network down"))
    _patch_seams(monkeypatch, client)

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9))
    assert "❌ 创建定时操作失败" in result[0].text
    assert "network down" in result[0].text


def test_create_scheduled_action_default_locale_when_empty(monkeypatch):
    """locale 为空 → 回退到 'zh-CN'。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    created_id = "t_loc"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id=created_id)])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry(created_id)], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(created_id, 1), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9, locale="", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["locale"] == "zh-CN"


def test_create_scheduled_action_non_200_status(monkeypatch):
    """status_code=500 + 有 created id → ok=False → ⚠️ 未解析分支。"""
    client = _FakeBatchClient(responses=["resp"], status_code=500)
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies",
                        lambda _t, _r: [_created_body(task_id="t_1")])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_create_scheduled_action",
                        title="T", instructions="I", hour=9, response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# C. gemini_delete_scheduled_action（MCP handler）
# ---------------------------------------------------------------------------


def test_delete_scheduled_action_rejects_empty_id(monkeypatch):
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="  "))
    assert "action_id 不能为空" in result[0].text
    assert client.call_count == 0


def test_delete_scheduled_action_rejects_client_without_batch_execute(monkeypatch):
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    assert "不支持定时操作 RPC" in result[0].text


def test_delete_scheduled_action_deleted_state_by_id(monkeypatch):
    """task_state_id==6 → deleted_by_id_after_delete=True → '✅ 已删除...deleted'。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    action_id = "a_del"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry(action_id)], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(action_id, 6, "deleted"), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id=action_id))
    assert "✅ 已删除" in result[0].text
    assert "deleted" in result[0].text


def test_delete_scheduled_action_still_visible_in_registry(monkeypatch):
    """registry 仍可见 + task None → still_visible_in_registry → ✅ 接受 + 校验状态。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    action_id = "a_still"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry(action_id)], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id=action_id))
    assert "✅ 删除请求已被 Gemini 接受" in result[0].text
    assert "still_visible_in_registry" in result[0].text


def test_delete_scheduled_action_not_visible_not_readable(monkeypatch):
    """registry 非空无匹配 + task None → not_visible_not_readable_by_id → '✅ 已删除'。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry("other")], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    assert "✅ 已删除 Gemini 定时操作" in result[0].text


def test_delete_scheduled_action_registry_empty_not_readable(monkeypatch):
    """registry 空 + task None → registry_empty_not_readable_by_id → ✅ registry 为空。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    assert "✅ 删除请求已被 Gemini 接受" in result[0].text
    assert "registry 为空" in result[0].text


def test_delete_scheduled_action_not_visible_active_or_unknown(monkeypatch):
    """registry 非空无匹配 + task 存在(state_id!=6) → not_visible_active_or_unknown → ⚠️ 仍可读取。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    action_id = "a_active"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry("other")], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(action_id, 1, "active"), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id=action_id))
    assert "⚠️" in result[0].text
    assert "仍可读取" in result[0].text


def test_delete_scheduled_action_registry_empty_active_or_unknown(monkeypatch):
    """registry 空 + task 存在(state_id!=6) → registry_empty_active_or_unknown → ⚠️ 仍可读取。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    action_id = "a_empty_active"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(action_id, 1, "active"), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id=action_id))
    assert "⚠️" in result[0].text
    assert "仍可读取" in result[0].text


def test_delete_scheduled_action_verification_error(monkeypatch):
    """_fetch_scheduled_registry 抛异常 → verification_status='verification_error'。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(side_effect=RuntimeError("registry boom")))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    # verification_error + task None → readable False → 走 else ✅ 接受分支
    assert "✅ 删除请求已被 Gemini 接受" in result[0].text
    assert "verification_error" in result[0].text


def test_delete_scheduled_action_get_task_error(monkeypatch):
    """_fetch_scheduled_task_by_id 抛异常 → get_task_error 记录。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([_registry_entry("a_1")], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(side_effect=RuntimeError("task boom")))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    # still_visible_in_registry + get_task 失败 → readable None (falsy) → else 分支
    assert "✅ 删除请求已被 Gemini 接受" in result[0].text
    assert "still_visible_in_registry" in result[0].text


def test_delete_scheduled_action_empty_bodies(monkeypatch):
    """bodies 为空 → 不做 verification + ok=False → ⚠️ 响应无法确认。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    assert "⚠️ 删除请求已发送，但响应无法确认" in result[0].text


def test_delete_scheduled_action_non_200_status(monkeypatch):
    """status_code=500 + bodies 非空 → ok=False → ⚠️ 响应无法确认。"""
    client = _FakeBatchClient(responses=["resp"], status_code=500)
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    assert "⚠️ 删除请求已发送，但响应无法确认" in result[0].text


def test_delete_scheduled_action_response_format_json(monkeypatch):
    """response_format='json' → 返回 JSON payload。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    action_id = "a_json"
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(_task_entry(action_id, 6, "deleted"), {})))

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action",
                        action_id=action_id, response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["id"] == action_id
    assert payload["deleted_by_id_after_delete"] is True
    assert payload["verification_status"] == "deleted_state_by_id"


def test_delete_scheduled_action_json_empty_bodies(monkeypatch):
    """response_format='json' + 空 bodies → ok=False payload。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action",
                        action_id="a_1", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert payload["verification_status"] == "not_attempted"


def test_delete_scheduled_action_batch_execute_exception(monkeypatch):
    """_batch_execute 抛异常 → 顶层 except → '❌ 删除定时操作失败'。"""
    client = _FakeBatchClient(raise_exc=RuntimeError("network down"))
    _patch_seams(monkeypatch, client)

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_scheduled_action", action_id="a_1"))
    assert "❌ 删除定时操作失败" in result[0].text
    assert "network down" in result[0].text


def test_delete_scheduled_action_request_payload_format(monkeypatch):
    """删除 RPC payload 形如 [None, [action_id]]。"""
    client = _FakeBatchClient(responses=["resp"])
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [["ok"]])
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))

    action_id = "a_payload"
    mcp = _make_mcp()
    _run(_call(mcp, "gemini_delete_scheduled_action", action_id=action_id))

    first_payload = client.captured_payloads[0][0]
    payload_list = json.loads(first_payload.payload)
    assert payload_list == [None, [action_id]]
