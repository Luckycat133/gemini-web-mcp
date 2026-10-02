"""Research mutations use the real SDK builder once after uncertain failure."""

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.constants import Endpoint, Model
from gemini_webapi.exceptions import APIError

import gemini_webapi.client as upstream_client
import gemini_webapi.constants as upstream_constants
from src.domain import OperationState
from src.infrastructure.state_store import StateStore
from src.services.operations import OperationService
from src.services.research import (
    ResearchRequest,
    ResearchService,
    ResearchServiceDependencies,
    wait_for_deep_research_by_chat,
)
from src.thinking_client import ThinkingLevelGeminiClient, client_generation_once
from tests.test_media_generation_failures import failing_sdk_client


class SealedSession:
    cookies = {}

    def __init__(self, captured):
        self.captured = captured

    def stream(self, method, url, **kwargs):
        assert method == "POST" and url == Endpoint.GENERATE
        self.captured.append(kwargs)
        raise APIError("Offline uncertainty after research submission")

    async def close(self):
        return None

    async def get(self, *_args, **_kwargs):
        raise AssertionError("Offline fixture must not make GET requests")

    async def post(self, *_args, **_kwargs):
        raise AssertionError("Offline fixture must not make POST requests")


def _seal_research_authorization(monkeypatch, client):
    """Supply already-authorized synthetic state in either supported SDK."""
    if hasattr(client, "account_status"):
        client.account_status = upstream_constants.AccountStatus.AVAILABLE

    async def noop(*_args, **_kwargs):
        return None

    # SDK2.0 performs account capability/preflight RPCs before collection;
    # SDK2.1.1 checks the locally observed AccountStatus instead. Neither is
    # under test here, and every real RPC/HTTP method remains sealed.
    for method in ("_assert_deep_research_capable", "_deep_research_preflight"):
        if hasattr(client, method):
            monkeypatch.setattr(client, method, noop)


def _sealed_sdk(monkeypatch, captured):
    # These are synthetic values, so constructor cookie discovery never runs.
    client = ThinkingLevelGeminiClient(secure_1psid="offline-research-fixture")
    client._running = True
    client.auto_close = False
    client.access_token = "offline-fixture-token"
    _seal_research_authorization(monkeypatch, client)

    async def noop(*_args, **_kwargs):
        return None

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Offline fixture must not initialize or make RPC requests")

    activity = "_sync_activity" if hasattr(client, "_sync_activity") else "_send_bard_activity"
    monkeypatch.setattr(client, activity, noop)
    if hasattr(client, "_fetch_usage_info"):
        monkeypatch.setattr(client, "_fetch_usage_info", noop)
    monkeypatch.setattr(client, "init", forbidden)
    monkeypatch.setattr(client, "_batch_execute", forbidden)
    monkeypatch.setattr(client, "close", noop)
    monkeypatch.setattr(upstream_client, "save_cookies", lambda *_args: None)
    decorators = importlib.import_module(upstream_client.running.__module__)
    delay_factor = "_DELAY_FACTOR" if hasattr(decorators, "_DELAY_FACTOR") else "DELAY_FACTOR"
    monkeypatch.setattr(decorators, delay_factor, 0)
    client.client = SealedSession(captured)
    client._install_thinking_transport()
    return client


