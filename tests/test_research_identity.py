"""Early research identities survive SDK rollback and ownership changes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import APIError

from src.domain import CleanupObservation, CleanupState
from src.infrastructure.operation_store import OperationStore
from src.services import research
from src.services.cleanup_scope import observe_upstream_identity
from src.thinking_client import ThinkingLevelGeminiClient


@pytest.fixture(autouse=True)
def synthetic_account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "offline-research-account")


def test_real_sdk_early_identity_survives_rollback_and_restart(tmp_path, monkeypatch):
    inner = json.dumps([None, ["c_early", "r_early", "rc_early"]], separators=(",", ":"))
    frame = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(frame) + 1) + "\n" + frame).encode()

    class Response:
        status_code = 200
        http_version = 2

        async def aiter_content(self):
            yield wire
            raise APIError("synthetic failure")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Transport:
        requests = 0

        def stream(self, *_args, **_kwargs):
            self.requests += 1
            return Response()

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(GeminiClient, "_sync_activity", noop)
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda *_a, **_kw: None)
    monkeypatch.setattr(GeminiClient, "close", noop)
    client = ThinkingLevelGeminiClient()
    from gemini_webapi.types import AvailableModel
    client._model_registry = {"flash": AvailableModel(model_id="flash", model_name="gemini-flash", display_name="Flash", description="", capacity=1)}
    client.client = transport = Transport()
    client._running = True
    client._install_thinking_transport()
    store = OperationStore(tmp_path / "operations.sqlite")
    monkeypatch.setattr(research, "OperationStore", lambda: store)
    scheduled = []

    def schedule(cid, **kwargs):
        scheduled.append((cid, kwargs))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)

    service = research.ResearchService(research.ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=schedule, resolve_model=lambda model: model,
    ))
    handle = "op_" + "a" * 32
    result = asyncio.run(service.start(research.ResearchRequest(query="synthetic", operation_id=handle)))
    saved = OperationStore(store.path).get(handle)
    assert result.data.upstream_chat_id == "c_early"
    assert saved.upstream_chat_id == "c_early" and saved.upstream_response_id == "r_early"
    assert saved.upstream_candidate_id == "rc_early" and saved.cleanup_protected
    assert saved.phase == "plan_outcome_unverified" and saved.state == "partial"
    assert transport.requests == 1
    assert scheduled and all(cid == "c_early" for cid, _ in scheduled)


def test_research_checkpoint_rejects_conflicting_chat_id(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    handle = "op_" + "b" * 32
    store.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
    assert store.attach_late_chat(handle, "c_original")
    schedule = []
    with pytest.raises(ValueError):
        research.research_cleanup_checkpoint(
            store, handle, None, SimpleNamespace(cid="c_other"), retain_chat=False, delete_after_seconds=None,
            source="synthetic", schedule_chat_cleanup=lambda *args, **kwargs: schedule.append(args),
        )
    assert store.get(handle).upstream_chat_id == "c_original"
    assert not schedule


def test_early_identity_followed_by_plan_claims_confirmation_once(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "success.sqlite")
    monkeypatch.setattr(research, "OperationStore", lambda: store)
    chat = SimpleNamespace(cid="", rid="", rcid="", metadata=[])

    class Client:
        def start_chat(self, **_kwargs):
            return chat

        async def create_deep_research_plan(self, *_args, **_kwargs):
            chat.cid = "c_plan"
            chat.metadata = ["c_plan", "r_plan", "rc_plan"]
            observe_upstream_identity(chat)
            return SimpleNamespace(cid="c_plan", research_id="research_plan")

        start_deep_research = AsyncMock(return_value=SimpleNamespace(state="running", metadata=["c_plan"]))
        wait_for_deep_research = AsyncMock()

    client = Client()
    service = research.ResearchService(research.ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda model: model,
    ))
    handle = "op_" + "d" * 32
    result = asyncio.run(service.start(research.ResearchRequest(query="synthetic", operation_id=handle)))
    assert result.data.state.value == "running"
    assert store.get(handle).upstream_chat_id == "c_plan"
    client.start_deep_research.assert_awaited_once()


@pytest.mark.parametrize("mode", ["cancel", "timeout", "account_change"])
def test_early_identity_stays_protected_across_interruption(tmp_path, monkeypatch, mode):
    store = OperationStore(tmp_path / "interrupt.sqlite")
    monkeypatch.setattr(research, "OperationStore", lambda: store)
    monkeypatch.setattr(research, "phase_timeout", lambda _: 0.01 if mode == "timeout" else 20)
    schedule = []
    seen = asyncio.Event()
    release = asyncio.Event()
    chat = SimpleNamespace(cid="", rid="", rcid="", metadata=[])

    class Client:
        def start_chat(self, **_kwargs):
            return chat

        async def create_deep_research_plan(self, *_args, **_kwargs):
            if mode == "account_change":
                monkeypatch.setenv("GEMINI_PSID", "different-offline-account")
            observe_upstream_identity(SimpleNamespace(cid="c_early", metadata=["c_early", "r_early", "rc_early"]))
            seen.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                if mode != "timeout":
                    raise
                await release.wait()
                observe_upstream_identity(SimpleNamespace(cid="c_early", metadata=["c_early", "r_early", "rc_early"]))
            return SimpleNamespace(cid="c_early")

        start_deep_research = AsyncMock(side_effect=AssertionError("must not confirm"))
        wait_for_deep_research = AsyncMock()

    client = Client()
    def schedule_cleanup(cid, **_kwargs):
        schedule.append(cid)
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)

    service = research.ResearchService(research.ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=schedule_cleanup, resolve_model=lambda model: model,
    ))
    handle = "op_" + "c" * 32

    async def run():
        task = asyncio.create_task(service.start(research.ResearchRequest(query="synthetic", operation_id=handle)))
        if mode == "account_change":
            await task
        else:
            await seen.wait()
            if mode == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.data.upstream_chat_id == "c_early"
                before = store.get(handle)
                release.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert store.get(handle).state == before.state
        client.start_deep_research.assert_not_awaited()

    asyncio.run(run())
    saved = OperationStore(store.path).get(handle)
    assert saved.upstream_chat_id == "c_early" and saved.cleanup_protected
    if mode != "account_change":
        assert saved.phase == "plan_outcome_unverified"
        assert saved.upstream_response_id == "r_early" and saved.upstream_candidate_id == "rc_early"
    else:
        assert not schedule
        assert saved.phase == "plan_outcome_unverified"
        assert saved.upstream_response_id == "r_early" and saved.upstream_candidate_id == "rc_early"


def test_lost_worker_still_binds_identity_without_scheduling_or_confirming(tmp_path, monkeypatch):
    now = [100.0]
    store = OperationStore(tmp_path / "lease.sqlite", clock=lambda: now[0])
    monkeypatch.setattr(research, "OperationStore", lambda: store)
    handle = "op_" + "e" * 32
    store.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
    assert store.claim_worker(handle, "old")
    chat = SimpleNamespace(cid="", rid="", rcid="", metadata=[])

    class Client:
        def start_chat(self, **_kwargs):
            return chat

        async def create_deep_research_plan(self, *_args, **_kwargs):
            now[0] += 61
            assert store.claim_worker(handle, "new")
            observe_upstream_identity(SimpleNamespace(cid="c_old", metadata=["c_old", "r_old", "rc_old"]))
            raise AssertionError("lost worker must stop")

        start_deep_research = AsyncMock(side_effect=AssertionError("must not confirm"))
        wait_for_deep_research = AsyncMock()

    client = Client()
    schedule = []
    service = research.ResearchService(research.ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda *args, **kwargs: schedule.append(args), resolve_model=lambda model: model,
    ))
    asyncio.run(service.start(research.ResearchRequest(query="synthetic", operation_id=handle), worker_token="old"))
    saved = OperationStore(store.path).get(handle)
    assert saved.worker_token == "new" and saved.phase == "plan_outcome_unverified" and saved.cleanup_protected
    assert (saved.upstream_chat_id, saved.upstream_response_id, saved.upstream_candidate_id) == ("c_old", "r_old", "rc_old")
    assert not schedule
    client.start_deep_research.assert_not_awaited()


@pytest.mark.parametrize("metadata", [["c_plan", "r_other", "rc_plan"], ["c_plan", "r_plan", "rc_other"]])
def test_recovery_does_not_confirm_a_different_turn(tmp_path, monkeypatch, metadata):
    store = OperationStore(tmp_path / "recovery.sqlite")
    handle = "op_" + "f" * 32
    store.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
    record = store.get(handle)
    assert store.attach_research_observation(handle, "c_plan", account_key=record.account_key,
                                             response_id="r_plan", candidate_id="rc_plan")
    record = store.get(handle)
    client = SimpleNamespace(start_deep_research=AsyncMock(), start_chat=AsyncMock())
    monkeypatch.setattr(research, "read_existing_research_plan", AsyncMock(
        return_value=SimpleNamespace(cid="c_plan", metadata=metadata)))
    service = research.ResearchService(research.ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda *_a, **_k: None, resolve_model=lambda model: model,
    ))
    result = asyncio.run(service._resume_existing_plan(store, record, 20))
    assert result.data.state.value == "partial"
    assert store.get(handle).phase == "plan_outcome_unverified"
    client.start_chat.assert_not_called()
    client.start_deep_research.assert_not_awaited()
