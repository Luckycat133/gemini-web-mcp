"""manage.py 6 个只读 MCP handler 的 markdown 渲染 + inspect_account 行为契约测试。

调研发现 manage.py 中以下 7 个只读 / 读私有 RPC handler 的 markdown 渲染分支
与早退/异常路径此前仅 ``test_tool_workflows.py`` 单点 happy-path 间接覆盖，关键
分支（reject_code 后缀、surface 分组、可达/不可达状态、empty_hint 诊断、enabled
三态、has_more 下一页、顶层 except）零断言：

1. ``gemini_inspect_account`` [2756-2792]：无 inspect_account_status 早退、
   markdown 渲染 summary + rpc（含 HTTP status_code + reject_code 后缀）、
   空 summary+rpc 兜底、json 响应、顶层 except。
2. ``gemini_probe_web_features`` [2794-2880]：19 行 markdown 渲染（surface 分组
   + 可达/不可达 + reject/error suffix）、无 _batch_execute 早退、json 响应。
3. ``gemini_list_public_links`` [2976-3020]：17 行 markdown 渲染（links +
   has_more 下一页）+ 空 placeholder + 顶层 except。
4. ``gemini_list_library_capabilities`` [3086-3132]：16 行 markdown 渲染 + 顶层 except。
5. ``gemini_list_notebooks`` [3134-3177]：16 行 markdown 渲染 + 顶层 except
   （走 ``_fetch_native_notebooks`` 而非 ``_extract_rpc_bodies``）。
6. ``gemini_list_scheduled_actions`` [3361-3416]：21 行 markdown 渲染
   （enabled 三态 + label + hour + timezone + empty_hint）+ 顶层 except。
7. ``gemini_get_scheduled_action`` [3418-3469]：22 行 markdown 渲染
   （found item 单条详情）+ 空 id 早退 + 顶层 except。

mock 边界：
- client_wrapper 接缝：``get_gemini_client`` / ``initialize_client``
- tools.manage 内部接缝：``_extract_rpc_bodies`` / ``_summarize_probe_response``
  / ``_fetch_native_notebooks`` / ``_fetch_scheduled_registry``
  / ``_fetch_scheduled_task_by_id`` / ``WEB_FEATURE_PROBES``
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


class _InspectClient:
    """带 ``inspect_account_status`` 但无 ``_batch_execute`` 的 client，
    用于 ``gemini_inspect_account`` 测试。"""

    def __init__(self, status=None, raise_exc=None):
        self._status = status
        self._raise_exc = raise_exc

    async def inspect_account_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._status


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


def _probe(surface, name, rpcid, payload="[]", source_path="/app", observed="observed"):
    """构造受控的 WEB_FEATURE_PROBES 条目。"""
    return {
        "surface": surface,
        "name": name,
        "rpcid": rpcid,
        "payload": payload,
        "source_path": source_path,
        "observed": observed,
    }


# ===========================================================================
# Section A: gemini_inspect_account
# ===========================================================================


def test_inspect_account_no_inspect_status_short_circuits(monkeypatch):
    """client 无 inspect_account_status → 早退 '❌ 当前 gemini-webapi 不支持 inspect_account_status。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_inspect_account"))
    assert "❌ 当前 gemini-webapi 不支持 inspect_account_status" in result[0].text


def test_inspect_account_markdown_with_summary_and_rpc_and_reject_code(monkeypatch):
    """markdown 渲染 summary（key/value 列表）+ rpc（含 HTTP status_code + reject_code 后缀）。

    - activity: ok=True, status_code=200, reject_code=None → '可用 HTTP 200'（无 reject 后缀）
    - library:  ok=False, status_code=200, reject_code=5   → '不可用 HTTP 200 reject=5'
    - sharing:  ok=False, status_code=None, reject_code=8  → '不可用 reject=8'（无 HTTP 后缀）
    """
    status = {
        "source_path": "/app",
        "account_path": "",
        "summary": {"tier": "plus", "deep_research_feature_present": True},
        "rpc": {
            "activity": {"ok": True, "status_code": 200, "reject_code": None},
            "library": {"ok": False, "status_code": 200, "reject_code": 5},
            "sharing": {"ok": False, "status_code": None, "reject_code": 8},
        },
    }
    client = _InspectClient(status=status)
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_inspect_account"))
    text = result[0].text
    assert text.startswith("## Gemini 账号能力状态")
    assert "- tier: plus" in text
    assert "- deep_research_feature_present: True" in text
    assert "## Web RPC 探测" in text
    assert "- activity: 可用 HTTP 200" in text  # reject_code None → 无 reject 后缀
    assert "- library: 不可用 HTTP 200 reject=5" in text
    assert "- sharing: 不可用 reject=8" in text  # status_code None → 无 HTTP 后缀