@pytest.mark.parametrize("phase", ["plan", "start", "fallback"])
def test_research_service_sdk_failure_submits_each_mutation_once(tmp_path, monkeypatch, phase):
    captured, readbacks = [], []
    client = _sealed_sdk(monkeypatch, captured)

    async def noop(*_args, **_kwargs):
        return None

    async def read_existing(cid, **_kwargs):
        readbacks.append(cid)
        return None

    monkeypatch.setattr(client, "fetch_latest_chat_response", read_existing)
    if phase == "start":
        # Only the preceding successful plan is replaced. Confirmation still
        # uses the real SDK _collect -> ChatSession -> builder -> retry wrapper.
        async def observed_plan(_query, *, chat, model):
            chat.cid = "c_owned_offline"
            return SimpleNamespace(cid=chat.cid, metadata=list(chat.metadata), research_id=None,
                                   title="Offline plan", confirm_prompt="Start research")
        monkeypatch.setattr(client, "create_deep_research_plan", observed_plan)

    operations = OperationService(StateStore(tmp_path / "state.sqlite3"), scope_provider=lambda: "scope_offline")
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=noop, cleanup_due_remote_chats=noop,
        schedule_chat_cleanup=lambda *_args, **_kwargs: None,
        resolve_model=lambda _model: Model.UNSPECIFIED,
    ), operations=operations)
    request = ResearchRequest("offline research request", idempotency_key="same-opaque-key",
                              output_dir=str(tmp_path / "reports"))

    async def run():
        if phase == "fallback":
            # Actual generate_content(deep_research=True), not a fake top-level
            # generation counter, is exercised by the compatibility fallback.
            first = await service.execute_fallback(client, request)
            second = await service.execute_fallback(client, request)
            operation_id = first.result.data.operation_id
            assert second.result.data.operation_id == operation_id
        else:
            first = await service.start(request)
            assert first.data.state == OperationState.ACCEPTED
            await operations._tasks[first.data.operation_id]
            second = await service.start(request)
            operation_id = first.data.operation_id
            assert second.data.operation_id == operation_id
        record = operations.store.operations.get("scope_offline", operation_id)
        assert record.state == OperationState.FAILED
        assert record.attempt_count == 1
        assert record.upstream_chat_id == ("c_owned_offline" if phase == "start" else None)

    asyncio.run(run())
    assert len(captured) == 1
    assert "current_retry" not in captured[0]
    assert readbacks == (["c_owned_offline"] if phase == "start" else [])


def test_generation_once_is_client_and_task_scoped_and_resets_after_failure(monkeypatch):
    first_requests, second_requests = [], []
    first = _sealed_sdk(monkeypatch, first_requests)
    second = _sealed_sdk(monkeypatch, second_requests)

    async def generate(client):
        with pytest.raises(APIError):
            await client.generate_content("offline request", model=Model.UNSPECIFIED)

    async def run():
        with client_generation_once(first):
            # The second client retains its normal SDK retry policy even in a
            # child task inheriting this scope. Both real HTTP builders run.
            await asyncio.gather(generate(first), generate(second))
        await generate(first)

    asyncio.run(run())
    assert len(first_requests) == 7  # one scoped post, then six ordinary posts
    assert len(second_requests) == 6


def test_generation_once_keeps_real_sdk_readback_rpc_retries(monkeypatch):
    captured = []
    client = _sealed_sdk(monkeypatch, captured)
    posts = []

    async def fail_readback(url, **kwargs):
        posts.append((url, kwargs))
        raise APIError("Offline readback transport failure")

    client.client.post = fail_readback
    # Restore the actual decorated read-back RPC owner that the sealed builder
    # fixture blocks by default. There is still no provider I/O.
    monkeypatch.setattr(client, "_batch_execute", GeminiClient._batch_execute.__get__(client))

    async def run():
        with client_generation_once(client), pytest.raises(APIError):
            await client._batch_execute([])

    asyncio.run(run())
    assert len(posts) == 3
    assert captured == []
    assert all("current_retry" not in kwargs for _url, kwargs in posts)


