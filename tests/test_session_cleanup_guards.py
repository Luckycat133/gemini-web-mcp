"""Session holds cross old cleanup deadlines without cancelling accepted work."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi.exceptions import TimeoutError as SDKTimeoutError
from gemini_webapi.exceptions import APIError, AuthError, GeminiError, ModelInvalidError, TemporarilyBlockedError, UsageLimitExceededError

from src.constants import DEFAULT_SESSION_MAX_AGE_SECONDS
from src.domain import CleanupObservation, CleanupState, DomainResult
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.chat import ChatService, ChatServiceDependencies, SessionMessageRequest
from src.services.cleanup_scope import observe_upstream_identity
from src.services.lifecycle import ConversationLifecycleService
from src.session_manager import SessionData, SessionOperationResult, SessionService, session_cleanup_protected


@pytest.fixture(autouse=True)
def private_test_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite3"))
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(tmp_path / "cleanup.sqlite3"))


def _service(state, send, schedule):
    return ChatService(ChatServiceDependencies(
        client_provider=lambda: SimpleNamespace(timeout=30, watchdog_timeout=30), client_initializer=AsyncMock(),
        cleanup_due_remote_chats=AsyncMock(), create_session=lambda *_args, **_kwargs: None,
        lookup_session=lambda *_args: SessionOperationResult.success(state),
        send_session_message=send, send_session_message_stream=send,
        schedule_response_cleanup=lambda *_args, **_kwargs: None, schedule_chat_cleanup=schedule,
        normalize_model=lambda value: value, resolve_model=lambda value: value, account_key_provider=lambda: "account",
    ))


@pytest.mark.parametrize("stream", [False, True])
def test_default_manager_guard_protects_send_before_first_frame_and_pending_turn(monkeypatch, tmp_path, stream):
    state = SessionData(session=SimpleNamespace(cid="c_one", metadata=["c_one", "r_old", "rc_old"]),
                        session_id="sess_one", account_key="account")
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "account")
    delete = AsyncMock(return_value=DomainResult.success(None, verification_status="verified_absent"))
    monkeypatch.setattr("src.services.history.delete_chat_result", delete)
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", AsyncMock(return_value=None))
    async def send(*_args, **_kwargs):
        # Cross the pre-existing timer before any new CID or response frame.
        await asyncio.sleep(0.06)
        delete.assert_not_awaited()
        assert state._policy_lock.locked()
        observe_upstream_identity(SimpleNamespace(cid="c_one", metadata=["c_one", "r_new", "rc_new"]))
        raise SDKTimeoutError("Synthetic incomplete reply")
    service = _service(state, send, manager.schedule_cleanup_result)
    async def run():
        manager.schedule_cleanup_result("c_one", delete_after_seconds=0.01)
        method = service.send_session_stream if stream else service.send_session
        result = await method(SessionMessageRequest(session_id="sess_one", message="synthetic next request"))
        assert not result.ok and state.pending_turn == ("c_one", "r_new", "rc_new")
        assert manager._store.get("account", "c_one")["delete_at"] >= time.time() + DEFAULT_SESSION_MAX_AGE_SECONDS - 2
        await manager.delete_chat_result("c_one")
        assert manager._store.get("account", "c_one")["state"] == "pending"
        delete.assert_not_awaited()
    asyncio.run(run())


def test_reset_extends_existing_unknown_turn_hold_and_does_not_dispatch_delete(tmp_path):
    sessions = SessionService()
    state = sessions.store_session("sess_one", SimpleNamespace(cid="c_one"))
    state.account_key = "account"
    state.pending_turn = ("c_one", "r_one", "rc_one")
    state.unverified_chat_ids.add("c_one")
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "account")
    manager.schedule_cleanup_result("c_one", delete_after_seconds=0, dispatch=False)
    lifecycle = ConversationLifecycleService(lambda: sessions, lambda: manager)
    delete = AsyncMock()
    result = asyncio.run(lifecycle.reset_one("sess_one", delete_callback=delete))
    assert result.ok and result.meta.verification_status == "local_state_removed_cleanup_pending"
    assert manager._store.get("account", "c_one")["delete_at"] >= time.time() + DEFAULT_SESSION_MAX_AGE_SECONDS - 2
    assert manager._store.get("account", "c_one")["state"] == "pending"
    delete.assert_not_awaited()


def test_reset_unknown_identity_reports_pending_without_guessing_a_chat_id(tmp_path):
    sessions = SessionService()
    state = sessions.store_session("sess_new", SimpleNamespace(cid=""))
    state.account_key, state.pending_turn = "account", ("", "", None)
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "account")
    lifecycle = ConversationLifecycleService(lambda: sessions, lambda: manager)
    delete = AsyncMock()
    result = asyncio.run(lifecycle.reset_one("sess_new", delete_callback=delete))
    assert result.ok and result.meta.verification_status == "local_state_removed_cleanup_pending"
    assert result.meta.details["lifecycle"].cleanup.state is CleanupState.PENDING
    assert result.meta.details["lifecycle"].upstream_chat_id is None
    assert not manager._store.all_records("account")
    delete.assert_not_awaited()


def test_session_guard_is_account_bound_and_releases_completed_idle_sessions():
    state = SessionData(session=SimpleNamespace(cid="c_guard"), account_key="owner")
    async def run():
        async with state._policy_lock:
            assert session_cleanup_protected("c_guard", account_key="owner")
            assert not session_cleanup_protected("c_guard", account_key="different")
        assert not session_cleanup_protected("c_guard", account_key="owner")
        state.pending_turn = ("c_guard", "r_one", None)
        assert session_cleanup_protected("c_guard", account_key="owner")
        state.pending_turn = None
        assert not session_cleanup_protected("c_guard", account_key="owner")
    asyncio.run(run())


@pytest.mark.parametrize("change", ["cid", "rid", "rcid"])
def test_success_callback_cannot_clear_an_observed_different_turn(monkeypatch, change):
    state = SessionData(session=SimpleNamespace(cid="c_one", metadata=["c_one", "r_old", "rc_old"]),
                        session_id="sess_one", account_key="account")
    async def send(*_args, **_kwargs):
        observe_upstream_identity(SimpleNamespace(cid="c_one", metadata=["c_one", "r_expected", "rc_expected"]))
        metadata = ["c_one", "r_expected", "rc_expected"]
        metadata[{"cid": 0, "rid": 1, "rcid": 2}[change]] = "unrelated"
        return SessionOperationResult.success(state, SimpleNamespace(metadata=metadata, text="unrelated reply"))
    read = AsyncMock(return_value=None)
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", read)
    service = _service(state, send, lambda cid, **_kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid))
    result = asyncio.run(service.send_session(SessionMessageRequest(session_id="sess_one", message="synthetic")))
    assert not result.ok and state.pending_turn == ("c_one", "r_expected", "rc_expected")
    read.assert_awaited_once_with(service._dependencies.client_provider(), "c_one", "r_expected", "rc_expected")


@pytest.mark.parametrize("error", [APIError("synthetic parse error"), GeminiError("synthetic connection lost")])
def test_ambiguous_sdk_errors_with_only_old_identity_never_recover_old_reply_or_resend(monkeypatch, error):
    state = SessionData(session=SimpleNamespace(cid="c_one", metadata=["c_one", "r_old", "rc_old"]),
                        session_id="sess_one", account_key="account")
    send = AsyncMock(side_effect=error)
    read = AsyncMock(side_effect=AssertionError("The previous reply is not the unknown new turn"))
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", read)
    service = _service(state, send, lambda *_args, **_kwargs: None)
    async def run():
        first = await service.send_session(SessionMessageRequest(session_id="sess_one", message="synthetic request"))
        again = await service.send_session(SessionMessageRequest(session_id="sess_one", message="never resend"))
        assert not first.ok and not again.ok and state.pending_turn == ("c_one", "", None)
    asyncio.run(run())
    assert send.await_count == 1
    read.assert_not_awaited()


@pytest.mark.parametrize("error", [AuthError("synthetic auth rejection"), UsageLimitExceededError("synthetic quota rejection"),
                                  ModelInvalidError("synthetic model rejection"), TemporarilyBlockedError("synthetic rate rejection"),
                                  ValueError("synthetic local validation")])
def test_explicit_rejection_or_local_preflight_without_identity_does_not_invent_pending(error):
    state = SessionData(session=SimpleNamespace(cid="", metadata=[]), session_id="sess_new", account_key="account")
    send = AsyncMock(side_effect=error)
    service = _service(state, send, lambda *_args, **_kwargs: None)
    with pytest.raises(type(error)):
        asyncio.run(service.send_session(SessionMessageRequest(session_id="sess_new", message="synthetic request")))
    assert state.pending_turn is None


@pytest.mark.parametrize("observed", [False, True])
def test_cancellation_preserves_unknown_turn_and_propagates_without_recovery(monkeypatch, observed):
    state = SessionData(session=SimpleNamespace(cid="", metadata=[]), session_id="sess_new", account_key="account")
    async def send(*_args, **_kwargs):
        if observed:
            observe_upstream_identity(SimpleNamespace(cid="c_one", metadata=["c_one", "r_one", "rc_one"]))
        raise asyncio.CancelledError()
    read = AsyncMock(side_effect=AssertionError("Cancellation must not start another read or generation"))
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", read)
    service = _service(state, send, lambda *_args, **_kwargs: None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.send_session(SessionMessageRequest(session_id="sess_new", message="synthetic request")))
    assert state.pending_turn == (("c_one", "r_one", "rc_one") if observed else ("", "", None))
    read.assert_not_awaited()