def test_inspect_account_markdown_empty_summary_and_rpc(monkeypatch):
    """summary={} 且 rpc={} → 仅渲染标题行，无列表项、无 RPC 段。"""
    status = {"summary": {}, "rpc": {}}
    client = _InspectClient(status=status)
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_inspect_account"))
    text = result[0].text
    assert text == "## Gemini 账号能力状态"
    assert "## Web RPC 探测" not in text


def test_inspect_account_json_response(monkeypatch):
    """response_format=json → 返回 sanitized dict 的 JSON。"""
    status = {
        "source_path": "/app",
        "account_path": "/account",
        "summary": {"tier": "free"},
        "rpc": {
            "activity": {"ok": True, "status_code": 200, "reject_code": None},
        },
    }
    client = _InspectClient(status=status)
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_inspect_account", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["source_path"] == "/app"
    assert payload["account_path"] == "/account"
    assert payload["summary"] == {"tier": "free"}
    assert payload["rpc"]["activity"] == {
        "ok": True, "status_code": 200, "reject_code": None,
    }
    # 不应暴露原始 status 中的 raw_preview 等字段
    assert "raw_preview" not in payload["rpc"]["activity"]


def test_inspect_account_top_level_exception(monkeypatch):
    """inspect_account_status 抛异常 → 顶层 except 返回 '❌ 检查失败: {e}'。"""
    client = _InspectClient(raise_exc=RuntimeError("auth expired"))
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_inspect_account"))
    assert "❌ 检查失败" in result[0].text
    assert "auth expired" in result[0].text


# ===========================================================================
# Section B: gemini_probe_web_features
# ===========================================================================


def test_probe_web_features_no_batch_execute_short_circuits(monkeypatch):
    """client 无 _batch_execute → 早退 '❌ 当前客户端不支持底层 batch RPC 探测。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_probe_web_features"))
    assert "❌ 当前客户端不支持底层 batch RPC 探测" in result[0].text


def test_probe_web_features_markdown_grouped_reachable_unreachable_reject(monkeypatch):
    """markdown 渲染 surface 分组 + 可达/不可达 + reject 后缀。

    - probe A (surface=history, rpcid=rpcA)：可达（ok=True, reject_code=None）
    - probe B (surface=sharing, rpcid=rpcB)：不可达（ok=False, reject_code=7）
    """
    probes = [
        _probe("history", "fake_history_probe", "rpcA", source_path="/app"),
        _probe("sharing", "fake_sharing_probe", "rpcB", source_path="/app/sharing"),
    ]
    monkeypatch.setattr(manage_tools, "WEB_FEATURE_PROBES", probes)
    client = _FakeBatchClient(responses=["r1", "r2"], status_code=200)
    _patch_seams(monkeypatch, client)

    summaries = [
        {"parsed": True, "response_parts": 1, "body_count": 1, "reject_code": None},
        {"parsed": True, "response_parts": 1, "body_count": 0, "reject_code": 7},
    ]

    def fake_summarize(_text, _rpcid):
        return summaries.pop(0)

    monkeypatch.setattr(manage_tools, "_summarize_probe_response", fake_summarize)

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_probe_web_features", surface="all"))
    text = result[0].text
    assert "## Gemini Web 功能探测" in text
    assert "范围: all" in text
    assert "可用: 1/2" in text
    assert "### history" in text
    assert "### sharing" in text
    assert "- fake_history_probe (rpcA): 可达" in text
    assert "- fake_sharing_probe (rpcB): 不可达, reject=7" in text
    assert "说明: 输出已省略原始响应正文和账号内容" in text


def test_probe_web_features_markdown_error_suffix(monkeypatch):
    """_batch_execute 抛异常 → markdown 含 ', error=...' 后缀。"""
    probes = [
        _probe("history", "fake_history_probe", "rpcA", source_path="/app"),
    ]
    monkeypatch.setattr(manage_tools, "WEB_FEATURE_PROBES", probes)
    client = _FakeBatchClient(raise_exc=ValueError("boom"))
    _patch_seams(monkeypatch, client)

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_probe_web_features"))
    text = result[0].text
    assert "## Gemini Web 功能探测" in text
    assert "可用: 0/1" in text
    assert "- fake_history_probe (rpcA): 不可达, error=ValueError: boom" in text


def test_probe_web_features_json_response(monkeypatch):
    """response_format=json → 返回 payload 含 surface/count/ok_count/results/note。"""
    probes = [
        _probe("history", "fake_history_probe", "rpcA", source_path="/app"),
    ]
    monkeypatch.setattr(manage_tools, "WEB_FEATURE_PROBES", probes)
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)

    monkeypatch.setattr(
        manage_tools, "_summarize_probe_response",
        lambda _t, _r: {"parsed": True, "response_parts": 1, "body_count": 1, "reject_code": None},
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_probe_web_features", response_format="json"))
    payload = json.loads(result[0].text)
    assert payload["surface"] == "all"
    assert payload["count"] == 1
    assert payload["ok_count"] == 1
    assert payload["results"][0]["name"] == "fake_history_probe"
    assert payload["results"][0]["rpcid"] == "rpcA"
    assert payload["results"][0]["ok"] is True
    assert payload["results"][0]["reject_code"] is None
    # note 字段说明 probe 输出已省略原始响应正文和账号内容
    assert "omits" in payload["note"]
    assert "response bodies" in payload["note"]


# ===========================================================================
# Section C: gemini_list_public_links
# ===========================================================================


def test_list_public_links_no_batch_execute_short_circuits(monkeypatch):
    """client 无 _batch_execute → 早退 '❌ 当前客户端不支持公开链接 RPC。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_public_links"))
    assert "❌ 当前客户端不支持公开链接 RPC" in result[0].text


