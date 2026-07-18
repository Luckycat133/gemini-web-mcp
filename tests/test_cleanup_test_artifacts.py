"""gemini_cleanup_test_artifacts 及相关 helper 的行为测试。

调研发现该工具标注 destructiveHint=True（DESTRUCTIVE_REMOTE），但此前仅有
注解形状测试，从未被 mcp.call_tool 行为调用，相关 helper
（_split_cleanup_markers / _marker_hits / _cleanup_test_artifacts_payload /
_format_cleanup_markdown）也零行为覆盖。作为破坏性工具（dry_run=False 会真实
删除远端聊天与定时任务），风险最高，本文件补充行为断言：

- _split_cleanup_markers: 空字符串、空白过滤、多值逗号分隔、保留原大小写
- _marker_hits: 大小写不敏感、None/空文本、多 marker 命中、保留命中 marker 原形式
- _format_cleanup_markdown: 空 payload、含 chats 三态、含 scheduled、含 errors、dry_run 提示
- _cleanup_test_artifacts_payload:
  * chats dry_run 命中 id / title
  * chats dry_run 无命中
  * chats dry_run=False 成功删除 / 删除抛异常 / 缺 delete_chat 能力
  * chats scan_turns 命中 turn / scan_turns 抛异常不中断
  * 缺 list_chats 能力 → errors 记录
  * target=chats 跳过 scheduled（_batch_execute 不被调用）
  * target=scheduled 跳过 chats（list_chats 不被调用）
  * 空 markers 回退 codex-
  * max_chats 夹紧到 [1, 100]
- 工具层: 注册 + DESTRUCTIVE_REMOTE 注解 + call_tool dry_run 路径 + response_format=json
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from mcp.server.fastmcp import FastMCP

import src.tools.manage as manage_tools
from src.tools.manage import (
    _cleanup_test_artifacts_payload,
    _format_cleanup_markdown,
    _marker_hits,
    _split_cleanup_markers,
    register_manage_tools,
)


# ---------------------------------------------------------------------------
# _split_cleanup_markers
# ---------------------------------------------------------------------------


def test_split_cleanup_markers_empty_string():
    """空字符串解析为空列表。"""
    assert _split_cleanup_markers("") == []


def test_split_cleanup_markers_filters_whitespace_entries():
    """逗号分隔的空白条目被过滤掉。"""
    assert _split_cleanup_markers("codex-, , Cleanup Verification Marker") == [
        "codex-",
        "Cleanup Verification Marker",
    ]


def test_split_cleanup_markers_preserves_case_and_strip_spaces():
    """strip 前后空格但保留 marker 原大小写（小写转换发生在 _marker_hits 内部）。"""
    assert _split_cleanup_markers("  CODEX- , test marker  ") == ["CODEX-", "test marker"]


# ---------------------------------------------------------------------------
# _marker_hits
# ---------------------------------------------------------------------------


def test_marker_hits_case_insensitive():
    """marker 与 text 大小写不敏感匹配，返回 marker 原形式。"""
    hits = _marker_hits("CODEX-abc title", ["codex-"])
    assert hits == ["codex-"]


def test_marker_hits_none_text_returns_empty():
    """text=None 转为空串，任何非空 marker 都不命中。"""
    assert _marker_hits(None, ["codex-"]) == []


def test_marker_hits_multiple_markers_independent():
    """多个 marker 独立匹配，命中几个返回几个。"""
    hits = _marker_hits("codex-1 and Cleanup Verification Marker", ["codex-", "Cleanup Verification Marker", "nope"])
    assert "codex-" in hits
    assert "Cleanup Verification Marker" in hits
    assert "nope" not in hits


# ---------------------------------------------------------------------------
# _format_cleanup_markdown
# ---------------------------------------------------------------------------


def _empty_payload(**overrides):
    base = {
        "name": "gemini_cleanup_test_artifacts",
        "dry_run": True,
        "target": "all",
        "markers": ["codex-"],
        "scan_turns": False,
        "max_chats": 25,
        "matched_chat_count": 0,
        "matched_scheduled_count": 0,
        "deleted_chat_count": 0,
        "deleted_scheduled_count": 0,
        "matched_chats": [],
        "matched_scheduled_actions": [],
        "errors": [],
    }
    base.update(overrides)
    return base


def test_format_cleanup_markdown_empty_dry_run_has_hint():
    """空 payload + dry_run=True → 含末尾删除提示行。"""
    text = _format_cleanup_markdown(_empty_payload())
    assert text.startswith("## Gemini Test Artifact Cleanup")
    assert "Dry run: True" in text
    assert "Set dry_run=false to delete" in text
    assert "### Chats" not in text
    assert "### Scheduled Actions" not in text
    assert "### Errors" not in text


def test_format_cleanup_markdown_dry_run_false_no_hint():
    """dry_run=False 不输出末尾提示行。"""
    text = _format_cleanup_markdown(_empty_payload(dry_run=False))
    assert "Set dry_run=false to delete" not in text


def test_format_cleanup_markdown_chat_three_states():
    """chats 三态：deleted / matched / error=...。"""
    payload = _empty_payload(
        dry_run=False,
        matched_chat_count=3,
        matched_chats=[
            {"title": "Deleted", "id": "c1", "deleted": True, "delete_error": "", "matched_fields": ["id"]},
            {"title": "Matched", "id": "c2", "deleted": False, "delete_error": "", "matched_fields": ["title"]},
            {"title": "Failed", "id": "c3", "deleted": False, "delete_error": "RuntimeError: boom", "matched_fields": ["id"]},
        ],
    )
    text = _format_cleanup_markdown(payload)
    assert "### Chats" in text
    assert "Deleted (c1) [deleted" in text
    assert "Matched (c2) [matched" in text
    assert "Failed (c3) [error=RuntimeError: boom" in text


def test_format_cleanup_markdown_scheduled_verification_status_preferred():
    """scheduled 优先用 verification_status，其次 deleted/matched。"""
    payload = _empty_payload(
        matched_scheduled_count=2,
        matched_scheduled_actions=[
            {"title": "Task1", "id": "s1", "deleted": True, "verification_status": "deleted_state_by_id", "delete_error": ""},
            {"title": "Task2", "id": "s2", "deleted": False, "verification_status": "dry_run", "delete_error": ""},
        ],
    )
    text = _format_cleanup_markdown(payload)
    assert "### Scheduled Actions" in text
    assert "Task1 (s1) [deleted_state_by_id]" in text
    assert "Task2 (s2) [dry_run]" in text


def test_format_cleanup_markdown_errors_section():
    """errors 非空 → 输出 ### Errors 段。"""
    payload = _empty_payload(errors=[{"target": "chats", "error": "list_chats unavailable"}])
    text = _format_cleanup_markdown(payload)
    assert "### Errors" in text
    assert "chats: list_chats unavailable" in text