@pytest.mark.parametrize("phase", ["plan", "start", "fallback"])
def test_real_research_stream_retains_observed_source_after_sdk_rollback(tmp_path, monkeypatch, phase):
    requests, decisions, readbacks = [], [], []
    cid = "c_owned_offline_observed"
    client = failing_sdk_client(monkeypatch, requests, cid=cid)
    _seal_research_authorization(monkeypatch, client)

    async def noop(*_args, **_kwargs):
        return None

    async def existing_source(chat_id, **_kwargs):
        readbacks.append(chat_id)
        return None

    monkeypatch.setattr(client, "close", noop)
    monkeypatch.setattr(client, "fetch_latest_chat_response", existing_source)
    if phase == "start":
        async def source_not_allocated_yet(_query, *, chat, model):
            return SimpleNamespace(cid="", metadata=list(chat.metadata), research_id="provider_offline",
                                   title="Offline plan", confirm_prompt="Start research")
        monkeypatch.setattr(client, "create_deep_research_plan", source_not_allocated_yet)

    store = StateStore(tmp_path / "state.sqlite3")
    operations = OperationService(store, scope_provider=lambda: "scope_offline")
    dependencies = ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=noop, cleanup_due_remote_chats=noop,
        schedule_chat_cleanup=lambda chat_id, **kwargs: decisions.append((chat_id, kwargs)),
        resolve_model=lambda _model: Model.UNSPECIFIED,
    )
    service = ResearchService(dependencies, operations=operations)
    request = ResearchRequest("offline research request", retain_chat=False, output_dir=str(tmp_path / "reports"))

    async def run():
        if phase == "fallback":
            execution = await service.execute_fallback(client, request)
            operation_id = execution.result.data.operation_id
            assert execution.result.data.upstream_chat_id == cid
        else:
            started = await service.start(request)
            operation_id = started.data.operation_id
            await operations._tasks[operation_id]
        record = store.operations.get("scope_offline", operation_id)
        assert record.state == OperationState.FAILED
        assert record.upstream_chat_id == cid
        assert record.provider_operation_id == ("provider_offline" if phase == "start" else None)
        assert record.artifacts == ()
        assert decisions == [(cid, {"retain_chat": True, "delete_after_seconds": None, "source": "gemini_research"})]

        # A later service recovers by this exact observed source even though
        # the real SDK rolled its conversation metadata back after the frame.
        readbacks.clear()
        restarted = ResearchService(dependencies, operations=OperationService(
            StateStore(store.path), scope_provider=lambda: "scope_offline",
        ))
        result = await restarted.result(operation_id)
        assert result.data.upstream_chat_id == cid
        assert result.data.state == OperationState.FAILED
        assert readbacks == [cid]

    asyncio.run(run())
    assert len(requests) == 1
    assert "current_retry" not in requests[0]


def test_research_source_is_durable_before_next_frame_and_cancellation(tmp_path, monkeypatch):
    async def run():
        requests, decisions = [], []
        cid = "c_owned_offline_paused"
        client = failing_sdk_client(monkeypatch, requests, cid=cid, pause=True)
        _seal_research_authorization(monkeypatch, client)
        observed = asyncio.Event()
        original = client.start_owned_chat

        def observable_chat(*, model, on_chat_observed):
            def persist(chat_id):
                on_chat_observed(chat_id)
                observed.set()
            return original(model=model, on_chat_observed=persist)

        async def noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(client, "start_owned_chat", observable_chat)
        monkeypatch.setattr(client, "close", noop)
        operations = OperationService(StateStore(tmp_path / "state.sqlite3"), scope_provider=lambda: "scope_offline")
        service = ResearchService(ResearchServiceDependencies(
            client_provider=lambda: client, client_initializer=noop, cleanup_due_remote_chats=noop,
            schedule_chat_cleanup=lambda chat_id, **kwargs: decisions.append((chat_id, kwargs)),
            resolve_model=lambda _model: Model.UNSPECIFIED,
        ), operations=operations)
        started = await service.start(ResearchRequest("offline request", output_dir=str(tmp_path / "reports")))
        await asyncio.wait_for(observed.wait(), timeout=1)
        before = StateStore(operations.store.path).operations.get("scope_offline", started.data.operation_id)
        assert before.state == OperationState.RUNNING
        assert before.upstream_chat_id == cid
        task = operations._tasks[started.data.operation_id]
        task.cancel()
        await task
        after = operations.store.operations.get("scope_offline", started.data.operation_id)
        assert after.state == OperationState.TIMED_OUT
        assert after.upstream_chat_id == cid
        assert after.artifacts == ()
        assert len(requests) == 1
        assert decisions[0][0] == cid and decisions[0][1]["retain_chat"] is True

    asyncio.run(run())


