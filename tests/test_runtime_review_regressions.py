"""Offline regressions for authentication, cleanup and session lifetime boundaries."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from gemini_webapi import GeminiClient

from src import client_manager, client_wrapper, server, skill_server
from src.client_manager import ClientManager
from src.cookie_manager import CookieManager
from src.domain import CleanupState
from src.infrastructure.rpc_contracts import get_contract
from src.remote_chat_cleanup_manager import CleanupTask, RemoteChatCleanupManager
from src.services.lifecycle import ConversationLifecycleService
from src.session_manager import SessionService


def _history_response(rows=None, *, status_code=200):
    contract = get_contract("history.page")
    body = [None, None, rows or []]
    return SimpleNamespace(
        status_code=status_code,
        text=json.dumps([["wrb.fr", contract.rpc_id, json.dumps(body)]]),
    )


def _cleanup_client(*, delete_side_effect=None):
    return SimpleNamespace(
        delete_chat=AsyncMock(side_effect=delete_side_effect),
        _batch_execute=AsyncMock(return_value=_history_response()),
        close=AsyncMock(),
    )


def test_http_200_delete_rejection_is_not_completed_and_can_retry(monkeypatch):
    from src.services import history

    shared_delete = history.delete_chat_result
    results = []

    async def observe_delete(client, chat_id):
        result = await shared_delete(client, chat_id)
        results.append(result)
        return result

    monkeypatch.setattr(history, "delete_chat_result", observe_delete)
    delete_requests = [
        ("GzXR5e", ["c_rejected"]),
        ("qWymEb", ["c_rejected", [1, None, 0, 1]]),
    ]

    class Client(GeminiClient):
        # Keep upstream state, account checks and deletion; replace only RPC I/O.

        def __init__(self):
            super().__init__()
            self.delete_calls = []
            self.history_filters = []
            self.rejected = True

        async def _batch_execute(self, calls, **_kwargs):
            assert len(calls) == 1
            rpc_id = str(calls[0].rpcid)
            payload = json.loads(calls[0].payload)
            if rpc_id == get_contract("history.page").rpc_id:
                self.history_filters.append(payload[2])
                rows = [["c_rejected", "Dummy", False]] if self.rejected else []
                return _history_response(rows)
            self.delete_calls.append((rpc_id, payload))
            response = (
                ["wrb.fr", rpc_id, None, None, None, [7]]
                if self.rejected
                else ["wrb.fr", rpc_id, json.dumps([])]
            )
            return SimpleNamespace(
                status_code=200,
                text=json.dumps([response]),
            )

    async def run():
        manager, client = RemoteChatCleanupManager(), Client()
        failed = await manager.delete_chat_result("c_rejected", client=client)
        assert failed.state is CleanupState.FAILED
        assert failed.attempts == 1
        assert failed.diagnostic_id
        assert client.delete_calls == delete_requests
        assert client.history_filters == [[False, None, True]]
        assert results[0].meta.verification_status == "still_present"
        assert results[0].data["deleted"] is False
        assert "c_rejected" in manager.list_pending_cleanup()
        assert "c_rejected" not in manager._completed_cleanup
        client.rejected = False
        completed = await manager.delete_chat_result("c_rejected", client=client)
        assert completed.state is CleanupState.COMPLETED
        assert completed.attempts == 2
        assert client.delete_calls == delete_requests * 2
        assert client.history_filters == [[False, None, True], [False, None, True], [True, None, True]]
        assert results[1].meta.verification_status == "verified_absent"
        assert results[1].data["deleted"] is True
        assert manager.list_pending_cleanup() == {}

    asyncio.run(run())


@pytest.mark.parametrize("readback", ["unavailable", "http_error", "changed_shape"])
def test_cleanup_requires_complete_readback_evidence(readback):
    client = _cleanup_client()
    if readback == "unavailable":
        del client._batch_execute
    elif readback == "http_error":
        client._batch_execute.return_value = _history_response(status_code=503)
    else:
        client._batch_execute.return_value = _history_response([{"new_shape": "c_unknown"}])
    manager = RemoteChatCleanupManager()

    result = asyncio.run(manager.delete_chat_result("c_unknown", client=client))

    assert result.state is CleanupState.FAILED
    assert result.diagnostic_id
    assert manager.list_pending_cleanup()["c_unknown"].last_diagnostic_id == result.diagnostic_id
    assert "c_unknown" not in manager._completed_cleanup


@pytest.mark.parametrize("cancel_all_waiters", [False, True])
def test_cancelled_waiter_does_not_cancel_shared_cleanup(cancel_all_waiters):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def delete(_cid):
            started.set()
            await release.wait()

        client = _cleanup_client(delete_side_effect=delete)
        manager = RemoteChatCleanupManager()
        first = asyncio.create_task(manager.delete_chat_result("c_shared", client=client))
        await started.wait()
        second = asyncio.create_task(manager.delete_chat_result("c_shared", client=client))
        await asyncio.sleep(0)
        first.cancel()
        if cancel_all_waiters:
            second.cancel()
        assert isinstance((await asyncio.gather(first, return_exceptions=True))[0], asyncio.CancelledError)
        if cancel_all_waiters:
            await asyncio.gather(second, return_exceptions=True)
        release.set()
        if not cancel_all_waiters:
            result = await second
            assert result.state is CleanupState.ALREADY_COMPLETED
        for _ in range(10):
            await asyncio.sleep(0)
        observed = manager.get_cleanup_observation("c_shared")
        assert observed is not None and observed.state is CleanupState.COMPLETED
        assert manager._inflight_cleanup == {}
        assert manager.list_pending_cleanup() == {}
        client.delete_chat.assert_awaited_once()

    asyncio.run(run())


def test_cancelled_upstream_cleanup_retains_retry_diagnostic():
    async def run():
        started = asyncio.Event()

        async def delete(_cid):
            started.set()
            await asyncio.Event().wait()

        manager = RemoteChatCleanupManager()
        waiter = asyncio.create_task(
            manager.delete_chat_result("c_cancelled", client=_cleanup_client(delete_side_effect=delete)),
        )
        await started.wait()
        manager._inflight_cleanup["c_cancelled"].cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        observed = manager.get_cleanup_observation("c_cancelled")
        assert observed is not None and observed.state is CleanupState.FAILED
        assert observed.diagnostic_id
        assert manager.list_pending_cleanup()["c_cancelled"].last_diagnostic_id == observed.diagnostic_id

    asyncio.run(run())


@pytest.mark.parametrize("resolution", ["provider", "initializer", "async_initializer"])
@pytest.mark.parametrize("due", [False, True])
def test_cleanup_initialization_failure_is_recorded(resolution, due):
    def fail():
        raise PermissionError("dummy initialization failure")

    async def async_fail():
        fail()

    manager = RemoteChatCleanupManager(client_provider=fail if resolution == "provider" else None)
    initializer = async_fail if resolution == "async_initializer" else fail if resolution == "initializer" else None
    if due:
        manager._pending_cleanup["c_init"] = CleanupTask(time.time() - 1, source="due")
        result = asyncio.run(manager.cleanup_due_chat_results(client_initializer=initializer))[0]
    else:
        result = asyncio.run(manager.delete_chat_result("c_init", client_initializer=initializer))
    assert result.state is CleanupState.FAILED
    assert result.attempts == 1
    assert result.diagnostic_id
    assert manager.list_pending_cleanup()["c_init"].last_diagnostic_id == result.diagnostic_id


@pytest.mark.parametrize("operation", ["delete", "due"])
def test_cleanup_facade_records_initialization_failure(monkeypatch, operation):
    cleanup = RemoteChatCleanupManager()
    monkeypatch.setattr(client_wrapper, "_cleanup_manager", cleanup)
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: object())
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock(side_effect=PermissionError("dummy expired")))
    if operation == "due":
        cleanup._pending_cleanup["c_facade"] = CleanupTask(time.time() - 1)
        assert asyncio.run(client_wrapper.cleanup_due_remote_chats()) == 0
    else:
        result = asyncio.run(client_wrapper.delete_remote_chat_result("c_facade"))
        assert result.state is CleanupState.FAILED
    observed = cleanup.get_cleanup_observation("c_facade")
    assert observed is not None and observed.diagnostic_id
    assert cleanup.list_pending_cleanup()["c_facade"].last_diagnostic_id == observed.diagnostic_id


@pytest.mark.parametrize("callback_installed", [False, True])
@pytest.mark.parametrize("optional_fields", [False, True])
def test_compact_cookie_update_retires_client_and_replaces_all_auth(monkeypatch, callback_installed, optional_fields):
    monkeypatch.setenv("GEMINI_PSID", "dummy-old")
    monkeypatch.setenv("GEMINI_PSIDTS", "dummy-old-ts")
    monkeypatch.setenv("GEMINI_PSIDCC", "dummy-old-cc")
    cookies = CookieManager(auto_refresh=False)
    fresh_cookies = {"__Secure-1PSID": "dummy-new"}
    if optional_fields:
        fresh_cookies.update({"__Secure-1PSIDTS": "dummy-new-ts", "__Secure-1PSIDCC": "dummy-new-cc"})
    manager = ClientManager()
    old_client = SimpleNamespace(close=AsyncMock())
    manager._client = old_client
    monkeypatch.setattr(client_wrapper, "_client_manager", manager)
    monkeypatch.setattr(client_wrapper, "_session_manager", SessionService())
    monkeypatch.setattr(client_wrapper, "get_cookie_manager", lambda: cookies)
    monkeypatch.setattr(client_wrapper, "_prepare_browser_cookie_cache", lambda **_kwargs: None)
    monkeypatch.setattr(
        cookies,
        "get_cookies_from_browser",
        lambda _browser, profile="": fresh_cookies,
    )
    if callback_installed:
        cookies.on_cookie_update = client_wrapper._on_cookie_update

    def create_client():
        manager._client = SimpleNamespace(psid=os.environ["GEMINI_PSID"], cookies=cookies.get_cookie().extra_cookies)

    monkeypatch.setattr(manager, "_create_client", create_client)

    async def run():
        response = await skill_server.mcp.call_tool("cookie", {"action": "get", "profile": "Profile 2"})
        assert "Loaded" in response.content[0].text
        fresh_client = manager.get_client()
        assert fresh_client is not old_client
        assert fresh_client.psid == "dummy-new"
        assert fresh_client.cookies == fresh_cookies
        assert os.environ.get("GEMINI_PSIDTS") == ("dummy-new-ts" if optional_fields else None)
        assert os.environ.get("GEMINI_PSIDCC") == ("dummy-new-cc" if optional_fields else None)
        await asyncio.sleep(0)
        old_client.close.assert_awaited_once()
        assert manager._generation == 1

    asyncio.run(run())


def test_compact_startup_installs_cookie_lifecycle(monkeypatch):
    calls = []
    monkeypatch.setattr(skill_server, "_init_default_prompts", lambda: None)
    monkeypatch.setattr(skill_server, "init_cookie_manager_integration", lambda: calls.append("cookies"), raising=False)
    monkeypatch.setattr(skill_server.mcp, "run", lambda: calls.append("run"))
    skill_server.main()
    assert calls == ["cookies", "run"]


@pytest.mark.parametrize("browser", ["chrome", "firefox", "edge"])
def test_explicit_missing_profile_never_loads_default_account(monkeypatch, browser):
    import browser_cookie3

    default_jar = Mock(return_value=[SimpleNamespace(name="__Secure-1PSID", value="dummy-other", domain="google.com")])
    monkeypatch.setattr(browser_cookie3, browser, default_jar)
    monkeypatch.setattr(CookieManager, "_browser_cookie_candidates", staticmethod(lambda *_args: []))
    assert CookieManager.get_cookies_from_browser(browser, profile="Missing Profile") == {}
    default_jar.assert_not_called()


def test_cookie_update_callback_does_not_hold_cookie_lock(monkeypatch):
    cookies = CookieManager(auto_refresh=False)
    manager = ClientManager()
    creating, callback_started = threading.Event(), threading.Event()
    errors = []

    def callback(_cookie):
        callback_started.set()
        manager.reset()

    def create_client():
        creating.set()
        assert callback_started.wait(2)
        manager._client = SimpleNamespace(cookies=cookies.get_cookie().extra_cookies)

    def run(function):
        try:
            function()
        except BaseException as error:
            errors.append(error)

    cookies.on_cookie_update = callback
    monkeypatch.setattr(manager, "_create_client", create_client)
    creator = threading.Thread(target=lambda: run(manager.get_client), daemon=True)
    updater = threading.Thread(target=lambda: run(lambda: cookies.update_cookie("dummy-new")), daemon=True)
    creator.start()
    assert creating.wait(2)
    updater.start()
    creator.join(2)
    updater.join(2)
    assert not creator.is_alive() and not updater.is_alive()
    assert errors == []
    assert cookies.get_cookie().psid == "dummy-new"


def test_cookie_updates_publish_callbacks_in_order_without_blocking_readers():
    first_published, release_first, second_started = threading.Event(), threading.Event(), threading.Event()
    cookies = CookieManager(auto_refresh=False)
    observed = []

    def notify(cookie):
        observed.append(cookie.psid)
        if cookie.psid == "dummy-first":
            first_published.set()
            assert release_first.wait(2)

    def update_second():
        second_started.set()
        cookies.update_cookie("dummy-second", notify=notify)

    first = threading.Thread(target=lambda: cookies.update_cookie("dummy-first", notify=notify))
    second = threading.Thread(target=update_second)
    first.start()
    assert first_published.wait(2)
    second.start()
    assert second_started.wait(2)
    assert cookies.get_cookie().psid == "dummy-first"
    release_first.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert observed == ["dummy-first", "dummy-second"]
    assert cookies.get_cookie().psid == "dummy-second"


@pytest.mark.parametrize("stream", [False, True])
def test_busy_session_is_not_expired_or_deleted(monkeypatch, stream):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class Session:
            cid = "c_busy"

            async def send_message(self, **_kwargs):
                started.set()
                await release.wait()
                return SimpleNamespace(text="done")

            async def send_message_stream(self, **kwargs):
                yield await self.send_message(**kwargs)

        client = _cleanup_client()
        sessions = SessionService(max_age=10, id_factory=lambda: "sess_busy")
        cleanup = RemoteChatCleanupManager(client_provider=lambda: client)
        lifecycle = ConversationLifecycleService(lambda: sessions, lambda: cleanup)
        lifecycle.create_session(Session(), delete_after_seconds=0)
        send = lifecycle.send_message_stream if stream else lifecycle.send_message
        sending = asyncio.create_task(send("sess_busy", prompt="dummy"))
        await started.wait()
        sessions._sessions["sess_busy"].created_at = time.time() - 100
        active = lifecycle.list_sessions()
        await asyncio.sleep(0)
        deleted_during_send = client.delete_chat.await_count
        release.set()
        result = await sending
        assert "sess_busy" in active
        assert deleted_during_send == 0
        assert result.ok
        assert lifecycle.list_sessions() == {}
        observed = await cleanup.delete_chat_result("c_busy", client=client)
        assert observed.state in {CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED}
        client.delete_chat.assert_awaited_once_with("c_busy")

    asyncio.run(run())


def test_proxy_warning_omits_credentials(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_PROXY", "http://dummy-user:dummy-password@127.0.0.1:1234/private?token=dummy-token")
    monkeypatch.setattr(client_manager.socket, "create_connection", Mock(side_effect=OSError("unreachable")))
    with caplog.at_level(logging.WARNING, logger="src.client_manager"):
        assert client_manager.get_configured_proxy() is None
    assert "http://127.0.0.1:1234" in caplog.text
    assert all(secret not in caplog.text for secret in ("dummy-user", "dummy-password", "dummy-token", "/private"))


@pytest.mark.parametrize("delete_fails", [False, True])
def test_primary_reset_exposes_remote_side_effect_and_real_cleanup_result(monkeypatch, delete_fails):
    async def run():
        client = _cleanup_client(delete_side_effect=ConnectionError("dummy offline") if delete_fails else None)
        manager = ClientManager()
        manager._client, manager._initialized = client, True
        manager._client_loop = asyncio.get_running_loop()
        sessions = SessionService(id_factory=lambda: "sess_reset")
        cleanup = RemoteChatCleanupManager()
        monkeypatch.setattr(client_wrapper, "_client_manager", manager)
        monkeypatch.setattr(client_wrapper, "_session_manager", sessions)
        monkeypatch.setattr(client_wrapper, "_cleanup_manager", cleanup)
        client_wrapper.create_session(SimpleNamespace(cid="c_reset"), retain_chat=False)

        response = await server.mcp.call_tool("gemini_reset", {})
        payload = response.content[0].meta["domain_result"]
        client.delete_chat.assert_awaited_once_with("c_reset")
        client.close.assert_awaited_once()
        assert payload["data"]["client_state"] == "reset"
        assert payload["meta"]["operation_state"] == ("partial" if delete_fails else "completed")
        assert bool(payload["warnings"]) is delete_fails
        if delete_fails:
            assert "c_reset" in cleanup.list_pending_cleanup()
            assert "清理" in response.content[0].text
        tools = await server.mcp.list_tools()
        reset = next(tool for tool in tools if tool.name == "gemini_reset")
        assert reset.annotations.destructive_hint is True
        assert reset.annotations.open_world_hint is True
        assert "远端" in reset.description

    asyncio.run(run())
