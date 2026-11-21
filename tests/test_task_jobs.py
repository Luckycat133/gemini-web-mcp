"""Offline contracts for Gemini Task background execution and read-only recovery."""

import asyncio
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import CleanupObservation, CleanupState, DomainErrorCode, OperationState
from src.infrastructure.operation_store import OperationStore
from src.remote_chat_cleanup_manager import extract_remote_chat_id
import src.client_wrapper as wrapper
import src.services.task_jobs as task_jobs
from src.services.cleanup_scope import observe_upstream_identity


@pytest.fixture(autouse=True)
def _synthetic_account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-test-account")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "false")
    monkeypatch.setenv("GEMINI_AUTO_REFRESH", "false")
    monkeypatch.setattr(task_jobs, "refresh_task_schedule_guard", AsyncMock(return_value=[]))
    monkeypatch.setattr(task_jobs, "fetch_task_file_cards", AsyncMock(return_value=()))


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "state" / "operations.sqlite3"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(tmp_path / "cleanup.sqlite3"))
    return OperationStore(path)


def _candidate(rcid="rc_one", text="task answer text"):
    return SimpleNamespace(rcid=rcid, text=text, completion_state=2)


def _response(cid="c_one", rid="r_one", rcid="rc_one", text="task answer text"):
    return SimpleNamespace(
        metadata=[cid, rid], rcid=rcid, candidates=[_candidate(rcid, text)], text=text,
    )


def _history(*turns):
    return SimpleNamespace(turns=list(turns))


def _model_turn(cid, rid, rcid, text):
    return SimpleNamespace(role="model", model_output=_response(cid, rid, rcid, text))


def _metadata(status=3, chat_id="c_one"):
    return {
        "chat_id": chat_id, "upstream_status": status, "description": "a task description",
        "is_pinned": False, "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-02T00:00:00",
    }


def _make_record(store, handle="op_" + "t" * 32, retain_chat=False, delete_after_seconds=None):
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard",
                 retain_chat=retain_chat, delete_after_seconds=delete_after_seconds)
    assert store.claim_worker(handle, "worker")
    store.checkpoint_media(handle, worker_token="worker", chat_id="c_one", response_id="r_one", candidate_id="rc_id")
    return store.get(handle)


class TaskClient:
    def __init__(self, response=None, history=None, raise_on_generate=False):
        self.response = response
        self.history = history
        self.calls = []
        self.read_calls = []
        self.timeout = 30.0
        self.watchdog_timeout = 30.0
        self.raise_on_generate = raise_on_generate

    def task_generation_scope(self, conversation_id=None):
        return nullcontext()

    def notebook_scope(self, references):
        return nullcontext()

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_on_generate:
            raise AssertionError("generate_content must not be called during recovery")
        # Simulate the SDK observing the response frame as it streams in.
        if self.response is not None:
            observe_upstream_identity(self.response)
        return self.response

    async def read_chat(self, cid, limit=10):
        self.read_calls.append((cid, limit))
        return self.history

    async def _batch_execute(self, calls, **kwargs):
        from src.infrastructure.rpc_contracts import get_contract
        payload = json.loads(calls[0].payload)
        cid, limit = payload[:2]
        history = await self.read_chat(cid, limit=limit)
        turns = []
        for turn in getattr(history, "turns", ()):
            response = getattr(turn, "model_output", None)
            if response is None:
                continue
            candidates = [[c.rcid, [c.text], None, None, None, None, None, None,
                           [c.completion_state]] for c in response.candidates]
            turns.append([[response.metadata[0], response.metadata[1]], None, None, [candidates]])
        body = [turns]
        return SimpleNamespace(status_code=200, text=json.dumps([
            ["wrb.fr", get_contract("web_workflow.turn").rpc_id, json.dumps(body)]]))


def _install_execute_doubles(monkeypatch, client, metadata=_metadata()):
    schedule_calls = []

    def fake_schedule(value, *, retain_chat=False, delete_after_seconds=None, source=""):
        schedule_calls.append(
            (extract_remote_chat_id(value), retain_chat, delete_after_seconds, source),
        )
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_one")

    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", fake_schedule)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=metadata))
    return schedule_calls


def test_execute_one_dispatch_checkpoints_then_recovers_completed(store, monkeypatch):
    handle = "op_" + "t" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")
    client = TaskClient(_response())
    schedule_calls = _install_execute_doubles(monkeypatch, client)

    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "private prompt", "timeout_seconds": 30},
        operation_id=handle, store=store, worker_token="worker",
    ))

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["prompt"] == "private prompt"
    assert call["model"] == "flash"
    assert call["thinking_level"] == "standard"
    assert call["timeout"] == 30
    assert "deep_research" not in call
    assert client.timeout == 30
    record = store.get(handle)
    assert record.state == "completed"
    assert record.phase == "task_completed"
    assert record.verification_status == "task_completed_verified"
    assert record.upstream_chat_id == "c_one"
    assert record.upstream_response_id == "r_one"
    assert record.upstream_candidate_id == "rc_one"
    assert ("c_one", False, 1800, "task_job:in_progress") in schedule_calls
    assert b"private prompt" not in store.path.read_bytes()


def test_execute_early_cid_checkpointed_through_cleanup_frames(store, monkeypatch):
    handle = "op_" + "e" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")

    async def generate_content(**kwargs):
        observe_upstream_identity(SimpleNamespace(cid="c_one"))
        return _response()

    client = TaskClient()
    client.generate_content = generate_content
    schedule_calls = _install_execute_doubles(monkeypatch, client)

    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "private prompt"}, operation_id=handle, store=store, worker_token="worker",
    ))

    record = store.get(handle)
    assert record.state == "completed"
    assert record.upstream_chat_id == "c_one"
    assert record.upstream_response_id == "r_one"
    assert record.upstream_candidate_id == "rc_one"
    # The early CID registers the cleanup ledger exactly once (deduplicated).
    assert schedule_calls.count(("c_one", False, 1800, "task_job:in_progress")) == 1


def test_execute_rejects_blank_prompt_before_initializing(store, monkeypatch):
    handle = "op_" + "b" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")
    initialized = []
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: TaskClient())
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock(side_effect=lambda: initialized.append(True) or None))
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k: None)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata()))

    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "   "}, operation_id=handle, store=store, worker_token="worker",
    ))

    assert initialized == []
    record = store.get(handle)
    assert record.state == "failed"
    assert record.phase == "input_rejected"
    assert record.error_code == "INVALID_ARGUMENT"
    assert record.upstream_chat_id is None


def test_execute_timeout_after_checkpoint_is_partial_and_not_replayed(store, monkeypatch):
    handle = "op_" + "o" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")

    async def generate_content(**kwargs):
        client.calls.append(kwargs)
        observe_upstream_identity(SimpleNamespace(cid="c_one"))
        raise asyncio.TimeoutError()

    client = TaskClient()
    client.generate_content = generate_content
    schedule_calls = _install_execute_doubles(monkeypatch, client)

    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "private prompt", "timeout_seconds": 5},
        operation_id=handle, store=store, worker_token="worker",
    ))

    assert len(client.calls) == 1
    record = store.get(handle)
    assert record.state == "partial"
    assert record.phase == "task_outcome_unverified"
    assert record.error_code == "TIMED_OUT"
    assert record.upstream_chat_id == "c_one"  # early identity preserved under protection
    assert ("c_one", False, 1800, "task_job:in_progress") in schedule_calls


def test_execute_cancellation_propagates_and_keeps_lease(store, monkeypatch):
    handle = "op_" + "c" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")

    async def generate_content(**kwargs):
        observe_upstream_identity(SimpleNamespace(cid="c_one"))
        await asyncio.Event().wait()
        return _response()

    client = TaskClient()
    client.generate_content = generate_content
    _install_execute_doubles(monkeypatch, client)

    async def run():
        job = asyncio.create_task(task_jobs.execute_task_job(
            {"prompt": "private prompt"}, operation_id=handle, store=store, worker_token="worker",
        ))
        await asyncio.sleep(0.2)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(run())
    record = store.get(handle)
    assert record.state == "running"
    assert record.worker_token == "worker"  # lease retained; the caller's loop retires it


