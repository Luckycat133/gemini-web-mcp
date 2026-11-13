"""Default cleanup waits for evidence and survives the serving lifecycle."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import client_wrapper as cw
from src.client_manager import ClientInitializationResetError
from src.domain import CleanupObservation, CleanupState, DomainResult
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services import history


@pytest.mark.parametrize("initialization_resets", [False, True])
def test_default_cleanup_does_not_register_or_dispatch_under_a_switched_account(
    monkeypatch, tmp_path, initialization_resets,
):
    account = "synthetic-original"
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite3",
        account_key_provider=lambda: account,
        cleanup_guard=lambda _cid: False,
    )
    delete = AsyncMock(return_value=DomainResult.success({}, verification_status="verified_absent"))

    async def initialize():
        nonlocal account
        account = "synthetic-replacement"
        if initialization_resets:
            raise ClientInitializationResetError("synthetic account changed")

    monkeypatch.setattr(cw, "_cleanup_manager", manager)
    monkeypatch.setattr(cw, "get_gemini_client", object)
    monkeypatch.setattr(cw, "initialize_client", initialize)
    monkeypatch.setattr(history, "delete_chat_result", delete)

    result = asyncio.run(cw.finalize_remote_chat_cleanup("c_owned", delete_after_seconds=0))

    assert result.state is CleanupState.FAILED
    assert manager._store.get("synthetic-original", "c_owned")["state"] == "pending"
    assert manager._store.get("synthetic-replacement", "c_owned") is None
    delete.assert_not_awaited()


def test_default_finalization_awaits_delete_without_dispatch_race(monkeypatch):
    calls = []

    async def delete(cid, *, client, expected_account):
        assert expected_account == cw._cleanup_manager._account()
        await asyncio.sleep(0)
        calls.append(cid)
        return CleanupObservation(CleanupState.COMPLETED, cid, attempts=1)

    monkeypatch.setattr(cw._lifecycle_service, "delete_chat_result", delete)
    monkeypatch.setattr(cw, "schedule_remote_chat_cleanup", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not detach")))
    result = asyncio.run(cw.finalize_remote_chat_cleanup("c_abc123", client=object()))
    assert calls == ["c_abc123"]
    assert result.state is CleanupState.COMPLETED


def test_explicit_retention_never_calls_delete(monkeypatch):
    delete = AsyncMock()
    monkeypatch.setattr(cw._lifecycle_service, "delete_chat_result", delete)
    result = asyncio.run(cw.finalize_remote_chat_cleanup("c_abc123", retain_chat=True))
    assert result.state is CleanupState.RETAINED
    delete.assert_not_awaited()


def test_finalization_applies_explicit_delay_after_stream_progress_hold():
    import time

    async def run():
        cw.schedule_remote_chat_cleanup("c_progress", delete_after_seconds=1800)
        result = await cw.finalize_remote_chat_cleanup("c_progress", delete_after_seconds=60)
        assert result.state is CleanupState.PENDING
        assert 55 <= result.delete_at - time.time() <= 60
        assert cw.list_pending_remote_chat_cleanup()["c_progress"]["delete_at"] == result.delete_at

    asyncio.run(run())


def test_unwritable_queue_does_not_hide_the_generated_result(monkeypatch):
    monkeypatch.setattr(cw._lifecycle_service, "delete_chat_result", AsyncMock(side_effect=OSError("unwritable")))
    monkeypatch.setattr(cw._cleanup_manager, "record_cleanup_failure", lambda *_a, **_k: (_ for _ in ()).throw(OSError("unwritable")))
    result = asyncio.run(cw.finalize_remote_chat_cleanup("c_abc123", client=object()))
    assert result.state is CleanupState.FAILED
    assert result.diagnostic_id


def test_server_shutdown_cleans_sessions_before_closing_client(monkeypatch):
    events = []

    async def reset_all(**kwargs):
        events.append("sessions")

    async def close():
        events.append("client")

    monkeypatch.setattr(cw._lifecycle_service, "reset_all", reset_all)
    monkeypatch.setattr(cw, "_drain_due_cleanup", AsyncMock())
    monkeypatch.setattr(cw, "_client_manager", SimpleNamespace(reset_async=close))

    async def run():
        async with cw.server_lifespan(None):
            events.append("serving")

    asyncio.run(run())
    assert events == ["serving", "sessions", "client"]


def test_server_shutdown_steps_share_one_total_deadline(monkeypatch):
    """A slow earlier step reduces the remaining budget for later steps."""
    events = []
    deadlines = []
    step_results = []
    offset = [0.0]
    real_await_step = cw._await_shutdown_step

    class LoopClock:
        def __init__(self, loop):
            self.loop = loop

        def time(self):
            return self.loop.time() + offset[0]

    class AsyncioClock:
        def __getattr__(self, name):
            return getattr(asyncio, name)

        def get_running_loop(self):
            return LoopClock(asyncio.get_running_loop())

    async def checked_step(awaitable, deadline):
        deadlines.append(deadline)
        if len(deadlines) == 1:
            # Keep the production 30-second shared deadline while making its
            # remaining budget short enough for a deterministic regression.
            offset[0] = 29.88
        result = await real_await_step(awaitable, deadline)
        step_results.append(result)
        return result

    async def close_browser_sessions():
        events.append("browser_sessions")

    async def reset_all(**_kwargs):
        events.append("sessions")
        await asyncio.sleep(0.02)

    async def drain():
        events.append("drain")
        try:
            await asyncio.sleep(1)
        finally:
            events.append("drain_cancelled")

    async def close():
        events.append("client")

    from src.services import task_browser_sessions

    monkeypatch.setattr(task_browser_sessions, "close_task_browser_sessions", close_browser_sessions)
    monkeypatch.setattr(cw, "asyncio", AsyncioClock())
    monkeypatch.setattr(cw, "_await_shutdown_step", checked_step)
    monkeypatch.setattr(cw._lifecycle_service, "reset_all", reset_all)
    monkeypatch.setattr(cw, "_drain_due_cleanup", drain)
    monkeypatch.setattr(cw, "_client_manager", SimpleNamespace(reset_async=close))

    async def run():
        async with cw.server_lifespan(None):
            pass
        await asyncio.sleep(0)  # Let the timed-out task observe its cancellation.

    asyncio.run(run())
    assert len(deadlines) == 4
    assert len(set(deadlines)) == 1
    assert step_results == [True, True, False]  # Cancelled maintenance is suppressed by the lifespan.
    assert events == ["browser_sessions", "sessions", "drain", "drain_cancelled"]


def test_serving_repairs_account_bound_operation_ownership_before_draining(monkeypatch):
    from src.infrastructure import operation_store
    from src.services import background

    events = []
    store = object()

    async def run():
        drained = asyncio.Event()

        async def maintain():
            events.append("workers")

        def repair(actual_store, get_observation, schedule):
            assert actual_store is store
            assert get_observation == cw._cleanup_manager.get_cleanup_observation
            assert schedule is cw.schedule_remote_chat_cleanup
            events.append("repair")

        async def drain():
            events.append("drain")
            drained.set()

        monkeypatch.setattr(operation_store, "OperationStore", lambda: store)
        monkeypatch.setattr(background, "maintain_background_operations", maintain)
        monkeypatch.setattr(background, "repair_operation_cleanup", repair)
        monkeypatch.setattr(cw, "_drain_due_cleanup", drain)
        monkeypatch.setattr(cw._lifecycle_service, "reset_all", AsyncMock())
        monkeypatch.setattr(cw._client_manager, "reset_async", AsyncMock())
        async with cw.server_lifespan(None):
            await asyncio.wait_for(drained.wait(), 2)

    asyncio.run(run())
    assert events[:3] == ["workers", "repair", "drain"]