def test_list_public_links_markdown_with_links_and_has_more(monkeypatch):
    """markdown 渲染 links（disabled 三态映射 启用/禁用 + ID + URL）+ has_more 下一页。

    _parse_public_link_entry 期望 entry 形如 [id, title, disabled, _, url]。
    """
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
    assert "## Gemini 公开链接" in text
    assert "共 2 条；当前 offset=0 count=1" in text
    assert "1. Link One [启用]" in text
    assert "ID: id_1" in text
    assert "URL: https://example.com/1" in text
    # 第二条因 limit=1 不在当前页，但 has_more=True 触发下一页提示
    assert "Link Two" not in text
    assert "下一页: offset=1" in text


def test_list_public_links_empty_placeholder(monkeypatch):
    """空 entries → '暂无公开链接。'"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [[]])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_public_links"))
    assert result[0].text == "暂无公开链接。"


def test_list_public_links_top_level_exception(monkeypatch):
    """_extract_rpc_bodies 抛异常 → 顶层 except '❌ 读取公开链接失败: {e}'。"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_extract_rpc_bodies",
        lambda _t, _r: (_ for _ in ()).throw(RuntimeError("parse fail")),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_public_links"))
    assert "❌ 读取公开链接失败" in result[0].text
    assert "parse fail" in result[0].text


# ===========================================================================
# Section D: gemini_list_library_capabilities
# ===========================================================================


def test_list_library_capabilities_no_batch_execute_short_circuits(monkeypatch):
    """client 无 _batch_execute → 早退 '❌ 当前客户端不支持 Library RPC。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_library_capabilities"))
    assert "❌ 当前客户端不支持 Library RPC" in result[0].text


def test_list_library_capabilities_markdown_with_entries_and_has_more(monkeypatch):
    """markdown 渲染 aliases/name/description/details + has_more 下一页。

    _parse_library_capability 期望 [aliases_list, name, description, details]。
    bodies 结构：bodies[0] = [[entry1, entry2, ...]]，即 bodies[0][0] = entries。
    """
    entries = [
        [["alias_a", "alias_b"], "Capability One", "描述一", "更多细节"],
        [[], "Capability Two", "描述二", ""],
    ]
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [[entries]])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_library_capabilities", limit=1, offset=0))
    text = result[0].text
    assert "## Gemini Library 能力" in text
    assert "共 2 条；当前 offset=0 count=1" in text
    # 第一条优先用 name
    assert "1. Capability One" in text
    assert "描述一" in text
    assert "更多细节" in text  # details 渲染在换行后
    # 第二条因 limit=1 不在当前页，但 has_more=True
    assert "Capability Two" not in text
    assert "下一页: offset=1" in text


def test_list_library_capabilities_empty_placeholder(monkeypatch):
    """空 entries → '暂无 Library 能力条目。'"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda _t, _r: [[]])

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_library_capabilities"))
    assert result[0].text == "暂无 Library 能力条目。"


