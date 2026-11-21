"""Detached research observation saves once without replaying a query or plan."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import client_wrapper, operation_worker
from src.domain import CleanupObservation, CleanupState
from src.infrastructure.operation_store import OperationStore
from src.services import background
from src.services.research_result_storage import save_research_result


@pytest.fixture
def worker_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-research-worker-owner")
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", lambda: None)
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", AsyncMock())
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(reset_async=AsyncMock()))
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    return OperationStore()


@pytest.mark.parametrize("kind", ["research", "research_observe"])
def test_worker_saves_full_report_and_cleans_without_a_result_caller(worker_environment, monkeypatch, kind):
    store = worker_environment
    store.create("op_worker", "gemini_research", requested_model="flash", thinking_level="high")
    if kind == "research_observe":
        store.update("op_worker", state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_original")
    assert store.claim_worker("op_worker", "owned-worker")
    doc = SimpleNamespace(ready=True, content="# Full report\nIndependent saved evidence", sources=[])
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=SimpleNamespace(deep_research_document=doc)))
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    calls = []
    async def start(request, operation_id, token, *, resume):
        assert kind == "research" and resume is False
        calls.append(dict(request))
        store.update(operation_id, state="running", phase="start_returned", upstream_chat_id="c_original")
    monkeypatch.setattr(operation_worker, "_research", start)
    async def cleanup(cid, **kwargs):
        rows = store.get_media_artifacts("op_worker")
        assert len(rows) == 1 and Path(rows[0]["local_path"]).read_text() == doc.content
        assert not store.get("op_worker").cleanup_protected
        return CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=cid)
    finalize = AsyncMock(side_effect=cleanup)
    monkeypatch.setattr(client_wrapper, "finalize_remote_chat_cleanup", finalize)
    asyncio.run(operation_worker.run_worker({"kind": kind, "operation_id": "op_worker",
        "worker_token": "owned-worker", "request": {"query": "Original ephemeral request"}},
        store=store, observation_seconds=1, poll_seconds=0.001))
    record = store.get("op_worker")
    assert record.state == "completed" and record.phase == "research_report_saved"
    assert record.cleanup_state == "completed" and record.worker_token is None
    assert calls == ([{"query": "Original ephemeral request"}] if kind == "research" else [])
    client.fetch_latest_chat_response.assert_awaited_once()
    assert client.fetch_latest_chat_response.await_args.args == ("c_original",)
    finalize.assert_awaited_once()


def test_hard_crash_lease_recovery_dispatches_only_an_observer_for_the_same_handle(worker_environment, monkeypatch):
    store = worker_environment
    store.create("op_crashed", "gemini_research")
    store.update("op_crashed", state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_original")
    assert store.claim_worker("op_crashed", "dead-worker")
    with store._connect() as db:
        db.execute("UPDATE operations SET heartbeat_at=0,updated_at=0 WHERE operation_id='op_crashed'")
    assert [r.operation_id for r in store.recoverable_research()] == ["op_crashed"]
    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)
    asyncio.run(background.maintain_background_operations())
    dispatch.assert_awaited_once()
    args = dispatch.await_args.args
    assert args[1].operation_id == "op_crashed" and args[1].upstream_chat_id == "c_original"
    assert args[2] == "research_observe"
    assert store.claim_worker("op_crashed", "replacement-worker")
    assert not store.is_worker_owner("op_crashed", "dead-worker")


def test_saved_report_crash_before_cleanup_remains_recoverable(worker_environment):
    store = worker_environment
    store.create("op_saved", "gemini_research")
    store.update("op_saved", state="running", phase="start_returned", upstream_chat_id="c_owned")
    save_research_result(store, store.get("op_saved"), SimpleNamespace(ready=True, content="Saved report"))
    with store._connect() as db:
        db.execute("UPDATE operations SET updated_at=0 WHERE operation_id='op_saved'")
    assert [r.operation_id for r in store.recoverable_research()] == ["op_saved"]
    assert store.claim_worker("op_saved", "cleanup-observer")


@pytest.mark.parametrize("case", ["no_account", "different_account", "empty"])
def test_restart_maintenance_does_not_adopt_unknown_or_other_accounts(worker_environment, monkeypatch, case):
    store = worker_environment
    if case != "empty":
        store.create("op_bound", "gemini_research")
        store.update("op_bound", state="partial", phase="start_returned", upstream_chat_id="c_old")
        with store._connect() as db:
            db.execute("UPDATE operations SET updated_at=0 WHERE operation_id='op_bound'")
    if case == "no_account":
        monkeypatch.delenv("GEMINI_PSID")
    elif case == "different_account":
        monkeypatch.setenv("GEMINI_PSID", "another-owner")
    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)
    asyncio.run(background.maintain_background_operations())
    assert store.recoverable_research() == ()
    dispatch.assert_not_awaited()


def test_observer_deadline_preserves_unknown_chat_without_generation(worker_environment, monkeypatch):
    store = worker_environment
    store.create("op_unknown", "gemini_research")
    store.update("op_unknown", state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_original")
    assert store.claim_worker("op_unknown", "observer")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=None))
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    start = AsyncMock(side_effect=AssertionError("Read-only observer must not confirm or replay"))
    finalize = AsyncMock()
    monkeypatch.setattr(operation_worker, "_research", start)
    monkeypatch.setattr(client_wrapper, "finalize_remote_chat_cleanup", finalize)
    asyncio.run(operation_worker.run_worker({"kind": "research_observe", "operation_id": "op_unknown",
        "worker_token": "observer"}, store=store, observation_seconds=0.02, poll_seconds=0.001))
    record = store.get("op_unknown")
    assert record.cleanup_protected and record.state == "partial" and record.worker_token is None
    assert record.upstream_chat_id == "c_original" and not store.get_media_artifacts("op_unknown")
    start.assert_not_awaited()
    finalize.assert_not_awaited()
