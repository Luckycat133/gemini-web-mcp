"""Uncertain ordinary submissions retain a source without replaying the prompt."""

import asyncio
import json
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi import GeminiClient

import src.session_manager as session_module
import src.skill_server as compact
import src.surfaces.assist as assist
import src.tools.chat as primary
from src.adapters.mcp_sdk import MCPServer
from src.domain import CleanupState, OperationState
from src.infrastructure.state_store import StateStore, authentication_scope
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.chat import ChatRequest, ChatService, ChatServiceDependencies, SessionMessageRequest, StartSessionRequest
from src.services.lifecycle import ConversationLifecycleService
from src.services.understanding import UnderstandInputKind
from src.session_manager import SessionService
from src.thinking_client import ThinkingLevelGeminiClient, _generation_once


CID = "c_ordinary_fixture"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    client = object.__new__(ThinkingLevelGeminiClient)
    calls = []
    control = SimpleNamespace(fail=True, partial=False, entered=None, release=None)
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(ThinkingLevelGeminiClient, "delete_chat", delete)

    async def submit(_client, *, chat=None, current_retry=5, **kwargs):
        calls.append({"chat": chat, "current_retry": current_retry, **kwargs})
        backup = chat.metadata if chat is not None else None
        if chat is not None:
            chat.metadata = [CID, "r_fixture", "rc_fixture"]
        if control.entered is not None:
            control.entered.set()
            await control.release.wait()
        if control.fail:
            if chat is not None:
                chat.metadata = backup  # The SDK rolls mutable metadata back.
            raise TimeoutError("Private provider timeout fixture")
        return SimpleNamespace(text="Answer fixture", metadata=[CID], images=[], videos=[], media=[])

    async def submit_stream(_client, *, chat=None, current_retry=5, **kwargs):
        # Keep the real ThinkingClient wrapper and owned metadata observer.
        should_fail = control.fail
        if control.partial:
            control.fail = False
        try:
            result = await submit(_client, chat=chat, current_retry=current_retry, **kwargs)
        finally:
            control.fail = should_fail
        yield result
        if should_fail:
            chat.metadata = ["", "", ""]
            raise TimeoutError("Private provider timeout fixture")

    monkeypatch.setattr(GeminiClient, "generate_content", submit)
    monkeypatch.setattr(GeminiClient, "generate_content_stream", submit_stream)
    sessions = SessionService(max_age=1, id_factory=lambda: "sess_ordinary_fixture")
    store = StateStore(tmp_path / "owned-submission.sqlite3")
    scope = authentication_scope("fixture-account", store=store)
    cleanup = RemoteChatCleanupManager(state_store=store)
    cleanup.bind_authentication_scope(scope)
    lifecycle = ConversationLifecycleService(lambda: sessions, lambda: cleanup)
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(),
        cleanup_due_remote_chats=AsyncMock(return_value=0),
        create_session=lifecycle.create_session, lookup_session=lifecycle.lookup_session,
        send_session_message=lifecycle.send_message, send_session_message_stream=lifecycle.send_message_stream,
        schedule_response_cleanup=lifecycle.schedule_cleanup_from_response,
        schedule_chat_cleanup=lifecycle.schedule_cleanup,
        normalize_model=lambda model: model, resolve_model=lambda _model: "gemini-3-flash",
    ))
    return SimpleNamespace(client=client, calls=calls, control=control, delete=delete, sessions=sessions,
                           cleanup=cleanup, lifecycle=lifecycle, service=service, store=store, scope=scope)


def _assert_retained(result, runtime):
    assert result.ok is False
    assert result.meta.operation_state is OperationState.TIMED_OUT
    assert result.retryable is False
    assert result.data.remote_chat_id == CID
    assert result.data.lifecycle.upstream_chat_id == CID
    assert result.data.lifecycle.retain_chat is True
    assert result.data.lifecycle.cleanup.state is CleanupState.RETAINED
    assert result.data.task_mode is False and result.data.task_status is None
    assert result.warnings[0].code == "SOURCE_RETAINED"
    assert "Private provider" not in json.dumps(result.to_dict())
    assert "Private prompt" not in json.dumps(result.to_dict())
    assert "task_status" not in result.meta.details
    assert runtime.cleanup.list_pending_cleanup() == {}
    assert runtime.cleanup.get_cleanup_observation(CID).state is CleanupState.RETAINED
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["current_retry"] == 0
    assert runtime.calls[0]["chat"].cid == ""
    assert runtime.calls[0]["chat"].observed_chat_id == CID
    assert _generation_once.get() is None
    runtime.delete.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