def test_execute_account_switch_before_start_touches_nothing(store, monkeypatch):
    handle = "op_" + "s" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")
    monkeypatch.setattr("src.infrastructure.account_identity.current_account_key", lambda: "switched-account")
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: TaskClient())
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k: None)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata()))

    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "private prompt"}, operation_id=handle, store=store, worker_token="worker",
    ))

    record = store.get(handle)
    assert record.state == "accepted"
    assert record.phase == "created"
    assert record.upstream_chat_id is None
    assert record.worker_token == "worker"


def test_execute_notebooks_resolved_before_generation(store, monkeypatch):
    handle = "op_" + "n" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")
    client = TaskClient(_response())
    notebook_scopes = []

    def notebook_scope(ids):
        notebook_scopes.append(tuple(ids))
        return nullcontext()

    client.notebook_scope = notebook_scope
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k: None)
    resolved = AsyncMock(return_value=(("nb_one", "Notebook One"),))
    monkeypatch.setattr(task_jobs, "resolve_notebook_context", resolved)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata()))

    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "private prompt", "notebook_ids": ["nb_one"]},
        operation_id=handle, store=store, worker_token="worker",
    ))
    resolved.assert_awaited_once_with(client, ["nb_one"])
    assert notebook_scopes == [(("nb_one", "Notebook One"),)]
    assert len(client.calls) == 1


def test_execute_unresolvable_notebook_fails_without_generation(store, monkeypatch):
    handle = "op_" + "u" * 32
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")
    client = TaskClient(raise_on_generate=True)
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k: None)
    monkeypatch.setattr(
        task_jobs, "resolve_notebook_context",
        AsyncMock(side_effect=task_jobs.ModelPolicyError("The requested notebook context could not be verified.")),
    )
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata()))

    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "private prompt", "notebook_ids": ["nb_missing"]},
        operation_id=handle, store=store, worker_token="worker",
    ))

    assert client.calls == []
    record = store.get(handle)
    assert record.state == "failed"
    assert record.phase == "task_notebook_unresolved"
    assert record.error_code == "CAPABILITY_UNAVAILABLE"


def test_recover_completes_from_fresh_metadata_without_generation(store, monkeypatch):
    record = _make_record(store)
    client = TaskClient(raise_on_generate=True)
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    asyncio.run(task_jobs.recover_task_job(record, store=store, worker_token="worker"))

    assert client.calls == []
    updated = store.get(record.operation_id)
    assert updated.state == "completed"
    assert updated.phase == "task_completed"
    assert updated.verification_status == "task_completed_verified"


def test_recover_non_completed_status_stays_partial(store, monkeypatch):
    record = _make_record(store)
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: TaskClient(raise_on_generate=True))
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(2)))

    asyncio.run(task_jobs.recover_task_job(record, store=store, worker_token="worker"))

    updated = store.get(record.operation_id)
    assert updated.state == "partial"
    assert updated.phase == "task_outcome_unverified"
    assert updated.cleanup_protected


def test_recover_authoritative_absence_stays_partial(store, monkeypatch):
    record = _make_record(store)
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: TaskClient(raise_on_generate=True))
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=None))

    asyncio.run(task_jobs.recover_task_job(record, store=store, worker_token="worker"))

    updated = store.get(record.operation_id)
    assert updated.state == "partial"
    assert updated.phase == "task_outcome_unverified"
    assert updated.cleanup_protected


def test_recover_incomplete_identity_partial_without_read(store, monkeypatch):
    store.create("op_" + "i" * 32, "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker("op_" + "i" * 32, "worker")
    store.checkpoint_media("op_" + "i" * 32, worker_token="worker", chat_id="c_one")
    fetched = []
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: TaskClient(raise_on_generate=True))
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(
        task_jobs, "fetch_task_metadata", AsyncMock(side_effect=lambda *a, **k: fetched.append(True) or _metadata(3)),
    )

    asyncio.run(task_jobs.recover_task_job(store.get("op_" + "i" * 32), store=store, worker_token="worker"))

    assert fetched == []
    updated = store.get("op_" + "i" * 32)
    assert updated.state == "partial"
    assert updated.phase == "task_identity_incomplete"


def _read_record(store, handle="op_" + "r" * 32, state="completed", cleanup_state="not_scheduled",
                 retain_chat=False, delete_after_seconds=None):
    store.create(handle, "gemini_run_task", requested_model="flash", thinking_level="standard",
                 retain_chat=retain_chat, delete_after_seconds=delete_after_seconds)
    assert store.claim_worker(handle, "worker")
    store.checkpoint_media(
        handle, worker_token="worker", chat_id="c_one", response_id="r_one", candidate_id="rc_id",
    )
    store.update(handle, state=state, phase="task_completed")
    if cleanup_state != "not_scheduled":
        store.update_cleanup(handle, cleanup_state)
    return store.get(handle)


def test_read_status_fresh_metadata_no_text_and_no_cleanup(store, monkeypatch):
    record = _read_record(store)
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", "task answer text")))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=initializer,
        finalize_chat_cleanup=finalize, include_answer=False,
    ))

    assert result.ok
    data = result.data
    assert data.state == OperationState.COMPLETED
    assert data.task_response is None
    assert data.upstream_status == 3
    assert data.upstream_chat_id == "c_one"
    assert data.report_available is False
    assert data.verification_status == "task_completed_verified"
    initializer.assert_awaited_once()
    assert client.read_calls == []
    finalize.assert_not_awaited()
    assert store.get(record.operation_id).cleanup_protected


def test_read_status_unknown_status_stays_partial(store, monkeypatch):
    record = _read_record(store, state="running")
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(2)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: TaskClient(), client_initializer=initializer,
        finalize_chat_cleanup=AsyncMock(), include_answer=False,
    ))

    assert result.ok
    assert result.data.state == OperationState.PARTIAL
    assert result.data.upstream_status == 2
    assert result.data.verification_status == "task_status_not_completed"
    assert result.data.task_response is None


def test_read_status_authoritative_absence_partial(store, monkeypatch):
    record = _read_record(store, state="running")
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=None))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: TaskClient(), client_initializer=initializer,
        finalize_chat_cleanup=AsyncMock(), include_answer=False,
    ))

    assert result.ok
    assert result.data.state == OperationState.PARTIAL
    assert result.data.upstream_status is None
    assert result.data.verification_status == "task_readback_unverified"


def test_read_result_exact_saved_turn_releases_cleanup(store, monkeypatch):
    record = _read_record(store, retain_chat=False, delete_after_seconds=None)
    client = TaskClient(history=_history(
        _model_turn("c_one", "r_other", "rc_other", "unrelated"),
        _model_turn("c_one", "r_one", "rc_id", "task answer text"),
    ))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=initializer,
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert result.ok
    data = result.data
    assert data.task_response == "task answer text"
    assert data.report_available is False
    assert data.verification_status == "task_answer_observed"
    assert data.upstream_status == 3
    assert client.read_calls == [("c_one", 10)]
    assert store.get(record.operation_id).cleanup_protected is False
    updated = store.get(record.operation_id)
    assert updated.cleanup_state == "completed"
    finalize.assert_awaited_once_with(
        "c_one", retain_chat=False, delete_after_seconds=None,
        source="gemini_run_task", client=client,
    )


def test_read_result_ignores_unrelated_later_turn(store, monkeypatch):
    record = _read_record(store)
    read_state = {"n": 0}

    async def read_chat(cid, limit=10):
        read_state["n"] += 1
        client.read_calls.append((cid, limit))
        if read_state["n"] == 1:
            return None
        return _history(_model_turn("c_one", "r_later", "rc_later", "unrelated answer"))

    client = TaskClient()
    client.read_chat = read_chat
    finalize = AsyncMock()
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=initializer,
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert result.ok
    assert result.data.task_response is None
    assert result.data.verification_status == "task_answer_not_observed"
    assert result.data.report_available is False
    assert client.read_calls == [("c_one", 10), ("c_one", 100)]
    finalize.assert_not_awaited()
    assert store.get(record.operation_id).cleanup_protected


