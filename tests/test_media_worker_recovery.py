"""Offline checks for exact-handle recovery during Web throttling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import client_wrapper, operation_worker
from src.infrastructure.operation_store import OperationStore
from src.services import media_jobs


@pytest.fixture
def observer(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-media-recovery-account")
    clock = SimpleNamespace(now=1000.0, sleeps=[], on_wait=lambda: None)
    store = OperationStore(tmp_path / "operations.sqlite", clock=lambda: clock.now)
    store.create_or_get("op_media", "gemini_create_media", media_type="video",
                        effective_backend="Gemini Web native browser")
    assert store.claim_worker("op_media", "observer")
    assert store.checkpoint_media("op_media", worker_token="observer", chat_id="c_0123456789abcdef",
                                  response_id="r_original", candidate_id="rc_original", native_submission=True)

    async def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds
        clock.on_wait()

    async def heartbeat(*_args):
        await asyncio.Future()

    monkeypatch.setattr(operation_worker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(operation_worker, "asyncio", SimpleNamespace(
        create_task=asyncio.create_task, wait_for=asyncio.wait_for, sleep=sleep,
        CancelledError=asyncio.CancelledError,
    ))
    monkeypatch.setattr(operation_worker, "_heartbeat", heartbeat)
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", lambda: None)
    drain = AsyncMock()
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", drain)
    monkeypatch.setattr(client_wrapper._client_manager, "reset_async", AsyncMock())
    generate = AsyncMock()
    monkeypatch.setattr(media_jobs, "execute_media_job", generate)
    return store, clock, drain, generate


def _record_throttle(store, error_code):
    assert store.finish_worker(
        "op_media", worker_token="observer", state="queued", phase="native_media_outcome_unverified",
        verification_status="not_observed", error_code=error_code,
    )


def _resume(store, *, observation_seconds):
    asyncio.run(operation_worker.run_worker(
        {"kind": "media_resume", "operation_id": "op_media", "worker_token": "observer"},
        store=store, observation_seconds=observation_seconds, poll_seconds=30,
    ))


@pytest.mark.parametrize("initial_error,reads", [("RATE_LIMITED", [300.0, 900.0]), (None, [0.0, 300.0, 900.0])])
def test_worker_only_reobserves_original_turn_with_increasing_backoff(observer, monkeypatch, initial_error, reads):
    store, clock, drain, generate = observer
    _record_throttle(store, initial_error)
    seen = []

    async def recover(record, *, store, worker_token):
        assert worker_token == "observer"
        assert record.operation_id == "op_media"
        assert (record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id) == (
            "c_0123456789abcdef", "r_original", "rc_original",
        )
        assert record.cleanup_protected
        seen.append(clock.now - 1000.0)
        _record_throttle(store, "RATE_LIMITED")

    monkeypatch.setattr(media_jobs, "recover_media_job", recover)
    _resume(store, observation_seconds=1000)

    assert seen == reads and clock.sleeps == [300.0, 600.0]
    record = store.get("op_media")
    assert record.cleanup_protected and record.state == "queued" and record.error_code == "RATE_LIMITED"
    assert record.worker_token is None
    generate.assert_not_awaited()
    drain.assert_not_awaited()


@pytest.mark.parametrize("condition", ["deadline", "account", "lease"])
def test_worker_rechecks_budget_identity_and_lease_before_throttled_read(observer, monkeypatch, condition):
    store, clock, drain, generate = observer
    _record_throttle(store, "RATE_LIMITED")
    recover = AsyncMock()
    monkeypatch.setattr(media_jobs, "recover_media_job", recover)

    def move_lease():
        store.retire_worker("op_media", "observer")
        assert store.claim_worker("op_media", "another-observer")

    if condition == "account":
        clock.on_wait = lambda: monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
    if condition == "lease":
        clock.on_wait = move_lease
    _resume(store, observation_seconds=299 if condition == "deadline" else 1000)

    assert clock.sleeps == ([] if condition == "deadline" else [300.0])
    record = store.get("op_media")
    assert record.cleanup_protected and record.error_code == "RATE_LIMITED"
    assert record.worker_token == ("another-observer" if condition == "lease" else None)
    recover.assert_not_awaited()
    generate.assert_not_awaited()
    drain.assert_not_awaited()


def _verified_cleanup(observer, monkeypatch, tmp_path, *, completed=True):
    from src.infrastructure.cleanup_store import CleanupStore

    store, *_ = observer
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(tmp_path / "cleanup.sqlite"))
    store.release_cleanup("op_media")
    store.update_cleanup("op_media", "pending")
    record = store.get("op_media")
    cleanup = CleanupStore()
    cleanup.schedule(record.account_key, record.upstream_chat_id, 0, "synthetic-observer", retain=False)
    if completed:
        assert cleanup.record_verified_absence(record.account_key, record.upstream_chat_id)
    return cleanup, record


def test_cleaned_media_resume_exits_before_startup_sdk_and_due_cleanup(observer, monkeypatch, tmp_path):
    from unittest.mock import Mock

    store, _, drain, generate = observer
    cleanup, original = _verified_cleanup(observer, monkeypatch, tmp_path)
    proof = cleanup.get(original.account_key, original.upstream_chat_id)
    startup, initialize, recover = Mock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", startup)
    monkeypatch.setattr(client_wrapper, "initialize_client", initialize)
    monkeypatch.setattr(media_jobs, "recover_media_job", recover)
    _resume(store, observation_seconds=100)
    saved = store.get("op_media")
    assert saved.state == "unavailable" and saved.verification_status == "remote_media_already_cleaned"
    assert saved.cleanup_state == "completed" and not saved.cleanup_protected and saved.worker_token is None
    assert (saved.upstream_chat_id, saved.upstream_response_id, saved.upstream_candidate_id) == (
        original.upstream_chat_id, original.upstream_response_id, original.upstream_candidate_id,
    )
    assert cleanup.get(original.account_key, original.upstream_chat_id) == proof
    startup.assert_not_called()
    initialize.assert_not_awaited()
    recover.assert_not_awaited()
    generate.assert_not_awaited()
    drain.assert_not_awaited()


def test_worker_rechecks_verified_cleanup_after_wait_before_next_read(observer, monkeypatch, tmp_path):
    store, clock, drain, generate = observer
    cleanup, original = _verified_cleanup(observer, monkeypatch, tmp_path, completed=False)
    seen = []

    async def recover(record, *, store, worker_token):
        seen.append(clock.now)
        assert record.upstream_chat_id == original.upstream_chat_id

    clock.on_wait = lambda: cleanup.record_verified_absence(original.account_key, original.upstream_chat_id)
    monkeypatch.setattr(media_jobs, "recover_media_job", recover)
    _resume(store, observation_seconds=100)
    assert len(seen) == 1 and clock.sleeps == [30]
    assert store.get("op_media").state == "unavailable" and store.get("op_media").cleanup_state == "completed"
    generate.assert_not_awaited()
    drain.assert_not_awaited()


def test_inner_recovery_cleanup_reconciliation_also_skips_final_network_drain(observer, monkeypatch, tmp_path):
    store, _, drain, generate = observer
    cleanup, original = _verified_cleanup(observer, monkeypatch, tmp_path, completed=False)
    real_recover = media_jobs.recover_media_job
    initialize = AsyncMock()
    monkeypatch.setattr(client_wrapper, "initialize_client", initialize)

    async def cleanup_then_recover(record, *, store, worker_token):
        assert cleanup.record_verified_absence(original.account_key, original.upstream_chat_id)
        await real_recover(record, store=store, worker_token=worker_token)

    monkeypatch.setattr(media_jobs, "recover_media_job", cleanup_then_recover)
    _resume(store, observation_seconds=100)
    assert store.get("op_media").state == "unavailable" and store.get("op_media").worker_token is None
    initialize.assert_not_awaited()
    generate.assert_not_awaited()
    drain.assert_not_awaited()
