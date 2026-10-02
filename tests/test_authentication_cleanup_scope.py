"""Authentication replacement never transfers old cleanup into a new account."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import client_manager, client_wrapper
from src.client_manager import ClientManager
from src.cookie_manager import CookieData
from src.domain import CleanupObservation, CleanupState, DomainResult
from src.infrastructure.rpc_contracts import get_contract
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.session_manager import SessionService


def _client(psid):
    return SimpleNamespace(
        cookies={"__Secure-1PSID": psid},
        init=AsyncMock(),
        close=AsyncMock(),
        delete_chat=AsyncMock(),
        _batch_execute=AsyncMock(
            return_value=SimpleNamespace(
                status_code=200,
                text=json.dumps([["wrb.fr", get_contract("history.page").rpc_id, json.dumps([None, None, []])]]),
            ),
        ),
    )


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "dummy-old")
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)
    monkeypatch.delenv("GEMINI_PSIDCC", raising=False)
    old, new = _client("dummy-old"), _client("dummy-new")
    manager = ClientManager()
    manager._client, manager._initialized = old, True
    sessions = SessionService()
    cleanup = RemoteChatCleanupManager(client_provider=lambda: client_wrapper._initialize_cleanup_client())
    monkeypatch.setattr(client_wrapper, "_client_manager", manager)
    monkeypatch.setattr(client_wrapper, "_session_manager", sessions)
    monkeypatch.setattr(client_wrapper, "_cleanup_manager", cleanup)
    monkeypatch.setattr(manager, "_create_client", lambda: setattr(manager, "_client", new))
    return SimpleNamespace(manager=manager, sessions=sessions, cleanup=cleanup, old=old, new=new)


def _replace_authentication():
    client_wrapper._on_cookie_update(CookieData(psid="dummy-new", extra_cookies={"__Secure-1PSID": "dummy-new"}))


def test_authentication_switch_cancels_old_queue_and_sessions_with_locators(scope):
    async def run():
        client_wrapper.get_gemini_client()
        created = client_wrapper.create_session(SimpleNamespace(cid="c_old_session"))
        client_wrapper.schedule_remote_chat_cleanup("c_old_job", delete_after_seconds=100)
        old_delay = scope.cleanup._delayed_cleanup["c_old_job"]
        assert (await scope.cleanup.delete_chat_result("c_old_complete", client=scope.old)).state is CleanupState.COMPLETED
        scope.old.delete_chat.reset_mock()

        _replace_authentication()
        await asyncio.gather(old_delay, return_exceptions=True)
        assert scope.sessions.list_sessions() == {}
        assert scope.cleanup.list_pending_cleanup() == {}
        assert scope.cleanup._completed_cleanup == {}
        assert old_delay.cancelled()
        assert scope.cleanup.authentication_generation() == 1
        assert scope.manager._generation == 1
        for cid in ("c_old_job", "c_old_session"):
            observation = scope.cleanup.get_cleanup_observation(cid)
            assert observation.state is CleanupState.CANCELLED
            assert observation.upstream_chat_id == cid
            assert observation.cancellation_reason == "authentication_context_changed"
            assert observation.diagnostic_id
            public = DomainResult.success(observation).to_dict()["data"]
            assert public["cancellation_reason"] == "authentication_context_changed"
        assert created.session._authentication_invalidated
        assert await client_wrapper.cleanup_due_remote_chats(client=scope.new) == 0
        scope.old.delete_chat.assert_not_awaited()
        scope.new.delete_chat.assert_not_awaited()

        # A fresh request can explicitly restore work; old completion cache does
        # not claim that the same resource in this account was already deleted.
        assert client_wrapper.get_gemini_client() is scope.new
        restored = await client_wrapper.delete_remote_chat_result("c_old_complete", client=scope.new)
        assert restored.state is CleanupState.COMPLETED
        scope.new.delete_chat.assert_awaited_once_with("c_old_complete")

    asyncio.run(run())


def test_same_authentication_refresh_preserves_session_and_pending_jobs(scope):
    async def run():
        client_wrapper.get_gemini_client()
        created = client_wrapper.create_session(SimpleNamespace(cid="c_same"))
        pending = client_wrapper.schedule_remote_chat_cleanup("c_same", delete_after_seconds=100)
        client_wrapper._on_cookie_update(CookieData(psid="dummy-old", extra_cookies=dict(scope.old.cookies)))
        assert scope.cleanup.authentication_generation() == 0
        assert scope.manager._generation == 0
        assert scope.manager.get_client() is scope.old
        assert scope.sessions.get_session(created.session.session_id) is created.session
        assert scope.cleanup.get_cleanup_observation("c_same") == pending
        scope.old.close.assert_not_awaited()

    asyncio.run(run())


def test_ordinary_reset_keeps_immediate_verified_cleanup_semantics(scope):
    async def run():
        client_wrapper.get_gemini_client()
        client_wrapper.create_session(SimpleNamespace(cid="c_regular_reset"))
        result = await client_wrapper.reset_client_async()
        assert result.meta.operation_state.value == "completed"
        assert result.data.cleanup_failure_count == 0
        assert scope.cleanup.authentication_generation() == 0
        assert scope.cleanup.get_cleanup_observation("c_regular_reset").state is CleanupState.COMPLETED
        scope.old.delete_chat.assert_awaited_once_with("c_regular_reset")
        scope.old.close.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("late_finishes_first", [False, True])
def test_late_old_delete_cannot_complete_or_overwrite_new_work(late_finishes_first):
    async def run():
        started, old_release, new_started, new_release = (asyncio.Event() for _ in range(4))
        old, new = _client("dummy-old"), _client("dummy-new")

        async def old_delete(_cid):
            started.set()
            try:
                await old_release.wait()
            except asyncio.CancelledError:
                # Some upstream I/O cannot be stopped immediately.
                await old_release.wait()

        async def new_delete(_cid):
            new_started.set()
            await new_release.wait()

        old.delete_chat.side_effect, new.delete_chat.side_effect = old_delete, new_delete
        manager = RemoteChatCleanupManager()
        waiting_old = asyncio.create_task(manager.delete_chat_result("c_reused", client=old))
        await asyncio.wait_for(started.wait(), 1)
        manager.invalidate_authentication_context()
        await asyncio.sleep(0)
        waiting_new = asyncio.create_task(manager.delete_chat_result("c_reused", client=new))
        await asyncio.wait_for(new_started.wait(), 1)
        new_task = manager._inflight_cleanup["c_reused"]
        if late_finishes_first:
            old_release.set()
            old_result = await waiting_old
            assert manager._inflight_cleanup["c_reused"] is new_task
            new_release.set()
            new_result = await waiting_new
        else:
            new_release.set()
            new_result = await waiting_new
            old_release.set()
            old_result = await waiting_old
        assert old_result.state is CleanupState.CANCELLED
        assert old_result.cancellation_reason == "authentication_context_changed"
        assert new_result.state is CleanupState.COMPLETED
        assert manager.get_cleanup_observation("c_reused") is new_result
        assert manager._completed_cleanup["c_reused"] is new_result
        assert manager.list_pending_cleanup() == {}
        assert manager._inflight_cleanup == {}

    asyncio.run(run())


def test_authentication_switch_during_initializer_never_deletes_with_new_client():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        new = _client("dummy-new")

        async def initializer():
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return new

        manager = RemoteChatCleanupManager()
        waiter = asyncio.create_task(manager.delete_chat_result("c_old_init", client_initializer=initializer))
        await asyncio.wait_for(started.wait(), 1)
        manager.invalidate_authentication_context()
        await asyncio.sleep(0)
        release.set()
        result = await waiter
        assert result.state is CleanupState.CANCELLED
        assert manager.list_pending_cleanup() == {}
        new.delete_chat.assert_not_awaited()

    asyncio.run(run())


def test_due_snapshot_cannot_transfer_remaining_chats_to_new_context():
    manager = RemoteChatCleanupManager()
    manager.schedule_cleanup("c_first", delete_after_seconds=0)
    manager.schedule_cleanup("c_second", delete_after_seconds=0)
    client = _client("dummy-old")

    async def delete(_cid):
        manager.invalidate_authentication_context()

    client.delete_chat.side_effect = delete
    results = asyncio.run(manager.cleanup_due_chat_results(client=client))

    assert len(results) == 2
    assert all(result.state is CleanupState.CANCELLED for result in results)
    client.delete_chat.assert_awaited_once_with("c_first")
    assert manager.list_pending_cleanup() == {}
    assert manager._completed_cleanup == {}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("known_chat", [False, True])
def test_busy_session_authentication_switch_preserves_late_locator_without_cleanup(scope, stream, known_chat):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class Session:
            cid = "c_late_session" if known_chat else None

            async def send_message(self, **_kwargs):
                started.set()
                await release.wait()
                self.cid = "c_late_session"
                return SimpleNamespace(cid=self.cid, text="old account response")

            async def send_message_stream(self, **kwargs):
                yield await self.send_message(**kwargs)

        client_wrapper.get_gemini_client()
        created = client_wrapper.create_session(Session())
        send = client_wrapper.send_session_message_stream if stream else client_wrapper.send_session_message
        waiter = asyncio.create_task(send(created.session.session_id, prompt="dummy"))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.to_thread(_replace_authentication)
        release.set()
        result = await waiter
        assert result.ok is False
        assert result.meta.operation_state.value == "cancelled"
        assert result.meta.verification_status == "authentication_context_changed"
        assert result.to_dict()["meta"]["details"]["lifecycle"]["upstream_chat_id"] == "c_late_session"
        observation = scope.cleanup.get_cleanup_observation("c_late_session")
        assert observation.state is CleanupState.CANCELLED
        assert observation.diagnostic_id == result.meta.diagnostic_id
        assert scope.cleanup.list_pending_cleanup() == {}
        scope.new.delete_chat.assert_not_awaited()

    asyncio.run(run())


def test_late_response_and_start_session_cannot_requeue_under_new_authentication(scope):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def old_request():
            assert client_wrapper.get_gemini_client() is scope.old
            started.set()
            await release.wait()
            response = SimpleNamespace(cid="c_late_response")
            scheduled = client_wrapper.schedule_remote_chat_cleanup_from_response(response, delete_after_seconds=0)
            created = client_wrapper.create_session(SimpleNamespace(cid="c_late_start"))
            return scheduled.cleanup_observation, created

        old_request_task = asyncio.create_task(old_request())
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.to_thread(_replace_authentication)
        assert client_wrapper.get_gemini_client() is scope.new
        new_job = client_wrapper.schedule_remote_chat_cleanup("c_new_job", delete_after_seconds=100)
        release.set()
        old_observation, created = await old_request_task
        assert old_observation.state is CleanupState.CANCELLED
        assert created.ok is False and created.meta.operation_state.value == "cancelled"
        assert set(scope.cleanup.list_pending_cleanup()) == {"c_new_job"}
        assert scope.cleanup.get_cleanup_observation("c_new_job") is new_job
        assert scope.cleanup.get_cleanup_observation("c_late_start").state is CleanupState.CANCELLED
        assert scope.sessions.list_sessions() == {}
        scope.new.delete_chat.assert_not_awaited()

    asyncio.run(run())


def test_same_task_cookie_callback_preserves_captured_old_response_context(scope):
    async def run():
        assert client_wrapper.get_gemini_client() is scope.old
        _replace_authentication()
        old = client_wrapper.schedule_remote_chat_cleanup_from_response(SimpleNamespace(cid="c_same_task_old"))
        assert old.cleanup_observation.state is CleanupState.CANCELLED
        created = client_wrapper.create_session(SimpleNamespace(cid="c_same_task_start"))
        assert created.ok is False and created.meta.verification_status == "authentication_context_changed"
        with pytest.raises(RuntimeError, match="Authentication context changed"):
            client_wrapper.store_session("sess_old", SimpleNamespace(cid="c_same_task_store"))
        deleted = await client_wrapper.delete_remote_chat_result("c_old_explicit", client=scope.new)
        assert deleted.state is CleanupState.CANCELLED
        scope.new.delete_chat.assert_not_awaited()
        assert scope.cleanup.list_pending_cleanup() == {}

        assert client_wrapper.get_gemini_client() is scope.new
        fresh = client_wrapper.schedule_remote_chat_cleanup("c_same_task_new", delete_after_seconds=100)
        assert fresh.state is CleanupState.PENDING
        assert set(scope.cleanup.list_pending_cleanup()) == {"c_same_task_new"}

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("error", [ConnectionError("dummy closed client"), asyncio.CancelledError()])
def test_old_session_io_interruption_after_switch_reports_authentication_cancellation(scope, stream, error):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class Session:
            cid = "c_interrupted_old"

            async def send_message(self, **_kwargs):
                started.set()
                await release.wait()
                raise error

            async def send_message_stream(self, **kwargs):
                yield await self.send_message(**kwargs)

        client_wrapper.get_gemini_client()
        created = client_wrapper.create_session(Session())
        send = client_wrapper.send_session_message_stream if stream else client_wrapper.send_session_message
        waiter = asyncio.create_task(send(created.session.session_id, prompt="dummy"))
        await asyncio.wait_for(started.wait(), 1)
        _replace_authentication()
        release.set()
        result = await waiter
        assert result.ok is False and result.meta.verification_status == "authentication_context_changed"
        assert result.meta.operation_state.value == "cancelled"
        observation = scope.cleanup.get_cleanup_observation("c_interrupted_old")
        assert observation.state is CleanupState.CANCELLED
        assert result.meta.diagnostic_id == observation.diagnostic_id
        assert scope.cleanup.list_pending_cleanup() == {}

    asyncio.run(run())


def test_unpublished_new_cookie_snapshot_is_not_mixed_into_old_client(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "dummy-old")
    monkeypatch.setattr(
        client_manager,
        "get_cookie_manager",
        lambda: SimpleNamespace(get_cookie=lambda: CookieData(psid="dummy-new", extra_cookies={"__Secure-1PSID": "dummy-new"})),
    )
    assert client_manager.get_extra_cookies() == {}


def test_default_cleanup_payload_shape_stays_compatible():
    assert "cancellation_reason" not in DomainResult.success(CleanupObservation()).to_dict()["data"]
