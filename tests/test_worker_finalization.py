"""Worker finalization retains ownership and business errors when helpers fail."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import operation_worker
from src.infrastructure.operation_store import OperationRecord


class _Store:
    def __init__(self, *, heartbeat_error=None, proof_error=None, retire_error=None, replacement=False):
        self.record = OperationRecord(operation_id="synthetic", operation="gemini_create_media", state="running",
            phase="generation_observed", created_at=1, updated_at=1, media_type="video", account_key="synthetic-owner",
            upstream_chat_id="c_0123456789abcdef", upstream_response_id="r_synthetic", upstream_candidate_id="rc_synthetic")
        self.token = "worker"
        self.body_done = False
        self.heartbeat_error, self.proof_error, self.retire_error = heartbeat_error, proof_error, retire_error
        self.replacement = replacement
        self.retire_calls = []

    def is_worker_owner(self, _operation_id, token):
        return self.token == token

    def bind_worker(self, *_args):
        return True

    def heartbeat_worker(self, *_args):
        if self.heartbeat_error:
            raise self.heartbeat_error
        return True

    def get(self, _operation_id):
        if self.body_done and self.proof_error:
            raise self.proof_error
        return self.record

    def finish_worker(self, *_args, **_kwargs):
        return True

    def retire_worker(self, operation_id, token):
        self.retire_calls.append((operation_id, token))
        if self.retire_error:
            raise self.retire_error
        if self.token == token:
            self.token = None


@pytest.fixture
def environment(monkeypatch):
    from src import client_wrapper
    from src.services import media_jobs

    drain, reset = AsyncMock(), AsyncMock()
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", lambda: None)
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", drain)
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(reset_async=reset))
    monkeypatch.setattr(operation_worker, "current_account_key", lambda: "synthetic-owner")
    monkeypatch.setattr(operation_worker, "operation_account_matches", lambda _record: True)
    monkeypatch.setattr(operation_worker, "reconcile_owned_media_cleanup", lambda *_args: False)
    monkeypatch.setattr(operation_worker, "has_verified_owned_cleanup", lambda *_args: False)

    async def execute(_request, *, store, **_kwargs):
        await asyncio.sleep(0)  # Allow an independently failing heartbeat to finish.
        store.body_done = True
        if store.replacement:
            store.token = "replacement"

    monkeypatch.setattr(media_jobs, "execute_media_job", execute)
    return drain, reset


def _run(store):
    return operation_worker.run_worker({"operation_id": "synthetic", "worker_token": "worker", "kind": "media",
                                       "request": {}}, store=store, observation_seconds=0)


@pytest.mark.parametrize("error", [OSError("synthetic heartbeat I/O"), sqlite3.OperationalError("synthetic DB lock")])
def test_heartbeat_failure_cannot_skip_own_lease_retirement_or_client_reset(environment, error):
    drain, reset = environment
    store = _Store(heartbeat_error=error)
    asyncio.run(_run(store))
    assert store.retire_calls == [("synthetic", "worker")] and store.token is None
    assert store.record.state == "running" and store.record.phase == "generation_observed" and store.record.cleanup_protected
    drain.assert_not_awaited()
    reset.assert_awaited_once()


@pytest.mark.parametrize("failure", ["get", "proof"])
def test_cleanup_proof_read_failure_does_not_skip_retirement_or_expand_drain(environment, monkeypatch, failure):
    drain, reset = environment
    store = _Store(replacement=True, proof_error=sqlite3.OperationalError("synthetic read") if failure == "get" else None)
    if failure == "proof":
        def unavailable(_record):
            raise OSError("synthetic cleanup proof")
        monkeypatch.setattr(operation_worker, "has_verified_owned_cleanup", unavailable)
    asyncio.run(_run(store))
    assert store.retire_calls == [("synthetic", "worker")] and store.token == "replacement"
    drain.assert_not_awaited()
    reset.assert_awaited_once()


def test_retirement_never_clears_a_replacement_workers_token(environment):
    _drain, reset = environment
    store = _Store(replacement=True)
    asyncio.run(_run(store))
    assert store.retire_calls == [("synthetic", "worker")] and store.token == "replacement"
    assert store.record.cleanup_protected
    reset.assert_awaited_once()


def test_retirement_io_failure_is_not_reported_as_success_but_reset_is_still_attempted(environment):
    drain, reset = environment
    failure = OSError("synthetic retirement I/O")
    store = _Store(retire_error=failure)
    with pytest.raises(OSError) as caught:
        asyncio.run(_run(store))
    assert caught.value is failure and store.token == "worker"
    assert store.retire_calls == [("synthetic", "worker")]
    drain.assert_not_awaited()
    reset.assert_awaited_once()


@pytest.mark.parametrize("retire_fails", [False, True])
@pytest.mark.parametrize("heartbeat_fails", [False, True])
def test_cancellation_preserves_original_exception_and_attempts_retirement(
    environment, monkeypatch, retire_fails, heartbeat_fails,
):
    from src.services import media_jobs

    drain, reset = environment
    store = _Store(heartbeat_error=OSError("synthetic heartbeat") if heartbeat_fails else None,
                   retire_error=OSError("synthetic retire") if retire_fails else None)

    async def exercise():
        started = asyncio.Event()

        async def blocked(*_args, **_kwargs):
            await asyncio.sleep(0)
            started.set()
            await asyncio.Future()

        monkeypatch.setattr(media_jobs, "execute_media_job", blocked)
        task = asyncio.create_task(_run(store))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert store.retire_calls == [("synthetic", "worker")]
    assert store.token == ("worker" if retire_fails else None)
    assert store.record.cleanup_protected
    drain.assert_not_awaited()
    reset.assert_awaited_once()


def test_finalization_errors_do_not_replace_an_unhandled_business_exception(environment, monkeypatch):
    from src.services import media_jobs

    class BusinessAbort(BaseException):
        pass

    original = BusinessAbort()
    store = _Store(heartbeat_error=OSError("synthetic heartbeat"), retire_error=OSError("synthetic retire"))

    async def aborted(*_args, **_kwargs):
        await asyncio.sleep(0)
        raise original

    monkeypatch.setattr(media_jobs, "execute_media_job", aborted)
    with pytest.raises(BusinessAbort) as caught:
        asyncio.run(_run(store))
    assert caught.value is original and store.retire_calls == [("synthetic", "worker")]
    drain, reset = environment
    drain.assert_not_awaited()
    reset.assert_awaited_once()


def test_cancellation_during_client_reset_remains_visible_after_token_is_retired(environment):
    drain, reset = environment
    store = _Store()

    async def exercise():
        started = asyncio.Event()

        async def reset_client():
            started.set()
            await asyncio.Future()

        reset.side_effect = reset_client
        task = asyncio.create_task(_run(store))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert store.retire_calls == [("synthetic", "worker")] and store.token is None
    reset.assert_awaited_once()
