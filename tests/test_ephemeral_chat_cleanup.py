"""Owned generation chats are finalized before the MCP request returns."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import client_wrapper
from src.domain import CleanupState
from src.infrastructure.rpc_contracts import get_contract
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services import lifecycle as lifecycle_module
from src.services.lifecycle import ConversationLifecycleService
from src.session_manager import SessionService


def _client():
    return SimpleNamespace(
        delete_chat=AsyncMock(),
        _batch_execute=AsyncMock(
            return_value=SimpleNamespace(
                status_code=200,
                text=json.dumps([["wrb.fr", get_contract("history.page").rpc_id, json.dumps([None, None, []])]]),
            ),
        ),
    )


def _lifecycle(manager):
    return ConversationLifecycleService(
        session_provider=SessionService,
        cleanup_provider=lambda: manager,
    )


def test_default_finalization_waits_for_verified_cleanup_before_loop_exit():
    client = _client()
    manager = RemoteChatCleanupManager()
    manager.schedule_cleanup("c_unrelated", delete_after_seconds=3600)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def delete(_cid):
            started.set()
            await release.wait()

        client.delete_chat.side_effect = delete
        task = asyncio.create_task(
            _lifecycle(manager).finalize_generated_chat(
                SimpleNamespace(cid="c_generated"), owns_chat=True, client=client,
            ),
        )
        await started.wait()
        assert not task.done()
        release.set()
        return await task

    result = asyncio.run(run())

    assert result.state is CleanupState.COMPLETED
    assert manager.get_cleanup_observation("c_generated").state is CleanupState.COMPLETED
    assert set(manager.list_pending_cleanup()) == {"c_unrelated"}
    client.delete_chat.assert_awaited_once_with("c_generated")
    assert client._batch_execute.await_count == 2


@pytest.mark.parametrize("response", [SimpleNamespace(cid="c_empty"), SimpleNamespace(metadata=["c_failed", "r_dummy"])])
def test_known_empty_or_failed_generation_chat_can_be_finalized(response):
    client, manager = _client(), RemoteChatCleanupManager()

    result = asyncio.run(_lifecycle(manager).finalize_generated_chat(response, owns_chat=True, client=client))

    assert result.state is CleanupState.COMPLETED
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_awaited_once_with(result.upstream_chat_id)


def test_loaded_existing_chat_is_never_queued_or_deleted_by_finalization():
    client, manager = _client(), RemoteChatCleanupManager()

    result = asyncio.run(
        _lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_existing"), owns_chat=False, client=client,
        ),
    )

    assert result.state is CleanupState.RETAINED
    assert result.upstream_chat_id == "c_existing"
    assert manager.list_pending_cleanup() == {}
    assert manager.list_cleanup_observations() == {}
    client.delete_chat.assert_not_awaited()
    client._batch_execute.assert_not_awaited()


@pytest.mark.parametrize(
    ("response", "expected"),
    [(SimpleNamespace(), CleanupState.NOT_APPLICABLE), (SimpleNamespace(cid="c_"), CleanupState.INVALID_ID)],
)
def test_missing_or_invalid_generated_locator_never_scans_or_deletes_account_chats(response, expected):
    client, manager = _client(), RemoteChatCleanupManager()

    result = asyncio.run(_lifecycle(manager).finalize_generated_chat(response, owns_chat=True, client=client))

    assert result.state is expected
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()
    client._batch_execute.assert_not_awaited()


@pytest.mark.parametrize("policy", [{"retain_chat": True}, {"preserve_for_recovery": True}])
def test_explicit_retention_or_unsaved_artifact_preserves_the_known_locator(policy):
    client, manager = _client(), RemoteChatCleanupManager()
    manager.schedule_cleanup("c_recover", delete_after_seconds=0)

    result = asyncio.run(
        _lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_recover"), owns_chat=True, client=client, **policy,
        ),
    )

    assert result.state is CleanupState.RETAINED
    assert result.upstream_chat_id == "c_recover"
    assert manager.list_pending_cleanup() == {}
    assert manager.get_cleanup_observation("c_recover").state is CleanupState.RETAINED
    client.delete_chat.assert_not_awaited()


def test_explicit_positive_delay_remains_pending_without_deleting_early():
    client, manager = _client(), RemoteChatCleanupManager()

    result = asyncio.run(
        _lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_delayed"), owns_chat=True, delete_after_seconds=60, client=client,
        ),
    )

    assert result.state is CleanupState.PENDING
    assert "c_delayed" in manager.list_pending_cleanup()
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("policy", [{}, {"retain_chat": True}, {"preserve_for_recovery": True}, {"delete_after_seconds": 60}])
def test_old_account_generation_cannot_finalize_in_the_new_account(policy):
    client, manager = _client(), RemoteChatCleanupManager()
    previous_generation = manager.authentication_generation()
    manager.invalidate_authentication_context()

    result = asyncio.run(
        _lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_old"), owns_chat=True, client=client,
            authentication_generation=previous_generation, **policy,
        ),
    )

    assert result.state is CleanupState.CANCELLED
    assert result.cancellation_reason == "authentication_context_changed"
    assert result.upstream_chat_id == "c_old"
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()


def test_cleanup_failure_returns_diagnostic_and_retains_the_failed_job():
    client, manager = _client(), RemoteChatCleanupManager()
    client.delete_chat.side_effect = ConnectionError("offline")

    result = asyncio.run(
        _lifecycle(manager).finalize_generated_chat(SimpleNamespace(cid="c_retry"), owns_chat=True, client=client),
    )

    assert result.state is CleanupState.FAILED
    assert result.diagnostic_id
    assert manager.list_pending_cleanup()["c_retry"].last_diagnostic_id == result.diagnostic_id


def test_retention_cancels_an_already_scheduled_background_timer():
    client = _client()
    manager = RemoteChatCleanupManager(client_provider=lambda: client)

    async def run():
        manager.schedule_cleanup_result("c_recover", delete_after_seconds=3600)
        timer = manager._delayed_cleanup["c_recover"]
        await asyncio.sleep(0)
        result = await _lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_recover"), owns_chat=True, preserve_for_recovery=True, client=client,
        )
        await asyncio.sleep(0)
        assert timer.cancelled()
        assert manager._delayed_cleanup == {}
        return result

    assert asyncio.run(run()).state is CleanupState.RETAINED
    client.delete_chat.assert_not_awaited()


def test_default_finalization_cancels_old_timer_after_foreground_cleanup():
    client = _client()
    manager = RemoteChatCleanupManager(client_provider=lambda: client)

    async def run():
        manager.schedule_cleanup_result("c_finished", delete_after_seconds=3600)
        timer = manager._delayed_cleanup["c_finished"]
        await asyncio.sleep(0)
        result = await _lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_finished"), owns_chat=True, client=client,
        )
        await asyncio.sleep(0)
        assert timer.cancelled()
        assert manager._delayed_cleanup == {}
        return result

    assert asyncio.run(run()).state is CleanupState.COMPLETED
    client.delete_chat.assert_awaited_once_with("c_finished")


def test_earlier_explicit_deadline_replaces_the_timer_without_a_followup_call():
    client = _client()
    manager = RemoteChatCleanupManager(client_provider=lambda: client)

    async def run():
        confirmed = asyncio.Event()

        async def empty_readback(*_args, **_kwargs):
            if client._batch_execute.await_count == 2:
                confirmed.set()
            return client._batch_execute.return_value

        client._batch_execute.side_effect = empty_readback
        manager.schedule_cleanup_result("c_earlier", delete_after_seconds=3600)
        previous_timer = manager._delayed_cleanup["c_earlier"]
        previous_deadline = manager.list_pending_cleanup()["c_earlier"].delete_at
        await asyncio.sleep(0)
        scheduled = manager.schedule_cleanup_result("c_earlier", delete_after_seconds=0)
        assert scheduled.delete_at < previous_deadline
        await asyncio.wait_for(confirmed.wait(), timeout=1)
        assert previous_timer.cancelled()
        assert manager.get_cleanup_observation("c_earlier").state is CleanupState.COMPLETED

    asyncio.run(run())
    client.delete_chat.assert_awaited_once_with("c_earlier")


def test_wrapper_passes_captured_generation_without_rebinding_to_new_authentication(monkeypatch):
    manager = RemoteChatCleanupManager()
    lifecycle = _lifecycle(manager)
    context = manager.authentication_context()
    client = _client()
    monkeypatch.setattr(client_wrapper, "_cleanup_manager", manager)
    monkeypatch.setattr(client_wrapper, "_lifecycle_service", lifecycle)
    token = client_wrapper._request_authentication_generation.set(context)
    manager.invalidate_authentication_context()
    try:
        result = asyncio.run(
            client_wrapper.finalize_generated_chat_cleanup(
                SimpleNamespace(cid="c_late"), owns_chat=True, client=client,
            ),
        )
    finally:
        client_wrapper._request_authentication_generation.reset(token)

    assert result.state is CleanupState.CANCELLED
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("blocked_phase", ["delete", "readback"])
def test_cleanup_wait_is_bounded_without_cancelling_a_shared_delete(monkeypatch, blocked_phase):
    monkeypatch.setattr(lifecycle_module, "_GENERATED_CHAT_CLEANUP_WAIT_SECONDS", 0.01)
    client, manager = _client(), RemoteChatCleanupManager()

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(*_args, **_kwargs):
            started.set()
            await release.wait()
            return client._batch_execute.return_value

        if blocked_phase == "delete":
            client.delete_chat.side_effect = blocked
        else:
            client._batch_execute.side_effect = blocked
        finalizer = asyncio.create_task(_lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_slow"), owns_chat=True, client=client,
        ))
        await started.wait()
        worker = manager._inflight_cleanup["c_slow"]
        joined = asyncio.create_task(manager.delete_chat_result("c_slow", client=client))
        await asyncio.sleep(0)

        timed_out = await asyncio.wait_for(finalizer, timeout=0.2)
        assert timed_out.state is CleanupState.PENDING
        assert timed_out.diagnostic_id
        assert timed_out.attempts == 1
        assert not worker.done()
        assert not joined.done()
        pending = manager.list_pending_cleanup()["c_slow"]
        assert pending.attempts == 1
        assert pending.last_diagnostic_id == timed_out.diagnostic_id
        assert manager.get_cleanup_observation("c_slow") == timed_out

        release.set()
        observed = await joined
        assert observed.state is CleanupState.ALREADY_COMPLETED
        assert manager.get_cleanup_observation("c_slow").state is CleanupState.COMPLETED
        assert manager.get_cleanup_observation("c_slow").attempts == 1
        assert manager.list_pending_cleanup() == {}

    asyncio.run(run())
    client.delete_chat.assert_awaited_once_with("c_slow")
    assert client._batch_execute.await_count == 2


def test_cleanup_timeout_uses_the_original_generation_even_if_authentication_changes(monkeypatch):
    monkeypatch.setattr(lifecycle_module, "_GENERATED_CHAT_CLEANUP_WAIT_SECONDS", 0.01)
    client, manager = _client(), RemoteChatCleanupManager()

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def late_delete(_cid):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        client.delete_chat.side_effect = late_delete
        finalizer = asyncio.create_task(_lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_old_slow"), owns_chat=True, client=client,
        ))
        await started.wait()
        worker = manager._inflight_cleanup["c_old_slow"]
        manager.invalidate_authentication_context()

        timed_out = await asyncio.wait_for(finalizer, timeout=0.2)
        assert timed_out.state is CleanupState.CANCELLED
        assert timed_out.cancellation_reason == "authentication_context_changed"
        assert manager.list_pending_cleanup() == {}
        release.set()
        late_result = await worker
        assert late_result.state is CleanupState.CANCELLED
        assert manager.get_cleanup_observation("c_old_slow").state is CleanupState.CANCELLED
        assert "c_old_slow" not in manager._completed_cleanup

    asyncio.run(run())
    client.delete_chat.assert_awaited_once_with("c_old_slow")


def test_late_failure_after_wait_timeout_keeps_one_real_attempt_and_a_retryable_job(monkeypatch):
    monkeypatch.setattr(lifecycle_module, "_GENERATED_CHAT_CLEANUP_WAIT_SECONDS", 0.01)
    client, manager = _client(), RemoteChatCleanupManager()

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def failure(_cid):
            started.set()
            await release.wait()
            raise ConnectionError("offline")

        client.delete_chat.side_effect = failure
        finalizer = asyncio.create_task(_lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_failed_late"), owns_chat=True, client=client,
        ))
        await started.wait()
        worker = manager._inflight_cleanup["c_failed_late"]
        assert (await asyncio.wait_for(finalizer, timeout=0.2)).state is CleanupState.PENDING
        release.set()
        failed = await worker
        assert failed.state is CleanupState.FAILED
        assert failed.attempts == 1
        assert failed.diagnostic_id
        assert manager.get_cleanup_observation("c_failed_late") == failed
        assert manager.list_pending_cleanup()["c_failed_late"].attempts == 1

    asyncio.run(run())
    client.delete_chat.assert_awaited_once_with("c_failed_late")


def test_automatic_due_cleanup_skips_the_inflight_job_after_wait_timeout(monkeypatch):
    monkeypatch.setattr(lifecycle_module, "_GENERATED_CHAT_CLEANUP_WAIT_SECONDS", 0.01)
    client, manager = _client(), RemoteChatCleanupManager()

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(_cid):
            started.set()
            await release.wait()

        client.delete_chat.side_effect = blocked
        finalizer = asyncio.create_task(_lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_busy"), owns_chat=True, client=client,
        ))
        await started.wait()
        worker = manager._inflight_cleanup["c_busy"]
        timed_out = await asyncio.wait_for(finalizer, timeout=0.2)
        assert timed_out.state is CleanupState.PENDING
        assert "c_busy" in manager.list_pending_cleanup()
        assert await asyncio.wait_for(manager.cleanup_due_chat_results(client=client), timeout=0.2) == ()
        assert not worker.done()
        assert client.delete_chat.await_count == 1
        release.set()
        assert (await worker).state is CleanupState.COMPLETED

    asyncio.run(run())
    client.delete_chat.assert_awaited_once_with("c_busy")


def test_automatic_due_cleanup_does_not_join_a_worker_started_after_its_snapshot():
    client, manager = _client(), RemoteChatCleanupManager()
    manager.schedule_cleanup("c_first", delete_after_seconds=0)
    manager.schedule_cleanup("c_peer", delete_after_seconds=0)

    async def run():
        first_started, peer_started = asyncio.Event(), asyncio.Event()
        first_release, peer_release = asyncio.Event(), asyncio.Event()

        async def delete(cid):
            if cid == "c_first":
                first_started.set()
                await first_release.wait()
            else:
                peer_started.set()
                await peer_release.wait()

        client.delete_chat.side_effect = delete
        automatic = asyncio.create_task(manager.cleanup_due_chat_results(client=client))
        await first_started.wait()
        peer = asyncio.create_task(manager.delete_chat_result("c_peer", client=client))
        await peer_started.wait()
        first_release.set()
        observed = await asyncio.wait_for(automatic, timeout=0.2)
        assert len(observed) == 1 and observed[0].upstream_chat_id == "c_first"
        assert not peer.done()
        peer_release.set()
        assert (await peer).state is CleanupState.COMPLETED

    asyncio.run(run())
    assert client.delete_chat.await_count == 2


@pytest.mark.parametrize("failed", [False, True])
def test_timeout_observation_never_downgrades_a_finished_worker(failed):
    client, manager = _client(), RemoteChatCleanupManager()
    if failed:
        client.delete_chat.side_effect = ConnectionError("offline")
    terminal = asyncio.run(manager.delete_chat_result("c_finished", client=client))

    observed = manager.record_cleanup_wait_timeout("c_finished", authentication_generation=manager.authentication_generation())

    assert observed == terminal
    assert manager.get_cleanup_observation("c_finished") == terminal
    assert observed.attempts == 1
    client.delete_chat.assert_awaited_once_with("c_finished")


@pytest.mark.parametrize("decision", ["retain", "requeue_future", "requeue_due"])
def test_automatic_due_snapshot_rechecks_retention_and_replaced_jobs(decision):
    client, manager = _client(), RemoteChatCleanupManager()
    manager.schedule_cleanup("c_first", delete_after_seconds=0)
    manager.schedule_cleanup("c_next", delete_after_seconds=0)

    async def run():
        first_started, first_release = asyncio.Event(), asyncio.Event()

        async def delete(cid):
            if cid == "c_first":
                first_started.set()
                await first_release.wait()

        client.delete_chat.side_effect = delete
        batch = asyncio.create_task(manager.cleanup_due_chat_results(client=client))
        await asyncio.wait_for(first_started.wait(), timeout=1)
        retained = manager.schedule_cleanup_result("c_next", retain_chat=True, source="recovery")
        assert retained.state is CleanupState.RETAINED
        if decision != "retain":
            # Public scheduling also works outside an event loop, without a timer.
            await asyncio.to_thread(
                manager.schedule_cleanup_result,
                "c_next",
                delete_after_seconds=3600 if decision == "requeue_future" else 0,
                source="replacement",
            )
        first_release.set()
        observed = await asyncio.wait_for(batch, timeout=1)

        assert [result.upstream_chat_id for result in observed] == ["c_first"]
        client.delete_chat.assert_awaited_once_with("c_first")
        current = manager.get_cleanup_observation("c_next")
        if decision == "retain":
            assert current == retained
            assert manager.list_pending_cleanup() == {}
        else:
            assert current.state is CleanupState.PENDING
            assert current.source == "replacement"
            assert manager.list_pending_cleanup()["c_next"].attempts == 0
            if decision == "requeue_due":
                fresh_batch = await manager.cleanup_due_chat_results(client=client)
                assert len(fresh_batch) == 1
                assert fresh_batch[0].upstream_chat_id == "c_next"
                assert fresh_batch[0].state is CleanupState.COMPLETED
                assert client.delete_chat.await_count == 2

    asyncio.run(run())


def test_automatic_due_snapshot_does_not_retry_a_peer_attempt_that_finished():
    client, manager = _client(), RemoteChatCleanupManager()
    manager.schedule_cleanup("c_first", delete_after_seconds=0)
    manager.schedule_cleanup("c_peer", delete_after_seconds=0)

    async def run():
        first_started, first_release = asyncio.Event(), asyncio.Event()

        async def delete(cid):
            if cid == "c_first":
                first_started.set()
                await first_release.wait()
            else:
                raise ConnectionError("offline")

        client.delete_chat.side_effect = delete
        batch = asyncio.create_task(manager.cleanup_due_chat_results(client=client))
        await asyncio.wait_for(first_started.wait(), timeout=1)
        peer = await manager.delete_chat_result("c_peer", client=client)
        assert peer.state is CleanupState.FAILED
        first_release.set()
        observed = await asyncio.wait_for(batch, timeout=1)

        assert [result.upstream_chat_id for result in observed] == ["c_first"]
        assert client.delete_chat.await_count == 2
        assert manager.get_cleanup_observation("c_peer") == peer
        assert manager.list_pending_cleanup()["c_peer"].attempts == 1
        fresh_batch = await manager.cleanup_due_chat_results(client=client)
        assert len(fresh_batch) == 1
        assert fresh_batch[0].state is CleanupState.FAILED
        assert fresh_batch[0].attempts == 2

    asyncio.run(run())


@pytest.mark.parametrize("pending_source", [None, "original_policy"])
def test_generated_chat_immediate_cleanup_preserves_source_on_success(pending_source):
    client, manager = _client(), RemoteChatCleanupManager()
    if pending_source is not None:
        manager.schedule_cleanup("c_source_success", delete_after_seconds=3600, source=pending_source)
    expected_source = pending_source if pending_source is not None else "gemini_generate_image"

    async def run():
        lifecycle = _lifecycle(manager)
        response = SimpleNamespace(cid="c_source_success")
        result = await lifecycle.finalize_generated_chat(
            response, owns_chat=True, source="gemini_generate_image", client=client,
        )
        assert result.state is CleanupState.COMPLETED
        assert result.source == expected_source
        assert manager.get_cleanup_observation(response.cid) == result
        repeated = await lifecycle.finalize_generated_chat(
            response, owns_chat=True, source="different_caller", client=client,
        )
        assert repeated.state is CleanupState.ALREADY_COMPLETED
        assert repeated.idempotent
        assert repeated.source == expected_source

    asyncio.run(run())
    client.delete_chat.assert_awaited_once_with("c_source_success")


@pytest.mark.parametrize("pending_source", [None, "original_policy"])
def test_generated_chat_immediate_cleanup_preserves_source_on_failure(pending_source):
    client, manager = _client(), RemoteChatCleanupManager()
    if pending_source is not None:
        manager.schedule_cleanup("c_source_failure", delete_after_seconds=3600, source=pending_source)
    expected_source = pending_source if pending_source is not None else "gemini_generate_image"
    client.delete_chat.side_effect = ConnectionError("offline")

    result = asyncio.run(_lifecycle(manager).finalize_generated_chat(
        SimpleNamespace(cid="c_source_failure"), owns_chat=True, source="gemini_generate_image", client=client,
    ))

    assert result.state is CleanupState.FAILED
    assert result.source == expected_source
    assert manager.get_cleanup_observation("c_source_failure") == result
    assert manager.list_pending_cleanup()["c_source_failure"].source == expected_source
    client.delete_chat.assert_awaited_once_with("c_source_failure")


@pytest.mark.parametrize("pending_source", [None, "original_policy"])
def test_generated_chat_cleanup_source_survives_timeout_and_late_failure(monkeypatch, pending_source):
    monkeypatch.setattr(lifecycle_module, "_GENERATED_CHAT_CLEANUP_WAIT_SECONDS", 0.01)
    client, manager = _client(), RemoteChatCleanupManager()
    if pending_source is not None:
        manager.schedule_cleanup("c_source_late", delete_after_seconds=3600, source=pending_source)
    expected_source = pending_source if pending_source is not None else "gemini_generate_image"

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def fail(_cid):
            started.set()
            await release.wait()
            raise ConnectionError("offline")

        client.delete_chat.side_effect = fail
        finalizer = asyncio.create_task(_lifecycle(manager).finalize_generated_chat(
            SimpleNamespace(cid="c_source_late"), owns_chat=True, source="gemini_generate_image", client=client,
        ))
        await asyncio.wait_for(started.wait(), timeout=1)
        worker = manager._inflight_cleanup["c_source_late"]
        pending = await asyncio.wait_for(finalizer, timeout=1)
        assert pending.state is CleanupState.PENDING
        assert pending.source == expected_source
        assert manager.list_pending_cleanup()["c_source_late"].source == expected_source
        release.set()
        late = await worker
        assert late.state is CleanupState.FAILED
        assert late.attempts == 1
        assert late.source == expected_source
        assert manager.get_cleanup_observation("c_source_late") == late
        assert manager.list_pending_cleanup()["c_source_late"].source == expected_source

    asyncio.run(run())
    client.delete_chat.assert_awaited_once_with("c_source_late")