def test_list_library_capabilities_top_level_exception(monkeypatch):
    """_extract_rpc_bodies 抛异常 → 顶层 except '❌ 读取 Library 能力失败: {e}'。"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_extract_rpc_bodies",
        lambda _t, _r: (_ for _ in ()).throw(RuntimeError("parse fail")),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_library_capabilities"))
    assert "❌ 读取 Library 能力失败" in result[0].text
    assert "parse fail" in result[0].text


# ===========================================================================
# Section E: gemini_list_notebooks
# ===========================================================================


def test_list_notebooks_no_batch_execute_short_circuits(monkeypatch):
    """client 无 _batch_execute → 早退 '❌ 当前客户端不支持 Gemini Notebooks RPC。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebooks"))
    assert "❌ 当前客户端不支持 Gemini Notebooks RPC" in result[0].text


def test_list_notebooks_markdown_with_notebooks_and_has_more(monkeypatch):
    """markdown 渲染 emoji + title + source_count + ID + has_more 下一页。

    走 ``_fetch_native_notebooks`` 接缝而非 ``_extract_rpc_bodies``。
    """
    notebooks = [
        {
            "id": "n_1",
            "title": "Math Notes",
            "emoji": "📚",
            "source_count": 3,
        },
        {
            "id": "n_2",
            "title": "",  # 空 title → 渲染 (untitled)
            "emoji": "",
            "source_count": 0,
        },
    ]
    diagnostic = {
        "source_rpc": "CNgdBe",
        "observed": "2026-07-04 Pro UI / Native Gemini Notebooks",
    }
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=(notebooks, diagnostic)),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebooks", limit=1, offset=0))
    text = result[0].text
    assert "## Gemini 原生笔记本" in text
    assert "共 2 个；当前 offset=0 count=1" in text
    assert "1. 📚 Math Notes · sources=3" in text
    assert "ID: n_1" in text
    # 第二条因 limit=1 不在当前页
    assert "下一页: offset=1" in text


def test_list_notebooks_empty_placeholder(monkeypatch):
    """空 notebooks → '暂无 Gemini 原生笔记本。'"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(return_value=([], {
            "source_rpc": "CNgdBe",
            "observed": "2026-07-04 Pro UI / Native Gemini Notebooks",
        })),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebooks"))
    assert result[0].text == "暂无 Gemini 原生笔记本。"


def test_list_notebooks_top_level_exception(monkeypatch):
    """_fetch_native_notebooks 抛异常 → 顶层 except '❌ 读取 Gemini Notebooks 失败: {e}'。"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_native_notebooks",
        AsyncMock(side_effect=RuntimeError("rpc down")),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_notebooks"))
    assert "❌ 读取 Gemini Notebooks 失败" in result[0].text
    assert "rpc down" in result[0].text


# ===========================================================================
# Section F: gemini_list_scheduled_actions
# ===========================================================================