def test_read_result_truncates_long_answer_with_warning(store, monkeypatch):
    record = _read_record(store)
    long_text = "x" * 5000
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", long_text)))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=initializer,
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert result.ok
    assert len(result.data.task_response) == 4000
    assert result.data.task_response == "x" * 4000
    assert any(w.code == "task_answer_truncated" for w in result.warnings)
    artifact = result.data.artifacts[0]
    from pathlib import Path
    assert Path(artifact.local_path).read_text() == long_text
    assert artifact.size_bytes == len(long_text.encode())
    assert b"x" * 100 not in store.path.read_bytes()


def test_task_result_survives_remote_cleanup_and_a_new_reader(store, monkeypatch):
    record = _read_record(store)
    text = "complete synthetic result " * 400
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", text)))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))
    finalizer = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    first = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalizer, include_answer=True))
    assert first.ok and first.data.artifacts
    reloaded = OperationStore(store.path)
    initialize = AsyncMock(side_effect=AssertionError("cached result must not need remote auth"))
    second = asyncio.run(task_jobs.read_task_operation(
        reloaded.get(record.operation_id), store=reloaded, client_provider=lambda: None,
        client_initializer=initialize, finalize_chat_cleanup=None, include_answer=True))
    assert second.ok and second.data.task_response == text[:4000]
    assert second.data.artifacts[0].id == first.data.artifacts[0].id
    assert second.data.cleanup.state == CleanupState.COMPLETED
    initialize.assert_not_awaited()


def test_changed_task_artifact_is_not_reported_available_or_silently_replaced(store, monkeypatch):
    from pathlib import Path
    record = _read_record(store)
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", "original answer")))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))
    first = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED)),
        include_answer=True))
    path = Path(first.data.artifacts[0].local_path)
    path.write_text("changed bytes")
    result = asyncio.run(task_jobs.read_task_operation(
        store.get(record.operation_id), store=store, client_provider=lambda: client,
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert not result.ok and result.error.code == DomainErrorCode.VERIFICATION_FAILED
    assert path.read_text() == "changed bytes"


def test_wrong_task_metadata_cannot_authorize_answer_read_or_cleanup(store, monkeypatch):
    record = _read_record(store)
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", "answer")))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3, "c_other")))
    finalizer = AsyncMock()
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalizer, include_answer=True))
    assert result.data.task_response is None and client.read_calls == []
    finalizer.assert_not_awaited()
    assert store.get(record.operation_id).cleanup_protected


def test_task_answer_outside_first_window_is_read_by_exact_identity(store, monkeypatch):
    record = _read_record(store)
    client = TaskClient()
    client.read_chat = AsyncMock(side_effect=[
        _history(_model_turn("c_one", "r_new", "rc_new", "unrelated")),
        _history(_model_turn("c_one", "r_one", "rc_id", "original exact answer")),
    ])
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED)),
        include_answer=True))
    assert result.ok and result.data.task_response == "original exact answer"
    assert [call.kwargs["limit"] for call in client.read_chat.await_args_list] == [10, 100]


def test_task_transport_missing_never_sends_an_ordinary_chat(store, monkeypatch):
    record = _make_record(store)
    client = TaskClient(_response())
    client.task_generation_scope = None
    _install_execute_doubles(monkeypatch, client)
    asyncio.run(task_jobs.execute_task_job({"prompt": "synthetic"}, operation_id=record.operation_id,
                                          store=store, worker_token="worker"))
    assert client.calls == []


def test_task_execute_uses_client_initialized_during_auth(store, monkeypatch):
    handle = "op_" + "a" * 32
    store.create(handle, "gemini_run_task")
    store.claim_worker(handle, "worker")
    client = TaskClient(_response())
    state = {"client": None}
    _install_execute_doubles(monkeypatch, client)
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: state["client"])
    async def initialize():
        state["client"] = client
    monkeypatch.setattr(wrapper, "initialize_client", initialize)
    asyncio.run(task_jobs.execute_task_job({"prompt": "synthetic"}, operation_id=handle,
                                          store=store, worker_token="worker"))
    assert len(client.calls) == 1


def test_read_result_deleted_after_cleanup_reports_absence(store, monkeypatch):
    record = _read_record(store, state="completed", cleanup_state="completed")
    client = TaskClient()
    finalize = AsyncMock()
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=initializer,
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert not result.ok
    assert result.error.code == DomainErrorCode.CAPABILITY_UNAVAILABLE
    assert result.meta.verification_status == "task_removed_after_cleanup"
    finalize.assert_not_awaited()
    assert client.read_calls == []


def test_read_result_deleted_after_cleanup_without_completion_is_unavailable(store, monkeypatch):
    record = _read_record(store, state="partial", cleanup_state="completed")
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: TaskClient(), client_initializer=AsyncMock(),
        finalize_chat_cleanup=AsyncMock(), include_answer=True,
    ))

    assert not result.ok
    assert result.error.code == DomainErrorCode.CAPABILITY_UNAVAILABLE
    assert result.meta.verification_status == "task_removed_after_cleanup"
    assert result.data is None


def test_read_result_without_cleanup_callback_remains_unverified(store, monkeypatch):
    record = _read_record(store)
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", "task answer text")))
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=initializer,
        finalize_chat_cleanup=None, include_answer=True,
    ))

    assert result.ok
    assert result.data.task_response == "task answer text"
    assert any(w.code == "cleanup_unverified" for w in result.warnings)
    # update_cleanup must not be called when finalization is not verified.
    assert store.get(record.operation_id).cleanup_state == "not_scheduled"


def test_read_account_switch_during_init_refused(store, monkeypatch):
    record = _read_record(store)
    keys = ["synthetic-offline-test-account"]
    monkeypatch.setattr("src.infrastructure.account_identity.current_account_key", lambda: keys[0])

    async def switch_during_init():
        keys[0] = "switched-account"

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: TaskClient(), client_initializer=switch_during_init,
        finalize_chat_cleanup=AsyncMock(), include_answer=True,
    ))

    assert not result.ok
    assert result.error.code == DomainErrorCode.CAPABILITY_UNAVAILABLE
    assert result.meta.verification_status == "account_mismatch_or_unbound"


def test_read_metadata_timeout_keeps_partial(store, monkeypatch):
    record = _read_record(store, state="running")
    initializer = AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(side_effect=asyncio.TimeoutError()))

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: TaskClient(), client_initializer=initializer,
        finalize_chat_cleanup=AsyncMock(), include_answer=True,
    ))

    assert result.ok
    assert result.data.state == OperationState.PARTIAL
    assert result.data.upstream_status is None
    assert result.data.task_response is None
    assert result.data.verification_status == "task_readback_unverified"


def test_read_without_upstream_chat_never_initializes_client(store, monkeypatch):
    store.create("op_" + "z" * 32, "gemini_run_task", requested_model="flash", thinking_level="standard")
    store.update("op_" + "z" * 32, state="running", phase="task_generation_observed")
    initialized = []
    provider_called = []

    async def initializer():
        initialized.append(True)

    result = asyncio.run(task_jobs.read_task_operation(
        store.get("op_" + "z" * 32), store=store,
        client_provider=lambda: provider_called.append(True) or TaskClient(),
        client_initializer=initializer, finalize_chat_cleanup=AsyncMock(), include_answer=True,
    ))

    assert result.ok
    assert result.data.state == OperationState.PARTIAL
    assert result.data.verification_status == "task_identity_incomplete"
    assert result.data.task_response is None
    assert initialized == []
    assert provider_called == []


