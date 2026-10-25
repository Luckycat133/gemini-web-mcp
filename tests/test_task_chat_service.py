"""Task streams retain their source until matching native terminal proof."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import session_manager as session_module
import src.skill_server as compact
import src.tools.chat as primary
from src.domain import CleanupState, OperationState
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.state_store import StateStore, authentication_scope
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.chat import ChatRequest, ChatService, ChatServiceDependencies, SessionMessageRequest, StartSessionRequest
from src.services.lifecycle import ConversationLifecycleService
from src.services.tasks import get_task_status_result
from src.session_manager import SessionService
from src.thinking_client import _generation_once


class _OwnedSession:
    def __init__(self, client, callback=None, *, gem=None):
        self.client, self.callback, self.gem = client, callback, gem
        self.cid = ""
        self.observed_chat_id = None

    def observe(self):
        self.cid = self.observed_chat_id = "c_task_fixture"
        if self.callback:
            self.callback(self.cid)

    async def send_message(self, **kwargs):
        return await self.client.generate_content(chat=self, **kwargs)

    async def send_message_stream(self, **kwargs):
        async for response in self.client.generate_content_stream(chat=self, **kwargs):
            yield response


class _Client:
    def __init__(self, status=2):
        self.status = status
        self.calls, self.reads, self.owners = [], [], []
        self.error = None
        self.scope_active = False
        self.delete_chat = AsyncMock()

    def start_chat(self, **kwargs):
        return SimpleNamespace(cid="", **kwargs)

    def start_owned_chat(self, *, model=None, gem=None, on_chat_observed=None):
        session = _OwnedSession(self, on_chat_observed, gem=gem)
        self.owners.append(session)
        return session

    @contextmanager
    def task_scope(self):
        self.scope_active = True
        try:
            yield
        finally:
            self.scope_active = False

    def _generate(self, kwargs, stream):
        self.calls.append((kwargs, self.scope_active, stream))
        assert kwargs.get("task_mode") is True or self.scope_active
        assert _generation_once.get() is self
        assert "media_mode" not in kwargs and "media_observation" not in kwargs
        owner = kwargs["chat"]
        owner.observe()
        return owner

    async def generate_content(self, **kwargs):
        owner = self._generate(kwargs, False)
        if self.error:
            owner.cid = ""  # SDK rollback must not erase the observed locator.
            raise self.error
        return SimpleNamespace(text="Task response fixture", metadata=[owner.cid])

    async def generate_content_stream(self, **kwargs):
        owner = self._generate(kwargs, True)
        yield SimpleNamespace(text="Task ", text_delta="Task ", metadata=[owner.cid])
        if self.error:
            owner.cid = ""
            raise self.error
        yield SimpleNamespace(text="response", text_delta="response", metadata=[])

    async def _batch_execute(self, requests, **_kwargs):
        rpc_id = requests[0].rpcid
        if rpc_id == get_contract("tasks.get").rpc_id:
            self.reads.append(json.loads(requests[0].payload))
            if isinstance(self.status, Exception):
                raise self.status
            goal = [None] * 14
            goal[0], goal[1], goal[2], goal[13] = "goal_fixture", "Private goal fixture", self.status, "c_task_fixture"
            body = [] if self.status is None else [goal]
        else:
            assert rpc_id == get_contract("history.page").rpc_id
            body = [None, None, []]
        return SimpleNamespace(status_code=200, text=json.dumps([["wrb.fr", rpc_id, json.dumps(body)]]))


def _service(*, status=2, manager=None, sessions=None):
    client = _Client(status)
    sessions = sessions or SessionService(id_factory=lambda: "sess_task_fixture")
    manager = manager or RemoteChatCleanupManager()
    lifecycle = ConversationLifecycleService(lambda: sessions, lambda: manager)
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(),
        cleanup_due_remote_chats=AsyncMock(return_value=0),
        create_session=sessions.create_session, lookup_session=lifecycle.lookup_session,
        send_session_message=lifecycle.send_message, send_session_message_stream=lifecycle.send_message_stream,
        schedule_response_cleanup=lifecycle.schedule_cleanup_from_response,
        schedule_chat_cleanup=lifecycle.schedule_cleanup,
        normalize_model=lambda model: model, resolve_model=lambda model: model,
    ))
    return service, client, manager, sessions, lifecycle


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("status,state", [
    (0, OperationState.PARTIAL), (1, OperationState.QUEUED), (2, OperationState.RUNNING),
    (5, OperationState.PARTIAL), (6, OperationState.RUNNING), (7, OperationState.QUEUED),
    (None, OperationState.PARTIAL), ("DONE", OperationState.PARTIAL),
    (TimeoutError("Private read failure"), OperationState.PARTIAL),
])
def test_task_response_or_stream_end_does_not_authorize_source_cleanup(streaming, status, state):
    service, client, manager, _sessions, _lifecycle = _service(status=status)
    sender = service.generate_stream if streaming else service.generate
    result = asyncio.run(sender(ChatRequest(message="Private task prompt", task_mode=True, delete_after_seconds=0)))

    assert result.ok is True
    assert result.meta.operation_state is state
    assert result.data.remote_chat_id == "c_task_fixture"
    assert result.data.task_status["terminal"] is False
    assert result.data.lifecycle.retain_chat is True
    assert result.data.lifecycle.cleanup.state is CleanupState.RETAINED
    assert result.warnings[0].code == "TASK_SOURCE_RETAINED"
    assert manager.list_pending_cleanup() == {}
    assert len(client.calls) == 1
    assert client.reads == [["c_task_fixture"]]
    assert "Private task prompt" not in json.dumps(result.to_dict())
    assert "Private goal fixture" not in json.dumps(result.to_dict())
    client.delete_chat.assert_not_awaited()
    assert _generation_once.get() is None


@pytest.mark.parametrize("status", [3, 4, 8])
@pytest.mark.parametrize("retain", [False, True])
def test_only_native_terminal_proof_restores_original_cleanup_policy(status, retain):
    service, client, manager, _sessions, _lifecycle = _service(status=status)
    result = asyncio.run(service.generate(ChatRequest(
        message="Task fixture", task_mode=True, retain_chat=retain, delete_after_seconds=60,
    )))

    assert result.data.task_status["terminal"] is True
    assert result.data.lifecycle.retain_chat is retain
    assert result.data.lifecycle.cleanup.state is (CleanupState.RETAINED if retain else CleanupState.PENDING)
    assert bool(manager.list_pending_cleanup()) is not retain
    assert result.warnings == ()
    client.delete_chat.assert_not_awaited()


def test_task_recovery_reads_same_source_without_replaying_prompt_or_scheduling_cleanup():
    service, client, manager, _sessions, _lifecycle = _service()

    async def run():
        started = await service.generate(ChatRequest(message="Task fixture", task_mode=True))
        client.status = 3
        recovered = await get_task_status_result(client, started.data.remote_chat_id)
        assert recovered.meta.operation_state is OperationState.COMPLETED
        assert await manager.cleanup_due_chats(client=client) == 0

    asyncio.run(run())
    assert len(client.calls) == 1
    assert client.reads == [["c_task_fixture"], ["c_task_fixture"]]
    assert manager.get_cleanup_observation("c_task_fixture").state is CleanupState.RETAINED
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("session", [False, True])
def test_task_timeout_preserves_observed_source_and_never_resubmits(streaming, session):
    service, client, manager, sessions, lifecycle = _service()
    client.error = TimeoutError("Private task timeout fixture")

    async def run():
        if session:
            started = await service.start_session(StartSessionRequest(task_mode=True, gem_id="gem_fixture"))
            sender = service.send_session_stream if streaming else service.send_session
            result = await sender(SessionMessageRequest(session_id=started.data.session_id, message="Private task prompt"))
            assert sessions.get_session(started.data.session_id).task_pending is True
            delete = AsyncMock(return_value=True)
            await lifecycle.reset_one(started.data.session_id, delete_callback=delete)
            delete.assert_not_awaited()
        else:
            sender = service.generate_stream if streaming else service.generate
            result = await sender(ChatRequest(message="Private task prompt", task_mode=True))
        return result

    result = asyncio.run(run())
    assert result.ok is False
    assert result.meta.operation_state is OperationState.TIMED_OUT
    assert result.data.remote_chat_id == "c_task_fixture"
    assert result.data.lifecycle.cleanup.state is CleanupState.RETAINED
    assert result.error.retryable is False
    assert "do not resubmit" in result.suggested_action
    assert "Private task" not in json.dumps(result.to_dict())
    assert client.owners[0].cid == "" and client.owners[0].observed_chat_id == "c_task_fixture"
    assert len(client.calls) == 1 and client.reads == []
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
def test_task_session_inherits_mode_and_releases_guard_only_on_terminal_readback(streaming):
    service, client, manager, sessions, lifecycle = _service()

    async def run():
        started = await service.start_session(StartSessionRequest(task_mode=True, gem_id="gem_fixture", delete_after_seconds=60))
        state = sessions.get_session(started.data.session_id)
        assert state.task_mode is True and state.retain_chat is False
        assert state.session.gem == "gem_fixture"
        sender = service.send_session_stream if streaming else service.send_session
        first = await sender(SessionMessageRequest(session_id=state.session_id, message="Task fixture"))
        assert first.meta.operation_state is OperationState.RUNNING
        assert state.task_pending is True and state.retain_chat is False
        assert manager.list_pending_cleanup() == {}
        client.status = 3
        second = await sender(SessionMessageRequest(session_id=state.session_id, message="Explicit follow-up fixture"))
        assert second.meta.operation_state is OperationState.COMPLETED
        assert state.task_pending is False and state.retain_chat is False
        assert second.data.lifecycle.cleanup.state is CleanupState.PENDING
        delete = AsyncMock(return_value=True)
        await lifecycle.reset_one(state.session_id, delete_callback=delete)
        delete.assert_awaited_once_with("c_task_fixture")

    asyncio.run(run())
    assert all(scope and "task_mode" not in kwargs for kwargs, scope, _stream in client.calls)


@pytest.mark.parametrize("method", ["generate", "generate_stream", "start_session", "send_session", "send_session_stream"])
@pytest.mark.parametrize("conflict", [{"temporary": True}, {"learning_mode": "quiz"}])
def test_unsupported_task_combinations_fail_before_submission(method, conflict):
    service, client, _manager, sessions, _lifecycle = _service()
    if method.startswith("send_"):
        sessions.store_session("sess_task_fixture", _OwnedSession(client), task_mode=True)
        request = SessionMessageRequest(session_id="sess_task_fixture", message="Task fixture", **conflict)
    elif method == "start_session":
        request = StartSessionRequest(task_mode=True, **conflict)
    else:
        request = ChatRequest(message="Task fixture", task_mode=True, **conflict)
    result = asyncio.run(getattr(service, method)(request))
    assert result.error_code == "INVALID_ARGUMENT"
    assert client.calls == [] and client.reads == []


@pytest.mark.parametrize("detach", ["reset_one", "reset_all", "pop", "remove", "clear", "expire"])
def test_local_session_detachment_cannot_delete_pending_task(monkeypatch, detach):
    now = [1000.0]
    monkeypatch.setattr(session_module, "time", SimpleNamespace(time=lambda: now[0]))
    sessions = SessionService(max_age=1)
    service, client, manager, _sessions, lifecycle = _service(sessions=sessions)
    state = sessions.store_session("sess_task_fixture", SimpleNamespace(cid="c_task_fixture"), task_mode=True)
    delete = AsyncMock(return_value=True)

    async def run():
        if detach == "reset_one":
            await lifecycle.reset_one(state.session_id, delete_callback=delete)
        elif detach == "reset_all":
            await lifecycle.reset_all(delete_callback=delete)
        elif detach == "pop":
            lifecycle.pop_session(state.session_id)
        elif detach == "remove":
            lifecycle.remove_session(state.session_id)
        elif detach == "clear":
            lifecycle.clear_sessions()
        else:
            now[0] += 2
            lifecycle.cleanup_expired_sessions()
        assert await manager.cleanup_due_chats(client=client) == 0

    asyncio.run(run())
    assert manager.get_cleanup_observation("c_task_fixture").state is CleanupState.RETAINED
    assert manager.list_pending_cleanup() == {}
    delete.assert_not_awaited()
    client.delete_chat.assert_not_awaited()


def test_task_retention_cancels_existing_durable_cleanup_across_restart(tmp_path):
    store = StateStore(tmp_path / "task_cleanup.sqlite3")
    scope = authentication_scope("fixture-account", store=store)
    manager = RemoteChatCleanupManager(state_store=store)
    manager.bind_authentication_scope(scope)
    manager.schedule_cleanup("c_task_fixture", delete_after_seconds=3600)
    service, client, _manager, _sessions, _lifecycle = _service(manager=manager)
    asyncio.run(service.generate(ChatRequest(message="Private task prompt", task_mode=True)))
    assert store.cleanup_jobs.get(scope, "c_task_fixture").state == "retained"
    restarted = RemoteChatCleanupManager(state_store=StateStore(store.path))
    restarted.bind_authentication_scope(scope)
    assert asyncio.run(restarted.cleanup_due_chats(client=client)) == 0
    assert restarted.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()
    assert "Private task prompt" not in store.path.read_bytes().decode("utf-8", errors="ignore")


def test_first_task_send_upgrades_blank_session_without_losing_gem():
    service, client, _manager, sessions, _lifecycle = _service()

    async def run():
        started = await service.start_session(StartSessionRequest(gem_id="gem_fixture"))
        state = sessions.get_session(started.data.session_id)
        assert state.task_mode is False and client.owners == []
        result = await service.send_session(SessionMessageRequest(
            session_id=state.session_id, message="Task fixture", task_mode=True,
        ))
        assert result.meta.operation_state is OperationState.RUNNING
        assert state.task_mode is True and state.task_pending is True
        assert state.session.gem == "gem_fixture"

    asyncio.run(run())
    assert len(client.calls) == 1 and len(client.owners) == 1


def test_turning_task_mode_off_still_protects_previous_nonterminal_source():
    service, client, manager, _sessions, _lifecycle = _service()

    async def run():
        started = await service.start_session(StartSessionRequest(task_mode=True))
        await service.send_session(SessionMessageRequest(session_id=started.data.session_id, message="Task fixture"))
        # Use a normal sender after explicitly disabling Task; the previous Task
        # still needs native status proof before this chat can be cleaned up.
        async def ordinary(**kwargs):
            assert client.scope_active is False and "task_mode" not in kwargs
            return SimpleNamespace(text="Ordinary follow-up", metadata=["c_task_fixture"])
        client.generate_content = ordinary
        result = await service.send_session(SessionMessageRequest(
            session_id=started.data.session_id, message="Ordinary fixture", task_mode=False,
        ))
        assert result.data.task_mode is False
        assert result.data.task_status["status"] == "running"
        assert result.data.lifecycle.retain_chat is True
        assert result.meta.operation_state is OperationState.RUNNING

    asyncio.run(run())
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("session", [False, True])
def test_task_stream_cancellation_retains_source_before_reply_is_available(session):
    service, client, manager, sessions, lifecycle = _service()

    async def run():
        observed = asyncio.Event()

        async def blocked_stream(**kwargs):
            owner = client._generate(kwargs, True)
            observed.set()
            try:
                await asyncio.Event().wait()
                yield SimpleNamespace(text="unreachable")
            finally:
                owner.cid = ""

        client.generate_content_stream = blocked_stream
        if session:
            started = await service.start_session(StartSessionRequest(task_mode=True))
            pending = service.send_session_stream(SessionMessageRequest(session_id=started.data.session_id, message="Task fixture"))
        else:
            pending = service.generate_stream(ChatRequest(message="Task fixture", task_mode=True))
        task = asyncio.create_task(pending)
        await asyncio.wait_for(observed.wait(), timeout=1)
        assert manager.get_cleanup_observation("c_task_fixture").state is CleanupState.RETAINED
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        if session:
            assert sessions.get_session(started.data.session_id).upstream_chat_id == "c_task_fixture"
            delete = AsyncMock(return_value=True)
            await lifecycle.reset_one(started.data.session_id, delete_callback=delete)
            delete.assert_not_awaited()

    asyncio.run(run())
    assert len(client.calls) == 1 and client.reads == []
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()


def test_task_without_observable_source_capability_does_not_submit():
    service, client, _manager, _sessions, _lifecycle = _service()
    client.start_owned_chat = None
    result = asyncio.run(service.generate(ChatRequest(message="Task fixture", task_mode=True)))
    assert result.error_code == "CAPABILITY_UNAVAILABLE"
    assert client.calls == [] and client.reads == []


@pytest.mark.parametrize("session", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("timeout", [False, True])
def test_real_compact_cleanup_callbacks_preserve_task_source(monkeypatch, session, streaming, timeout):
    _shared, client, manager, sessions, lifecycle = _service()
    for name, value in {
        "get_gemini_client": lambda: client,
        "initialize_client": AsyncMock(),
        "cleanup_due_remote_chats": AsyncMock(return_value=0),
        "create_session": lifecycle.create_session,
        "lookup_session": lifecycle.lookup_session,
        "send_session_message": lifecycle.send_message,
        "send_session_message_stream": lifecycle.send_message_stream,
        "schedule_remote_chat_cleanup": lifecycle.schedule_cleanup,
        "schedule_remote_chat_cleanup_from_response": lifecycle.schedule_cleanup_from_response,
    }.items():
        monkeypatch.setattr(compact, name, value)
    # Keep the real compact callbacks: their required keyword arguments differ
    # from Lifecycle's defaults and must be honored during early CID retention.
    service = compact._build_chat_service()
    if timeout:
        client.error = TimeoutError("Private compact task timeout fixture")

    async def run():
        if session:
            started = await service.start_session(StartSessionRequest(task_mode=True))
            sender = service.send_session_stream if streaming else service.send_session
            result = await sender(SessionMessageRequest(session_id=started.data.session_id, message="Task fixture"))
            assert sessions.get_session(started.data.session_id).task_pending is True
        else:
            sender = service.generate_stream if streaming else service.generate
            result = await sender(ChatRequest(message="Task fixture", task_mode=True))
        assert result.ok is not timeout
        assert result.meta.operation_state is (OperationState.TIMED_OUT if timeout else OperationState.RUNNING)
        assert result.data.remote_chat_id == "c_task_fixture"
        assert result.data.lifecycle.cleanup.state is CleanupState.RETAINED
        assert result.data.task_status["terminal"] is False
        return result

    result = asyncio.run(run())
    assert manager.get_cleanup_observation("c_task_fixture").state is CleanupState.RETAINED
    assert manager.list_pending_cleanup() == {}
    assert len(client.calls) == 1
    if timeout:
        assert client.reads == []
        assert result.error_code == "TIMED_OUT"
        assert result.retryable is False
    else:
        assert client.reads == [["c_task_fixture"]]
    client.delete_chat.assert_not_awaited()


def _adapter_services(monkeypatch, client, lifecycle):
    for module in (primary, compact):
        for name, value in {
            "get_gemini_client": lambda: client,
            "initialize_client": AsyncMock(),
            "cleanup_due_remote_chats": AsyncMock(return_value=0),
            "create_session": lifecycle.create_session,
            "lookup_session": lifecycle.lookup_session,
            "send_session_message": lifecycle.send_message,
            "send_session_message_stream": lifecycle.send_message_stream,
            "schedule_remote_chat_cleanup": lifecycle.schedule_cleanup,
            "schedule_remote_chat_cleanup_from_response": lifecycle.schedule_cleanup_from_response,
        }.items():
            monkeypatch.setattr(module, name, value)
    return primary._build_chat_service(), compact._build_chat_service()


@pytest.mark.parametrize("first_stream,second_stream", [(False, False), (False, True), (True, False), (True, True)])
def test_cross_adapter_task_workflow_serializes_goal_readback_and_cleanup(monkeypatch, first_stream, second_stream):
    _service_unused, client, manager, sessions, lifecycle = _service(status=3)
    first_service, second_service = _adapter_services(monkeypatch, client, lifecycle)

    async def run():
        first_read, release_first = asyncio.Event(), asyncio.Event()
        second_attempted, second_started, release_second = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_batch, original_generate, original_stream = client._batch_execute, client.generate_content, client.generate_content_stream

        async def delayed_goal(requests, **kwargs):
            result = await original_batch(requests, **kwargs)
            if requests[0].rpcid == get_contract("tasks.get").rpc_id and len(client.reads) == 1:
                first_read.set()  # A valid DONE response is captured but not delivered.
                await release_first.wait()
            return result

        async def generate(**kwargs):
            second = kwargs["prompt"] == "Second Task fixture"
            if second:
                client.status = 2
            result = await original_generate(**kwargs)
            if second:
                second_started.set()
                await release_second.wait()
            return result

        async def stream(**kwargs):
            second = kwargs["prompt"] == "Second Task fixture"
            if second:
                client.status = 2
            async for result in original_stream(**kwargs):
                if second and not second_started.is_set():
                    second_started.set()
                    await release_second.wait()
                yield result

        client._batch_execute, client.generate_content, client.generate_content_stream = delayed_goal, generate, stream
        started = await first_service.start_session(StartSessionRequest(task_mode=True, delete_after_seconds=0))
        state = sessions.get_session(started.data.session_id)
        first_sender = first_service.send_session_stream if first_stream else first_service.send_session
        second_sender = second_service.send_session_stream if second_stream else second_service.send_session
        first = asyncio.create_task(first_sender(SessionMessageRequest(session_id=state.session_id, message="First Task fixture")))

        async def second_request():
            second_attempted.set()
            return await second_sender(SessionMessageRequest(session_id=state.session_id, message="Second Task fixture"))

        second = None
        try:
            await asyncio.wait_for(first_read.wait(), timeout=2)
            second = asyncio.create_task(second_request())
            await asyncio.wait_for(second_attempted.wait(), timeout=2)
            overlapped_goal = second_started.is_set()
            release_first.set()
            first_result = await asyncio.wait_for(first, timeout=2)
            await asyncio.wait_for(second_started.wait(), timeout=2)
            # The second generation remains blocked. An old DONE read must not
            # have cleared its protection or left an executable delete behind.
            deleted_during_second = await manager.cleanup_due_chats(client=client)
            protected_during_second = state.task_pending
            release_second.set()
            second_result = await asyncio.wait_for(second, timeout=2)
        finally:
            release_first.set()
            release_second.set()
            await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
        assert deleted_during_second == 0
        assert protected_during_second is True
        assert overlapped_goal is False
        assert first_result.meta.operation_state is OperationState.COMPLETED
        assert second_result.meta.operation_state is OperationState.RUNNING
        assert state.task_pending is True

    asyncio.run(run())
    assert len(client.calls) == 2
    assert manager.list_pending_cleanup() == {}
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
def test_completed_task_can_switch_off_and_next_omitted_mode_stays_ordinary(monkeypatch, streaming):
    _service_unused, client, _manager, sessions, lifecycle = _service(status=3)
    first_service, second_service = _adapter_services(monkeypatch, client, lifecycle)
    ordinary_requests = []

    async def ordinary(**kwargs):
        ordinary_requests.append((kwargs["prompt"], client.scope_active, dict(kwargs)))
        return SimpleNamespace(text="Ordinary response fixture", metadata=["c_task_fixture"])

    async def ordinary_stream(**kwargs):
        yield await ordinary(**kwargs)

    async def run():
        started = await first_service.start_session(StartSessionRequest(task_mode=True, delete_after_seconds=60))
        state = sessions.get_session(started.data.session_id)
        completed = await first_service.send_session(SessionMessageRequest(session_id=state.session_id, message="Task fixture"))
        assert completed.meta.operation_state is OperationState.COMPLETED and state.task_pending is False
        client.generate_content, client.generate_content_stream = ordinary, ordinary_stream
        second_sender = second_service.send_session_stream if streaming else second_service.send_session
        third_sender = first_service.send_session_stream if streaming else first_service.send_session
        switched = await second_sender(SessionMessageRequest(session_id=state.session_id, message="Disable Task fixture", task_mode=False))
        inherited = await third_sender(SessionMessageRequest(session_id=state.session_id, message="Omitted mode fixture"))
        assert switched.ok and inherited.ok
        assert state.task_mode is False
        assert switched.data.task_mode is inherited.data.task_mode is False
        assert switched.data.task_status is inherited.data.task_status is None

    asyncio.run(run())
    assert [prompt for prompt, _scope, _kwargs in ordinary_requests] == ["Disable Task fixture", "Omitted mode fixture"]
    assert all(not scope and "task_mode" not in kwargs for _prompt, scope, kwargs in ordinary_requests)
    assert client.reads == [["c_task_fixture"]]  # Only the completed Task needed a goal read.
