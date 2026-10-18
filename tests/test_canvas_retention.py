"""Canvas sources are retained in shared cleanup state as soon as IDs arrive."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi import GeminiClient

import src.client_wrapper as wrapper
import src.services.canvas as canvas
from src.domain import CleanupState, DomainResult, OperationState
from src.infrastructure.state_store import StateStore, authentication_scope
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.lifecycle import ConversationLifecycleService
from src.session_manager import SessionService
from src.thinking_client import ThinkingLevelGeminiClient


CID = "c_canvas_retention_fixture"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "canvas-retention.sqlite3")
    scope = authentication_scope("fixture-account", store=store)
    cleanup = RemoteChatCleanupManager(state_store=store)
    cleanup.bind_authentication_scope(scope)
    cleanup.schedule_cleanup(CID, delete_after_seconds=3600)
    lifecycle = ConversationLifecycleService(lambda: SessionService(), lambda: cleanup)
    monkeypatch.setattr(wrapper, "_cleanup_manager", cleanup)
    monkeypatch.setattr(wrapper, "_lifecycle_service", lifecycle)
    client = object.__new__(ThinkingLevelGeminiClient)
    delete = AsyncMock()
    monkeypatch.setattr(ThinkingLevelGeminiClient, "delete_chat", delete)
    control = SimpleNamespace(outcome="success", observe=True, entered=None, release=None)
    calls = []

    async def submit(_client, *, chat, current_retry=5, **kwargs):
        calls.append({"chat": chat, "current_retry": current_retry, **kwargs})
        backup = chat.metadata
        if control.observe:
            chat.metadata = [CID, "r_fixture", "rc_fixture"]
        if control.entered is not None:
            control.entered.set()
            try:
                await control.release.wait()
            finally:
                chat.metadata = backup
        if control.outcome == "timeout":
            chat.metadata = backup
            raise TimeoutError("Private Canvas timeout fixture")
        return SimpleNamespace(metadata=[CID] if control.observe else [])

    monkeypatch.setattr(GeminiClient, "generate_content", submit)
    item = {"artifact_id": "artifact_fixture", "text": "Canvas body fixture", "kind": "document"}
    read = AsyncMock(return_value=DomainResult.success({"items": [item]}))
    monkeypatch.setattr(canvas, "list_canvas_chat_artifacts_result", read)
    return SimpleNamespace(client=client, cleanup=cleanup, store=store, scope=scope, calls=calls,
                           control=control, read=read, delete=delete)


def _assert_durable_retention(runtime):
    assert runtime.cleanup.get_cleanup_observation(CID).state is CleanupState.RETAINED
    assert runtime.cleanup.list_pending_cleanup() == {}
    assert runtime.store.cleanup_jobs.get(runtime.scope, CID).state == "retained"
    restarted = RemoteChatCleanupManager(state_store=StateStore(runtime.store.path))
    restarted.bind_authentication_scope(runtime.scope)
    assert asyncio.run(restarted.cleanup_due_chats(client=runtime.client)) == 0
    assert restarted.get_cleanup_observation(CID).state is CleanupState.RETAINED
    serialized = runtime.store.path.read_bytes().decode("utf-8", errors="ignore")
    assert "Private Canvas prompt" not in serialized and "Canvas body fixture" not in serialized
    assert len(runtime.calls) == 1 and runtime.calls[0]["current_retry"] == 0
    runtime.delete.assert_not_awaited()


@pytest.mark.parametrize("outcome,state", [
    ("success", OperationState.COMPLETED), ("empty", OperationState.ACCEPTED),
    ("read_error", OperationState.ACCEPTED), ("timeout", OperationState.TIMED_OUT),
])
def test_canvas_results_return_real_retention_and_cancel_existing_cleanup(runtime, outcome, state):
    runtime.control.outcome = outcome
    if outcome == "empty":
        runtime.read.return_value = DomainResult.success({"items": []})
    elif outcome == "read_error":
        runtime.read.side_effect = TimeoutError("Private read fixture")
    result = asyncio.run(canvas.generate_canvas_result(runtime.client, prompt="Private Canvas prompt fixture"))
    assert result.meta.operation_state is state
    assert result.ok is (outcome != "timeout")
    assert result.data["source_chat_id"] == CID and result.data["retained"] is True
    assert result.data["cleanup"].state is CleanupState.RETAINED
    assert result.data["lifecycle"].upstream_chat_id == CID
    assert result.data["lifecycle"].cleanup is result.data["cleanup"]
    assert result.meta.details["lifecycle"].cleanup is result.data["cleanup"]
    assert "Private Canvas" not in json.dumps(result.to_dict())
    if outcome == "timeout":
        assert result.retryable is False
        assert runtime.calls[0]["chat"].cid == ""
        assert runtime.calls[0]["chat"].observed_chat_id == CID
        runtime.read.assert_not_awaited()
    _assert_durable_retention(runtime)


def test_canvas_cancellation_keeps_early_observed_source_registered_after_sdk_rollback(runtime):
    async def run():
        runtime.control.entered, runtime.control.release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(canvas.generate_canvas_result(runtime.client, prompt="Private Canvas prompt fixture"))
        await asyncio.wait_for(runtime.control.entered.wait(), timeout=1)
        early = runtime.cleanup.get_cleanup_observation(CID)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert early.state is CleanupState.RETAINED

    asyncio.run(run())
    assert runtime.calls[0]["chat"].cid == ""
    assert runtime.calls[0]["chat"].observed_chat_id == CID
    runtime.read.assert_not_awaited()
    _assert_durable_retention(runtime)


def test_canvas_cleanup_storage_failure_is_reported_without_claiming_retention(runtime, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("Fixture storage unavailable")

    monkeypatch.setattr(runtime.cleanup, "_durable_schedule_locked", unavailable)
    result = asyncio.run(canvas.generate_canvas_result(runtime.client, prompt="Private Canvas prompt fixture"))
    assert result.ok and result.meta.operation_state is OperationState.PARTIAL
    assert result.data["source_chat_id"] == CID and result.data["retained"] is False
    assert result.data["cleanup"].state is CleanupState.FAILED
    assert result.warnings[0].code == "CANVAS_RETENTION_UNVERIFIED"
    assert len(runtime.calls) == 1


def test_canvas_failure_without_source_does_not_claim_a_retained_chat(runtime):
    runtime.control.observe = False
    runtime.control.outcome = "timeout"
    result = asyncio.run(canvas.generate_canvas_result(runtime.client, prompt="Private Canvas prompt fixture"))
    assert result.ok is False and result.retryable is False
    assert result.data["source_chat_id"] is None and result.data["retained"] is False
    assert result.data["cleanup"].state is CleanupState.NOT_APPLICABLE
    assert result.warnings[0].code == "CANVAS_SOURCE_UNOBSERVED"
    runtime.read.assert_not_awaited()
    assert len(runtime.calls) == 1