def test_ordinary_one_shot_failure_preserves_owned_source_after_sdk_rollback(runtime, streaming):
    sender = runtime.service.generate_stream if streaming else runtime.service.generate
    result = asyncio.run(sender(ChatRequest(message="Private prompt fixture", delete_after_seconds=0)))
    _assert_retained(result, runtime)


def test_failed_stream_retains_source_and_reports_partial_stream_evidence(runtime):
    runtime.control.partial = True
    result = asyncio.run(runtime.service.generate_stream(ChatRequest(message="Private prompt fixture")))
    _assert_retained(result, runtime)
    assert result.data.stream.chunk_count == 1
    assert result.data.stream.text_length == len("Answer fixture")
    assert "Answer fixture" not in json.dumps(result.to_dict())


def test_uncertain_submission_retention_survives_cleanup_manager_restart(runtime):
    runtime.cleanup.schedule_cleanup(CID, delete_after_seconds=0)
    failed = asyncio.run(runtime.service.generate(ChatRequest(message="Private prompt fixture")))
    _assert_retained(failed, runtime)
    restarted = RemoteChatCleanupManager(state_store=StateStore(runtime.store.path))
    restarted.bind_authentication_scope(runtime.scope)
    assert asyncio.run(restarted.cleanup_due_chats(client=runtime.client)) == 0
    assert restarted.get_cleanup_observation(CID).state is CleanupState.RETAINED
    assert runtime.store.cleanup_jobs.get(runtime.scope, CID).state == "retained"
    assert "Private prompt" not in runtime.store.path.read_bytes().decode("utf-8", errors="ignore")
    runtime.delete.assert_not_awaited()


