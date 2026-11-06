"""Durable cleanup isolation, cross-process claims, and verified completion."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.domain import CleanupState, DomainResult
from src.infrastructure.cleanup_store import CleanupStore
from src.infrastructure.operation_store import OperationStore
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager


def test_claims_are_atomic_and_retained_records_cannot_be_claimed(tmp_path):
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("account-a", "c_1", 0, "test", retain=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: store.claim("account-a", "c_1"), range(8)))
    assert sum(token is not None for token in tokens) == 1
    store.finish("account-a", "c_1", next(token for token in tokens if token), verified=False, diagnostic_id=None)
    store.schedule("account-a", "c_1", 0, "test", retain=True)
    assert store.claim("account-a", "c_1") is None
    assert store.get("account-a", "c_1")["state"] == "retained"


def test_protected_research_survives_restart_then_deletes_after_release(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr("src.infrastructure.cleanup_store.time.time", lambda: clock[0])
    operation = OperationStore(tmp_path / "research.sqlite", clock=lambda: clock[0])
    operation.create("op_1", "gemini_research")
    operation.update("op_1", state="running", phase="start", upstream_chat_id="c_1")
    path = tmp_path / "cleanup.sqlite"
    manager = RemoteChatCleanupManager(
        store_path=path, account_key_provider=lambda: "a", cleanup_guard=operation.is_cleanup_protected,
    )
    manager.schedule_cleanup_result("c_1", delete_after_seconds=0, dispatch=False)
    send = AsyncMock(return_value=DomainResult.success({}, verification_status="verified_absent"))
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    result = asyncio.run(manager.delete_chat_result("c_1", client=object()))
    assert result.state is CleanupState.PENDING
    assert manager._store.get("a", "c_1")["attempts"] == 0
    assert manager._store.get("a", "c_1")["delete_at"] == 1060
    send.assert_not_awaited()
    reopened = RemoteChatCleanupManager(
        store_path=path, account_key_provider=lambda: "a", cleanup_guard=operation.is_cleanup_protected,
    )
    operation.release_cleanup("op_1")
    clock[0] += 59
    assert asyncio.run(reopened.cleanup_due_chats(client=object())) == 0
    send.assert_not_awaited()
    clock[0] += 1
    assert asyncio.run(reopened.cleanup_due_chats(client=object())) == 1
    send.assert_awaited_once()


def test_guard_database_error_fails_closed_without_attempt(monkeypatch, tmp_path):
    def broken(_cid):
        raise OSError("sensitive database detail")

    path = tmp_path / "cleanup.sqlite"
    manager = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a", cleanup_guard=broken)
    send = AsyncMock()
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    result = asyncio.run(manager.delete_chat_result("c_1", client=object()))
    assert result.state is CleanupState.PENDING
    row = CleanupStore(path).get("a", "c_1")
    assert row["attempts"] == 0 and row["lease_token"] is None
    assert row["delete_at"] > 0
    send.assert_not_awaited()


def test_protection_added_during_readback_releases_claim_without_delete(monkeypatch, tmp_path):
    protected = [False]
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite", account_key_provider=lambda: "a", cleanup_guard=lambda _cid: protected[0],
    )
    manager._store.schedule("a", "c_1", 0, "test", retain=False)
    token = manager._store.claim("a", "c_1")
    manager._store.finish("a", "c_1", token, verified=False, diagnostic_id="diag_1")

    async def observe(_client, _cid, *, expected_account, account_key_provider):
        assert expected_account == account_key_provider() == "a"
        protected[0] = True
        return False, {}

    send = AsyncMock()
    monkeypatch.setattr("src.services.history.observe_chat_absence", observe)
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    result = asyncio.run(manager.delete_chat_result("c_1", client=object()))
    assert result.state is CleanupState.PENDING
    row = manager._store.get("a", "c_1")
    assert row["attempts"] == 1 and row["lease_token"] is None
    send.assert_not_awaited()


def test_protection_guard_does_not_revive_retained_or_completed(tmp_path):
    calls = []
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite", account_key_provider=lambda: "a",
        cleanup_guard=lambda cid: calls.append(cid) or True,
    )
    manager.schedule_cleanup_result("c_1", retain_chat=True)
    assert asyncio.run(manager.delete_chat_result("c_1", client=object())).state is CleanupState.RETAINED
    manager._store.schedule("a", "c_2", 0, "test", retain=False)
    token = manager._store.claim("a", "c_2")
    manager._store.finish("a", "c_2", token, verified=True, diagnostic_id=None)
    assert asyncio.run(manager.delete_chat_result("c_2", client=object())).state is CleanupState.ALREADY_COMPLETED
    assert calls == []


def test_retain_does_not_claim_success_while_deletion_is_in_flight(monkeypatch, tmp_path):
    path = tmp_path / "cleanup.sqlite"
    first = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    second = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def delete(_client, _cid, *, expected_account, account_key_provider):
            assert expected_account == account_key_provider() == "a"
            started.set()
            await release.wait()
            return DomainResult.success({}, verification_status="verified_absent")

        monkeypatch.setattr("src.services.history.delete_chat_result", delete)
        task = asyncio.create_task(first.delete_chat_result("c_1", client=object()))
        await asyncio.wait_for(started.wait(), timeout=2)
        policy = second.schedule_cleanup_result("c_1", retain_chat=True)
        assert policy.state is CleanupState.FAILED
        assert second._store.get("a", "c_1")["state"] == "pending"
        release.set()
        assert (await task).state is CleanupState.COMPLETED

    asyncio.run(run())


def test_explicit_false_overrides_retained_after_restart_without_dispatch(monkeypatch, tmp_path):
    path = tmp_path / "cleanup.sqlite3"
    first = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    first.schedule_cleanup_result("c_1", retain_chat=True)
    reopened = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    client = SimpleNamespace(delete_chat=AsyncMock())

    async def schedule_only():
        result = reopened.schedule_cleanup_result("c_1", retain_chat=False, delete_after_seconds=0, dispatch=False)
        await asyncio.sleep(0.02)
        return result

    observation = asyncio.run(schedule_only())
    assert observation.state is CleanupState.PENDING
    assert CleanupStore(path).get("a", "c_1")["state"] == "pending"
    assert CleanupStore(path).get("a", "c_1")["attempts"] == 0
    client.delete_chat.assert_not_awaited()
    verified = AsyncMock(return_value=DomainResult.success({}, verification_status="verified_absent"))
    monkeypatch.setattr("src.services.history.delete_chat_result", verified)
    assert asyncio.run(reopened.delete_chat_result("c_1", client=client)).state is CleanupState.COMPLETED
    third = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    assert third.schedule_cleanup_result("c_1", retain_chat=False, dispatch=False).state is CleanupState.ALREADY_COMPLETED
    assert third._store.get("a", "c_1")["state"] == "completed"


def test_new_retain_policy_cancels_pending_timer_across_managers(monkeypatch, tmp_path):
    path = tmp_path / "cleanup.sqlite3"
    client = SimpleNamespace(delete_chat=AsyncMock())
    first = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a", client_provider=lambda: client)
    second = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    verified = AsyncMock(return_value=DomainResult.success({}, verification_status="verified_absent"))
    monkeypatch.setattr("src.services.history.delete_chat_result", verified)

    async def schedule_and_retain():
        first.schedule_cleanup_result("c_1", retain_chat=False, delete_after_seconds=0)
        second.schedule_cleanup_result("c_1", retain_chat=True)
        await asyncio.sleep(0.02)

    asyncio.run(schedule_and_retain())
    verified.assert_not_awaited()
    assert second._store.get("a", "c_1")["state"] == "retained"


def test_internal_reschedule_does_not_override_retain(tmp_path):
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("a", "c_1", 0, "user", retain=True)
    store.schedule("a", "c_1", 0, "retry", retain=False)
    assert store.get("a", "c_1")["state"] == "retained"
    assert store.claim("a", "c_1", force=True) is None


def test_explicit_ttl_reschedules_pending_without_bypassing_failure_backoff(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr("src.infrastructure.cleanup_store.time.time", lambda: clock[0])
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "a")
    manager.schedule_cleanup_result("c_1", delete_after_seconds=60, dispatch=False)
    manager.schedule_cleanup_result("c_1", delete_after_seconds=120, dispatch=False, reschedule_pending=True)
    assert manager._store.get("a", "c_1")["delete_at"] == 1120
    manager.schedule_cleanup_result("c_1", delete_after_seconds=10, dispatch=False, reschedule_pending=True)
    assert manager._store.get("a", "c_1")["delete_at"] == 1010
    token = manager._store.claim("a", "c_1", force=True)
    assert token is not None
    manager._store.finish("a", "c_1", token, verified=False, diagnostic_id="diag_1")
    manager.schedule_cleanup_result("c_1", delete_after_seconds=0, dispatch=False, reschedule_pending=True)
    assert manager._store.get("a", "c_1")["delete_at"] == 1030


def test_pending_queue_survives_reopen_and_is_scoped_to_account(tmp_path):
    path = tmp_path / "cleanup.sqlite3"
    a = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "account-a")
    a.schedule_cleanup("c_1", delete_after_seconds=0, source="test")
    b = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "account-b")
    assert not b.list_pending_cleanup()
    reopened = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "account-a")
    assert "c_1" in reopened.list_pending_cleanup()
    assert path.stat().st_mode & 0o077 == 0


def test_due_cleanup_does_not_run_after_switching_accounts(tmp_path):
    account = ["a"]
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: account[0])
    manager.schedule_cleanup("c_1", delete_after_seconds=0)
    client = SimpleNamespace(delete_chat=AsyncMock())
    account[0] = "b"
    assert asyncio.run(manager.cleanup_due_chats(client=client)) == 0
    client.delete_chat.assert_not_awaited()
    account[0] = "a"
    assert "c_1" in manager.list_pending_cleanup()


def test_scheduled_timer_never_deletes_under_a_different_account(tmp_path):
    account = ["a"]
    client = SimpleNamespace(delete_chat=AsyncMock())
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: account[0],
        client_provider=lambda: client,
    )

    async def run():
        manager.schedule_cleanup("c_1", delete_after_seconds=0)
        account[0] = "b"
        await asyncio.sleep(0.05)

    asyncio.run(run())
    client.delete_chat.assert_not_awaited()
    assert manager._store.get("b", "c_1") is None
    assert manager._store.get("a", "c_1")["state"] == "pending"


def test_expired_lease_can_be_recovered_by_another_process(tmp_path):
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("a", "c_1", 0, "test", retain=False)
    first = store.claim("a", "c_1", lease_seconds=-1)
    assert first is not None
    second = CleanupStore(store.path).claim("a", "c_1")
    assert second and second != first
    store.finish("a", "c_1", first, verified=True, diagnostic_id=None)
    assert store.get("a", "c_1")["state"] == "pending"
    store.finish("a", "c_1", second, verified=True, diagnostic_id=None)
    assert store.get("a", "c_1")["state"] == "completed"


def test_failed_cleanup_persists_backoff_but_explicit_force_can_retry(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr("src.infrastructure.cleanup_store.time.time", lambda: clock[0])
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("a", "c_1", 0, "test", retain=False)
    first = store.claim("a", "c_1")
    assert first is not None
    store.finish("a", "c_1", first, verified=False, diagnostic_id="diag_1")
    assert CleanupStore(store.path).get("a", "c_1")["delete_at"] == 1030.0
    clock[0] = 1029.0
    assert store.claim("a", "c_1") is None
    second = store.claim("a", "c_1", force=True)
    assert second is not None
    store.finish("a", "c_1", second, verified=False, diagnostic_id="diag_2")
    assert store.get("a", "c_1")["delete_at"] == 1089.0
    for _ in range(5):
        token = store.claim("a", "c_1", force=True)
        assert token is not None
        store.finish("a", "c_1", token, verified=False, diagnostic_id="diag_3")
    assert store.get("a", "c_1")["delete_at"] == clock[0] + 300


def test_force_delete_expedites_existing_grace_before_remote_request(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr("src.infrastructure.cleanup_store.time.time", lambda: clock[0])
    path = tmp_path / "cleanup.sqlite3"
    manager = RemoteChatCleanupManager(
        store_path=path, account_key_provider=lambda: "a", cleanup_guard=lambda _cid: False,
    )
    manager.schedule_cleanup_result("c_1", delete_after_seconds=1800, dispatch=False)
    assert manager._store.get("a", "c_1")["delete_at"] == 2800

    async def run():
        started = asyncio.Event()
        block = asyncio.Event()

        async def delete(_client, _cid, *, expected_account, account_key_provider):
            assert expected_account == account_key_provider() == "a"
            started.set()
            await block.wait()

        monkeypatch.setattr("src.services.history.delete_chat_result", delete)
        task = asyncio.create_task(manager.delete_chat_result("c_1", client=object()))
        await asyncio.wait_for(started.wait(), timeout=2)
        # Simulate inspecting durable state at an ungraceful process loss:
        # the prior 30-minute session grace must no longer defer recovery.
        row = CleanupStore(path).get("a", "c_1")
        assert row["delete_at"] == 1000
        assert row["lease_token"] is not None
        assert row["lease_until"] == 1600
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def test_expedite_keeps_retained_completed_and_leased_records_safe(monkeypatch, tmp_path):
    monkeypatch.setattr("src.infrastructure.cleanup_store.time.time", lambda: 1000.0)
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("a", "c_retained", 2800, "test", retain=True)
    store.expedite_pending("a", "c_retained")
    assert store.get("a", "c_retained")["state"] == "retained"
    store.schedule("a", "c_completed", 0, "test", retain=False)
    completed_token = store.claim("a", "c_completed")
    store.finish("a", "c_completed", completed_token, verified=True, diagnostic_id=None)
    store.expedite_pending("a", "c_completed")
    assert store.get("a", "c_completed")["state"] == "completed"
    store.schedule("a", "c_leased", 2800, "test", retain=False)
    lease_token = store.claim("a", "c_leased", force=True)
    store.expedite_pending("a", "c_leased")
    row = store.get("a", "c_leased")
    assert row["delete_at"] == 1000
    assert row["lease_token"] == lease_token and row["lease_until"] == 1600


def test_automatic_cleanup_does_not_recreate_removed_record(tmp_path):
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "a")
    client = SimpleNamespace(delete_chat=AsyncMock())
    result = asyncio.run(manager.delete_chat_result("c_unknown", client=client, _expected_account="a"))
    assert result.state is CleanupState.NOT_APPLICABLE
    client.delete_chat.assert_not_awaited()
    assert manager._store.get("a", "c_unknown") is None


def test_maintenance_does_not_retry_failed_cleanup_before_deadline(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr("src.infrastructure.cleanup_store.time.time", lambda: clock[0])
    send = AsyncMock(return_value=DomainResult.success({}, verification_status="not_available"))
    observe = AsyncMock(return_value=(False, {}))
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    monkeypatch.setattr("src.services.history.observe_chat_absence", observe)
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "a")
    manager.schedule_cleanup("c_1", delete_after_seconds=0)
    assert asyncio.run(manager.cleanup_due_chats(client=object())) == 0
    clock[0] += 15
    assert asyncio.run(manager.cleanup_due_chats(client=object())) == 0
    assert send.await_count == 1
    observe.assert_not_awaited()
    clock[0] += 15
    assert asyncio.run(manager.cleanup_due_chats(client=object())) == 0
    assert send.await_count == 2
    assert observe.await_count == 1


def test_delete_without_readback_stays_pending(tmp_path):
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "a")
    manager.schedule_cleanup("c_1", delete_after_seconds=0)
    client = SimpleNamespace(delete_chat=AsyncMock())
    result = asyncio.run(manager.delete_chat_result("c_1", client=client))
    assert result.state is CleanupState.FAILED
    assert "c_1" in manager.list_pending_cleanup()
    client.delete_chat.assert_awaited_once_with("c_1")


def test_verified_delete_is_completed_after_reopen(monkeypatch, tmp_path):
    async def verified(_client, _cid, *, expected_account, account_key_provider):
        assert expected_account == account_key_provider() == "a"
        return DomainResult.success({}, verification_status="verified_absent")

    monkeypatch.setattr("src.services.history.delete_chat_result", verified)
    path = tmp_path / "cleanup.sqlite3"
    manager = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    manager.schedule_cleanup("c_1", delete_after_seconds=0)
    assert asyncio.run(manager.delete_chat_result("c_1", client=object())).state is CleanupState.COMPLETED
    reopened = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    assert reopened.get_cleanup_observation("c_1").state is CleanupState.COMPLETED
    assert not reopened.list_pending_cleanup()


def test_retry_observes_absence_before_sending_a_second_delete(monkeypatch, tmp_path):
    send = AsyncMock(return_value=DomainResult.success({}, verification_status="not_available"))
    observe = AsyncMock(return_value=(True, {"complete": True}))
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    monkeypatch.setattr("src.services.history.observe_chat_absence", observe)
    path = tmp_path / "cleanup.sqlite3"
    manager = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    assert asyncio.run(manager.delete_chat_result("c_1", client=object())).state is CleanupState.FAILED
    reopened = RemoteChatCleanupManager(store_path=path, account_key_provider=lambda: "a")
    assert asyncio.run(reopened.delete_chat_result("c_1", client=object())).state is CleanupState.COMPLETED
    assert send.await_count == 1
    assert observe.await_count == 1


def test_retry_readback_failure_does_not_send_another_delete(monkeypatch, tmp_path):
    send = AsyncMock(return_value=DomainResult.success({}, verification_status="not_available"))
    observe = AsyncMock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    monkeypatch.setattr("src.services.history.observe_chat_absence", observe)
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "a")
    assert asyncio.run(manager.delete_chat_result("c_1", client=object())).state is CleanupState.FAILED
    assert asyncio.run(manager.delete_chat_result("c_1", client=object())).state is CleanupState.FAILED
    assert "c_1" in manager.list_pending_cleanup()
    assert send.await_count == 1
    assert observe.await_count == 1


def test_stale_automatic_dispatch_cannot_bypass_persisted_backoff(monkeypatch, tmp_path):
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "a")
    manager._store.schedule("a", "c_1", 0, "test", retain=False)
    token = manager._store.claim("a", "c_1")
    manager._store.finish("a", "c_1", token, verified=False, diagnostic_id=None)
    send = AsyncMock()
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    result = asyncio.run(manager.delete_chat_result("c_1", client=object(), _expected_account="a"))
    assert result.state is CleanupState.PENDING
    assert manager._store.get("a", "c_1")["attempts"] == 1
    send.assert_not_awaited()
