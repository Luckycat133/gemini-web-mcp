"""Real SDK transport regressions with synthetic frames and no network access."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from curl_cffi.requests.exceptions import ReadTimeout
from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import TimeoutError as SDKTimeoutError
from gemini_webapi.types import AvailableModel, Candidate, ModelOutput

from src.domain import CleanupObservation, CleanupState
from src.services.chat import ChatService, ChatServiceDependencies, SessionMessageRequest
from src.session_manager import SessionService
from src.thinking_client import ThinkingLevelGeminiClient, strict_session_recovery_scope


class Response:
    status_code = 200
    http_version = 2

    def __init__(self, *, wire=None, timeout=False):
        self.wire, self.timeout = wire, timeout

    async def aiter_content(self):
        if self.timeout:
            raise ReadTimeout("Synthetic timeout before any response frame")
        if self.wire:
            yield self.wire

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class Transport:
    def __init__(self, response):
        self.response, self.requests = response, 0

    def stream(self, *_args, **_kwargs):
        self.requests += 1
        return self.response


@pytest.fixture(autouse=True)
def offline_sdk(monkeypatch):
    policy = SimpleNamespace(clock=time.time, flash_exhausted=lambda *_args: False,
                             clear_flash_exhausted=lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.thinking_client.ModelPolicyStore", lambda: policy)
    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    monkeypatch.setattr(GeminiClient, "_fetch_usage_info", AsyncMock())
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda _client: True)


def _client(response):
    client = ThinkingLevelGeminiClient()
    client._model_registry = {"flash": AvailableModel(model_id="flash", model_name="gemini-flash",
        display_name="Flash", description="", capacity=1)}
    client.client = Transport(response)
    client._running = True
    return client


def _service(client, sessions):
    return ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_args, **_kwargs: None, lookup_session=sessions.lookup_session,
        send_session_message=sessions.send_message, send_session_message_stream=sessions.send_message_stream,
        schedule_response_cleanup=lambda *_args, **_kwargs: None,
        schedule_chat_cleanup=lambda cid, **_kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        normalize_model=lambda value: value, resolve_model=lambda value: value, account_key_provider=lambda: "account",
    ))


@pytest.mark.parametrize("stream", [False, True])
def test_real_sdk_timeout_before_cid_blocks_another_send_until_explicit_reset(stream):
    client = _client(Response(timeout=True))
    sessions = SessionService()
    state = sessions.store_session("sess_new", client.start_chat(model="flash"))
    state.account_key = "account"
    service = _service(client, sessions)
    method = service.send_session_stream if stream else service.send_session
    async def run():
        first = await method(SessionMessageRequest(session_id="sess_new", message="synthetic request"))
        second = await method(SessionMessageRequest(session_id="sess_new", message="never regenerate"))
        read = await method(SessionMessageRequest(session_id="sess_new", message="", recover_only=True))
        assert not first.ok and not second.ok and not read.ok
        assert state.pending_turn == ("", "", None) and client.client.requests == 1
        sessions.reset_one("sess_new")
        fresh = sessions.store_session("sess_reset", client.start_chat(model="flash"))
        fresh.account_key = "account"
        await method(SessionMessageRequest(session_id="sess_reset", message="explicit new session"))
        assert client.client.requests == 2
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_real_sdk_empty_eof_without_cid_is_unknown_and_never_regenerated(monkeypatch, stream):
    client = _client(Response())
    # Avoid the SDK's real cookie-cache close path; the generation and empty
    # stream handling themselves still run through the installed SDK.
    monkeypatch.setattr(GeminiClient, "close", AsyncMock())
    sessions = SessionService()
    state = sessions.store_session("sess_empty", client.start_chat(model="flash"))
    state.account_key = "account"
    service = _service(client, sessions)
    method = service.send_session_stream if stream else service.send_session
    async def run():
        first = await method(SessionMessageRequest(session_id="sess_empty", message="synthetic request"))
        again = await method(SessionMessageRequest(session_id="sess_empty", message="must not regenerate"))
        assert not first.ok and not again.ok
        assert first.meta.verification_status == "session_reply_unverified"
        assert state.pending_turn == ("", "", None)
        assert client.client.requests == 1
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("ready", [False, True])
def test_real_sdk_latest_reply_recovery_cannot_replace_observed_turn(monkeypatch, stream, ready):
    inner = json.dumps([None, ["c_one", "r_expected", "rc_expected"]], separators=(",", ":"))
    frame = json.dumps([[None, None, inner]], separators=(",", ":"))
    client = _client(Response(wire=(str(len(frame) + 1) + "\n" + frame).encode()))
    sessions = SessionService()
    state = sessions.store_session("sess_one", client.start_chat(model="flash",
        metadata=["c_one", "r_old", "rc_old"]))
    state.account_key = "account"
    unrelated = ModelOutput(metadata=["c_one", "r_unrelated", "rc_unrelated"],
        candidates=[Candidate(rcid="rc_unrelated", text="UNRELATED SYNTHETIC REPLY")])
    native = AsyncMock(return_value=SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=unrelated)]))
    monkeypatch.setattr(GeminiClient, "read_chat", native)
    exact = AsyncMock(return_value={"chat_id": "c_one", "rid": "r_expected", "rcid": "rc_expected",
        "completed": ready, "text": "EXACT SYNTHETIC REPLY" if ready else None})
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", exact)
    service = _service(client, sessions)
    method = service.send_session_stream if stream else service.send_session
    result = asyncio.run(method(SessionMessageRequest(session_id="sess_one", message="synthetic original request")))
    assert client.client.requests == 1
    native.assert_not_awaited()
    exact.assert_awaited_once_with(client, "c_one", "r_expected", "rc_expected")
    assert result.ok is ready
    if ready:
        assert result.data.response.text == "EXACT SYNTHETIC REPLY"
        assert state.session.metadata[:3] == ["c_one", "r_expected", "rc_expected"]
        assert state.pending_turn is None
    else:
        assert state.pending_turn == ("c_one", "r_expected", "rc_expected")


def test_strict_session_scope_is_task_local_and_does_not_change_other_workflows(monkeypatch):
    native = AsyncMock(return_value="unscoped read")
    monkeypatch.setattr(GeminiClient, "read_chat", native)
    client = ThinkingLevelGeminiClient()
    async def run():
        strict_started, normal_finished = asyncio.Event(), asyncio.Event()
        async def strict():
            with strict_session_recovery_scope():
                strict_started.set()
                await normal_finished.wait()
                with pytest.raises(SDKTimeoutError):
                    await client.read_chat("c_fixture")
        async def normal():
            await strict_started.wait()
            assert await client.read_chat("c_fixture") == "unscoped read"
            normal_finished.set()
        await asyncio.gather(strict(), normal())
        assert await client.read_chat("c_fixture") == "unscoped read"
    asyncio.run(run())
    assert native.await_count == 2