# ---------------------------------------------------------------------------
# _cleanup_test_artifacts_payload — chats 路径
# ---------------------------------------------------------------------------


def _make_client_with_chats(chats, *, delete_chat_side_effect=None, read_chat_return=None):
    """构造 mock client。list_chats 是同步调用，其余为 async。"""
    client = MagicMock()
    client.list_chats.return_value = list(chats)
    client.delete_chat = AsyncMock(side_effect=delete_chat_side_effect)
    if read_chat_return is not None:
        client.read_chat = AsyncMock(return_value=read_chat_return)
    else:
        client.read_chat = AsyncMock(return_value=SimpleNamespace(turns=[]))
    # scheduled 路径需要 _batch_execute，默认不配置以避免误触发
    return client


def test_payload_chats_dry_run_match_id():
    """dry_run=True 命中 id → matched_chats 含条目，delete_chat 不被调用。"""
    client = _make_client_with_chats([
        {"id": "codex-abc", "title": "Test"},
        {"id": "normal-1", "title": "Keep"},
    ])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True,
    ))

    assert payload["matched_chat_count"] == 1
    assert payload["deleted_chat_count"] == 0
    assert payload["matched_chats"][0]["id"] == "codex-abc"
    assert "id" in payload["matched_chats"][0]["matched_fields"]
    assert payload["matched_chats"][0]["deleted"] is False
    client.delete_chat.assert_not_called()


