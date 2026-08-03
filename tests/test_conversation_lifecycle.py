"""P1.3 conversation lifecycle consistency and cleanup evidence."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from src.domain import CleanupState, DomainResult, OperationState
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services import ConversationLifecycleService
from src.session_manager import SessionService


def _service(
    *,
    sessions: SessionService | None = None,
    cleanup: RemoteChatCleanupManager | None = None,
) -> tuple[ConversationLifecycleService, SessionService, RemoteChatCleanupManager]:
    session_service = sessions or SessionService(id_factory=lambda: "sess_lifecycle")
    cleanup_manager = cleanup or RemoteChatCleanupManager()
    lifecycle = ConversationLifecycleService(
        session_provider=lambda: session_service,
        cleanup_provider=lambda: cleanup_manager,
    )
    return lifecycle, session_service, cleanup_manager


def test_cleanup_schedule_is_idempotent_and_retention_cancels_pending():
    manager = RemoteChatCleanupManager(default_retention_seconds=120)

    first = manager.schedule_cleanup_result("c_policy", source="primary")
    second = manager.schedule_cleanup_result("c_policy", source="compact")

    assert first.state is CleanupState.PENDING
    assert second.state is CleanupState.PENDING
    assert second.idempotent is True
    assert second.delete_at == first.delete_at
    assert len(manager.list_pending_cleanup()) == 1

    retained = manager.schedule_cleanup_result("c_policy", retain_chat=True)

    assert retained.state is CleanupState.RETAINED
    assert manager.list_pending_cleanup() == {}


def test_concurrent_and_repeated_delete_calls_upstream_exactly_once():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        class Client:
            async def delete_chat(self, cid: str) -> None:
                calls.append(cid)
                started.set()
                await release.wait()

        manager = RemoteChatCleanupManager()
        client = Client()
        first_task = asyncio.create_task(
            manager.delete_chat_result("c_once", client=client),
        )
        await started.wait()
        second_task = asyncio.create_task(
            manager.delete_chat_result("c_once", client=client),
        )
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(first_task, second_task)
        third = await manager.delete_chat_result("c_once", client=client)
        return calls, first, second, third

    calls, first, second, third = asyncio.run(run())

    assert calls == ["c_once"]
    assert first.state is CleanupState.COMPLETED
    assert second.state is CleanupState.ALREADY_COMPLETED
    assert second.idempotent is True
    assert third.state is CleanupState.ALREADY_COMPLETED
    assert third.idempotent is True


def test_failed_cleanup_keeps_safe_diagnostic_and_can_retry():
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def delete_chat(self, _cid: str) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("secret upstream failure")

    async def run():
        manager = RemoteChatCleanupManager()
        client = Client()
        failed = await manager.delete_chat_result("c_retry", client=client)
        pending_after_failure = manager.list_pending_cleanup()
        completed = await manager.delete_chat_result("c_retry", client=client)
        return manager, client, failed, pending_after_failure, completed

    manager, client, failed, pending_after_failure, completed = asyncio.run(run())

    assert failed.state is CleanupState.FAILED
    assert failed.diagnostic_id is not None
    assert failed.diagnostic_id.startswith("diag_")
    assert pending_after_failure["c_retry"].last_diagnostic_id == failed.diagnostic_id
    assert completed.state is CleanupState.COMPLETED
    assert completed.attempts == 2
    assert client.calls == 2
    assert manager.list_pending_cleanup() == {}

    payload = DomainResult.success(failed).to_dict()
    serialized = json.dumps(payload)
    assert failed.diagnostic_id in serialized
    assert "secret upstream failure" not in serialized
    assert "source" not in payload["data"]
    assert "delete_at" not in payload["data"]


def test_invalid_cleanup_id_has_no_upstream_or_unrelated_state_effect():
    class Client:
        async def delete_chat(self, _cid: str) -> None:
            raise AssertionError("invalid IDs must not reach the upstream client")

    manager = RemoteChatCleanupManager()
    manager.schedule_cleanup("c_survivor", delete_after_seconds=60)

    invalid = asyncio.run(manager.delete_chat_result("invalid", client=Client()))

    assert invalid.state is CleanupState.INVALID_ID
    assert set(manager.list_pending_cleanup()) == {"c_survivor"}


def test_lifecycle_service_reset_reports_cleanup_failure_without_touching_peer():
    ids = iter(("sess_target", "sess_peer"))
    sessions = SessionService(id_factory=lambda: next(ids))
    cleanup = RemoteChatCleanupManager()
    lifecycle, _, _ = _service(sessions=sessions, cleanup=cleanup)
    lifecycle.create_session(SimpleNamespace(cid="c_target"))
    lifecycle.create_session(SimpleNamespace(cid="c_peer"), retain_chat=True)

    async def failed_delete(cid: str):
        class Client:
            async def delete_chat(self, _cid: str) -> None:
                raise ConnectionError("offline")

        return await cleanup.delete_chat_result(cid, client=Client())

    result = asyncio.run(
        lifecycle.reset_one("sess_target", delete_callback=failed_delete),
    )

    assert result.ok is True
    assert result.meta.operation_state is OperationState.PARTIAL
    assert result.meta.verification_status == "local_state_removed_cleanup_failed"
    payload = result.to_dict()
    lifecycle_payload = payload["meta"]["details"]["lifecycle"]
    assert lifecycle_payload["session_state"] == "removed"
    assert lifecycle_payload["cleanup"]["state"] == "failed"
    assert lifecycle_payload["cleanup"]["diagnostic_id"].startswith("diag_")
    assert sessions.lookup_session("sess_peer").ok is True
    assert set(cleanup.list_pending_cleanup()) == {"c_target"}


def test_cleanup_callback_exception_is_returned_as_partial_diagnostic_result():
    lifecycle, sessions, cleanup = _service()
    lifecycle.create_session(SimpleNamespace(cid="c_init_failure"))

    async def initialization_failure(_cid: str) -> bool:
        raise PermissionError("expired private cookie")

    result = asyncio.run(
        lifecycle.reset_one(
            "sess_lifecycle",
            delete_callback=initialization_failure,
        )
    )

    assert result.ok is True
    assert result.meta.operation_state is OperationState.PARTIAL
    lifecycle_payload = result.to_dict()["meta"]["details"]["lifecycle"]
    assert lifecycle_payload["cleanup"]["state"] == "failed"
    diagnostic_id = lifecycle_payload["cleanup"]["diagnostic_id"]
    assert diagnostic_id.startswith("diag_")
    assert cleanup.list_pending_cleanup()["c_init_failure"].last_diagnostic_id == diagnostic_id
    assert sessions.list_sessions() == {}


def test_missing_reset_and_expiry_cleanup_are_isolated_and_observable():
    ids = iter(("sess_expiring", "sess_kept"))
    sessions = SessionService(max_age=10, id_factory=lambda: next(ids))
    lifecycle, _, cleanup = _service(sessions=sessions)
    lifecycle.create_session(SimpleNamespace(cid="c_expiring"))
    lifecycle.create_session(SimpleNamespace(cid="c_kept"), retain_chat=True)

    missing = asyncio.run(
        lifecycle.reset_one(
            "sess_missing",
            delete_callback=lambda _cid: _never_delete(),
        )
    )
    assert missing.ok is False
    assert sessions.lookup_session("sess_kept").ok is True
    assert cleanup.list_pending_cleanup() == {}

    sessions._sessions["sess_expiring"].created_at = time.time() - 100
    lookup = lifecycle.lookup_session("sess_expiring")

    assert lookup.ok is False
    assert lookup.to_dict()["meta"]["details"]["lifecycle"]["session_state"] == "absent"
    assert set(cleanup.list_pending_cleanup()) == {"c_expiring"}
    observation = cleanup.get_cleanup_observation("c_expiring")
    assert observation is not None
    assert observation.state is CleanupState.PENDING
    assert sessions.lookup_session("sess_kept").ok is True


async def _never_delete() -> bool:
    raise AssertionError("missing sessions must not trigger cleanup")


def test_reset_all_reports_each_retention_decision():
    ids = iter(("sess_delete", "sess_retain", "sess_local"))
    sessions = SessionService(id_factory=lambda: next(ids))
    cleanup = RemoteChatCleanupManager()
    lifecycle, _, _ = _service(sessions=sessions, cleanup=cleanup)
    lifecycle.create_session(SimpleNamespace(cid="c_delete"))
    lifecycle.create_session(SimpleNamespace(cid="c_retain"), retain_chat=True)
    lifecycle.create_session(SimpleNamespace())
    deleted: list[str] = []

    async def delete(cid: str) -> bool:
        deleted.append(cid)
        return True

    result = asyncio.run(lifecycle.reset_all(delete_callback=delete))

    assert result.ok is True
    assert result.data is not None
    assert result.data.removed_count == 3
    assert result.data.cleanup_failure_count == 0
    states = [item.cleanup.state for item in result.data.conversations]
    assert states == [
        CleanupState.COMPLETED,
        CleanupState.RETAINED,
        CleanupState.NOT_APPLICABLE,
    ]
    assert deleted == ["c_delete"]
    assert sessions.list_sessions() == {}