def _notebook_record(store, *, state="running"):
    handle = "op_" + "c" * 32
    store.create(handle, "gemini_notebook_chat", requested_model="flash", thinking_level="standard")
    assert store.claim_worker(handle, "worker")
    assert store.checkpoint_media(handle, worker_token="worker", chat_id="c_one", response_id="r_one", candidate_id="rc_one")
    store.update(handle, state=state, phase="notebook_reply_completed" if state == "completed" else "running")
    return store.get(handle)


def test_notebook_chat_dispatch_uses_chat_context_and_long_budget(store, monkeypatch):
    record = _notebook_record(store)
    client = TaskClient(_response())
    client.task_generation_scope = lambda: (_ for _ in ()).throw(AssertionError("must stay in chat mode"))
    async def generation(**kwargs):
        assert client.timeout == 600 and client.watchdog_timeout == 600
        client.calls.append(kwargs)
        observe_upstream_identity(client.response)
        return client.response
    client.generate_content = generation
    _install_execute_doubles(monkeypatch, client)
    metadata = task_jobs.fetch_task_metadata
    monkeypatch.setattr(task_jobs, "resolve_notebook_context", AsyncMock(return_value=(("notebooks/id", "N"),)))
    turn = AsyncMock(return_value={"completed": True, "text": "source answer"})
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    asyncio.run(task_jobs.execute_task_job(
        {"prompt": "read sources", "notebook_ids": ["notebooks/id"], "timeout_seconds": 600},
        operation_id=record.operation_id, store=store, worker_token="worker"))
    assert len(client.calls) == 1
    assert store.get(record.operation_id).state == "completed"
    assert store.get(record.operation_id).phase == "notebook_reply_completed"
    metadata.assert_not_awaited()
    turn.assert_awaited_once_with(client, "c_one", "r_one", "rc_one")


def test_notebook_chat_status_never_reads_reply_or_task_metadata(store, monkeypatch):
    record = _notebook_record(store, state="completed")
    turn, metadata = AsyncMock(), AsyncMock()
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", metadata)
    client = TaskClient()
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=AsyncMock(), include_answer=False))
    assert result.ok and result.data.execution_mode == "chat"
    assert result.data.state == OperationState.COMPLETED
    assert result.data.answer is None and result.data.task_response is None and result.data.upstream_status is None
    turn.assert_not_awaited()
    metadata.assert_not_awaited()
    assert not client.read_calls


@pytest.mark.parametrize("completed", [False, True])
def test_notebook_chat_only_saves_completed_exact_reply_and_restarts_offline(store, monkeypatch, completed):
    record = _notebook_record(store)
    answer = "source answer " * 800
    turn = AsyncMock(return_value={"completed": completed, "text": answer if completed else None})
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    metadata = AsyncMock(side_effect=AssertionError("not a Task"))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", metadata)
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: TaskClient(), client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True))
    assert result.ok and result.data.execution_mode == "chat" and result.data.task_response is None
    if not completed:
        from src.tools.operations import _present

        assert result.data.answer is None and not result.data.artifacts
        assert "Notebook Chat progress is unverified." in _present(result)[0].text
        assert store.get(record.operation_id).cleanup_protected
        finalize.assert_not_awaited()
        return
    assert len(result.data.answer) == task_jobs.TASK_ANSWER_MAX_CHARS
    assert Path(result.data.artifacts[0].local_path).read_text() == answer
    assert not store.get(record.operation_id).cleanup_protected
    finalize.assert_awaited_once()
    async def must_not_init():
        raise AssertionError("saved result must survive without upstream access")
    restored = OperationStore(store.path)
    cached = asyncio.run(task_jobs.read_task_operation(
        restored.get(record.operation_id), store=restored, client_provider=lambda: None,
        client_initializer=must_not_init, finalize_chat_cleanup=None, include_answer=True))
    assert cached.ok and cached.data.answer == result.data.answer
    assert cached.data.upstream_status is None and cached.data.execution_mode == "chat"


@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize("refusal", [
    "I'm having a hard time fulfilling your request. Can I help you with something else instead?",
    "I encountered an error doing what you asked. Could you try again?",
])
def test_notebook_chat_classifies_only_exact_completed_refusal(store, monkeypatch, quoted, refusal):
    record = _notebook_record(store)
    answer = f'The sample support text is "{refusal}"' if quoted else refusal
    client = TaskClient(raise_on_generate=True)
    _install_execute_doubles(monkeypatch, client)
    turn = AsyncMock(return_value={"completed": True, "text": answer})
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    expected_state = OperationState.COMPLETED if quoted else OperationState.FAILED

    asyncio.run(task_jobs.recover_task_job(record, store=store, worker_token="worker"))
    recovered = store.get(record.operation_id)
    assert recovered.state == expected_state.value and recovered.cleanup_protected
    if not quoted:
        assert recovered.verification_status == "notebook_reply_rejected"
        assert recovered.error_code == "UPSTREAM_REJECTED"

    result = asyncio.run(task_jobs.read_task_operation(
        recovered, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True,
    ))
    assert result.ok is quoted and result.data.state is expected_state
    assert result.data.answer == answer and result.data.execution_mode == "chat"
    assert Path(result.data.artifacts[0].local_path).read_text() == answer
    finalize.assert_awaited_once()
    assert [call.args for call in turn.await_args_list] == [(client, "c_one", "r_one", "rc_one")] * 2
    assert not client.calls
    saved = store.get(record.operation_id)
    assert not saved.cleanup_protected and saved.cleanup_state == "completed"
    restarted = OperationStore(store.path)
    cached = asyncio.run(task_jobs.read_task_operation(
        restarted.get(record.operation_id), store=restarted, client_provider=lambda: None,
        client_initializer=AsyncMock(side_effect=AssertionError("cached refusal must stay local")),
        finalize_chat_cleanup=None, include_answer=True,
    ))
    assert cached.ok is quoted and cached.data.state is expected_state
    assert cached.error_code == (None if quoted else "UPSTREAM_REJECTED")
    if not quoted:
        assert cached.error.message == "The Notebook Chat operation did not complete."


def test_notebook_chat_answer_changed_during_cleanup_stays_chat_and_protected(store):
    record = _notebook_record(store, state="completed")
    artifact = task_jobs.save_task_result(store, record, "Saved Notebook answer.")

    async def initialize():
        Path(artifact.local_path).write_text("Modified Notebook answer.")

    finalize = AsyncMock()
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: TaskClient(), client_initializer=initialize,
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert result.ok and result.data.state == OperationState.PARTIAL
    assert result.data.execution_mode == "chat"
    assert result.data.answer is None and result.data.task_response is None and not result.data.artifacts
    assert result.warnings[0].message == "Notebook Chat answer artifact could not be reverified."
    assert store.get(record.operation_id).cleanup_protected
    finalize.assert_not_awaited()


def test_completed_task_goal_does_not_authorize_partial_candidate_cleanup(store, monkeypatch):
    record = _read_record(store)
    partial = _model_turn("c_one", "r_one", "rc_id", "Working on it")
    partial.model_output.candidates[0].completion_state = 1
    client = TaskClient(history=_history(partial, _model_turn("c_one", "r_later", "rc_later", "other report")))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))
    finalize = AsyncMock()
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True))
    assert result.ok and result.data.task_response is None and not result.data.artifacts
    assert store.get(record.operation_id).cleanup_protected
    finalize.assert_not_awaited()


@pytest.mark.parametrize("candidate", [None, "rc_unique"])
def test_original_turn_candidate_recovery_requires_unique_observed_identity(store, monkeypatch, candidate):
    handle = "op_" + "d" * 32
    store.create(handle, "gemini_notebook_chat")
    assert store.claim_worker(handle, "worker")
    store.checkpoint_media(handle, worker_token="worker", chat_id="c_one", response_id="r_one")
    client = TaskClient(raise_on_generate=True)
    _install_execute_doubles(monkeypatch, client)
    fetch = AsyncMock(return_value={"rcid": candidate, "completed": True, "text": "answer"} if candidate else None)
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", fetch)
    asyncio.run(task_jobs.recover_task_job(store.get(handle), store=store, worker_token="worker"))
    saved = store.get(handle)
    assert saved.upstream_candidate_id == candidate
    assert saved.state == ("completed" if candidate else "partial")
    assert saved.cleanup_protected and not client.calls
    assert fetch.await_args_list[0].args == (client, "c_one", "r_one", None)


