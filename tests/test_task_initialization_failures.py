"""Task initialization failures retain their real classification without dispatching a prompt."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src import client_wrapper
from src.client_manager import ClientAuthenticationRequired
from src.infrastructure.operation_store import OperationStore
from src.services.task_jobs import execute_task_job


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-initialization-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create_or_get("task", "gemini_run_task", requested_model="neon", thinking_level="standard")
    assert store.claim_worker("task", "worker")
    provider = Mock()
    monkeypatch.setattr(client_wrapper, "get_gemini_client", provider)
    return store, provider


@pytest.mark.parametrize("error,expected", [
    (ConnectionError("synthetic connection failure"), "NETWORK_ERROR"),
    (RuntimeError("SSL_connect: synthetic upstream transport failure"), "NETWORK_ERROR"),
    (ClientAuthenticationRequired(), "AUTH_REQUIRED"),
    (RuntimeError("synthetic unknown initialization failure"), "INTERNAL_ERROR"),
])
def test_initialization_errors_are_classified_without_reaching_generation(setup, monkeypatch, error, expected):
    store, provider = setup
    initialize = AsyncMock(side_effect=error)
    monkeypatch.setattr(client_wrapper, "initialize_client", initialize)
    asyncio.run(execute_task_job({"prompt": "synthetic task", "model": "neon"},
                                 operation_id="task", store=store, worker_token="worker"))
    record = store.get("task")
    assert record.state == "partial"
    assert record.phase == "task_initialize_failed"
    assert record.error_code == expected
    assert record.cleanup_protected
    assert record.upstream_chat_id is None and record.upstream_response_id is None
    assert record.upstream_candidate_id is None and record.request_model is None
    assert record.worker_token == "worker"
    initialize.assert_awaited_once()
    provider.assert_not_called()


@pytest.mark.parametrize("fence", ["worker", "account"])
def test_initialization_failure_cannot_update_after_worker_or_account_changes(setup, monkeypatch, fence):
    store, provider = setup
    expected = []

    async def initialize():
        if fence == "worker":
            store.retire_worker("task", "worker")
            assert store.claim_worker("task", "replacement")
        else:
            monkeypatch.setenv("GEMINI_PSID", "different-synthetic-task-account")
        expected.append(store.get("task"))
        raise ConnectionError("synthetic initialization failure after a fence change")

    monkeypatch.setattr(client_wrapper, "initialize_client", initialize)
    asyncio.run(execute_task_job({"prompt": "synthetic task"}, operation_id="task",
                                 store=store, worker_token="worker"))
    assert store.get("task") == expected[0]
    assert store.get("task").phase == "created"
    provider.assert_not_called()


def test_initialization_failure_preserves_an_existing_turn_and_cleanup_guard(setup, monkeypatch):
    store, provider = setup
    assert store.checkpoint_media("task", worker_token="worker", chat_id="c_synthetic",
                                  response_id="r_synthetic", candidate_id="rc_synthetic")
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock(side_effect=ClientAuthenticationRequired()))
    asyncio.run(execute_task_job({"prompt": "synthetic continuation"}, operation_id="task",
                                 store=store, worker_token="worker"))
    record = store.get("task")
    assert (record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id) == (
        "c_synthetic", "r_synthetic", "rc_synthetic",
    )
    assert record.cleanup_protected and record.error_code == "AUTH_REQUIRED"
    assert record.phase == "task_initialize_failed"
    provider.assert_not_called()


def test_initialization_cancellation_still_propagates_without_reclassifying(setup, monkeypatch):
    store, provider = setup
    before = store.get("task")
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute_task_job({"prompt": "synthetic task"}, operation_id="task",
                                     store=store, worker_token="worker"))
    assert store.get("task") == before
    provider.assert_not_called()
