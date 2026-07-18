"""RemoteChatCleanupManager 的行为测试。

调研发现该模块覆盖率 81%（91 stmts, 17 miss），缺失行全部集中在
`RemoteChatCleanupManager` 类的实例方法——此前仅 `extract_remote_chat_id`
有直接测试（test_error_and_session.py），类的异步清理逻辑零直接覆盖：

- `schedule_cleanup_from_response`：cid 命中时调 schedule_cleanup（line 69）
- `schedule_cleanup`：无运行事件循环时 `except RuntimeError` 早退（lines 101-102）
- `_delete_after_delay`：pending 任务被覆盖（delete_at 不匹配）时早退（line 112）
- `delete_chat`：无 cid / client_initializer 解析 / client 无 delete_chat 方法 /
  client.delete_chat 抛异常（lines 124, 128, 133-134, 138-140）
- `cleanup_due_chats`：client_initializer 解析 / client_provider 解析 / due 循环
  删除（lines 163-166, 170-171）

测试策略：直接实例化 `RemoteChatCleanupManager`，用 `SimpleNamespace` 构造
带 `delete_chat` 异步方法的假 client。`schedule_cleanup` 在同步上下文调用
（无运行 loop）以触发 `except RuntimeError` 分支并避免创建真实删除任务；
`_delete_after_delay` / `delete_chat` / `cleanup_due_chats` 用 `asyncio.run`
在隔离事件循环中运行。
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.remote_chat_cleanup_manager import (
    CleanupTask,
    RemoteChatCleanupManager,
    extract_remote_chat_id,
)


def _run(coro):
    return asyncio.run(coro)


def _async_delete_client(*, side_effect=None):
    """构造带 async delete_chat 的假 client。"""
    client = SimpleNamespace()
    client.delete_chat = AsyncMock(side_effect=side_effect)
    return client


# ---------------------------------------------------------------------------
# extract_remote_chat_id（回归守护）
# ---------------------------------------------------------------------------


def test_extract_remote_chat_id_from_cid_attribute():
    assert extract_remote_chat_id(SimpleNamespace(cid="c_abc")) == "c_abc"


def test_extract_remote_chat_id_from_metadata_list():
    obj = SimpleNamespace(cid=None, metadata=["c_xyz", "r_resp"])
    assert extract_remote_chat_id(obj) == "c_xyz"


def test_extract_remote_chat_id_returns_none_when_no_match():
    assert extract_remote_chat_id(SimpleNamespace(cid=None, metadata=None)) is None


def test_extract_remote_chat_id_ignores_non_c_prefixed_cid():
    assert extract_remote_chat_id(SimpleNamespace(cid="not-c-prefixed")) is None


# ---------------------------------------------------------------------------
# schedule_cleanup_from_response
# ---------------------------------------------------------------------------


def test_schedule_cleanup_from_response_registers_when_cid_found():
    """response 含 c_ 前缀 cid → 调 schedule_cleanup 注册到 _pending_cleanup（line 69）。

    在同步上下文中调用（无运行 loop），schedule_cleanup 的 except RuntimeError
    分支触发，仅注册不创建删除任务——同时覆盖 lines 101-102。
    """
    manager = RemoteChatCleanupManager()
    response = SimpleNamespace(cid="c_found")
    cid = manager.schedule_cleanup_from_response(response, source="test")
    assert cid == "c_found"
    pending = manager.list_pending_cleanup()
    assert "c_found" in pending
    assert pending["c_found"].source == "test"


def test_schedule_cleanup_from_response_returns_none_when_no_cid():
    manager = RemoteChatCleanupManager()
    response = SimpleNamespace(cid=None, metadata=None)
    cid = manager.schedule_cleanup_from_response(response)
    assert cid is None
    assert manager.list_pending_cleanup() == {}


# ---------------------------------------------------------------------------
# schedule_cleanup 分支
# ---------------------------------------------------------------------------


def test_schedule_cleanup_skips_when_cid_falsy():
    manager = RemoteChatCleanupManager()
    manager.schedule_cleanup(None)
    manager.schedule_cleanup("")
    assert manager.list_pending_cleanup() == {}


def test_schedule_cleanup_skips_when_retain_chat_true():
    manager = RemoteChatCleanupManager()
    manager.schedule_cleanup("c_1", retain_chat=True)
    assert manager.list_pending_cleanup() == {}


def test_schedule_cleanup_uses_retention_provider_when_delete_after_none():
    manager = RemoteChatCleanupManager(retention_provider=lambda: 42)
    manager.schedule_cleanup("c_1")
    pending = manager.list_pending_cleanup()
    assert "c_1" in pending


def test_schedule_cleanup_no_running_loop_registers_only():
    """同步调用（无运行 loop）→ except RuntimeError 早退，仅注册不创建任务（lines 101-102）。"""
    manager = RemoteChatCleanupManager()
    manager.schedule_cleanup("c_1", delete_after_seconds=100)
    assert "c_1" in manager.list_pending_cleanup()


# ---------------------------------------------------------------------------
# _delete_after_delay
# ---------------------------------------------------------------------------


def test_delete_after_delay_returns_early_when_pending_delete_at_mismatch():
    """pending 任务 delete_at 与调用时 delete_at 不匹配 → 早退不删除（line 112）。"""
    manager = RemoteChatCleanupManager()
    # 直接注入一个 delete_at 不同的 pending 任务
    manager._pending_cleanup["c_1"] = CleanupTask(delete_at=99999.0, source="overwritten")
    # 用过去的 delete_at 调用（sleep(0)），guard 检测到 delete_at 不匹配 → return
    _run(manager._delete_after_delay("c_1", delete_at=1.0))
    # pending 任务仍存在（未被删除）
    assert "c_1" in manager._pending_cleanup


def test_delete_after_delay_returns_early_when_pending_missing():
    """pending 任务已被移除（not pending）→ 早退（line 112 的 not pending 分支）。"""
    manager = RemoteChatCleanupManager()
    _run(manager._delete_after_delay("c_missing", delete_at=1.0))
    assert manager.list_pending_cleanup() == {}


def test_delete_after_delay_happy_path_calls_delete_chat():
    """pending delete_at 匹配 → sleep 后调 delete_chat 删除（line 114 happy path）。"""
    client = _async_delete_client()
    manager = RemoteChatCleanupManager(client_provider=lambda: client)
    delete_at = time.time() - 1  # 过去时间 → sleep(0) 立即返回
    manager._pending_cleanup["c_1"] = CleanupTask(delete_at=delete_at, source="s")
    _run(manager._delete_after_delay("c_1", delete_at=delete_at))
    client.delete_chat.assert_awaited_once_with("c_1")
    assert "c_1" not in manager.list_pending_cleanup()


def test_schedule_cleanup_creates_task_in_running_loop():
    """在运行的事件循环中调用 schedule_cleanup → loop.create_task 创建任务（line 103），
    任务到期后调 delete_chat 删除远端 chat。"""
    client = _async_delete_client()
    manager = RemoteChatCleanupManager(client_provider=lambda: client)

    async def runner():
        manager.schedule_cleanup("c_async", delete_after_seconds=0)
        await asyncio.sleep(0.05)  # 让 create_task 创建的协程运行

    _run(runner())

    client.delete_chat.assert_awaited_once_with("c_async")
    assert "c_async" not in manager.list_pending_cleanup()


# ---------------------------------------------------------------------------
# delete_chat
# ---------------------------------------------------------------------------


def test_delete_chat_returns_false_when_no_cid():
    """cid 为 None/空 → 直接返回 False（line 124）。"""
    manager = RemoteChatCleanupManager()
    assert _run(manager.delete_chat(None)) is False
    assert _run(manager.delete_chat("")) is False


def test_delete_chat_uses_client_initializer():
    """client=None + client_initializer 提供 client → 用该 client 删除（line 128）。"""
    manager = RemoteChatCleanupManager()
    client = _async_delete_client()
    result = _run(manager.delete_chat("c_1", client_initializer=lambda: client))
    assert result is True
    client.delete_chat.assert_awaited_once_with("c_1")


def test_delete_chat_uses_client_provider_when_no_initializer():
    """client=None + 无 initializer + manager 有 client_provider → 用 provider（lines 129-130 已覆盖，此处确认）。"""
    client = _async_delete_client()
    manager = RemoteChatCleanupManager(client_provider=lambda: client)
    result = _run(manager.delete_chat("c_1"))
    assert result is True
    client.delete_chat.assert_awaited_once_with("c_1")


def test_delete_chat_returns_false_when_client_lacks_delete_chat():
    """client 无 delete_chat 方法 → 警告 + 返回 False（lines 133-134）。"""
    manager = RemoteChatCleanupManager()
    bare_client = SimpleNamespace()  # 无 delete_chat
    result = _run(manager.delete_chat("c_1", client=bare_client))
    assert result is False


def test_delete_chat_returns_false_when_client_delete_raises():
    """client.delete_chat 抛异常 → 警告 + 返回 False（lines 138-140）。"""
    manager = RemoteChatCleanupManager()
    client = _async_delete_client(side_effect=RuntimeError("network down"))
    result = _run(manager.delete_chat("c_1", client=client))
    assert result is False


def test_delete_chat_success_removes_pending_and_returns_true():
    """删除成功 → 从 _pending_cleanup 移除 + 返回 True（happy path 回归）。"""
    manager = RemoteChatCleanupManager()
    manager._pending_cleanup["c_1"] = CleanupTask(delete_at=99999.0, source="s")
    client = _async_delete_client()
    result = _run(manager.delete_chat("c_1", client=client))
    assert result is True
    assert "c_1" not in manager.list_pending_cleanup()


# ---------------------------------------------------------------------------
# cleanup_due_chats
# ---------------------------------------------------------------------------


def _register_due(manager, cid):
    """注册一个已到期的清理任务（delete_at 在过去）。"""
    manager._pending_cleanup[cid] = CleanupTask(delete_at=time.time() - 10, source="due")


def test_cleanup_due_chats_uses_client_initializer():
    """client=None + client_initializer 提供 client → 用该 client（lines 163-164）。"""
    manager = RemoteChatCleanupManager()
    _register_due(manager, "c_due1")
    client = _async_delete_client()
    deleted = _run(manager.cleanup_due_chats(client_initializer=lambda: client))
    assert deleted == 1
    client.delete_chat.assert_awaited_once_with("c_due1")


def test_cleanup_due_chats_uses_client_provider():
    """client=None + 无 initializer + manager 有 client_provider → 用 provider（lines 165-166）。"""
    client = _async_delete_client()
    manager = RemoteChatCleanupManager(client_provider=lambda: client)
    _register_due(manager, "c_due2")
    deleted = _run(manager.cleanup_due_chats())
    assert deleted == 1
    client.delete_chat.assert_awaited_once_with("c_due2")


def test_cleanup_due_chats_loops_over_multiple_due_and_counts_success():
    """多个 due cid → 循环删除，成功计数（lines 170-171）。"""
    manager = RemoteChatCleanupManager()
    _register_due(manager, "c_a")
    _register_due(manager, "c_b")
    _register_due(manager, "c_c")
    client = _async_delete_client()
    deleted = _run(manager.cleanup_due_chats(client=client))
    assert deleted == 3
    called_cids = {call.args[0] for call in client.delete_chat.call_args_list}
    assert called_cids == {"c_a", "c_b", "c_c"}


def test_cleanup_due_chats_counts_only_successful_deletes():
    """部分删除失败（delete_chat 抛异常）→ 不计入 deleted。"""
    manager = RemoteChatCleanupManager()
    manager._pending_cleanup["c_ok"] = CleanupTask(delete_at=time.time() - 10, source="s")
    manager._pending_cleanup["c_fail"] = CleanupTask(delete_at=time.time() - 10, source="s")

    # cleanup_due_chats 用同一 client 删所有 due cid；让 delete_chat 对 c_fail 抛异常
    async def selective_delete(cid):
        if cid == "c_fail":
            raise RuntimeError("nope")

    selective_client = SimpleNamespace(delete_chat=selective_delete)
    deleted = _run(manager.cleanup_due_chats(client=selective_client))
    assert deleted == 1  # 仅 c_ok 成功


def test_cleanup_due_chats_returns_zero_when_nothing_due():
    """无到期任务 → deleted=0。"""
    manager = RemoteChatCleanupManager()
    client = _async_delete_client()
    deleted = _run(manager.cleanup_due_chats(client=client))
    assert deleted == 0
    client.delete_chat.assert_not_awaited()


def test_cleanup_due_chats_with_explicit_client_skips_resolution():
    """显式传入 client → 跳过 client_initializer / client_provider 解析。"""
    init_calls = []
    manager = RemoteChatCleanupManager(
        client_provider=lambda: (_async_delete_client() if init_calls.append("provider") else None)
    )
    _register_due(manager, "c_x")
    explicit_client = _async_delete_client()
    deleted = _run(manager.cleanup_due_chats(client=explicit_client))
    assert deleted == 1
    assert init_calls == []  # provider 未被调用
    explicit_client.delete_chat.assert_awaited_once_with("c_x")