@pytest.mark.parametrize("session", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_cancellation_retains_observed_ordinary_source_without_replaying(runtime, session, streaming):
    async def run():
        runtime.control.entered, runtime.control.release = asyncio.Event(), asyncio.Event()
        if session:
            started = await runtime.service.start_session(StartSessionRequest())
            sender = runtime.service.send_session_stream if streaming else runtime.service.send_session
            request = SessionMessageRequest(session_id=started.data.session_id, message="Private prompt fixture")
        else:
            sender = runtime.service.generate_stream if streaming else runtime.service.generate
            request = ChatRequest(message="Private prompt fixture")
        task = asyncio.create_task(sender(request))
        await asyncio.wait_for(runtime.control.entered.wait(), timeout=1)
        assert runtime.cleanup.get_cleanup_observation(CID).state is CleanupState.RETAINED
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        if session:
            state = runtime.sessions.get_session(started.data.session_id)
            assert state.upstream_chat_id == CID and state.recovery_pending is True
            assert state.task_pending is False
        assert await runtime.cleanup.cleanup_due_chats(client=runtime.client) == 0

    asyncio.run(run())
    assert len(runtime.calls) == 1 and runtime.calls[0]["current_retry"] == 0
    assert runtime.cleanup.list_pending_cleanup() == {}
    runtime.delete.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
def test_bare_client_without_owned_factory_remains_compatible_and_reports_unobserved_source(runtime, streaming):
    calls = []

    async def fail(**kwargs):
        assert _generation_once.get() is client
        calls.append(kwargs)
        raise TimeoutError("Private provider timeout fixture")

    async def fail_stream(**kwargs):
        await fail(**kwargs)
        yield  # pragma: no cover - only the async generator protocol is needed.

    client = SimpleNamespace(generate_content=fail, generate_content_stream=fail_stream)
    runtime.service._dependencies = replace(runtime.service._dependencies, client_provider=lambda: client)
    sender = runtime.service.generate_stream if streaming else runtime.service.generate
    result = asyncio.run(sender(ChatRequest(message="Private prompt fixture")))
    assert result.error_code == "TIMED_OUT" and result.retryable is False
    assert result.data.remote_chat_id is None
    assert result.data.task_mode is False and result.data.task_status is None
    assert result.warnings[0].code == "SOURCE_UNOBSERVED"
    assert len(calls) == 1 and "chat" not in calls[0]
    assert runtime.cleanup.list_pending_cleanup() == {}


@pytest.mark.parametrize("streaming", [False, True])
def test_ordinary_owned_success_uses_observed_source_for_requested_cleanup(runtime, streaming):
    runtime.control.fail = False
    sender = runtime.service.generate_stream if streaming else runtime.service.generate
    result = asyncio.run(sender(ChatRequest(message="Private prompt fixture", delete_after_seconds=3600)))
    assert result.ok is True and result.meta.operation_state is OperationState.COMPLETED
    assert result.data.remote_chat_id == CID
    assert result.data.lifecycle.retain_chat is False
    assert result.data.lifecycle.cleanup.state is CleanupState.PENDING
    assert result.data.task_status is None
    assert len(runtime.calls) == 1 and runtime.calls[0]["current_retry"] == 0
    assert set(runtime.cleanup.list_pending_cleanup()) == {CID}


@pytest.mark.parametrize("streaming", [False, True])
def test_preparing_another_client_does_not_reenable_retries_for_existing_session_owner(runtime, streaming):
    state = runtime.sessions.store_session("sess_ordinary_fixture", runtime.client.start_owned_chat(model="gemini-3-flash"))
    state.session.cid = CID
    other_client = object.__new__(ThinkingLevelGeminiClient)
    runtime.service._dependencies = replace(runtime.service._dependencies, client_provider=lambda: other_client)
    sender = runtime.service.send_session_stream if streaming else runtime.service.send_session
    result = asyncio.run(sender(SessionMessageRequest(
        session_id=state.session_id, message="Private prompt fixture", prepare_client=True,
    )))
    assert result.ok is False and result.error_code == "TIMED_OUT"
    assert result.data.remote_chat_id == CID
    assert state.recovery_pending is True
    assert len(runtime.calls) == 1 and runtime.calls[0]["current_retry"] == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_ordinary_session_failure_is_single_submission_and_preserves_owned_source(runtime, streaming):
    async def run():
        started = await runtime.service.start_session(StartSessionRequest(gem_id="gem_fixture"))
        sender = runtime.service.send_session_stream if streaming else runtime.service.send_session
        return await sender(SessionMessageRequest(session_id=started.data.session_id, message="Private prompt fixture"))

    result = asyncio.run(run())
    _assert_retained(result, runtime)
    state = runtime.sessions.get_session(result.data.session_id)
    assert state.upstream_chat_id == CID and state.recovery_pending is True
    assert state.retain_chat is False and state.task_pending is False
    assert state.session.gem == "gem_fixture"
    assert "recovery_pending" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("detach", ["reset_one", "reset_all", "expire"])
def test_failed_ordinary_session_source_survives_reset_or_expiry(runtime, monkeypatch, streaming, detach):
    now = [1000.0]
    monkeypatch.setattr(session_module, "time", SimpleNamespace(time=lambda: now[0]))
    delete = AsyncMock(return_value=True)

    async def run():
        started = await runtime.service.start_session(StartSessionRequest())
        sender = runtime.service.send_session_stream if streaming else runtime.service.send_session
        failed = await sender(SessionMessageRequest(session_id=started.data.session_id, message="Private prompt fixture"))
        assert failed.data.remote_chat_id == CID
        if detach == "reset_one":
            await runtime.lifecycle.reset_one(started.data.session_id, delete_callback=delete)
        elif detach == "reset_all":
            await runtime.lifecycle.reset_all(delete_callback=delete)
        else:
            now[0] += 2
            runtime.lifecycle.cleanup_expired_sessions()
        assert await runtime.cleanup.cleanup_due_chats(client=runtime.client) == 0

    asyncio.run(run())
    assert runtime.sessions.list_sessions() == {}
    assert runtime.cleanup.get_cleanup_observation(CID).state is CleanupState.RETAINED
    assert runtime.cleanup.list_pending_cleanup() == {}
    delete.assert_not_awaited()
    runtime.delete.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
def test_successful_follow_up_clears_recovery_guard_and_restores_original_policy(runtime, streaming):
    async def run():
        started = await runtime.service.start_session(StartSessionRequest(delete_after_seconds=3600))
        sender = runtime.service.send_session_stream if streaming else runtime.service.send_session
        request = SessionMessageRequest(session_id=started.data.session_id, message="Private prompt fixture")
        failed = await sender(request)
        assert failed.data.remote_chat_id == CID
        runtime.control.fail = False
        result = await sender(request)
        assert result.ok is True
        assert result.data.session_state.recovery_pending is False
        assert result.data.lifecycle.retain_chat is False
        assert result.data.lifecycle.cleanup.state is CleanupState.PENDING
        assert result.data.task_mode is False and result.data.task_status is None
        assert result.data.remote_chat_id == CID

    asyncio.run(run())
    assert len(runtime.calls) == 2
    assert all(call["current_retry"] == 0 for call in runtime.calls)
    assert runtime.calls[0]["chat"] is runtime.calls[1]["chat"]
    assert set(runtime.cleanup.list_pending_cleanup()) == {CID}
    runtime.delete.assert_not_awaited()


def _bind_adapter(monkeypatch, module, runtime):
    for name, value in {
        "get_gemini_client": lambda: runtime.client,
        "initialize_client": AsyncMock(),
        "cleanup_due_remote_chats": AsyncMock(return_value=0),
        "create_session": runtime.lifecycle.create_session,
        "lookup_session": runtime.lifecycle.lookup_session,
        "send_session_message": runtime.lifecycle.send_message,
        "send_session_message_stream": runtime.lifecycle.send_message_stream,
        "schedule_remote_chat_cleanup": runtime.lifecycle.schedule_cleanup,
        "schedule_remote_chat_cleanup_from_response": runtime.lifecycle.schedule_cleanup_from_response,
    }.items():
        monkeypatch.setattr(module, name, value)


@pytest.mark.parametrize("surface", ["primary", "compact", "ask", "search", "understand_image", "understand"])
def test_real_surface_failure_outputs_keep_source_lifecycle(monkeypatch, runtime, tmp_path, surface):
    module = primary if surface == "primary" else compact if surface == "compact" else assist
    _bind_adapter(monkeypatch, module, runtime)

    async def run():
        if surface == "primary":
            server = MCPServer("ordinary-failure-fixture")
            primary.register_chat_tools(server)
            response = await server.call_tool("gemini_chat", {"message": "Private prompt fixture"})
            return response.content
        if surface == "compact":
            return await compact.chat(message="Private prompt fixture")
        if surface == "ask":
            return await assist.gemini_ask(prompt="Private prompt fixture")
        if surface == "search":
            return await assist.gemini_search(query="Search fixture")
        if surface == "understand_image":
            return await assist.gemini_understand_image(image="https://example.com/fixture.png", task="Image fixture")
        note = tmp_path / "note.txt"
        note.write_text("Input fixture", encoding="utf-8")
        return await assist.gemini_understand(task="Mixed fixture", inputs=[
            assist.UnderstandInput(id="note", kind=UnderstandInputKind.FILE, path=str(note)),
            assist.UnderstandInput(id="inline", kind=UnderstandInputKind.TEXT, text="Inline fixture"),
            assist.UnderstandInput(id="url", kind=UnderstandInputKind.URL, url="https://example.com/"),
        ])

    contents = asyncio.run(run())
    payload = contents[0].meta["domain_result"]
    assert payload["ok"] is False and payload["error"]["code"] == "TIMED_OUT"
    assert payload["error"]["retryable"] is False
    assert payload["data"]["lifecycle"]["upstream_chat_id"] == CID
    assert payload["data"]["lifecycle"]["cleanup"]["state"] == "retained"
    assert payload["meta"]["details"]["lifecycle"]["upstream_chat_id"] == CID
    assert payload["warnings"][0]["code"] == "SOURCE_RETAINED"
    assert "task_status" not in payload["data"] and "task_status" not in payload["meta"]["details"]
    assert "Private prompt" not in json.dumps(payload)
    assert len(runtime.calls) == 1 and runtime.calls[0]["current_retry"] == 0
    assert runtime.cleanup.list_pending_cleanup() == {}
    runtime.delete.assert_not_awaited()