def test_real_sdk_report_followup_posts_once_then_polls_existing_source(tmp_path, monkeypatch):
    from src.tools.research import _request_completed_research_report

    requests, polls = [], []
    client = _sealed_sdk(monkeypatch, requests)

    async def noop(*_args, **_kwargs):
        return None

    async def plan(_query, *, chat, model):
        chat.cid = "c_owned_offline"
        return SimpleNamespace(cid=chat.cid, metadata=list(chat.metadata), research_id=None, title="Offline plan")

    async def start(_plan, *, chat):
        return SimpleNamespace(metadata=[chat.cid], text="I'm on it", state="queued")

    async def completed(chat_id, **_kwargs):
        polls.append(chat_id)
        # Multiple completed acknowledgements must not trigger new report
        # mutations. The actual report finally arrives via read-back.
        if len(polls) >= 3:
            return SimpleNamespace(text="# Report\n\nObserved body.", state="completed", report_id="report_offline")
        return SimpleNamespace(text="I've finished the research", state="completed")

    monkeypatch.setattr(client, "create_deep_research_plan", plan)
    monkeypatch.setattr(client, "start_deep_research", start)
    monkeypatch.setattr(client, "fetch_latest_chat_response", completed)
    operations = OperationService(StateStore(tmp_path / "state.sqlite3"), scope_provider=lambda: "scope_offline")
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=noop, cleanup_due_remote_chats=noop,
        schedule_chat_cleanup=lambda *_args, **_kwargs: None, resolve_model=lambda _model: Model.UNSPECIFIED,
    ), operations=operations)
    request = ResearchRequest("offline request", output_dir=str(tmp_path / "reports"), retain_chat=True)

    async def run():
        _reserved, context = service.reserve(request)
        execution = await service.execute_native(client, request, context, wait_for_completion=True,
                                                 poll_interval=0, fetch_report=noop,
                                                 request_report=_request_completed_research_report)
        assert execution.result.data.state == OperationState.COMPLETED
        assert execution.result.data.artifacts[0].verification.status.value == "verified"
        record = operations.store.operations.get("scope_offline", context.operation_id)
        assert record.upstream_chat_id == "c_owned_offline"
        assert record.state == OperationState.COMPLETED

    asyncio.run(run())
    assert len(requests) == 1
    assert "current_retry" not in requests[0]
    assert polls == ["c_owned_offline"] * 3


def test_unknown_report_followup_response_is_not_generated_again_on_next_poll():
    calls, polls = [], []

    async def completed(_cid):
        polls.append("read")
        return SimpleNamespace(text="I've finished the research", state="completed")

    async def immersive(_client, _cid):
        # Stop deterministically after two read-only rounds, without a wall
        # clock race deciding whether the second follow-up runs.
        if len(polls) == 2:
            raise asyncio.TimeoutError
        return None

    async def unknown(_chat):
        calls.append("followup")
        return SimpleNamespace(text="I'm on it, still researching")

    async def run():
        result = await wait_for_deep_research_by_chat(
            SimpleNamespace(fetch_latest_chat_response=completed), SimpleNamespace(),
            SimpleNamespace(cid="c_owned_offline"), SimpleNamespace(),
            poll_interval=0, timeout=10, fetch_report=immersive, request_report=unknown,
        )
        assert result.done is False
        assert result.final_output is None

    asyncio.run(run())
    assert calls == ["followup"]
    assert polls == ["read", "read"]