@pytest.mark.parametrize("goal_status", [3, 5])
def test_confirmation_card_cannot_be_saved_as_final_task_answer_or_cleaned(store, monkeypatch, goal_status):
    record = _read_record(store, state="partial")
    card = "Confirm the synthetic test action?\nhttp://googleusercontent.com/action_card_content/synthetic"
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", card)))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(goal_status)))
    finalize = AsyncMock()
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True))
    assert result.ok and result.data.task_response is None and not result.data.artifacts
    assert store.get(record.operation_id).cleanup_protected
    finalize.assert_not_awaited()
    if goal_status == 5:
        assert result.data.input_required and result.data.input_prompt == card
        assert result.data.input_url == "https://gemini.google.com/app/one"


def test_task_input_status_is_metadata_only_and_worker_does_not_approve(store, monkeypatch):
    record = _read_record(store, state="partial")
    store.update(record.operation_id, state="partial", phase="task_input_required")
    record = store.get(record.operation_id)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(5)))
    client = TaskClient(raise_on_generate=True)
    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=AsyncMock(), include_answer=False))
    assert result.ok and result.data.input_required
    assert result.data.input_prompt is None and not client.read_calls and not client.calls
    assert store.get(record.operation_id).cleanup_protected


@pytest.mark.parametrize('choice_id', [None, 'vm'])
def test_explicit_task_reply_uses_existing_chat_and_binds_new_turn(store, monkeypatch, choice_id):
    record = _make_record(store)
    assert store.begin_task_input(record.operation_id, "input_token", response_id="r_one", candidate_id="rc_id", worker_token="next")
    client = TaskClient(_response(rid="r_next", rcid="rc_next", text="final result"))
    original_generate = client.generate_content
    async def generate_with_retained_previous_candidate(**kwargs):
        observe_upstream_identity(SimpleNamespace(metadata=["c_one", "r_next", "rc_id"], rcid="rc_id"))
        return await original_generate(**kwargs)
    client.generate_content = generate_with_retained_previous_candidate
    contexts = []
    choice_scopes = []
    def scope(cid=None, *, action_choice_id=None):
        choice_scopes.append((cid, action_choice_id))
        return nullcontext()
    client.task_generation_scope = scope
    client.start_chat = lambda **kw: contexts.append(kw["metadata"]) or SimpleNamespace(cid="c_one")
    _install_execute_doubles(monkeypatch, client)
    request = {"prompt": "explicit new user reply", "chat_context": ["c_one", "r_one", "rc_id"]}
    if choice_id is not None:
        request['task_action_choice_id'] = choice_id
    asyncio.run(task_jobs.execute_task_job(request,
        operation_id=record.operation_id, store=store, worker_token="next"))
    assert contexts == [["c_one", "r_one", "rc_id"]] and len(client.calls) == 1
    assert choice_scopes == [('c_one', choice_id)]
    saved = store.get(record.operation_id)
    assert saved.upstream_chat_id == "c_one" and saved.upstream_response_id == "r_next" and saved.upstream_candidate_id == "rc_next"


def test_task_result_exposes_public_option_ids_without_native_reply_context(store, monkeypatch):
    record = _read_record(store, state='partial')
    option_id = 'option_' + 'a' * 32
    monkeypatch.setattr(task_jobs, 'fetch_task_metadata', AsyncMock(return_value=_metadata(5)))
    monkeypatch.setattr(task_jobs, 'fetch_workflow_turn', AsyncMock(return_value={
        'input_required': True, 'input_prompt': 'Synthetic card', 'input_options': [
            {'label': 'Allow', 'prompt': 'synthetic-allow', 'input_option_id': option_id,
             'choice_label': 'Remote browser', 'action_choice_id': 'PRIVATE_NATIVE_CHOICE'},
        ], 'input_card': {'replies': [{'action_choice_id': 'PRIVATE_NATIVE_CHOICE'}]},
    }))
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: TaskClient(),
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert result.ok and result.data.input_options[0]['input_option_id'] == option_id
    assert result.data.input_id and result.data.input_required
    assert 'PRIVATE_NATIVE_CHOICE' not in json.dumps(result.to_dict(), default=str)


@pytest.mark.parametrize('snapshot', ['old_turn', 'cleared_turn', 'cid_only_worker_frame'])
@pytest.mark.parametrize('include_answer', [False, True])
def test_claimed_card_is_awaiting_continuation_instead_of_a_second_unsupported_input(store, monkeypatch, snapshot, include_answer):
    from src.services.task_inputs import input_handle
    original = _read_record(store, state='partial')
    assert store.begin_task_input(original.operation_id, input_handle(original), response_id='r_one',
                                  candidate_id='rc_id', worker_token='next')
    if snapshot == 'cid_only_worker_frame':
        assert store.checkpoint_media(original.operation_id, worker_token='next', chat_id='c_one')
    record = original if snapshot == 'old_turn' else store.get(original.operation_id)
    monkeypatch.setattr(task_jobs, 'fetch_task_metadata', AsyncMock(return_value=_metadata(5)))
    turn = AsyncMock(return_value={'input_required': True, 'input_options': [], 'input_prompt': 'Selected old card'})
    monkeypatch.setattr(task_jobs, 'fetch_workflow_turn', turn)
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: TaskClient(),
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=include_answer))
    assert result.ok and not result.data.input_required and not result.data.input_options
    assert result.data.input_id is None and result.data.input_prompt is None and result.data.input_url is None
    assert result.data.verification_status == 'task_awaiting_continuation'
    assert result.data.upstream_status == 5
    turn.assert_not_awaited()


def test_a_new_exact_card_after_claimed_reply_is_never_hidden(store, monkeypatch):
    from src.services.task_inputs import input_handle
    original = _read_record(store, state='partial')
    old_token = input_handle(original)
    assert store.begin_task_input(original.operation_id, old_token, response_id='r_one', candidate_id='rc_id', worker_token='next')
    assert store.checkpoint_media(original.operation_id, worker_token='next', chat_id='c_one', response_id='r_new', candidate_id='rc_new')
    record = store.get(original.operation_id)
    monkeypatch.setattr(task_jobs, 'fetch_task_metadata', AsyncMock(return_value=_metadata(5)))
    turn = AsyncMock(return_value={'input_required': True, 'input_options': [
        {'label': 'Allow', 'prompt': 'synthetic-allow', 'input_option_id': 'option_' + 'a' * 32},
    ], 'input_prompt': 'Fresh different card'})
    monkeypatch.setattr(task_jobs, 'fetch_workflow_turn', turn)
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: TaskClient(),
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert result.ok and result.data.input_required and result.data.input_options
    assert result.data.input_id == input_handle(record) and result.data.input_id != old_token
    assert result.data.input_prompt == 'Fresh different card'
    assert turn.await_args.args[1:] == ('c_one', 'r_new', 'rc_new')


def _setup_turn(**changes):
    return {"chat_id": "c_one", "rid": "r_one", "rcid": "rc_id", "completed": False,
            "input_required": False, "input_options": [], "setup_required": True,
            "setup_kind": "app_connection", "setup_app_name": "Synthetic App", **changes}


