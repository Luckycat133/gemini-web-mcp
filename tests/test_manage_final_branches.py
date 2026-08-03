"""manage.py 在 Cycle 40 后仍有 14 miss（99% 覆盖率）。本文件覆盖剩余可操作簇——
6 个 miss，此前零直接覆盖：

1. **cleanup scheduled `elif bodies: deleted=True` [2038-2039]**：
   dry_run=False + _batch_execute 返回非空 bodies + task_after_delete None 或
   task_state_id != 6 → verification_status='rpc_accepted', deleted=True。
   既有测试 ``test_payload_scheduled_dry_run_false_deletes`` 用 task_state_id=6
   触发 2035-2037 的 deleted_state_by_id 分支，未覆盖 2038-2039 的 elif bodies。
2. **cleanup scheduled 顶层 except [2054-2055]**：
   _fetch_scheduled_registry 抛异常 → errors 记录 {"target": "scheduled", ...}。
   既有测试 ``test_payload_scheduled_delete_error`` 用 _batch_execute 抛异常触发
   2040-2042 的内层 except，未覆盖 2054-2055 的外层 except。
3. **``_conversation_metadata_payload`` [991]**：单行函数，包装
   ``_conversation_history_payload([pinned, None, True], page_size, next_page_token)``，
   零直接调用。
4. **``resolve_manage_tool_names`` 空 configured 回退 [1426]**：
   ``layers=["", "  "]`` → 全被 strip 过滤 → ``if not configured: configured = {"all"}``。
5. **``_configured_manage_layers` manage: prefix [1457]**：
   ``GEMINI_TOOLS=manage:history-read`` → ``group.startswith("manage:")`` →
   ``layers.add("history-read")``。
6. **``_tool_availability` return [] [1601]**：
   构造不匹配任何分支的 tool dict（name="unknown_tool", group="unknown"）→ 兜底 ``return []``。

不可测试的 8 miss：
- [39-40]：``_GEMINI_WEBAPI_UTILS_AVAILABLE`` import 失败分支（venv 中模块可用，False 不可达）
- [2736, 2974, 3269, 3932]：4 个 Literal-protected dead code（``gemini_history`` /
  ``gemini_account_inventory`` / ``gemini_notebooks`` / ``gemini_manage_gems`` 的
  invalid action/surface 兜底，pydantic Literal 校验在 MCP 分发前拒绝，且这些函数
  在 ``register_manage_tools`` 闭包内定义，外部无法直接调用）
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import src.tools.manage as manage_tools
from src.tools.manage import (
    _cleanup_test_artifacts_payload,
    _configured_manage_layers,
    _conversation_metadata_payload,
    _tool_availability,
    resolve_manage_tool_names,
)


# ===========================================================================
# Section A: cleanup scheduled `elif bodies: deleted=True` [2038-2039]
# ===========================================================================


def test_payload_scheduled_rpc_accepted_but_not_deleted_state(monkeypatch):
    """dry_run=False + bodies 非空 + task_after_delete.task_state_id != 6
    → 覆盖 2038-2039 ``elif bodies: deleted = True``。

    verification_status 应为 'rpc_accepted'（bodies 非空但 task_state_id != 6），
    deleted=True（因为 elif bodies 命中）。
    """
    client = MagicMock()
    # _batch_execute 返回非空 response（_extract_rpc_bodies 会返回非空 bodies）
    client._batch_execute = AsyncMock(return_value=SimpleNamespace(text='["wrb.fr","Q4Gw3c","body"]'))

    entries = [{"id": "task-codex-1", "title": "Codex", "instructions": "", "schedule_label": ""}]
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=(entries, {"ok": True})),
    )
    # task_after_delete.task_state_id=3 (running) != 6 (deleted) → 不走 2035-2037
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_task_by_id",
        AsyncMock(return_value=({"task_state_id": 3}, {})),
    )
    # _extract_rpc_bodies 返回非空 → bodies 真值 → 2033 verification_status='rpc_accepted'
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda *a, **kw: ["body"])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=False,
    ))

    assert payload["deleted_scheduled_count"] == 1
    assert payload["matched_scheduled_actions"][0]["deleted"] is True
    assert payload["matched_scheduled_actions"][0]["verification_status"] == "rpc_accepted"


def test_payload_scheduled_rpc_accepted_with_task_after_none(monkeypatch):
    """dry_run=False + bodies 非空 + task_after_delete=None
    → 覆盖 2038-2039 ``elif bodies: deleted = True``（task_after_delete 为 None 时
    2035 的 ``if task_after_delete and ...`` 短路为 False）。
    """
    client = MagicMock()
    client._batch_execute = AsyncMock(return_value=SimpleNamespace(text='["wrb.fr","Q4Gw3c","body"]'))

    entries = [{"id": "task-codex-1", "title": "Codex", "instructions": "", "schedule_label": ""}]
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(return_value=(entries, {"ok": True})),
    )
    # task_after_delete=None → 2035 条件短路
    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_task_by_id",
        AsyncMock(return_value=(None, {})),
    )
    monkeypatch.setattr(manage_tools, "_extract_rpc_bodies", lambda *a, **kw: ["body"])

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=False,
    ))

    assert payload["matched_scheduled_actions"][0]["deleted"] is True
    assert payload["matched_scheduled_actions"][0]["verification_status"] == "rpc_accepted"


# ===========================================================================
# Section B: cleanup scheduled 顶层 except [2054-2055]
# ===========================================================================


def test_payload_scheduled_registry_exception_records_error(monkeypatch):
    """_fetch_scheduled_registry 抛异常 → 顶层 except（line 2054-2055）。

    既有 ``test_payload_scheduled_delete_error`` 用 _batch_execute 抛异常触发内层
    2040-2042，本测试让 _fetch_scheduled_registry 本身抛异常触发外层 2054-2055。
    """
    client = MagicMock()
    client._batch_execute = AsyncMock()

    monkeypatch.setattr(
        manage_tools, "_fetch_scheduled_registry",
        AsyncMock(side_effect=RuntimeError("registry boom")),
    )

    payload = asyncio.run(_cleanup_test_artifacts_payload(
        client, markers="codex-", target="scheduled", dry_run=True,
    ))

    # matched_scheduled 为空（异常在遍历 entries 前抛出）
    assert payload["matched_scheduled_count"] == 0
    # errors 记录外层异常
    assert any(
        err.get("target") == "scheduled" and "RuntimeError: registry boom" in err.get("error", "")
        for err in payload["errors"]
    )


# ===========================================================================
# Section C: _conversation_metadata_payload [991]
# ===========================================================================


def test_conversation_metadata_payload_pinned_true_with_token():
    """pinned=True + next_page_token → 包装为 [page_size, token, [True, None, True]]。"""
    payload = _conversation_metadata_payload(True, 50, "next_token_abc")
    parsed = json.loads(payload)
    assert parsed[0] == 50  # page_size
    assert parsed[1] == "next_token_abc"  # next_page_token
    assert parsed[2] == [True, None, True]  # filter_payload


def test_conversation_metadata_payload_pinned_false_no_token():
    """pinned=False + next_page_token=None → filter_payload=[False, None, True]。"""
    payload = _conversation_metadata_payload(False, 100, None)
    parsed = json.loads(payload)
    assert parsed[0] == 100
    assert parsed[1] is None  # next_page_token=None
    assert parsed[2] == [False, None, True]


def test_conversation_metadata_payload_uses_compact_separators():
    """payload 使用 compact separators（无多余空格）。"""
    payload = _conversation_metadata_payload(True, 50, "tok")
    assert ", " not in payload  # separators=(",", ":")


# ===========================================================================
# Section D: resolve_manage_tool_names 空 configured 回退 [1426]
# ===========================================================================


def test_resolve_manage_tool_names_empty_layers_falls_back_to_all():
    """layers=["", "  "] → 全被 strip 过滤 → configured 为空 → 回退 {"all"}。

    覆盖 line 1426：``if not configured: configured = {"all"}``。
    """
    # 所有元素 strip 后为空 → configured 集合为空 → 触发 1426 回退
    result = resolve_manage_tool_names(["", "  ", "\t"])
    # {"all"} layer → MANAGE_TOOL_LAYER_NAMES["all"] 包含所有 manage 工具
    assert "gemini_history" in result  # 来自 HISTORY_FACADE_TOOL_NAMES
    assert "gemini_manage_gems" in result  # 来自 GEMS_TOOL_NAMES
    assert "gemini_get_tool_manifest" in result  # 来自 MANIFEST_TOOL_NAMES


def test_resolve_manage_tool_names_none_layers_falls_back_to_all():
    """layers=None → (layers or ["all"]) → ["all"] → 直接走 all 分支（不触发 1426）。

    此测试确认 None 输入走的是 ``layers or ["all"]`` 而非 1426 回退，作为对照。
    """
    result = resolve_manage_tool_names(None)
    assert "gemini_history" in result


# ===========================================================================
# Section E: _configured_manage_layers manage: prefix [1457]
# ===========================================================================


def test_configured_manage_layers_manage_prefix(monkeypatch):
    """GEMINI_TOOLS=manage:history-read → group.startswith("manage:") →
    layers.add("history-read")。

    覆盖 line 1457：``layers.add(group.split(":", 1)[1])``。
    """
    monkeypatch.setenv("GEMINI_TOOLS", "manage:history-read")
    # _configured_tool_groups 读 GEMINI_TOOLS 环境变量
    layers = _configured_manage_layers(manage_tools._configured_tool_groups())
    assert "history-read" in layers


def test_configured_manage_layers_manage_prefix_with_all(monkeypatch):
    """GEMINI_TOOLS=manage:history-read,all → manage: 分支 + all profile 分支并存。"""
    monkeypatch.setenv("GEMINI_TOOLS", "manage:notebooks-write,all")
    layers = _configured_manage_layers(manage_tools._configured_tool_groups())
    # manage:notebooks-write → 1457 分支
    assert "notebooks-write" in layers
    # all → profile_layers["all"] = {"all"}
    assert "all" in layers


def test_configured_manage_layers_profile_only(monkeypatch):
    """GEMINI_TOOLS=history → 走 profile_layers 分支（非 manage: prefix）。

    此测试作为对照，确认非 manage: prefix 走 1458-1459 的 else 分支。
    """
    monkeypatch.setenv("GEMINI_TOOLS", "history")
    layers = _configured_manage_layers(manage_tools._configured_tool_groups())
    # profile_layers["history"] = {"history-read"}
    assert "history-read" in layers


# ===========================================================================
# Section F: _tool_availability return [] [1601]
# ===========================================================================


def test_tool_availability_unknown_tool_returns_empty():
    """构造不匹配任何分支的 tool dict → 兜底 return []。

    覆盖 line 1601：``return []``。
    """
    # name 不在任何 *_TOOL_NAMES 集合中，group 不匹配 cookie/prompts
    unknown_tool = {"name": "gemini_mystery_tool", "group": "unknown"}
    result = _tool_availability(unknown_tool)
    assert result == []


def test_tool_availability_known_tool_returns_layers():
    """已知 tool → 返回非空 layers（对照测试）。"""
    known_tool = {"name": "gemini_history", "group": "history"}
    result = _tool_availability(known_tool)
    # HISTORY_FACADE_TOOL_NAMES → ["history", "history-organize", "manage", "all"]
    assert "history" in result
    assert "all" in result
