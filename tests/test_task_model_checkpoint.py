"""Task model evidence comes from the dispatched selector, never the requested label."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.operation_store import OperationStore
from src.services.model_policy import ModelSelection
from src.services.task_jobs import _LostTaskLease, _checkpoint_task


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-model-account")
    return OperationStore(tmp_path / "operations.sqlite3")


def _response(*, selection=None, rid="r_task"):
    response = SimpleNamespace(metadata=["c_task", rid], rcid="rc_task")
    if selection is not None:
        response._mcp_model_selection = selection
    return response


@pytest.mark.parametrize("requested,effective", [
    ("flash", "gemini-4-hydrogen"),
    ("neon", "gemini-neon"),
    ("carbon", "gemini-carbon"),
])
def test_task_checkpoint_persists_actual_selector_without_rewriting_request(store, requested, effective):
    store.create_or_get("task", "gemini_run_task", requested_model=requested, thinking_level="standard")
    assert store.claim_worker("task", "worker")
    assert store.get("task").request_model is None
    selection = ModelSelection(requested, effective, None, True, "standard")
    _checkpoint_task(store, "task", "worker", _response(selection=selection))
    record = store.get("task")
    assert record.request_model == effective
    assert record.requested_model == requested
    assert (record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id) == (
        "c_task", "r_task", "rc_task",
    )
    _checkpoint_task(store, "task", "worker", _response())
    assert store.get("task").request_model == effective


def test_request_label_and_untyped_metadata_do_not_fabricate_actual_model(store):
    store.create_or_get("task", "gemini_run_task", requested_model="neon")
    assert store.claim_worker("task", "worker")
    _checkpoint_task(store, "task", "worker", _response(selection=SimpleNamespace(effective_model="gemini-neon")))
    assert store.get("task").request_model is None
    assert store.get("task").requested_model == "neon"


@pytest.mark.parametrize("invalid", ["worker", "account", "turn"])
def test_task_selector_evidence_cannot_overwrite_another_owner_or_turn(store, monkeypatch, invalid):
    store.create_or_get("task", "gemini_run_task", requested_model="neon")
    assert store.claim_worker("task", "worker")
    _checkpoint_task(store, "task", "worker", _response(
        selection=ModelSelection("neon", "gemini-neon", None, True, "standard")))
    worker, rid = "worker", "r_task"
    if invalid == "worker":
        worker = "other-worker"
    elif invalid == "account":
        monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
    else:
        rid = "r_different"
    response = _response(selection=ModelSelection("carbon", "gemini-carbon", None, True, "standard"), rid=rid)
    with pytest.raises(_LostTaskLease if invalid != "turn" else ValueError):
        _checkpoint_task(store, "task", worker, response)
    assert store.get("task").request_model == "gemini-neon"


def test_legacy_task_checkpoint_without_selection_adds_no_new_keyword():
    calls = []

    class OldStore:
        def checkpoint_media(self, operation_id, *, worker_token, chat_id, response_id, candidate_id):
            calls.append((operation_id, worker_token, chat_id, response_id, candidate_id))
            return True

    _checkpoint_task(OldStore(), "task", "worker", _response())
    assert calls == [("task", "worker", "c_task", "r_task", "rc_task")]


def test_task_worker_passes_the_observed_selector_into_exact_turn_recovery(store, monkeypatch):
    from src import client_wrapper
    from src.services import task_jobs

    store.create_or_get("task", "gemini_run_task", requested_model="flash", thinking_level="standard")
    assert store.claim_worker("task", "worker")
    response = _response(selection=ModelSelection("flash", "gemini-4-hydrogen", None, True, "standard"))
    generate = AsyncMock(return_value=response)
    client = SimpleNamespace(generate_content=generate, task_generation_scope=lambda: nullcontext())
    recover = AsyncMock()
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(task_jobs, "recover_task_job", recover)
    asyncio.run(task_jobs.execute_task_job({"prompt": "synthetic task", "model": "flash"},
                                           operation_id="task", store=store, worker_token="worker"))
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["model"] == "flash"
    recover.assert_awaited_once()
    recovered_record = recover.await_args.args[0]
    assert recovered_record.requested_model == "flash"
    assert recovered_record.request_model == "gemini-4-hydrogen"
    assert (recovered_record.upstream_response_id, recovered_record.upstream_candidate_id) == ("r_task", "rc_task")
