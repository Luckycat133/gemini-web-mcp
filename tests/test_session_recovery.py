"""An unverified session turn is recovered read-only, never regenerated."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi.exceptions import TimeoutError as SDKTimeoutError
from gemini_webapi.types import Candidate, ModelOutput, WebImage

from src.domain import CleanupObservation, CleanupState
from src.services.chat import ChatService, ChatServiceDependencies, SessionMessageRequest
from src.services.cleanup_scope import observe_upstream_identity
from src.session_manager import SessionData, SessionOperationResult


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("outcome", ["timeout", "blank"])
def test_unverified_reply_requires_exact_completed_turn_without_regeneration(monkeypatch, stream, ready, outcome):
    from src.services import web_context_transport

    client = SimpleNamespace(timeout=30, watchdog_timeout=30)
    session = SimpleNamespace(geminiclient=client, cid="", metadata=[])
    state = SessionData(session=session, session_id="sess_one", account_key="account")
    sends = []
    scheduled = []
    async def send(_sid, **_kwargs):
        sends.append(1)
        if outcome == "timeout":
            observe_upstream_identity(SimpleNamespace(cid="c_one", metadata=["c_one", "r_one", "rc_one"]))
            raise SDKTimeoutError("synthetic")
        reply = ModelOutput(metadata=["c_one", "r_one", "rc_one"], candidates=[
            Candidate(rcid="rc_one", text="  ", thoughts="synthetic internal content"),
        ])
        return SessionOperationResult.success(state, [reply] if stream else reply)
    lookup = AsyncMock(return_value={"chat_id": "c_one", "rid": "r_one", "rcid": "rc_one",
                                     "completed": ready, "text": "synthetic answer" if ready else None})
    monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", lookup)
    def schedule(cid, **_kwargs):
        scheduled.append(cid)
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_k: None, lookup_session=lambda *_a, **_k: SessionOperationResult.success(state),
        send_session_message=send, send_session_message_stream=send,
        schedule_response_cleanup=lambda *_a, **_k: None, schedule_chat_cleanup=schedule,
        normalize_model=lambda value: value, resolve_model=lambda value: value, account_key_provider=lambda: "account",
    ))
    method = service.send_session_stream if stream else service.send_session
    result = asyncio.run(method(SessionMessageRequest(session_id="sess_one", message="synthetic")))
    assert len(sends) == 1 and scheduled
    lookup.assert_awaited_once_with(client, "c_one", "r_one", "rc_one")
    if ready:
        assert result.ok and result.data.response.text == "synthetic answer"
        if stream:
            assert result.data.stream_text == "synthetic answer"
        assert result.meta.verification_status == "original_session_reply_verified"
        assert state.pending_turn is None and not state.unverified_chat_ids
        assert session.metadata == ["c_one", "r_one", "rc_one"]
    else:
        assert not result.ok and result.meta.verification_status == "session_reply_unverified"
        assert state.pending_turn == ("c_one", "r_one", "rc_one")
        blocked = asyncio.run(method(SessionMessageRequest(session_id="sess_one", message="do not resend")))
        assert not blocked.ok and len(sends) == 1
        lookup.return_value = {"chat_id": "c_one", "rid": "r_one", "rcid": "rc_one",
                               "completed": True, "text": "synthetic answer"}
        recovered = asyncio.run(method(SessionMessageRequest(session_id="sess_one", message="", recover_only=True)))
        assert recovered.ok and recovered.data.response.text == "synthetic answer" and len(sends) == 1
        if stream:
            assert recovered.data.stream_text == "synthetic answer"


@pytest.mark.parametrize("outcome", ["media", "earlier_text", "unrelated_text"])
def test_session_only_counts_selected_content_from_the_same_reply(monkeypatch, outcome):
    client = SimpleNamespace(timeout=30, watchdog_timeout=30)
    state = SessionData(session=SimpleNamespace(geminiclient=client, cid="", metadata=[]),
                        session_id="sess_one", account_key="account")
    metadata = ["c_one", "r_one", "rc_one"]
    image = WebImage(url="https://example.invalid/generated.png")
    terminal = ModelOutput(metadata=metadata, candidates=[
        Candidate(rcid="rc_unselected", text="unselected text"),
        Candidate(rcid="rc_one", text="", web_images=[image] if outcome == "media" else []),
    ], chosen=1)
    stream = outcome != "media"

    async def send(_sid, **_kwargs):
        observe_upstream_identity(terminal)
        if not stream:
            return SessionOperationResult.success(state, terminal)
        candidate_id = "rc_one" if outcome == "earlier_text" else "rc_unrelated"
        earlier = ModelOutput(metadata=metadata[:2], candidates=[Candidate(rcid=candidate_id, text="synthetic answer")])
        return SessionOperationResult.success(state, [earlier, terminal])

    read = AsyncMock(return_value=None)
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", read)
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_k: None, lookup_session=lambda *_a, **_k: SessionOperationResult.success(state),
        send_session_message=send, send_session_message_stream=send,
        schedule_response_cleanup=lambda *_a, **_k: None,
        schedule_chat_cleanup=lambda cid, **_k: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        normalize_model=lambda value: value, resolve_model=lambda value: value, account_key_provider=lambda: "account",
    ))
    method = service.send_session_stream if stream else service.send_session
    result = asyncio.run(method(SessionMessageRequest(session_id="sess_one", message="synthetic")))
    if outcome == "unrelated_text":
        assert not result.ok and result.meta.verification_status == "session_reply_unverified"
        assert state.pending_turn == tuple(metadata)
        read.assert_awaited_once_with(client, *metadata)
    else:
        assert result.ok and result.data.response is terminal
        assert state.pending_turn is None and not state.unverified_chat_ids
        read.assert_not_awaited()
        if stream:
            assert result.data.stream_text == "synthetic answer"
        else:
            assert result.data.response.images == [image]


def test_unverified_session_reset_keeps_durable_cleanup_pending():
    from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
    from src.services.lifecycle import ConversationLifecycleService
    from src.session_manager import SessionService

    sessions = SessionService()
    sessions.store_session("sess_one", SimpleNamespace(cid=""), model="flash")
    state = sessions.lookup_session("sess_one").session
    state.account_key = "account"
    state.unverified_chat_ids.add("c_one")
    manager = RemoteChatCleanupManager(account_key_provider=lambda: "account")
    lifecycle = ConversationLifecycleService(session_provider=lambda: sessions, cleanup_provider=lambda: manager)
    delete = AsyncMock()
    result = asyncio.run(lifecycle.reset_one("sess_one", delete_callback=delete))
    assert result.ok and result.meta.verification_status == "local_state_removed_cleanup_pending"
    assert result.meta.details["lifecycle"].cleanup.state is CleanupState.PENDING
    assert "c_one" in manager.list_pending_cleanup()
    delete.assert_not_awaited()


def test_new_session_turn_does_not_reuse_previous_candidate_id():
    state = SessionData(session=SimpleNamespace(), account_key="account")
    ChatService._remember_session_turn(
        state, SimpleNamespace(metadata=["c_one", "r_new", "rc_old"]), "c_one", ("c_one", "r_old", "rc_old"),
    )
    assert state.pending_turn == ("c_one", "r_new", None)
    ChatService._remember_session_turn(
        state, SimpleNamespace(metadata=["c_one", "r_new", "rc_new"]), "c_one", ("c_one", "r_old", "rc_old"),
    )
    assert state.pending_turn == ("c_one", "r_new", "rc_new")
    ChatService._remember_session_turn(
        state, SimpleNamespace(metadata=["c_one", "r_new", "rc_other"]), "c_one", ("c_one", "r_old", "rc_old"),
    )
    assert state.pending_turn == ("c_one", "r_new", "rc_new")


@pytest.mark.parametrize("outcome", ["timeout", "absent"])
def test_unverified_reply_without_new_identity_does_not_recover_previous_reply(monkeypatch, outcome):
    from src.services import web_context_transport

    client = SimpleNamespace(timeout=30, watchdog_timeout=30)
    session = SimpleNamespace(geminiclient=client, cid="c_one", metadata=["c_one", "r_old", "rc_old"])
    state = SessionData(session=session, session_id="sess_one", account_key="account")
    async def send(_sid, **_kwargs):
        if outcome == "timeout":
            raise SDKTimeoutError("synthetic")
        return SessionOperationResult.success(state)
    lookup = AsyncMock(side_effect=AssertionError("must not read previous reply"))
    monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", lookup)
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_k: None, lookup_session=lambda *_a, **_k: SessionOperationResult.success(state),
        send_session_message=send, send_session_message_stream=send,
        schedule_response_cleanup=lambda *_a, **_k: None,
        schedule_chat_cleanup=lambda cid, **_k: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        normalize_model=lambda value: value, resolve_model=lambda value: value, account_key_provider=lambda: "account",
    ))
    result = asyncio.run(service.send_session(SessionMessageRequest(session_id="sess_one", message="new question")))
    assert not result.ok and result.meta.verification_status == "session_reply_unverified"
    assert state.pending_turn == ("c_one", "", None)
    lookup.assert_not_awaited()


@pytest.mark.parametrize("change", ["other_chat", "other_rid", "other_candidate", "confirmation", "blank"])
def test_recovery_rejects_mismatched_or_unusable_reply(monkeypatch, change):
    from src.services import web_context_transport

    client = SimpleNamespace(timeout=30, watchdog_timeout=30)
    session = SimpleNamespace(geminiclient=client, metadata=[])
    state = SessionData(session=session, session_id="sess_one", account_key="account",
                        pending_turn=("c_one", "r_one", "rc_one"), unverified_chat_ids={"c_one"})
    result = {"chat_id": "c_one", "rid": "r_one", "rcid": "rc_one", "completed": True, "text": "answer"}
    if change == "other_chat":
        result["chat_id"] = "c_other"
    if change == "other_rid":
        result["rid"] = "r_other"
    if change == "other_candidate":
        result["rcid"] = "rc_other"
    if change == "confirmation":
        result["input_required"] = True
    if change == "blank":
        result["text"] = "  "
    monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", AsyncMock(return_value=result))
    send = AsyncMock(side_effect=AssertionError("must not send"))
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_k: None, lookup_session=lambda *_a, **_k: SessionOperationResult.success(state),
        send_session_message=send, send_session_message_stream=send, schedule_response_cleanup=lambda *_a, **_k: None,
        schedule_chat_cleanup=lambda *_a, **_k: None, normalize_model=lambda value: value,
        resolve_model=lambda value: value, account_key_provider=lambda: "account",
    ))
    observed = asyncio.run(service.send_session(SessionMessageRequest(session_id="sess_one", message="", recover_only=True)))
    assert not observed.ok and observed.meta.verification_status == "session_reply_unverified"
    assert state.pending_turn == ("c_one", "r_one", "rc_one")
    send.assert_not_awaited()


def test_recovery_fences_account_switch_during_read(monkeypatch):
    from src.services import web_context_transport

    account = ["original"]
    client = SimpleNamespace(timeout=30, watchdog_timeout=30)
    session = SimpleNamespace(geminiclient=client, metadata=[])
    state = SessionData(session=session, session_id="sess_one", account_key="original",
                        pending_turn=("c_one", "r_one", "rc_one"), unverified_chat_ids={"c_one"})
    async def read(*_args):
        account[0] = "other"
        return {"chat_id": "c_one", "rid": "r_one", "rcid": "rc_one", "completed": True, "text": "answer"}
    monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", read)
    schedule = []
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=lambda *_a, **_k: None, lookup_session=lambda *_a, **_k: SessionOperationResult.success(state),
        send_session_message=AsyncMock(), send_session_message_stream=AsyncMock(),
        schedule_response_cleanup=lambda *_a, **_k: None,
        schedule_chat_cleanup=lambda *args, **_k: schedule.append(args),
        normalize_model=lambda value: value, resolve_model=lambda value: value, account_key_provider=lambda: account[0],
    ))
    result = asyncio.run(service.send_session(SessionMessageRequest(session_id="sess_one", message="", recover_only=True)))
    assert not result.ok and result.meta.verification_status == "account_mismatch_or_unbound"
    assert state.pending_turn == ("c_one", "r_one", "rc_one") and session.metadata == []
    assert not schedule


def test_public_session_recover_only_omits_message_and_never_sends_prompt():
    from src.adapters.mcp_sdk import MCPServer
    from src.domain import DomainResult
    from src.services.chat import ChatOperationData
    from src.tools.chat import _register_conversation_tools

    service = SimpleNamespace(send_session=AsyncMock(return_value=DomainResult.success(ChatOperationData(
        requested_model="flash", normalized_model="flash", effective_model="flash",
        response=SimpleNamespace(text="recovered synthetic reply"),
    ))))
    server = MCPServer("session-recovery-test")
    _register_conversation_tools(server, service)
    async def run():
        return await server.call_tool("gemini_send_message", {"session_id": "sess_one", "recover_only": True})
    result = asyncio.run(run())
    assert not result.is_error
    assert "recovered synthetic reply" in result.content[0].text
    request = service.send_session.await_args.args[0]
    assert request.recover_only is True and request.message == ""