def test_list_scheduled_actions_no_batch_execute_short_circuits(monkeypatch):
    """client 无 _batch_execute → 早退 '❌ 当前客户端不支持定时操作 RPC。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_scheduled_actions"))
    assert "❌ 当前客户端不支持定时操作 RPC" in result[0].text


def test_list_scheduled_actions_markdown_enabled_three_states_with_label_hour_timezone(monkeypatch):
    """markdown 渲染 enabled 三态（enabled/disabled/unknown）+ label + hour + timezone 后缀。

    走 ``_fetch_scheduled_registry`` 接缝。
    """
    entries = [
        {
            "id": "task_1", "title": "Daily Report", "enabled": True,
            "schedule_label": "每日 09:00", "hour": 9, "timezone_name": "Asia/Shanghai",
        },
        {
            "id": "task_2", "title": "Inactive Task", "enabled": False,
            "schedule_label": "", "hour": None, "timezone_name": "",
        },
        {
            "id": "task_3", "title": "Unknown Task", "enabled": None,
            "schedule_label": "Weekly", "hour": 8, "timezone_name": "UTC",
        },
    ]
    diagnostic = {
        "source_rpc": "CNgdBe",  # 实际 probe rpcid
        "observed": "2026-06-20 Pro UI / Scheduled action registry",
    }
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=(entries, diagnostic)),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_scheduled_actions", scope="all"))
    text = result[0].text
    assert "## Gemini 定时操作" in text
    assert "范围: all" in text
    assert "### scheduled_actions_registry" in text
    # 第一条：enabled=True + label + hour + timezone 全渲染
    assert (
        "- Daily Report (task_1) [enabled], label=每日 09:00, hour=9, timezone=Asia/Shanghai"
        in text
    )
    # 第二条：enabled=False，无 label/hour/timezone 后缀
    assert "- Inactive Task (task_2) [disabled]" in text
    # 第三条：enabled=None → unknown
    assert "- Unknown Task (task_3) [unknown], label=Weekly, hour=8, timezone=UTC" in text


def test_list_scheduled_actions_empty_placeholder_with_empty_hint(monkeypatch):
    """空 entries + diagnostic.empty_hint → 渲染 '- 暂无条目' + '- 诊断: {empty_hint}'。"""
    diagnostic = {
        "source_rpc": "CNgdBe",
        "observed": "observed",
        "empty_hint": "The current Gemini cookie/session returned an empty scheduled-actions registry.",
    }
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=([], diagnostic)),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_scheduled_actions"))
    text = result[0].text
    assert "- 暂无条目" in text
    assert "- 诊断:" in text
    assert "empty scheduled-actions registry" in text


def test_list_scheduled_actions_top_level_exception(monkeypatch):
    """_fetch_scheduled_registry 抛异常 → 顶层 except '❌ 读取定时操作失败: {e}'。"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(side_effect=RuntimeError("rpc down")),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_scheduled_actions"))
    assert "❌ 读取定时操作失败" in result[0].text
    assert "rpc down" in result[0].text


# ===========================================================================
# Section G: gemini_get_scheduled_action
# ===========================================================================


def test_get_scheduled_action_empty_id_short_circuits(monkeypatch):
    """空 action_id（纯空白 strip 后为空）→ 早退 '❌ action_id 不能为空。'"""
    client = _FakeBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_scheduled_action", action_id="   "))
    assert "❌ action_id 不能为空" in result[0].text
    assert client.call_count == 0  # 早退前不应调用 _batch_execute


def test_get_scheduled_action_no_batch_execute_short_circuits(monkeypatch):
    """client 无 _batch_execute → 早退 '❌ 当前客户端不支持定时操作 RPC。'。"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_scheduled_action", action_id="task_1"))
    assert "❌ 当前客户端不支持定时操作 RPC" in result[0].text


def test_get_scheduled_action_markdown_found_item(monkeypatch):
    """markdown 渲染 found item：ID + 标题 + 状态 + 计划 + 小时 + 时区。

    走 ``_fetch_scheduled_task_by_id`` 接缝。
    """
    item = {
        "id": "task_1",
        "title": "Daily Standup",
        "enabled": True,
        "schedule_label": "每日 09:00",
        "hour": 9,
        "timezone_name": "Asia/Shanghai",
    }
    diagnostic = {
        "source_rpc": "kwDCne",
        "observed": "2026-06-20 Pro UI / Scheduled action get-by-id",
    }
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_task_by_id",
        AsyncMock(return_value=(item, diagnostic)),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_scheduled_action", action_id="task_1"))
    text = result[0].text
    assert "## Gemini 定时操作" in text
    assert "ID: task_1" in text
    assert "标题: Daily Standup" in text
    assert "状态: enabled" in text
    assert "计划: 每日 09:00" in text
    assert "小时: 9" in text
    assert "时区: Asia/Shanghai" in text


def test_get_scheduled_action_top_level_exception(monkeypatch):
    """_fetch_scheduled_task_by_id 抛异常 → 顶层 except '❌ 读取定时操作失败: {e}'。"""
    client = _FakeBatchClient(responses=["r1"], status_code=200)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_task_by_id",
        AsyncMock(side_effect=RuntimeError("rpc down")),
    )

    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_get_scheduled_action", action_id="task_1"))
    assert "❌ 读取定时操作失败" in result[0].text
    assert "rpc down" in result[0].text
