"""One-shot and session HTTP and SDK timeout budgets, without network access."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import APIError

from src.domain import CleanupObservation, CleanupState
from src.services import ChatRequest, ChatService, ChatServiceDependencies
from src.services import chat as chat_module
from src.thinking_client import ThinkingLevelGeminiClient


@pytest.mark.parametrize("stream", [False, True])
def test_real_sdk_chat_waits_past_old_watchdog_and_never_replays(monkeypatch, stream):
    """A slow first metadata frame remains observable and a failure dispatches once."""
    monkeypatch.setattr(chat_module, "CHAT_GENERATION_TIMEOUT_SECONDS", 1)
    inner = json.dumps([None, ["c_synthetic", "r_synthetic"]], separators=(",", ":"))
    body = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(body) + 1) + "\n" + body).encode()
    scheduled = []

    class Response:
        status_code = 200
        http_version = 2
        async def aiter_content(self):
            await asyncio.sleep(0.05)
            yield b" "
            yield wire
            raise APIError("synthetic stream failure")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return False

    class Transport:
        requests = 0
        def stream(self, *_args, **kwargs):
            self.requests += 1
            assert kwargs["timeout"] == 1
            assert (client.timeout, client.watchdog_timeout) == (1, 1)
            return Response()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(GeminiClient, "close", AsyncMock())
    monkeypatch.setattr(GeminiClient, "fetch_latest_chat_response", AsyncMock(return_value=None))
    client = ThinkingLevelGeminiClient()
    from gemini_webapi.types import AvailableModel
    client._model_registry = {"flash": AvailableModel(model_id="flash", model_name="gemini-flash", display_name="Flash", description="", capacity=1)}
    client.client = transport = Transport()
    client._running = True
    client.timeout = client.watchdog_timeout = 0.01
    def schedule(cid, **kwargs):
        scheduled.append((cid, kwargs))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_kw: None, lookup_session=lambda *_a, **_kw: None,
        send_session_message=AsyncMock(), send_session_message_stream=AsyncMock(),
        schedule_response_cleanup=lambda *_a, **_kw: None, schedule_chat_cleanup=schedule,
        normalize_model=lambda value: value, resolve_model=lambda _value: None,
        account_key_provider=lambda: "synthetic-account",
    ))

    with pytest.raises(APIError):
        asyncio.run((service.generate_stream if stream else service.generate)(ChatRequest(message="synthetic")))
    assert transport.requests == 1
    assert scheduled and all(cid == "c_synthetic" for cid, _ in scheduled)
    assert (client.timeout, client.watchdog_timeout) == (0.01, 0.01)


@pytest.mark.parametrize("stream", [False, True])
def test_timeout_lease_ends_before_cleanup(stream):
    class Client:
        timeout = 30
        watchdog_timeout = 40
        async def generate_content(self, **kwargs):
            assert kwargs["timeout"] == 300
            assert (self.timeout, self.watchdog_timeout) == (300, 300)
            return SimpleNamespace(text="answer", metadata=["c_synthetic"])
        async def generate_content_stream(self, **kwargs):
            yield await self.generate_content(**kwargs)

    client = Client()
    async def finalize(response, **_kwargs):
        assert (client.timeout, client.watchdog_timeout) == (30, 40)
        return CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=response.metadata[0])
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_kw: None, lookup_session=lambda *_a, **_kw: None,
        send_session_message=AsyncMock(), send_session_message_stream=AsyncMock(),
        schedule_response_cleanup=lambda *_a, **_kw: None, schedule_chat_cleanup=lambda *_a, **_kw: None,
        normalize_model=lambda value: value, resolve_model=lambda value: value,
        finalize_response_cleanup=finalize, account_key_provider=lambda: "synthetic-account",
    ))
    result = asyncio.run((service.generate_stream if stream else service.generate)(ChatRequest(message="synthetic")))
    assert result.ok and result.data.lifecycle.cleanup.state is CleanupState.COMPLETED


@pytest.mark.parametrize("stream", [False, True])
def test_total_deadline_rejects_late_result_despite_heartbeats_and_suppressed_cancel(monkeypatch, stream):
    from src.services.cleanup_scope import observe_upstream_identity

    monkeypatch.setattr(chat_module, "CHAT_GENERATION_TIMEOUT_SECONDS", 0.05)
    scheduled = []
    async def run():
        release = asyncio.Event()
        late_done = asyncio.Event()
        class Client:
            timeout = 0.01
            watchdog_timeout = 0.01
            calls = 0
            async def generate_content(self, **_kwargs):
                self.calls += 1
                observe_upstream_identity(SimpleNamespace(cid="c_synthetic"))
                try:
                    while True:
                        await asyncio.sleep(0.005)
                except asyncio.CancelledError:
                    await release.wait()
                    late_done.set()
                    return SimpleNamespace(text="late result", metadata=["c_synthetic"])
            async def generate_content_stream(self, **_kwargs):
                self.calls += 1
                yield SimpleNamespace(text="heartbeat", text_delta="heartbeat", metadata=["c_synthetic"])
                try:
                    while True:
                        await asyncio.sleep(0.005)
                        yield SimpleNamespace(text="heartbeat", text_delta="", metadata=["c_synthetic"])
                except asyncio.CancelledError:
                    await release.wait()
                    late_done.set()
                    yield SimpleNamespace(text="late result", metadata=["c_synthetic"])
        client = Client()
        def schedule(cid, **kwargs):
            scheduled.append((cid, kwargs))
            return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)
        finalize = AsyncMock()
        service = ChatService(ChatServiceDependencies(
            client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
            create_session=lambda *_a, **_kw: None, lookup_session=lambda *_a, **_kw: None,
            send_session_message=AsyncMock(), send_session_message_stream=AsyncMock(),
            schedule_response_cleanup=lambda *_a, **_kw: None, schedule_chat_cleanup=schedule,
            normalize_model=lambda value: value, resolve_model=lambda value: value,
            finalize_response_cleanup=finalize, account_key_provider=lambda: "synthetic-account",
        ))
        with pytest.raises(TimeoutError):
            await (service.generate_stream if stream else service.generate)(ChatRequest(message="synthetic"))
        assert client.calls == 1 and (client.timeout, client.watchdog_timeout) == (0.01, 0.01)
        assert scheduled and all(cid == "c_synthetic" for cid, _ in scheduled)
        finalize.assert_not_awaited()
        release.set()
        await asyncio.wait_for(late_done.wait(), 1)
        await asyncio.sleep(0)
        finalize.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_session_send_uses_and_restores_timeout_lease(stream, failure):
    from src.session_manager import SessionData, SessionOperationResult
    from src.services.chat import SessionMessageRequest

    client = SimpleNamespace(timeout=30, watchdog_timeout=40)
    session = SimpleNamespace(geminiclient=client, cid="c_synthetic")
    state = SessionData(session=session, session_id="sess_synthetic", account_key="synthetic-account")
    response = SimpleNamespace(text="answer", metadata=["c_synthetic"])
    calls = []

    async def send(_session_id, **kwargs):
        calls.append(kwargs)
        assert kwargs["timeout"] == 300
        assert (client.timeout, client.watchdog_timeout) == (300, 300)
        if failure:
            raise TimeoutError("synthetic timeout")
        return SessionOperationResult.success(state, [response] if stream else response)

    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_kw: None, lookup_session=lambda *_a, **_kw: SessionOperationResult.success(state),
        send_session_message=send, send_session_message_stream=send,
        schedule_response_cleanup=lambda *_a, **_kw: None,
        schedule_chat_cleanup=lambda cid, **_kw: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        normalize_model=lambda value: value, resolve_model=lambda value: value,
        account_key_provider=lambda: "synthetic-account",
    ))
    method = service.send_session_stream if stream else service.send_session
    if failure:
        result = asyncio.run(method(SessionMessageRequest(session_id=state.session_id, message="synthetic")))
        assert not result.ok and result.meta.verification_status == "session_reply_unverified"
    else:
        assert asyncio.run(method(SessionMessageRequest(session_id=state.session_id, message="synthetic"))).ok
    assert len(calls) == 1 and (client.timeout, client.watchdog_timeout) == (30, 40)


@pytest.mark.parametrize("stream", [False, True])
def test_real_sdk_session_uses_timeout_budget_and_never_replays(monkeypatch, stream):
    from src.services.chat import SessionMessageRequest
    from src.session_manager import SessionService

    monkeypatch.setattr(chat_module, "CHAT_GENERATION_TIMEOUT_SECONDS", 1)
    inner = json.dumps([None, ["c_session", "r_session"]], separators=(",", ":"))
    frame = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(frame) + 1) + "\n" + frame).encode()

    class Response:
        status_code = 200
        http_version = 2
        async def aiter_content(self):
            await asyncio.sleep(0.05)
            yield b" "
            yield wire
            raise APIError("synthetic")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return False

    class Transport:
        requests = 0
        def stream(self, *_args, **kwargs):
            self.requests += 1
            assert kwargs["timeout"] == 1
            assert (client.timeout, client.watchdog_timeout) == (1, 1)
            return Response()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    monkeypatch.setattr(GeminiClient, "close", AsyncMock())
    client = ThinkingLevelGeminiClient()
    from gemini_webapi.types import AvailableModel
    client._model_registry = {"flash": AvailableModel(model_id="flash", model_name="gemini-flash", display_name="Flash", description="", capacity=1)}
    client.client = transport = Transport()
    client._running = True
    client.timeout = client.watchdog_timeout = 0.01
    sessions = SessionService()
    sessions.store_session("sess_test", client.start_chat(), model="flash")
    state = sessions.lookup_session("sess_test").session
    state.account_key = "synthetic-account"
    scheduled = []

    def schedule(cid, **kwargs):
        scheduled.append((cid, kwargs))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)

    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=sessions.create_session, lookup_session=sessions.lookup_session,
        send_session_message=sessions.send_message, send_session_message_stream=sessions.send_message_stream,
        schedule_response_cleanup=lambda *_a, **_kw: None, schedule_chat_cleanup=schedule,
        normalize_model=lambda value: value, resolve_model=lambda value: value,
        account_key_provider=lambda: "synthetic-account",
    ))
    recovery = AsyncMock(return_value=None)
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", recovery)
    result = asyncio.run((service.send_session_stream if stream else service.send_session)(
        SessionMessageRequest(session_id="sess_test", message="synthetic")))
    assert not result.ok and result.meta.verification_status == "session_reply_unverified"
    assert state.pending_turn == ("c_session", "r_session", None)
    recovery.assert_awaited_once_with(client, "c_session", "r_session", None)
    assert transport.requests == 1 and scheduled
    assert (client.timeout, client.watchdog_timeout) == (0.01, 0.01)