def test_payload_chats_dry_run_match_title():
    """命中 title 而非 id → matched_fields 含 'title'。"""
    client = _make_client_with_chats([
        {"id": "chat-1", "title": "Cleanup Verification Marker run"},
    ])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="Cleanup Verification Marker", target="chats", dry_run=True,
    ))

    assert payload["matched_chat_count"] == 1
    assert "title" in payload["matched_chats"][0]["matched_fields"]
    assert "id" not in payload["matched_chats"][0]["matched_fields"]


def test_payload_chats_dry_run_no_match():
    """无 marker 命中 → matched_chats 为空。"""
    client = _make_client_with_chats([
        {"id": "normal-1", "title": "Regular chat"},
    ])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True,
    ))

    assert payload["matched_chat_count"] == 0
    assert payload["matched_chats"] == []


def test_payload_chats_dry_run_false_deletes_successfully():
    """dry_run=False + delete_chat 成功 → deleted=True，delete_chat 被调用。"""
    client = _make_client_with_chats([{"id": "codex-1", "title": "T"}])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=False,
    ))

    assert payload["deleted_chat_count"] == 1
    assert payload["matched_chats"][0]["deleted"] is True
    assert payload["matched_chats"][0]["delete_error"] == ""
    client.delete_chat.assert_awaited_once_with("codex-1")


def test_payload_chats_dry_run_false_delete_raises_records_error():
    """dry_run=False + delete_chat 抛异常 → deleted=False, delete_error 非空。"""
    client = _make_client_with_chats(
        [{"id": "codex-1", "title": "T"}],
        delete_chat_side_effect=RuntimeError("network down"),
    )

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=False,
    ))

    assert payload["deleted_chat_count"] == 0
    assert payload["matched_chats"][0]["deleted"] is False
    assert "RuntimeError: network down" in payload["matched_chats"][0]["delete_error"]


def test_payload_chats_dry_run_false_without_delete_chat_capability():
    """dry_run=False 但 client 无 delete_chat → delete_error='delete_chat unavailable'。"""
    client = MagicMock()
    client.list_chats.return_value = [{"id": "codex-1", "title": "T"}]
    # 不设置 delete_chat 属性
    if hasattr(client, "delete_chat"):
        del client.delete_chat

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=False,
    ))

    assert payload["matched_chats"][0]["deleted"] is False
    assert payload["matched_chats"][0]["delete_error"] == "delete_chat unavailable"


def test_payload_chats_missing_list_chats_records_error():
    """target=chats 但 client 无 list_chats → errors 记录 'list_chats unavailable'。"""
    client = MagicMock()
    # 不设置 list_chats
    if hasattr(client, "list_chats"):
        del client.list_chats

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True,
    ))

    assert payload["matched_chat_count"] == 0
    assert {"target": "chats", "error": "list_chats unavailable"} in payload["errors"]


def test_payload_chats_scan_turns_match():
    """scan_turns=True + turn 文本命中 marker → matched_fields 含 'turn'。"""
    client = MagicMock()
    client.list_chats.return_value = [{"id": "chat-no-id-match", "title": "No title match"}]
    client.read_chat = AsyncMock(return_value=SimpleNamespace(turns=[
        SimpleNamespace(role="user", text="codex-marker in turn"),
    ]))
    client.delete_chat = AsyncMock()

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True, scan_turns=True,
    ))

    assert payload["matched_chat_count"] == 1
    assert "turn" in payload["matched_chats"][0]["matched_fields"]


def test_payload_chats_scan_turns_read_raises_does_not_crash():
    """scan_turns=True + read_chat 抛异常 → errors 记录 chat:{id}，整体不中断。"""
    client = MagicMock()
    client.list_chats.return_value = [{"id": "chat-1", "title": "codex-match-in-title"}]
    client.read_chat = AsyncMock(side_effect=RuntimeError("read failed"))

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True, scan_turns=True,
    ))

    # title 已命中，chat 仍出现在 matched_chats
    assert payload["matched_chat_count"] == 1
    # read 异常进入 errors
    chat_errors = [e for e in payload["errors"] if e["target"].startswith("chat:")]
    assert len(chat_errors) == 1
    assert "chat-1" in chat_errors[0]["target"]


