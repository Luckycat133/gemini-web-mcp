"""Durable cleanup recovers only owned jobs within one credential scope."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src import client_wrapper
from src.domain import CleanupState
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.state_store import StateStore, authentication_scope
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.lifecycle import ConversationLifecycleService
from src.session_manager import SessionService


def _client():
    return SimpleNamespace(delete_chat=AsyncMock(), _batch_execute=AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        text=json.dumps([["wrb.fr", get_contract("history.page").rpc_id, json.dumps([None, None, []])]]),
    )))


def _manager(store, psid="fake-account-a", **kwargs):
    manager = RemoteChatCleanupManager(state_store=store, **kwargs)
    manager.bind_authentication_scope(authentication_scope(psid, store=store))
    return manager


def _service(manager):
    return ConversationLifecycleService(session_provider=SessionService, cleanup_provider=lambda: manager)


def test_restart_restores_allowed_job_and_persists_positive_completion(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = StateStore(path)
    first = _manager(store)
    first.schedule_cleanup("c_owned", delete_after_seconds=0, source="gemini_image")
    second = _manager(StateStore(path))
    client = _client()

    result = asyncio.run(second.cleanup_due_chat_results(client=client))

    assert len(result) == 1 and result[0].state is CleanupState.COMPLETED
    client.delete_chat.assert_awaited_once_with("c_owned")
    scope = authentication_scope("fake-account-a", store=store)
    record = store.cleanup_jobs.get(scope, "c_owned")
    assert record.state == "completed" and record.verification_status == "verified_absent"
    assert _manager(StateStore(path)).get_cleanup_observation("c_owned").state is CleanupState.COMPLETED


def test_missing_credentials_cannot_restore_or_queue_cleanup(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = RemoteChatCleanupManager(state_store=store)
    client = _client()
    result = manager.schedule_cleanup_result("c_owned", delete_after_seconds=0)
    direct = asyncio.run(manager.delete_chat_result("c_owned", client=client, allow_durable=True))
    assert result.state is direct.state is CleanupState.CANCELLED
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()


def test_new_credential_scope_cannot_see_or_resume_old_jobs(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    old = _manager(store)
    old.schedule_cleanup("c_old", delete_after_seconds=0)
    other = _manager(StateStore(store.path), psid="fake-account-b")
    assert other.list_durable_jobs() == ()
    assert other.list_pending_cleanup() == {}
    old.bind_authentication_scope(authentication_scope("fake-account-b", store=store))
    old_scope = authentication_scope("fake-account-a", store=store)
    assert store.cleanup_jobs.get(old_scope, "c_old").state == "cancelled"
    assert old.list_pending_cleanup() == {}


def test_generated_finalizer_registers_durable_authority_but_existing_chat_does_not(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    async def run():
        owned = await _service(manager).finalize_generated_chat(SimpleNamespace(cid="c_generated"), owns_chat=True, client=client)
        existing = await _service(manager).finalize_generated_chat(SimpleNamespace(cid="c_existing"), owns_chat=False, client=client)
        return owned, existing
    owned, existing = asyncio.run(run())
    assert owned.state is CleanupState.COMPLETED and existing.state is CleanupState.RETAINED
    assert [row.resource_id for row in manager.list_durable_jobs()] == ["c_generated"]


def test_arbitrary_explicit_delete_failure_grants_no_durable_retry(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    client.delete_chat.side_effect = ConnectionError("private response must not be persisted")
    result = asyncio.run(manager.delete_chat_result("c_existing", client=client))
    assert result.state is CleanupState.FAILED
    assert manager.list_durable_jobs() == ()
    assert manager.list_pending_cleanup()["c_existing"].durable_version is None
    assert _manager(StateStore(store.path)).list_pending_cleanup() == {}


def test_cross_client_atomic_claim_skips_active_peer_without_waiting(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    first, second, client = _manager(store), _manager(StateStore(store.path)), _client()
    first.schedule_cleanup("c_owned", delete_after_seconds=0)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(_cid):
            entered.set()
            await release.wait()
        client.delete_chat.side_effect = delete
        worker = asyncio.create_task(first.cleanup_due_chat_results(client=client))
        await entered.wait()
        async with asyncio.timeout(1):
            assert await second.cleanup_due_chat_results(client=client) == ()
            peer = await second.delete_chat_result("c_owned", client=client)
            assert peer.state is CleanupState.PENDING and peer.idempotent
        release.set()
        return await worker
    assert asyncio.run(run())[0].state is CleanupState.COMPLETED
    client.delete_chat.assert_awaited_once()


def test_expired_process_lease_recovers_absence_without_second_delete(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = _manager(store)
    manager.schedule_cleanup("c_owned", delete_after_seconds=0)
    record = manager.list_durable_jobs()[0]
    record = store.cleanup_jobs.update(record.scope_id, record.resource_id, expected_version=record.version,
                                       due_at=time.time() - 3)
    claimed = store.cleanup_jobs.claim(record.scope_id, record.resource_id, expected_version=record.version,
                                       lease_id="lease_dead_process", lease_until=time.time() - 1, now=time.time() - 2)
    assert claimed is not None
    restarted, client = _manager(StateStore(store.path)), _client()
    result = asyncio.run(restarted.cleanup_due_chat_results(client=client))
    assert result[0].state is CleanupState.COMPLETED and result[0].attempts == 2
    client.delete_chat.assert_not_awaited()
    assert client._batch_execute.await_count == 2


def test_heartbeat_prevents_peer_reclaim_during_slow_delete(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    first = _manager(store, lease_seconds=0.06)
    second, client = _manager(StateStore(store.path)), _client()
    first.schedule_cleanup("c_owned", delete_after_seconds=0)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(_cid):
            entered.set()
            await release.wait()
        client.delete_chat.side_effect = delete
        task = asyncio.create_task(first.cleanup_due_chat_results(client=client))
        await entered.wait()
        await asyncio.sleep(0.15)
        assert await second.cleanup_due_chat_results(client=client) == ()
        release.set()
        return await task
    assert asyncio.run(run())[0].state is CleanupState.COMPLETED
    client.delete_chat.assert_awaited_once()


@pytest.mark.parametrize("decision", ["retain", "cancel"])
def test_retained_or_cancelled_pending_job_cannot_reappear_after_restart(tmp_path, decision):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = _manager(store)
    manager.schedule_cleanup("c_owned", delete_after_seconds=0)
    job_id = manager.list_durable_jobs()[0].job_id
    if decision == "retain":
        assert manager.schedule_cleanup_result("c_owned", retain_chat=True).state is CleanupState.RETAINED
    else:
        assert manager.cancel_durable_job(job_id).state == "cancelled"
    resumed, client = _manager(StateStore(store.path)), _client()
    assert asyncio.run(resumed.cleanup_due_chat_results(client=client)) == ()
    result = asyncio.run(resumed.retry_durable_job(job_id, client=client))
    assert result.state is (CleanupState.RETAINED if decision == "retain" else CleanupState.CANCELLED)
    client.delete_chat.assert_not_awaited()


def test_late_cancelled_worker_cannot_overwrite_terminal_decision(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    manager.schedule_cleanup("c_owned", delete_after_seconds=0)
    job_id = manager.list_durable_jobs()[0].job_id
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(_cid):
            entered.set()
            await release.wait()
        client.delete_chat.side_effect = delete
        task = asyncio.create_task(manager.cleanup_due_chat_results(client=client))
        await entered.wait()
        manager.cancel_durable_job(job_id)
        release.set()
        return await task
    result = asyncio.run(run())
    assert result[0].state is CleanupState.CANCELLED
    assert manager.get_durable_job(job_id).state == "cancelled"


def test_failed_verification_is_persisted_as_failed_with_backoff(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    client._batch_execute.return_value.status_code = 503
    result = asyncio.run(_service(manager).finalize_generated_chat(SimpleNamespace(cid="c_owned"), owns_chat=True, client=client))
    record = manager.list_durable_jobs()[0]
    assert result.state is CleanupState.FAILED and record.state == "failed"
    assert record.due_at > time.time() and record.verification_status != "verified_absent"
    assert record.diagnostic_id and record.error_code


def test_only_controlled_source_kind_is_persisted(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = _manager(store)
    manager.schedule_cleanup("c_owned", delete_after_seconds=3600, source="private prompt with account content")
    assert manager.list_durable_jobs()[0].source == "generated_chat"


@pytest.mark.parametrize("source", ["creation:image", "creation.recover:music", "skill_create:image_edit", "gemini_generate_media:video"])
def test_creation_source_is_preserved_across_restart(tmp_path, source):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = _manager(store)
    manager.schedule_cleanup("c_owned", delete_after_seconds=3600, source=source)
    restored = _manager(StateStore(tmp_path / "state.sqlite3"))
    assert restored.get_cleanup_observation("c_owned").source == source


@pytest.mark.parametrize("configured", [False, True])
def test_cookie_timestamp_refresh_keeps_cleanup_generation(tmp_path, monkeypatch, configured):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = _manager(store)
    manager.schedule_cleanup("c_owned", delete_after_seconds=3600)
    if configured:
        monkeypatch.setenv("GEMINI_PSID", "fake-account-a")
    else:
        monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.setattr(client_wrapper, "_cleanup_manager", manager)
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(authentication_matches=lambda _: False, reset=Mock()))
    before = manager.authentication_generation()
    client_wrapper._on_cookie_update(SimpleNamespace(psid="fake-account-a", psidts="fresh-ts", extra_cookies={}))
    assert manager.authentication_generation() == before
    assert manager.list_durable_jobs()[0].state == "pending"


def test_expired_terminal_pruning_does_not_drop_retryable_jobs(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager = _manager(store)
    manager.schedule_cleanup("c_owned", delete_after_seconds=0)
    record = manager.list_durable_jobs()[0]
    store.cleanup_jobs.update(record.scope_id, record.resource_id, expected_version=record.version,
                              expires_at=time.time() - 1)
    assert store.cleanup_jobs.prune() == 0
    assert manager.get_durable_job(record.job_id) is not None


def test_scope_uses_local_secret_and_stays_stable_across_store_instances(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = authentication_scope("fake-account-a", store=StateStore(path))
    assert first == authentication_scope("fake-account-a", store=StateStore(path))
    assert first != authentication_scope("fake-account-b", store=StateStore(path))
    assert "fake-account-a" not in first


def test_durable_late_old_worker_never_completes_new_scope_or_suspends_its_job(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, old, new = _manager(store), _client(), _client()
    old_scope = authentication_scope("fake-account-a", store=store)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(_cid):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        old.delete_chat.side_effect = delete
        old_task = asyncio.create_task(_service(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_reused"), owns_chat=True, client=old,
        ))
        await entered.wait()
        manager.bind_authentication_scope(authentication_scope("fake-account-b", store=store))
        newer = await _service(manager).finalize_generated_chat(SimpleNamespace(cid="c_reused"), owns_chat=True, client=new)
        release.set()
        older = await old_task
        return older, newer
    older, newer = asyncio.run(run())
    assert older.state is CleanupState.CANCELLED and newer.state is CleanupState.COMPLETED
    assert store.cleanup_jobs.get(old_scope, "c_reused").state == "cancelled"
    assert manager.get_cleanup_observation("c_reused").state is CleanupState.COMPLETED
    assert "c_reused" not in manager._suspended_cleanup


def test_lost_authority_during_recovery_readback_prevents_delete(tmp_path, monkeypatch):
    from src.services import history
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    manager.schedule_cleanup("c_owned", delete_after_seconds=0)
    record = manager.list_durable_jobs()[0]
    store.cleanup_jobs.update(record.scope_id, record.resource_id, expected_version=record.version,
                              state="failed", attempts=1)
    async def absence(_client, _cid):
        manager.cancel_durable_job(record.job_id)
        return False, {"complete": True}
    monkeypatch.setattr(history, "observe_chat_absence", absence)
    results = asyncio.run(manager.cleanup_due_chat_results(client=client))
    assert results[0].state is CleanupState.CANCELLED
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("failure", ["claim", "terminal"])
def test_storage_failure_never_loses_generated_locator_or_claims_completion(tmp_path, monkeypatch, failure):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    def fail(*_args, **_kwargs):
        raise OSError("offline disk failure")
    monkeypatch.setattr(store.cleanup_jobs, "claim" if failure == "claim" else "update", fail)
    result = asyncio.run(_service(manager).finalize_generated_chat(SimpleNamespace(cid="c_owned"), owns_chat=True, client=client))
    assert result.state is CleanupState.FAILED and result.upstream_chat_id == "c_owned"
    assert result.diagnostic_id
    assert manager.list_durable_jobs()[0].state != "completed"
    assert client.delete_chat.await_count == (1 if failure == "terminal" else 0)


def test_retention_after_claim_preserves_current_attempt_but_withdraws_restart_retry(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    manager.schedule_cleanup("c_owned", delete_after_seconds=0)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(_cid):
            entered.set()
            await release.wait()
            raise ConnectionError("offline")
        client.delete_chat.side_effect = delete
        task = asyncio.create_task(manager.cleanup_due_chat_results(client=client))
        await entered.wait()
        assert manager.schedule_cleanup_result("c_owned", retain_chat=True).state is CleanupState.RETAINED
        release.set()
        return await task
    assert asyncio.run(run())[0].state is CleanupState.RETAINED
    resumed = _manager(StateStore(store.path))
    assert resumed.list_durable_jobs()[0].state == "retained"
    assert asyncio.run(resumed.cleanup_due_chat_results(client=client)) == ()
    client.delete_chat.assert_awaited_once()


def test_cross_client_policy_change_invalidates_due_snapshot(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    manager, peer, client = _manager(store), _manager(StateStore(store.path)), _client()
    manager.schedule_cleanup("c_first", delete_after_seconds=0)
    manager.schedule_cleanup("c_second", delete_after_seconds=0)
    async def delete(cid):
        if cid == "c_first":
            peer.schedule_cleanup_result("c_second", retain_chat=True)
    client.delete_chat.side_effect = delete
    observations = asyncio.run(manager.cleanup_due_chat_results(client=client))
    assert len(observations) == 1 and observations[0].state is CleanupState.COMPLETED
    client.delete_chat.assert_awaited_once_with("c_first")
    assert next(row for row in manager.list_durable_jobs() if row.resource_id == "c_second").state == "retained"


def test_cleanup_diagnostics_are_paginated_scoped_and_cancelled_jobs_do_not_run(tmp_path, monkeypatch):
    from src.services import cleanup
    store = StateStore(tmp_path / "state.sqlite3")
    manager, client = _manager(store), _client()
    for index in range(3):
        manager.schedule_cleanup(f"c_owned_{index}", delete_after_seconds=0)
    monkeypatch.setattr(cleanup, "_cleanup_owner", lambda _client=None: manager)
    async def run():
        first = await cleanup.list_cleanup_jobs(client, limit=2)
        assert first.data["has_more"] is True and first.data["next_offset"] == 2
        assert all("scope_id" not in row and "lease_id" not in row for row in first.data["jobs"])
        cancelled = await cleanup.cancel_cleanup_job(first.data["jobs"][0]["job_id"], client)
        assert cancelled.data["cancelled"] is True
        retried = await cleanup.retry_cleanup_job(first.data["jobs"][0]["job_id"], client)
        assert not retried.ok
        batch = await cleanup.cleanup_due_jobs(client, limit=1)
        assert batch.data["attempted_count"] == batch.data["verified_deleted_count"] == 1
        assert batch.data["pending_count"] == 1
        last = await cleanup.list_cleanup_jobs(client, limit=2, offset=2)
        assert last.data["has_more"] is False and last.data["next_offset"] is None
    asyncio.run(run())
    client.delete_chat.assert_awaited_once()