def test_exact_native_setup_is_attention_not_running_or_a_text_confirmation(store, monkeypatch):
    record = _read_record(store, state="partial")
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(8)))
    turn = AsyncMock(return_value=_setup_turn())
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    finalize = AsyncMock()
    client = TaskClient(raise_on_generate=True)
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: client,
        client_initializer=AsyncMock(), finalize_chat_cleanup=finalize, include_answer=True))
    assert result.ok and result.data.state == OperationState.PARTIAL and result.data.upstream_status == 8
    assert result.data.needs_attention and result.data.setup_required
    assert result.data.setup_kind == "app_connection" and result.data.setup_app_name == "Synthetic App"
    assert result.data.setup_url == "https://gemini.google.com/app/one"
    assert result.data.verification_status == "task_setup_required" and not result.data.continuation_possible
    assert not result.data.input_required and not result.data.input_options and result.data.input_id is None
    assert result.data.input_prompt is None and result.data.input_url is None
    assert result.data.task_response is None and not result.data.artifacts
    assert store.get(record.operation_id).cleanup_protected and not client.calls and not client.read_calls
    finalize.assert_not_awaited()
    turn.assert_awaited_once_with(client, "c_one", "r_one", "rc_id")
    assert any(warning.code == "task_setup_requires_native_ui" for warning in result.warnings)
    assert b"Synthetic App" not in store.path.read_bytes()


@pytest.mark.parametrize("changes", [{"chat_id": "c_other"}, {"rid": "r_other"}, {"rcid": "rc_other"},
                                    {"setup_required": False}, {"setup_kind": "unknown"},
                                    {"setup_app_name": ""}, {"setup_app_name": "x" * 161}])
def test_status8_alone_or_changed_exact_setup_identity_stays_unknown(store, monkeypatch, changes):
    record = _read_record(store, state="partial")
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(8)))
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", AsyncMock(return_value=_setup_turn(**changes)))
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: TaskClient(),
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert result.ok and result.data.upstream_status == 8 and not result.data.setup_required
    assert not result.data.needs_attention and result.data.setup_url is None
    assert not result.data.input_required and result.data.input_id is None
    assert store.get(record.operation_id).cleanup_protected


@pytest.mark.parametrize("status", [5, 8, 99])
def test_unknown_goal_does_not_classify_setup_from_old_phase_or_message_words(store, monkeypatch, status):
    record = _read_record(store, state="partial")
    if status != 8:
        store.update(record.operation_id, state="partial", phase="task_setup_required")
        record = store.get(record.operation_id)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(status)))
    turn = AsyncMock(return_value={"completed": True, "text": "Please connect Synthetic App first."})
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: TaskClient(),
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=False))
    assert result.ok and not result.data.setup_required and not result.data.needs_attention
    assert result.data.upstream_status == status and not result.data.artifacts
    turn.assert_not_awaited()


def test_worker_records_only_verified_setup_phase_for_metadata_only_status(store, monkeypatch):
    record = _make_record(store)
    client = TaskClient(raise_on_generate=True)
    _install_execute_doubles(monkeypatch, client, metadata=_metadata(8))
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", AsyncMock(return_value=_setup_turn()))
    asyncio.run(task_jobs.recover_task_job(record, store=store, worker_token="worker"))
    saved = store.get(record.operation_id)
    assert saved.state == "partial" and saved.phase == "task_setup_required" and saved.cleanup_protected
    assert b"Synthetic App" not in store.path.read_bytes()
    turn = AsyncMock(side_effect=AssertionError("Status must not read conversation content"))
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    result = asyncio.run(task_jobs.read_task_operation(saved, store=store, client_provider=lambda: client,
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=False))
    assert result.ok and not result.data.setup_required and result.data.needs_attention
    assert result.data.setup_app_name is None and result.data.setup_url == "https://gemini.google.com/app/one"
    assert result.data.verification_status == "task_setup_previously_observed"
    assert not result.data.continuation_possible and not client.calls
    turn.assert_not_awaited()


@pytest.mark.parametrize("turn", [None, _setup_turn(setup_required=False), _setup_turn(rcid="rc_other")])
def test_fresh_result_does_not_reuse_previously_observed_setup_after_card_changes(store, monkeypatch, turn):
    record = _read_record(store, state="partial")
    store.update(record.operation_id, state="partial", phase="task_setup_required")
    record = store.get(record.operation_id)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(8)))
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", AsyncMock(return_value=turn))
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: TaskClient(),
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert result.ok and not result.data.setup_required and not result.data.needs_attention
    assert result.data.setup_app_name is None and result.data.setup_url is None


@pytest.mark.parametrize("phase", ["initialize", "goal", "card"])
def test_setup_reads_scrub_results_after_account_switch_at_every_await(store, monkeypatch, phase):
    record = _read_record(store, state="partial")
    async def initialize():
        if phase == "initialize":
            monkeypatch.setenv("GEMINI_PSID", "other-synthetic-account")
    async def goal(*args):
        if phase == "goal":
            monkeypatch.setenv("GEMINI_PSID", "other-synthetic-account")
        return _metadata(8)
    async def card(*args):
        monkeypatch.setenv("GEMINI_PSID", "other-synthetic-account")
        return _setup_turn()
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", goal)
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", card)
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: TaskClient(),
        client_initializer=initialize, finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert not result.ok and result.data is None
    assert "Synthetic App" not in json.dumps(result.to_dict(), default=str)


@pytest.mark.parametrize("phase", ["initialize", "goal", "card"])
def test_setup_worker_never_checkpoints_card_state_after_account_switch(store, monkeypatch, phase):
    record = _make_record(store)
    client = TaskClient(raise_on_generate=True)
    _install_execute_doubles(monkeypatch, client, metadata=_metadata(8))
    async def initialize():
        if phase == "initialize":
            monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
    async def goal(*args):
        if phase == "goal":
            monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
        return _metadata(8)
    async def card(*args):
        monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
        return _setup_turn()
    monkeypatch.setattr(wrapper, "initialize_client", initialize)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", goal)
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", card)
    asyncio.run(task_jobs.recover_task_job(record, store=store, worker_token="worker"))
    saved = store.get(record.operation_id)
    assert saved.phase != "task_setup_required" and saved.cleanup_protected
    assert b"Synthetic App" not in store.path.read_bytes() and not client.calls


@pytest.mark.parametrize("card", [None, _setup_turn(setup_required=False), _setup_turn(chat_id="c_other")])
def test_worker_clears_pending_setup_phase_when_exact_card_is_no_longer_verified(store, monkeypatch, card):
    record = _make_record(store)
    client = TaskClient(raise_on_generate=True)
    _install_execute_doubles(monkeypatch, client, metadata=_metadata(8))
    turn = AsyncMock(side_effect=[_setup_turn(), card])
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    asyncio.run(task_jobs.recover_task_job(record, store=store, worker_token="worker"))
    assert store.get(record.operation_id).phase == "task_setup_required"
    asyncio.run(task_jobs.recover_task_job(store.get(record.operation_id), store=store, worker_token="worker"))
    saved = store.get(record.operation_id)
    assert saved.phase == "task_outcome_unverified" and saved.cleanup_protected
    assert not client.calls