# ---------------------------------------------------------------------------
# _cleanup_test_artifacts_payload — scheduled 路径
# ---------------------------------------------------------------------------


def test_payload_target_chats_skips_scheduled(monkeypatch):
    """target=chats → include_scheduled=False，_fetch_scheduled_registry 不被调用。"""
    client = _make_client_with_chats([{"id": "codex-1", "title": "T"}])

    registry_calls = []

    async def fake_registry(client_arg, timeout):
        registry_calls.append((client_arg, timeout))
        return [], {"ok": True}

    monkeypatch.setattr(manage_tools, "_fetch_scheduled_registry", fake_registry)

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True,
    ))

    assert registry_calls == []
    assert payload["matched_scheduled_count"] == 0


def test_payload_target_scheduled_skips_chats(monkeypatch):
    """target=scheduled → include_chats=False，list_chats 不被调用。"""
    client = MagicMock()
    client.list_chats = MagicMock(return_value=[])  # 不应被调用
    client._batch_execute = AsyncMock()

    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=([], {"ok": True})),
    )

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=True,
    ))

    client.list_chats.assert_not_called()
    assert payload["matched_chat_count"] == 0


def test_payload_scheduled_dry_run_matches(monkeypatch):
    """target=scheduled + dry_run=True → 命中条目 verification_status='dry_run'，不删除。"""
    client = MagicMock()
    client._batch_execute = AsyncMock()

    entries = [
        {"id": "task-codex-1", "title": "Codex task", "instructions": "", "schedule_label": ""},
        {"id": "task-other", "title": "Other", "instructions": "", "schedule_label": ""},
    ]
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=(entries, {"ok": True})),
    )
    task_by_id_calls = []

    async def fake_task_by_id(c, tid, timeout):
        task_by_id_calls.append(tid)
        return None, {}

    monkeypatch.setattr(manage_tools, "_fetch_scheduled_task_by_id", fake_task_by_id)

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=True,
    ))

    assert payload["matched_scheduled_count"] == 1
    assert payload["matched_scheduled_actions"][0]["id"] == "task-codex-1"
    assert payload["matched_scheduled_actions"][0]["verification_status"] == "dry_run"
    assert payload["matched_scheduled_actions"][0]["deleted"] is False
    # dry_run=True 不应做删除 RPC 也不应查 task_by_id
    assert task_by_id_calls == []


def test_payload_scheduled_dry_run_false_deletes(monkeypatch):
    """target=scheduled + dry_run=False + task_state_id=6 → deleted=True, verification_status='deleted_state_by_id'。"""
    client = MagicMock()
    client._batch_execute = AsyncMock(return_value=SimpleNamespace(text='["wrb.fr","Q4Gw3c","body"]'))

    entries = [{"id": "task-codex-1", "title": "Codex", "instructions": "", "schedule_label": ""}]
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=(entries, {"ok": True})),
    )
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_task_by_id",
        AsyncMock(return_value=({"task_state_id": 6}, {})),
    )
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda *a, **kw: ["body"])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=False,
    ))

    assert payload["deleted_scheduled_count"] == 1
    assert payload["matched_scheduled_actions"][0]["deleted"] is True
    assert payload["matched_scheduled_actions"][0]["verification_status"] == "deleted_state_by_id"


def test_payload_scheduled_delete_error(monkeypatch):
    """target=scheduled + dry_run=False + _batch_execute 抛异常 → verification_status='delete_error'。"""
    client = MagicMock()
    client._batch_execute = AsyncMock(side_effect=RuntimeError("rpc failed"))

    entries = [{"id": "task-codex-1", "title": "Codex", "instructions": "", "schedule_label": ""}]
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=(entries, {"ok": True})),
    )

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=False,
    ))

    assert payload["deleted_scheduled_count"] == 0
    assert payload["matched_scheduled_actions"][0]["verification_status"] == "delete_error"
    assert "RuntimeError: rpc failed" in payload["matched_scheduled_actions"][0]["delete_error"]


def test_payload_scheduled_missing_batch_execute_records_error():
    """target=scheduled 但 client 无 _batch_execute → errors 记录。"""
    client = MagicMock()
    # 不设置 _batch_execute
    if hasattr(client, "_batch_execute"):
        del client._batch_execute

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=True,
    ))

    assert {"target": "scheduled", "error": "_batch_execute unavailable"} in payload["errors"]