def test_failed_task_saves_verified_answer_before_cleanup_and_cached_result_stays_failed(store, monkeypatch):
    record = _read_record(store, state="partial")
    answer = "Synthetic failure explanation.\n" + "complete failure details\n" * 300
    client = TaskClient(history=_history(
        _model_turn("c_one", "r_other", "rc_other", "unrelated answer"),
        _model_turn("c_one", "r_one", "rc_id", answer),
    ), raise_on_generate=True)
    metadata = AsyncMock(return_value=_metadata(4))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", metadata)
    cleanup_observations = []

    async def finalize(cid, **kwargs):
        checkpoint = store.get(record.operation_id)
        artifacts = store.get_media_artifacts(record.operation_id)
        assert checkpoint.state == "failed"
        assert checkpoint.error_code == "UPSTREAM_REJECTED"
        assert not checkpoint.cleanup_protected
        assert len(artifacts) == 1
        path = Path(artifacts[0]["local_path"])
        assert path.read_text() == answer
        assert artifacts[0]["size_bytes"] == len(answer.encode())
        assert artifacts[0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        cleanup_observations.append(cid)
        return CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=cid)

    first = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert not first.ok and first.error_code == DomainErrorCode.UPSTREAM_REJECTED.value
    assert first.data.state == OperationState.FAILED and first.data.upstream_status == 4
    assert first.data.task_response == answer[:task_jobs.TASK_ANSWER_MAX_CHARS]
    assert first.data.verification_status == "task_answer_observed"
    assert first.data.cleanup.state == CleanupState.COMPLETED
    assert first.data.artifacts[0].verification.status.value == "verified"
    assert cleanup_observations == ["c_one"]
    assert client.read_calls == [("c_one", 10)] and not client.calls
    assert b"complete failure details" not in store.path.read_bytes()
    saved = store.get(record.operation_id)
    assert (saved.state, saved.phase, saved.cleanup_state) == ("failed", "task_failure_saved", "completed")

    reopened = OperationStore(store.path)
    initialize = AsyncMock(side_effect=AssertionError("cached failure must not initialize a remote client"))
    finalize_again = AsyncMock(side_effect=AssertionError("cached failure must not repeat cleanup"))
    metadata.side_effect = AssertionError("cached failure must not read remote metadata")

    def remote_client():
        raise AssertionError("cached failure must not access a remote client")

    cached = asyncio.run(task_jobs.read_task_operation(
        reopened.get(record.operation_id), store=reopened, client_provider=remote_client,
        client_initializer=initialize, finalize_chat_cleanup=finalize_again, include_answer=True,
    ))
    assert not cached.ok and cached.error_code == DomainErrorCode.UPSTREAM_REJECTED.value
    assert cached.data.state == OperationState.FAILED and cached.data.upstream_status == 4
    assert cached.data.task_response == answer[:task_jobs.TASK_ANSWER_MAX_CHARS]
    assert cached.data.artifacts[0].id == first.data.artifacts[0].id
    assert cached.data.cleanup.state == CleanupState.COMPLETED
    assert not cached.data.continuation_possible
    initialize.assert_not_awaited()
    finalize_again.assert_not_awaited()
    metadata.assert_awaited_once()

    from src.tools.operations import _present

    content = _present(cached)
    assert "UPSTREAM_REJECTED" in content[0].text
    assert f"Operation {record.operation_id}: failed." in content[0].text
    assert answer[:task_jobs.TASK_ANSWER_MAX_CHARS] in content[0].text
    assert str(first.data.artifacts[0].local_path) in content[0].text
    structured = content[0].meta["domain_result"]
    assert structured["ok"] is False and structured["meta"]["operation_state"] == "failed"
    assert structured["data"]["state"] == "failed"
    assert structured["data"]["artifacts"][0]["id"] == first.data.artifacts[0].id


def test_failed_task_without_a_completed_original_candidate_keeps_cleanup_protected(store, monkeypatch):
    record = _read_record(store, state="partial")
    turn = _model_turn("c_one", "r_one", "rc_id", "unfinished failure explanation")
    turn.model_output.candidates[0].completion_state = 1
    client = TaskClient(history=_history(turn), raise_on_generate=True)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(4)))
    finalize = AsyncMock()

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert not result.ok and result.data.state == OperationState.FAILED
    assert result.data.upstream_status == 4 and result.data.task_response is None
    assert result.data.verification_status == "task_answer_not_observed"
    assert not result.data.artifacts and not store.get_media_artifacts(record.operation_id)
    assert client.read_calls == [("c_one", 10), ("c_one", 100)] and not client.calls
    assert store.get(record.operation_id).cleanup_protected
    finalize.assert_not_awaited()


@pytest.mark.parametrize("include_answer", [False, True])
def test_unknown_task_permission_exposes_handoff_without_inventing_an_input_id(store, monkeypatch, include_answer):
    record = _read_record(store, state="partial")
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(5)))
    turn = AsyncMock(return_value={
        "completed": False, "input_required": True,
        "input_prompt": "Use the browser to review this unknown permission.", "input_options": (),
    })
    monkeypatch.setattr(task_jobs, "fetch_workflow_turn", turn)
    client = TaskClient(raise_on_generate=True)
    finalize = AsyncMock()

    result = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=include_answer,
    ))

    assert result.ok and result.data.state == OperationState.PARTIAL
    assert result.data.input_required and result.data.upstream_status == 5
    assert result.data.input_url == "https://gemini.google.com/app/one"
    assert result.data.input_id is None and not result.data.input_options
    assert result.data.task_response is None and not result.data.artifacts
    assert store.get(record.operation_id).cleanup_protected
    assert not client.calls and not client.read_calls
    finalize.assert_not_awaited()
    if include_answer:
        assert result.data.input_prompt == "Use the browser to review this unknown permission."
        turn.assert_awaited_once_with(client, "c_one", "r_one", "rc_id")
    else:
        assert result.data.input_prompt is None
        turn.assert_not_awaited()


@pytest.mark.parametrize(("upstream_status", "terminal_state"), [(3, OperationState.COMPLETED), (4, OperationState.FAILED)])
def test_cached_task_result_retries_unknown_schedule_guard_before_cleanup(store, monkeypatch, upstream_status, terminal_state):
    record = _read_record(store, state="partial")
    answer = "The complete synthetic terminal answer."
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", answer)), raise_on_generate=True)
    metadata = AsyncMock(return_value=_metadata(upstream_status))
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", metadata)
    guard = AsyncMock(side_effect=[RuntimeError("schedule read unavailable"), []])
    monkeypatch.setattr(task_jobs, "refresh_task_schedule_guard", guard)
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))

    first = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert first.data.state == terminal_state
    assert first.ok is (terminal_state != OperationState.FAILED)
    assert first.data.task_response == answer and first.data.artifacts
    assert Path(first.data.artifacts[0].local_path).read_text() == answer
    assert store.get(record.operation_id).cleanup_protected
    assert store.is_cleanup_protected("c_one")
    assert any(w.code == "cleanup_unverified" for w in first.warnings)
    finalize.assert_not_awaited()

    reopened = OperationStore(store.path)
    metadata.side_effect = AssertionError("cached answer must not reread Task metadata")
    second = asyncio.run(task_jobs.read_task_operation(
        reopened.get(record.operation_id), store=reopened, client_provider=lambda: client,
        client_initializer=AsyncMock(), finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert second.data.state == terminal_state
    assert second.ok is (terminal_state != OperationState.FAILED)
    assert second.data.task_response == answer
    assert second.data.artifacts[0].id == first.data.artifacts[0].id
    assert second.data.cleanup.state == CleanupState.COMPLETED
    assert not reopened.get(record.operation_id).cleanup_protected
    assert not reopened.is_cleanup_protected("c_one")
    assert not client.calls and client.read_calls == [("c_one", 10)]
    assert guard.await_count == 2
    assert all(call.args[:2] == (client, "c_one") for call in guard.await_args_list)
    metadata.assert_awaited_once()
    finalize.assert_awaited_once_with(
        "c_one", retain_chat=False, delete_after_seconds=None, source="gemini_run_task", client=client,
    )


def test_associated_schedule_retains_owned_task_chat_with_a_persistent_account_guard(store, monkeypatch):
    from src.infrastructure.task_schedule_store import TaskScheduleGuardStore
    from src.services import web_context_transport

    record = _read_record(store, state="partial")
    client = TaskClient(history=_history(_model_turn("c_one", "r_one", "rc_id", "scheduled result")), raise_on_generate=True)
    monkeypatch.setattr(task_jobs, "fetch_task_metadata", AsyncMock(return_value=_metadata(3)))
    batch = AsyncMock(return_value={"chat_id": "c_one", "schedule_ids": ["schedule_persistent"]})
    monkeypatch.setattr(web_context_transport, "_batch", batch)
    monkeypatch.setattr(task_jobs, "refresh_task_schedule_guard", web_context_transport.refresh_task_schedule_guard)
    finalize = AsyncMock()

    first = asyncio.run(task_jobs.read_task_operation(
        record, store=store, client_provider=lambda: client, client_initializer=AsyncMock(),
        finalize_chat_cleanup=finalize, include_answer=True,
    ))

    assert first.ok and first.data.artifacts and first.data.task_response == "scheduled result"
    assert first.data.cleanup.state == CleanupState.RETAINED
    assert any(w.code == "task_schedule_retained" for w in first.warnings)
    saved = store.get(record.operation_id)
    assert not saved.cleanup_protected and saved.cleanup_state == "retained"
    assert TaskScheduleGuardStore(store.path).protects("c_one")
    assert OperationStore(store.path).is_cleanup_protected("c_one")
    batch.assert_awaited_once_with(client, "tasks.schedule_refs", chat_id="c_one")
    finalize.assert_not_awaited()

    initializer = AsyncMock(side_effect=AssertionError("retained cached result must be available offline"))
    cached = asyncio.run(task_jobs.read_task_operation(
        OperationStore(store.path).get(record.operation_id), store=OperationStore(store.path),
        client_provider=lambda: None, client_initializer=initializer,
        finalize_chat_cleanup=finalize, include_answer=True,
    ))
    assert cached.ok and cached.data.cleanup.state == CleanupState.RETAINED
    assert cached.data.artifacts[0].id == first.data.artifacts[0].id
    initializer.assert_not_awaited()
    finalize.assert_not_awaited()

    monkeypatch.setenv("GEMINI_PSID", "another-synthetic-schedule-account")
    assert not TaskScheduleGuardStore(store.path).protects("c_one")
    assert not OperationStore(store.path).is_cleanup_protected("c_one")


def test_task_generated_files_are_committed_before_cleanup_and_reused(store, monkeypatch):
    from src.domain import Artifact, ArtifactKind, ArtifactState, ArtifactVerification, ArtifactVerificationStatus
    record = _read_record(store)
    client = TaskClient(history=_history(_model_turn('c_one', 'r_one', 'rc_id', 'answer')))
    client.client = object()
    monkeypatch.setattr(task_jobs, 'fetch_task_metadata', AsyncMock(return_value=_metadata(3)))
    monkeypatch.setattr(task_jobs, 'fetch_task_file_cards', AsyncMock(return_value=(object(),)))
    async def save(_client, _card, *, output_dir, **_kwargs):
        path = output_dir / 'task-file-test.csv'
        path.write_bytes(b'header\nrow\n')
        return Artifact(id='artifact_test', kind=ArtifactKind.FILE, state=ArtifactState.LOCAL,
                        mime_type='text/csv', local_path=str(path), size_bytes=11,
                        verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED,
                            details={'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    saver = AsyncMock(side_effect=save)
    monkeypatch.setattr(task_jobs, 'save_task_file', saver)
    async def finalize(*_args, **_kwargs):
        assert store.task_files_committed(record.operation_id)
        assert len(store.get_media_artifacts(record.operation_id)) == 2
        return CleanupObservation(state=CleanupState.COMPLETED)
    first = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: client,
        client_initializer=AsyncMock(), finalize_chat_cleanup=finalize, include_answer=True))
    assert first.ok and len(first.data.artifacts) == 2
    second = asyncio.run(task_jobs.read_task_operation(store.get(record.operation_id), store=store,
        client_provider=lambda: (_ for _ in ()).throw(AssertionError('cached result must be local')),
        client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert second.ok and [x.id for x in second.data.artifacts] == [x.id for x in first.data.artifacts]
    assert saver.await_count == 1
    Path(second.data.artifacts[1].local_path).write_bytes(b'tampered')
    changed = asyncio.run(task_jobs.read_task_operation(store.get(record.operation_id), store=store,
        client_provider=lambda: client, client_initializer=AsyncMock(), finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert changed.ok and changed.data.state == OperationState.PARTIAL
    assert changed.data.task_response == 'answer' and len(changed.data.artifacts) == 1
    assert changed.meta.verification_status == 'task_artifact_verification_failed'


def test_task_missing_file_evidence_never_releases_cleanup(store, monkeypatch):
    record = _read_record(store)
    client = TaskClient(history=_history(_model_turn('c_one', 'r_one', 'rc_id', 'answer')))
    monkeypatch.setattr(task_jobs, 'fetch_task_metadata', AsyncMock(return_value=_metadata(3)))
    monkeypatch.setattr(task_jobs, 'fetch_task_file_cards', AsyncMock(side_effect=ValueError('unverified')))
    finalize = AsyncMock()
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: client,
        client_initializer=AsyncMock(), finalize_chat_cleanup=finalize, include_answer=True))
    assert result.data.state == OperationState.PARTIAL and result.data.verification_status == 'task_files_unverified'
    assert store.get(record.operation_id).cleanup_protected and not store.task_files_committed(record.operation_id)
    assert not store.release_task_result_cleanup(record.operation_id)
    finalize.assert_not_awaited()


def test_legacy_deleted_task_still_returns_saved_answer_without_claiming_files(store):
    from src.services.task_result_storage import save_task_result
    record = _read_record(store)
    saved = save_task_result(store, record, 'legacy answer')
    with store._connect() as db:
        db.execute("UPDATE operations SET cleanup_protected=0, cleanup_state='completed', phase='task_answer_saved' WHERE operation_id=?", (record.operation_id,))
    result = asyncio.run(task_jobs.read_task_operation(store.get(record.operation_id), store=store,
        client_provider=lambda: None, client_initializer=AsyncMock(side_effect=AssertionError('no remote read')),
        finalize_chat_cleanup=AsyncMock(), include_answer=True))
    assert result.ok and result.data.state == OperationState.PARTIAL
    assert result.data.task_response == 'legacy answer' and result.data.artifacts[0].id == saved.id
    assert result.data.verification_status == 'task_file_manifest_unavailable'


def test_cached_file_disappearing_during_initializer_cannot_release_cleanup(store):
    from src.domain import Artifact, ArtifactKind, ArtifactState, ArtifactVerification, ArtifactVerificationStatus
    from src.services.task_result_storage import save_task_result
    record = _read_record(store)
    report = save_task_result(store, record, 'cached answer')
    path = Path(report.local_path).parent / 'task-file-test.csv'
    path.write_bytes(b'a,b\n')
    artifact = Artifact(id='artifact_test', kind=ArtifactKind.FILE, state=ArtifactState.LOCAL,
        local_path=str(path), mime_type='text/csv', size_bytes=4,
        verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED,
            details={'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    store.commit_task_files(record.operation_id, response_id='r_one', candidate_id='rc_id', artifacts=(artifact,))
    async def initialize():
        path.unlink()
    finalize = AsyncMock()
    result = asyncio.run(task_jobs.read_task_operation(store.get(record.operation_id), store=store,
        client_provider=lambda: object(), client_initializer=initialize, finalize_chat_cleanup=finalize,
        include_answer=True))
    assert result.ok and result.data.state == OperationState.PARTIAL and result.data.task_response == 'cached answer'
    assert len(result.data.artifacts) == 1 and store.get(record.operation_id).cleanup_protected
    finalize.assert_not_awaited()


def test_task_account_switch_after_first_file_stops_next_download(store, monkeypatch, tmp_path):
    from src.domain import Artifact, ArtifactKind, ArtifactState
    record = _read_record(store)
    client = TaskClient(history=_history(_model_turn('c_one', 'r_one', 'rc_id', 'answer')))
    client.client = object()
    monkeypatch.setattr(task_jobs, 'fetch_task_metadata', AsyncMock(return_value=_metadata(3)))
    monkeypatch.setattr(task_jobs, 'fetch_task_file_cards', AsyncMock(return_value=(object(), object())))
    async def switched(*_args, **_kwargs):
        monkeypatch.setenv('GEMINI_PSID', 'different-synthetic-account')
        return Artifact(id='artifact_dummy', kind=ArtifactKind.FILE, state=ArtifactState.LOCAL)
    saver = AsyncMock(side_effect=switched)
    monkeypatch.setattr(task_jobs, 'save_task_file', saver)
    finalize = AsyncMock()
    result = asyncio.run(task_jobs.read_task_operation(record, store=store, client_provider=lambda: client,
        client_initializer=AsyncMock(), finalize_chat_cleanup=finalize, include_answer=True))
    assert not result.ok and result.meta.verification_status == 'account_mismatch_or_unbound'
    assert saver.await_count == 1
    finalize.assert_not_awaited()