# ---------------------------------------------------------------------------
# _cleanup_test_artifacts_payload — 边界
# ---------------------------------------------------------------------------


def test_payload_empty_markers_falls_back_to_codex():
    """空 markers 字符串 → 回退为 ['codex-']。"""
    client = _make_client_with_chats([])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="", target="chats", dry_run=True,
    ))

    assert payload["markers"] == ["codex-"]


def test_payload_max_chats_clamped_to_100():
    """max_chats 超过上限被夹紧到 100。"""
    client = _make_client_with_chats([])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True, max_chats=500,
    ))

    assert payload["max_chats"] == 100


def test_payload_max_chats_clamped_to_1():
    """max_chats 低于下限被夹紧到 1。"""
    client = _make_client_with_chats([])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True, max_chats=0,
    ))

    assert payload["max_chats"] == 1


def test_payload_max_chats_invalid_falls_back_to_default():
    """max_chats 非数字 → 回退到默认 25。"""
    client = _make_client_with_chats([])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True, max_chats="abc",
    ))

    assert payload["max_chats"] == 25


def test_payload_max_chats_limits_scan_window():
    """max_chats 限制 list_chats 后的扫描窗口（切片发生在 list_chats 返回之后）。"""
    chats = [{"id": f"codex-{i}", "title": "T"} for i in range(10)]
    client = _make_client_with_chats(chats)

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="chats", dry_run=True, max_chats=3,
    ))

    # 切片 [:3] 只扫描前 3 条
    assert payload["matched_chat_count"] == 3
    # list_chats 被调用一次（不被 max_chats 改变）
    client.list_chats.assert_called_once_with()


# ---------------------------------------------------------------------------
# 工具层：注册 + 注解 + call_tool
# ---------------------------------------------------------------------------


def _register_cleanup_tool():
    """注册 manage 工具（layers=all 确保 cleanup 工具被注册），返回 mcp。"""
    mcp = FastMCP("test")
    register_manage_tools(mcp, layers=["all"])
    return mcp


async def _call_tool(mcp, name, **kwargs):
    """通过 mcp.call_tool 调用工具，返回 TextContent 列表。"""
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


def test_cleanup_tool_registered_with_destructive_annotation():
    """工具已注册且 destructiveHint=True。"""
    mcp = _register_cleanup_tool()
    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == "gemini_cleanup_test_artifacts")
    assert tool.annotations is not None
    assert tool.annotations.destructiveHint is True


def test_cleanup_tool_call_dry_run(monkeypatch):
    """call_tool dry_run=True 路径：返回 markdown，不调用 delete_chat。"""
    fake_client = _make_client_with_chats([{"id": "codex-1", "title": "T"}])
    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: fake_client)

    async def fake_init():
        return fake_client
    monkeypatch.setattr(manage_tools, "initialize_client", fake_init)

    mcp = _register_cleanup_tool()

    async def run():
        return await _call_tool(mcp, "gemini_cleanup_test_artifacts",
                                markers="codex-", target="chats", dry_run=True)

    result = asyncio.run(run())
    assert len(result) == 1
    assert "Gemini Test Artifact Cleanup" in result[0].text
    assert "Dry run: True" in result[0].text
    fake_client.delete_chat.assert_not_called()


def test_cleanup_tool_call_response_format_json(monkeypatch):
    """call_tool response_format=json 返回可解析的 JSON。"""
    fake_client = _make_client_with_chats([{"id": "codex-1", "title": "T"}])
    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: fake_client)

    async def fake_init():
        return fake_client
    monkeypatch.setattr(manage_tools, "initialize_client", fake_init)

    mcp = _register_cleanup_tool()

    async def run():
        return await _call_tool(mcp, "gemini_cleanup_test_artifacts",
                                markers="codex-", target="chats", dry_run=True,
                                response_format="json")

    result = asyncio.run(run())
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["name"] == "gemini_cleanup_test_artifacts"
    assert data["dry_run"] is True
    assert data["matched_chat_count"] == 1
