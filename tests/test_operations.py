"""Offline, isolated contracts for durable research operation recovery."""

import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.domain import CleanupObservation, CleanupState, OperationState
from src.infrastructure.cleanup_store import CleanupStore
from src.infrastructure.operation_store import RETENTION_SECONDS, OperationStore
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.operations import OperationService
from src.services.research import ResearchRequest, ResearchService, ResearchServiceDependencies


@pytest.fixture(autouse=True)
def _synthetic_account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-test-account")


def _fake_schedule(cid, *, retain_chat, **kwargs):
    return CleanupObservation(
        state=CleanupState.RETAINED if retain_chat else CleanupState.PENDING,
        upstream_chat_id=cid,
    )


@pytest.mark.parametrize("raises", [False, True])
def test_research_result_scrubs_report_when_account_changes_during_cleanup(tmp_path, monkeypatch, raises):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_cleanup_switch", "gemini_research")
    store.update("op_cleanup_switch", state="running", phase="start_returned", upstream_chat_id="c_owned")
    document = SimpleNamespace(ready=True, content="PRIVATE_SYNTHETIC_REPORT", markdown="PRIVATE_SYNTHETIC_REPORT")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=document)))
    async def finalize(*_args, **_kwargs):
        monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
        if raises:
            raise RuntimeError("Synthetic finalizer interruption")
        return CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_owned")
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock(), finalize).get_result("op_cleanup_switch"))
    assert not result.ok and result.data is None
    assert result.meta.verification_status == "account_mismatch_or_unbound"
    assert "PRIVATE_SYNTHETIC_REPORT" not in str(result.to_dict())
    assert store.get("op_cleanup_switch").cleanup_state == "not_scheduled"


def test_research_reads_the_replacement_client_after_initialization(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_reinitialized", "gemini_research")
    store.update("op_reinitialized", state="running", phase="start_returned", upstream_chat_id="c_owned")
    old = SimpleNamespace(fetch_latest_chat_response=AsyncMock(side_effect=AssertionError("Stale client")))
    fresh = SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=SimpleNamespace(
        deep_research_document=SimpleNamespace(ready=True, content="Synthetic report", markdown="Synthetic report"))))
    selected = [old]
    async def initialize():
        selected[0] = fresh
    result = asyncio.run(OperationService(store, lambda: selected[0], initialize).get_result("op_reinitialized"))
    assert result.ok and result.data.report_available
    old.fetch_latest_chat_response.assert_not_awaited()
    fresh.fetch_latest_chat_response.assert_awaited_once()


@pytest.mark.parametrize("cleanup_state", ["completed", "retained", "pending"])
def test_saved_plan_rejection_preserves_failure_without_remote_reads(tmp_path, cleanup_state):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_rejected", "gemini_research")
    store.update("op_rejected", state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_rejected")
    record = store.get("op_rejected")
    assert store.bind_research_turn("op_rejected", account_key=record.account_key, chat_id="c_rejected",
                                    response_id="r_rejected", candidate_id="rc_rejected")
    assert store.reject_research_turn("op_rejected", account_key=record.account_key, chat_id="c_rejected",
                                      response_id="r_rejected", candidate_id="rc_rejected")
    store.update_cleanup("op_rejected", cleanup_state)
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock())
    initializer = AsyncMock()
    service = OperationService(store, lambda: client, initializer)
    for read in (service.get_status, service.get_result):
        result = asyncio.run(read("op_rejected"))
        assert result.error.code.value == "UPSTREAM_REJECTED"
        assert result.meta.verification_status == "research_rejection_observed"
        assert result.data.state is OperationState.FAILED and not result.data.continuation_possible
        assert result.data.cleanup.state.value == cleanup_state
    initializer.assert_not_awaited()
    client.fetch_latest_chat_response.assert_not_awaited()


@pytest.mark.parametrize("same_chat, same_account, verified", [
    (True, True, True),
    (True, False, True),
    (False, True, True),
    (True, True, False),
])
def test_operation_reads_verified_delayed_cleanup_from_its_owned_ledger(tmp_path, same_chat, same_account, verified):
    store = OperationStore(tmp_path / "operations.sqlite")
    operation_id = "op_rejected"
    chat_id = "c_owned"
    store.create(operation_id, "gemini_research")
    store.update(operation_id, state="partial", phase="plan_outcome_unverified", upstream_chat_id=chat_id)
    record = store.get(operation_id)
    assert store.bind_research_turn(operation_id, account_key=record.account_key, chat_id=chat_id,
                                    response_id="r_owned", candidate_id="rc_owned")
    assert store.reject_research_turn(operation_id, account_key=record.account_key, chat_id=chat_id,
                                      response_id="r_owned", candidate_id="rc_owned")
    store.release_cleanup(operation_id)
    store.update_cleanup(operation_id, "pending")

    ledger = CleanupStore()
    ledger_account = record.account_key if same_account else "other-account"
    ledger_chat = chat_id if same_chat else "c_other"
    ledger.schedule(ledger_account, ledger_chat, 0, "test", retain=False)
    if verified:
        assert ledger.record_verified_absence(ledger_account, ledger_chat)

    initialize = AsyncMock()
    finalize = AsyncMock()
    service = OperationService(OperationStore(store.path), lambda: None, initialize, finalize)
    expected = CleanupState.COMPLETED if same_chat and same_account and verified else CleanupState.PENDING
    for read in (service.get_status, service.get_result):
        result = asyncio.run(read(operation_id))
        assert result.data.cleanup.state is expected
        assert result.data.state is OperationState.FAILED
    initialize.assert_not_awaited()
    finalize.assert_not_awaited()


def test_operation_store_survives_reopen_and_contains_metadata_only(tmp_path):
    path = tmp_path / "state.sqlite"
    store = OperationStore(path)
    store.create("op_123", "gemini_research")
    store.update("op_123", state="running", phase="start_returned", upstream_chat_id="c_test")
    record = OperationStore(path).get("op_123")
    assert record and record.upstream_chat_id == "c_test" and record.state == "running"
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(operations)")}
    assert not columns.intersection({"prompt", "report", "cookie", "response", "title"})
    assert path.stat().st_mode & 0o077 == 0


def test_schema_and_completed_state_are_guarded(tmp_path):
    path = tmp_path / "operations.sqlite"
    now = [1000000.0]
    store = OperationStore(path, clock=lambda: now[0])
    store.create("op_1", "gemini_research")
    store.update("op_1", state="completed", phase="report_observed")
    store.update("op_1", state="running", phase="start_returned")
    assert store.get("op_1").state == "completed"
    path.chmod(0o666)
    store.get("op_1")
    assert path.stat().st_mode & 0o077 == 0
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer"):
        store.get("op_1")


def test_v1_store_migrates_existing_operations_as_retained(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE operations (
            operation_id TEXT PRIMARY KEY, operation TEXT NOT NULL, state TEXT NOT NULL, phase TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, upstream_chat_id TEXT,
            upstream_operation_id TEXT, verification_status TEXT NOT NULL)""")
        db.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("old", "gemini_research", "running", "start_returned", time.time(), time.time(),
                    "c_1", None, "upstream_running"))
        db.execute("PRAGMA user_version = 1")
    store = OperationStore(path)
    old = store.get("old")
    assert old and old.retain_chat is True and old.delete_after_seconds is None
    store.create("new", "gemini_research")
    assert store.get("new").retain_chat is False
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6


def test_v2_migrates_to_protected_until_released(tmp_path):
    path = tmp_path / "v2.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE operations (
            operation_id TEXT PRIMARY KEY, operation TEXT NOT NULL, state TEXT NOT NULL, phase TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, upstream_chat_id TEXT,
            upstream_operation_id TEXT, verification_status TEXT NOT NULL, retain_chat INTEGER NOT NULL,
            delete_after_seconds INTEGER, cleanup_state TEXT NOT NULL)""")
        db.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("old", "gemini_research", "running", "start_returned", time.time(), time.time(),
                    "c_1", None, "upstream_running", 0, None, "pending"))
        db.execute("PRAGMA user_version = 2")
    store = OperationStore(path)
    assert store.is_cleanup_protected("c_1")
    store.release_cleanup("old")
    assert not store.is_cleanup_protected("c_1")


def test_expiry_uses_store_clock(tmp_path):
    now = [1000000.0]
    store = OperationStore(tmp_path / "operations.sqlite", clock=lambda: now[0])
    store.create("old", "gemini_research")
    store.update("old", state="running", phase="start_returned", upstream_chat_id="c_protected")
    now[0] += RETENTION_SECONDS + 1
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=None))
    service = OperationService(store, lambda: client, AsyncMock())
    result = asyncio.run(service.get_status("old"))
    assert result.meta.verification_status == "progress_unverified"
    assert any(w.code == "expired_protected_operation" for w in result.warnings)
    assert store.get("old").cleanup_protected is True
    assert store.is_cleanup_protected("c_protected")
    store.create("another", "gemini_research")
    assert store.is_cleanup_protected("c_protected")
    store.release_cleanup("old")
    store.delete_expired("old")
    assert store.get("old") is None


def test_expired_protected_operation_can_retrieve_report_and_release(tmp_path):
    now = [1000000.0]
    store = OperationStore(tmp_path / "operations.sqlite", clock=lambda: now[0])
    store.create("old", "gemini_research")
    store.update("old", state="running", phase="start_returned", upstream_chat_id="c_protected")
    now[0] += RETENTION_SECONDS + 1
    doc = SimpleNamespace(ready=True, content="report", markdown="report")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=doc),
    ))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_protected"))
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock(), finalize).get_result("old"))
    assert result.ok and result.data.report == "report"
    assert any(w.code == "expired_protected_operation" for w in result.warnings)
    assert not store.is_cleanup_protected("c_protected")


def test_concurrent_writers_and_incompatible_schema(tmp_path):
    path = tmp_path / "operations.sqlite"
    store = OperationStore(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: store.create(f"op_{n}", "gemini_research"), range(12)))
    assert all(store.get(f"op_{n}") for n in range(12))
    incompatible = tmp_path / "incompatible.sqlite"
    with sqlite3.connect(incompatible) as db:
        db.execute("CREATE TABLE operations (operation_id TEXT)")
    with pytest.raises(RuntimeError, match="incompatible"):
        OperationStore(incompatible).get("op_1")


def test_concurrent_claims_grant_only_one_creator(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    handle = "op_" + "a" * 32
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.create_or_get(handle, "gemini_research"), range(8)))
    assert sum(created for _, created in claims) == 1
    assert {record.operation_id for record, _ in claims} == {handle}


def test_status_and_result_require_explicit_live_handle(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock())
    service = OperationService(store, lambda: client, AsyncMock())
    assert asyncio.run(service.get_status("missing")).meta.verification_status == "operation_not_found"
    store.create("expired", "gemini_research")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE operations SET created_at=?", (time.time() - RETENTION_SECONDS - 10,))
    assert asyncio.run(service.get_result("expired")).meta.verification_status == "operation_expired"
    assert store.get("expired").cleanup_protected is True
    store.create("nochat", "gemini_research")
    store.update("nochat", state="running", phase="start_returned", upstream_operation_id="agency-placeholder-task-id")
    assert asyncio.run(service.get_status("nochat")).meta.verification_status == "continuation_unavailable"
    client.fetch_latest_chat_response.assert_not_awaited()


def test_status_does_not_mistake_reply_text_for_report_and_result_reads_document(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_1", "gemini_research")
    store.update("op_1", state="running", phase="start_returned", upstream_chat_id="c_1")
    response = SimpleNamespace(text="Finished!", deep_research_document=SimpleNamespace(content=""))
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=response))
    service = OperationService(store, lambda: client, AsyncMock())
    pending = asyncio.run(service.get_status("op_1"))
    assert pending.ok and pending.data and pending.data.state == OperationState.PARTIAL
    assert not pending.data.report_available and pending.data.report is None
    document = SimpleNamespace(ready=True, content="Body text", markdown="Body text\n\n## Sources\n")
    client.fetch_latest_chat_response.return_value = SimpleNamespace(deep_research_document=document)
    result = asyncio.run(service.get_result("op_1"))
    assert result.ok and result.data and result.data.report_available
    assert result.data.report == document.markdown
    assert result.data.artifact and result.data.artifact.kind == "report"
    assert result.data.artifact.delivery == "inline"
    assert result.data.artifact.size_bytes == len(document.markdown.encode())
    assert store.get("op_1").state == "completed"
    assert b"Body text" not in store.path.read_bytes()
    client.fetch_latest_chat_response.return_value = None
    later = asyncio.run(service.get_status("op_1"))
    assert later.data and later.data.state == OperationState.COMPLETED
    assert later.data.report_available and later.data.report is None
    assert store.get("op_1").state == "completed"


def test_result_finalizes_only_own_chat_and_preserves_inline_report(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_1", "gemini_research", delete_after_seconds=42)
    store.update("op_1", state="running", phase="start_returned", upstream_chat_id="c_1")
    doc = SimpleNamespace(ready=True, content="body", markdown="body with sources")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=doc),
    ))
    async def finalizer(cid, **kwargs):
        assert not store.is_cleanup_protected(cid)
        return CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=cid)
    finalize = AsyncMock(side_effect=finalizer)
    service = OperationService(store, lambda: client, AsyncMock(), finalize)
    status = asyncio.run(service.get_status("op_1"))
    assert status.ok and not status.data.report_available
    assert store.is_cleanup_protected("c_1")
    finalize.assert_not_awaited()
    result = asyncio.run(service.get_result("op_1"))
    assert result.ok and result.data.report == "body with sources"
    assert result.data.cleanup.state == CleanupState.COMPLETED
    assert result.data.continuation_possible is False
    finalize.assert_awaited_once_with("c_1", retain_chat=False, delete_after_seconds=42, source="gemini_research")
    assert store.get("op_1").cleanup_state == "completed"
    cached = asyncio.run(service.get_result("op_1"))
    assert cached.meta.verification_status == "research_artifact_verified"
    assert cached.data.report == result.data.report
    assert cached.data.artifacts[0].id == result.data.artifacts[0].id
    assert asyncio.run(service.get_status("op_1")).data.continuation_possible is False


def test_result_respects_explicit_retain_and_records_failed_cleanup(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    doc = SimpleNamespace(ready=True, content="body", markdown="body")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=doc),
    ))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.FAILED, upstream_chat_id="c_1"))
    service = OperationService(store, lambda: client, AsyncMock(), finalize)
    store.create("keep", "gemini_research", retain_chat=True)
    store.update("keep", state="running", phase="start_returned", upstream_chat_id="c_1")
    kept = asyncio.run(service.get_result("keep"))
    assert kept.ok and kept.data.cleanup.state == CleanupState.RETAINED
    finalize.assert_not_awaited()
    store.create("delete", "gemini_research")
    store.update("delete", state="running", phase="start_returned", upstream_chat_id="c_1")
    failed = asyncio.run(service.get_result("delete"))
    assert failed.ok and failed.data.report == "body" and failed.data.cleanup.state == CleanupState.FAILED
    assert store.get("delete").cleanup_state == "failed"
    assert any(w.code == "cleanup_failed" for w in failed.warnings)


def test_pending_cleanup_is_visible_without_hiding_report(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("later", "gemini_research", delete_after_seconds=30)
    store.update("later", state="running", phase="start_returned", upstream_chat_id="c_1")
    doc = SimpleNamespace(ready=True, content="body", markdown="body")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=doc),
    ))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_1"))
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock(), finalize).get_result("later"))
    assert result.ok and result.data.report == "body" and not result.data.continuation_possible
    assert result.data.cleanup.state == CleanupState.PENDING
    assert any(w.code == "cleanup_pending" for w in result.warnings)


def test_invalid_cleanup_is_warned_without_hiding_report(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("invalid", "gemini_research")
    store.update("invalid", state="running", phase="start_returned", upstream_chat_id="c_1")
    doc = SimpleNamespace(ready=True, content="body", markdown="body")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=doc),
    ))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.INVALID_ID, upstream_chat_id="c_1"))
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock(), finalize).get_result("invalid"))
    assert result.ok and result.data.report == "body"
    assert any(w.code == "cleanup_failed" for w in result.warnings)


def test_missing_readback_is_explicitly_unverified(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_missing", "gemini_research")
    store.update("op_missing", state="accepted", phase="start_returned", upstream_chat_id="c_one")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=None))
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock()).get_result("op_missing"))
    assert result.data and not result.data.report_available
    assert result.meta.verification_status == "readback_unverified"
    assert result.warnings[0].code == "readback_unverified"
    assert result.data.read_window_limit == 100


def test_report_outside_first_window_is_recovered_without_another_start(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_older", "gemini_research")
    store.update("op_older", state="accepted", phase="start_returned", upstream_chat_id="c_one")
    report = SimpleNamespace(deep_research_document=SimpleNamespace(ready=True, content="Verified report body"))
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(side_effect=[SimpleNamespace(text="later turn"), report]))
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock()).get_result("op_older"))
    assert result.data and result.data.report == "Verified report body"
    assert result.data.read_window_limit == 100
    assert [call.kwargs["limit"] for call in client.fetch_latest_chat_response.await_args_list] == [10, 100]


def test_cancelled_start_preserves_preconfirm_chat_checkpoint(monkeypatch, tmp_path):
    path = tmp_path / "operations.sqlite"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
    plan = SimpleNamespace(cid="c_checkpoint", research_id="agency-placeholder-task-id")
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(return_value=plan),
        start_deep_research=AsyncMock(side_effect=asyncio.CancelledError),
        wait_for_deep_research=AsyncMock(),
        fetch_latest_chat_response=AsyncMock(return_value=None),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(),
        cleanup_due_remote_chats=AsyncMock(), schedule_chat_cleanup=_fake_schedule,
        resolve_model=lambda model: model,
    ))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.start(ResearchRequest(query="secret query")))
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT operation_id, phase, upstream_chat_id FROM operations").fetchone()
    assert row[1:] == ("confirmation_requested", "c_checkpoint")
    assert b"secret query" not in path.read_bytes()
    assert client.start_deep_research.await_count == 1
    assert OperationStore(path).is_cleanup_protected("c_checkpoint")
    recover = OperationService(OperationStore(path), lambda: client, AsyncMock())
    assert asyncio.run(recover.get_status(row[0])).ok
    assert client.start_deep_research.await_count == 1


def test_cleanup_is_durably_registered_before_research_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    cleanup_path = tmp_path / "cleanup.sqlite"
    manager = RemoteChatCleanupManager(store_path=cleanup_path, account_key_provider=lambda: "test-account")
    async def confirm(plan, chat=None):
        assert "c_checkpoint" in manager.list_pending_cleanup()
        assert manager.list_pending_cleanup()["c_checkpoint"].delete_at > time.time() + 1700
        raise asyncio.CancelledError
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(return_value=SimpleNamespace(cid="c_checkpoint", research_id=None)),
        start_deep_research=AsyncMock(side_effect=confirm), wait_for_deep_research=AsyncMock(),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=manager.schedule_cleanup_result, resolve_model=lambda model: model,
    ))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.start(ResearchRequest(query="test")))
    reopened = RemoteChatCleanupManager(store_path=cleanup_path, account_key_provider=lambda: "test-account")
    assert "c_checkpoint" in reopened.list_pending_cleanup()


def test_cleanup_registration_failure_prevents_research_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(return_value=SimpleNamespace(cid="c_1", research_id=None)),
        start_deep_research=AsyncMock(), wait_for_deep_research=AsyncMock(),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kwargs: CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid),
        resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="test")))
    assert not result.ok
    client.start_deep_research.assert_not_awaited()


def test_plan_timeout_with_observed_chat_checkpoints_guard_before_queue(monkeypatch, tmp_path):
    db_path = tmp_path / "operations.sqlite"
    cleanup_path = tmp_path / "cleanup.sqlite"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(db_path))
    manager = RemoteChatCleanupManager(store_path=cleanup_path, account_key_provider=lambda: "account")
    def schedule(cid, **kwargs):
        assert OperationStore(db_path).is_cleanup_protected(cid)
        return manager.schedule_cleanup_result(cid, **kwargs)
    async def plan(query, chat=None, model=None):
        chat.cid = "c_before_timeout"
        raise asyncio.TimeoutError
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(side_effect=plan), start_deep_research=AsyncMock(),
        wait_for_deep_research=AsyncMock(),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=schedule, resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="q")))
    assert not result.ok and result.data.upstream_chat_id == "c_before_timeout"
    assert OperationStore(db_path).is_cleanup_protected("c_before_timeout")
    assert "c_before_timeout" in manager.list_pending_cleanup()
    client.start_deep_research.assert_not_awaited()


def test_plan_cancellation_keeps_original_cancellation_if_queue_fails(monkeypatch, tmp_path):
    db_path = tmp_path / "operations.sqlite"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(db_path))
    async def plan(query, chat=None, model=None):
        chat.cid = "c_before_cancel"
        raise asyncio.CancelledError
    def fail_schedule(cid, **kwargs):
        assert OperationStore(db_path).is_cleanup_protected(cid)
        raise OSError("test queue failure")
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(side_effect=plan), start_deep_research=AsyncMock(),
        wait_for_deep_research=AsyncMock(),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=fail_schedule, resolve_model=lambda model: model,
    ))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.start(ResearchRequest(query="q")))
    assert OperationStore(db_path).is_cleanup_protected("c_before_cancel")
    client.start_deep_research.assert_not_awaited()


def test_checkpoint_failure_never_confirms_remote_research(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    plan = SimpleNamespace(cid="c_checkpoint", research_id="r_one")
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(return_value=plan),
        start_deep_research=AsyncMock(), wait_for_deep_research=AsyncMock(),
    )
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_checkpoint"))
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(),
        cleanup_due_remote_chats=AsyncMock(), schedule_chat_cleanup=_fake_schedule,
        resolve_model=lambda model: model,
        finalize_chat_cleanup=finalize,
    ))
    original_update = OperationStore.update
    def fail_checkpoint(self, operation_id, **kwargs):
        if kwargs.get("phase") == "plan_created":
            raise OSError("test-only failure")
        return original_update(self, operation_id, **kwargs)
    monkeypatch.setattr(OperationStore, "update", fail_checkpoint)
    result = asyncio.run(service.start(ResearchRequest(query="test")))
    assert not result.ok
    assert result.meta.details["exception_type"] == "OSError"
    client.start_deep_research.assert_not_awaited()
    finalize.assert_awaited_once_with("c_checkpoint", retain_chat=False, delete_after_seconds=None, source="gemini_research")
    assert result.data.cleanup.state == CleanupState.COMPLETED
    assert not OperationStore(tmp_path / "operations.sqlite").is_cleanup_protected("c_checkpoint")


def test_caller_handle_validation_and_duplicate_precede_client_access(monkeypatch, tmp_path):
    path = tmp_path / "operations.sqlite"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
    def provider():
        raise AssertionError("client must not be accessed")
    service = ResearchService(ResearchServiceDependencies(
        client_provider=provider, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=_fake_schedule, resolve_model=lambda model: model,
    ))
    for invalid in ("", "op_secret", "op_" + "A" * 32, "op_" + "f" * 31):
        result = asyncio.run(service.start(ResearchRequest(query="q", operation_id=invalid)))
        assert not result.ok and result.error.code.value == "INVALID_ARGUMENT"
    assert not path.exists()
    handle = "op_" + "a" * 32
    store = OperationStore(path)
    store.create(handle, "gemini_research")
    store.update(handle, state="accepted", phase="plan_created", upstream_chat_id="c_1")
    recovered = asyncio.run(service.start(ResearchRequest(query="q", operation_id=handle)))
    assert recovered.ok and recovered.data.operation_id == handle
    assert recovered.data.continuation_possible and recovered.data.upstream_chat_id == "c_1"
    assert any(w.code == "operation_already_exists" for w in recovered.warnings)


def test_caller_handle_retries_after_cancel_do_not_repeat_start(monkeypatch, tmp_path):
    path = tmp_path / "operations.sqlite"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
    handle = "op_" + "b" * 32
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(return_value=SimpleNamespace(cid="c_1", research_id=None)),
        start_deep_research=AsyncMock(side_effect=asyncio.CancelledError),
        wait_for_deep_research=AsyncMock(),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=_fake_schedule, resolve_model=lambda model: model,
    ))
    request = ResearchRequest(query="test", operation_id=handle)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.start(request))
    recovered = asyncio.run(service.start(request))
    assert recovered.ok and recovered.data.upstream_chat_id == "c_1"
    assert client.create_deep_research_plan.await_count == 1
    assert client.start_deep_research.await_count == 1


def test_operation_recognizes_confirmed_running_research_from_bounded_rpc(tmp_path):
    import json

    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_running", "gemini_research")
    store.update("op_running", state="accepted", phase="start_returned", upstream_chat_id="c_synthetic")
    candidate = [None] * 31
    candidate[8] = [1]
    candidate[30] = [["doc", None, "synthetic", "task", ""]]
    body = [[[None, None, None, [[candidate]]]]]
    wire = json.dumps([["wrb.fr", "hNvQHb", json.dumps(body)]])
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=None),
                             _batch_execute=AsyncMock(return_value=SimpleNamespace(text=wire)))
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock()).get_status("op_running"))
    assert result.ok and result.data.state is OperationState.RUNNING
    assert result.meta.verification_status == "upstream_running_observed"
    assert store.get("op_running").cleanup_protected
    client._batch_execute.assert_awaited_once()


def test_research_progress_requires_running_status_and_document_on_latest_turn():
    from src.infrastructure.rpc_parsers import parse_contract_body

    def candidate(status, document):
        row = [None] * 31
        row[8], row[30] = [status], [document] if document is not None else None
        return row

    doc = ["doc", None, "synthetic", "task", ""]
    def turn(row):
        return [None, None, None, [[row]]]

    for latest in (candidate(2, doc), candidate(1, None)):
        result = parse_contract_body("research.chat_progress", [[turn(latest), turn(candidate(1, doc))]])
        assert result.status == "empty" and result.value is False


def test_late_chat_attach_preserves_other_store_completion_and_never_reopens_cleanup(tmp_path):
    path = tmp_path / "interleaved.sqlite"
    late = OperationStore(path)
    winner = OperationStore(path)
    late.create("op_race", "gemini_research")
    stale_snapshot = late.get("op_race")
    assert stale_snapshot and stale_snapshot.cleanup_protected
    winner.update("op_race", state="completed", phase="report_observed", upstream_chat_id="c_same",
                  verification_status="report_observed")
    winner.release_cleanup("op_race")
    winner.update_cleanup("op_race", "completed")
    assert late.attach_late_chat("op_race", "c_same") is False
    result = late.get("op_race")
    assert result.state == "completed" and result.phase == "report_observed"
    assert result.verification_status == "report_observed" and result.cleanup_state == "completed"
    assert result.cleanup_protected is False
    with pytest.raises(ValueError, match="conflicts"):
        late.attach_late_chat("op_race", "c_other")


def test_late_chat_attach_preserves_timed_out_state_and_checks_identity(tmp_path):
    store = OperationStore(tmp_path / "late-atomic.sqlite")
    store.create("op_late", "gemini_research")
    store.update("op_late", state="timed_out", phase="start_returned", verification_status="completion_not_observed")
    assert store.attach_late_chat("op_late", "c_late") is True
    assert store.attach_late_chat("op_late", "c_late") is True
    with pytest.raises(ValueError, match="conflicts"):
        store.attach_late_chat("op_late", "c_other")
    record = store.get("op_late")
    assert (record.state, record.phase, record.verification_status) == ("timed_out", "start_returned", "completion_not_observed")
    assert record.upstream_chat_id == "c_late" and record.cleanup_protected


def test_existing_unknown_plan_is_claimed_once_without_resending_query(tmp_path, monkeypatch):
    import src.services.research as research
    from src.infrastructure.rpc_parsers import RPCEnvelope, RPCParseResult

    path = tmp_path / "resume.sqlite"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
    handle = "op_" + "d" * 32
    store = OperationStore(path)
    store.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
    store.update(handle, state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_fixture",
                 verification_status="start_outcome_unverified")
    monkeypatch.setattr(research, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=200, text="fixture")))
    monkeypatch.setattr(research, "parse_rpc_envelope", lambda *_: RPCEnvelope(True, 1, ([None],)))
    monkeypatch.setattr(research, "parse_contract_body", lambda *a, **k: RPCParseResult("success", {
        "plan": {"title": "Synthetic plan", "confirm_prompt": "Start research"},
        "metadata": ["c_fixture", "r_fixture", "rc_fixture"],
    }))
    gate = asyncio.Event()
    entered = asyncio.Event()
    async def confirm(plan, chat=None):
        entered.set()
        await gate.wait()
        return SimpleNamespace(text="Started", state="running")
    model = SimpleNamespace(model_name="gemini-pro")
    selected_models = []
    def start_chat(**kwargs):
        selected_models.append(kwargs["model"])
        return SimpleNamespace(cid=kwargs["cid"], metadata=kwargs["metadata"])
    client = SimpleNamespace(
        _model_registry={"pro": model}, _resolve_model_by_name=lambda _: model,
        start_chat=start_chat,
        start_deep_research=AsyncMock(side_effect=confirm),
        create_deep_research_plan=AsyncMock(), wait_for_deep_research=AsyncMock(),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=_fake_schedule, resolve_model=lambda model: model,
    ))
    async def run():
        first = asyncio.create_task(service.start(ResearchRequest(query="new ignored", operation_id=handle, retain_chat=True)))
        await entered.wait()
        second = await service.start(ResearchRequest(query="another ignored", operation_id=handle))
        assert second.data.operation_id == handle
        assert client.start_deep_research.await_count == 1
        gate.set()
        result = await first
        assert result.ok and result.data.state.value == "running"
        third = await service.start(ResearchRequest(query="retry ignored", operation_id=handle))
        assert third.data.operation_id == handle and client.start_deep_research.await_count == 1
    asyncio.run(run())
    client.create_deep_research_plan.assert_not_awaited()
    assert selected_models == [model]
    assert store.get(handle).retain_chat is False
    assert store.get(handle).requested_model == "pro" and store.get(handle).thinking_level == "extended"
    assert store.get(handle).phase == "confirmation_returned"


def test_two_stores_cannot_claim_one_confirmation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "claim.sqlite"
    first = OperationStore(path)
    second = OperationStore(path)
    first.create("op_claim", "gemini_research")
    first.update("op_claim", state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_claim")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.claim_research_confirmation(
            "op_claim", expected_phase="plan_outcome_unverified"), (first, second)))
    assert sorted(results) == [False, True]


@pytest.mark.parametrize("case", ["empty", "rejected", "changed_shape", "missing", "legacy_metadata", "already_confirming"])
def test_resume_never_confirms_without_proven_unclaimed_plan(tmp_path, monkeypatch, case):
    import src.services.research as research
    from src.infrastructure.rpc_parsers import RPCEnvelope, RPCParseResult

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / f"{case}.sqlite"))
    handle = "op_" + "e" * 32
    store = OperationStore()
    store.create(handle, "gemini_research", requested_model=None if case == "legacy_metadata" else "pro",
                 thinking_level="extended")
    phase = "confirmation_requested" if case == "already_confirming" else "plan_outcome_unverified"
    store.update(handle, state="partial", phase=phase, upstream_chat_id="c_safe")
    monkeypatch.setattr(research, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=200, text="fixture")))
    envelope = RPCEnvelope(False, 0, ()) if case == "missing" else RPCEnvelope(
        True, 1, ([None],), reject_code=7 if case == "rejected" else None,
    )
    monkeypatch.setattr(research, "parse_rpc_envelope", lambda *_: envelope)
    monkeypatch.setattr(research, "parse_contract_body", lambda *a, **k: RPCParseResult(
        "changed_shape" if case == "changed_shape" else "empty"))
    client = SimpleNamespace(start_deep_research=AsyncMock(), create_deep_research_plan=AsyncMock())
    initializer = AsyncMock()
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=initializer, cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=_fake_schedule, resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="ignored", operation_id=handle)))
    assert result.data.operation_id == handle
    assert store.get(handle).phase == phase and store.is_cleanup_protected("c_safe")
    client.start_deep_research.assert_not_awaited()
    client.create_deep_research_plan.assert_not_awaited()
    if case in {"legacy_metadata", "already_confirming"}:
        initializer.assert_not_awaited()


def test_account_binding_blocks_cross_account_and_legacy_remote_reads(monkeypatch, tmp_path):
    from src.infrastructure.account_identity import current_account_key

    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-a")
    store = OperationStore(tmp_path / "ops.sqlite")
    store.create("op_a", "research")
    store.update("op_a", state="running", phase="start_returned", upstream_chat_id="c_test")
    record = store.get("op_a")
    assert record.account_key == current_account_key() and "synthetic-account-a" not in str(record)
    provider = AsyncMock()
    initializer = AsyncMock()
    finalize = AsyncMock()
    service = OperationService(store, provider, initializer, finalize)
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-b")
    result = asyncio.run(service.get_result("op_a"))
    assert not result.ok and result.meta.verification_status == "account_mismatch_or_unbound"
    provider.assert_not_called()
    initializer.assert_not_awaited()
    finalize.assert_not_awaited()
    assert store.get("op_a").cleanup_protected
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-a")
    assert store.get("op_a").account_key == current_account_key()
    sentinel = object()
    service._read_report = AsyncMock(return_value=sentinel)
    assert asyncio.run(service.get_result("op_a")) is sentinel
    service._read_report.assert_awaited_once()

    # Simulate a migrated pre-v6 record; an authenticated account cannot claim it.
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE operations SET account_key=NULL,account_binding_pending=0 WHERE operation_id='op_a'")
    assert store.claim_worker("op_a", "worker")
    assert not store.bind_pending_account("op_a", worker_token="worker", account_key=current_account_key())
    result = asyncio.run(service.get_result("op_a"))
    assert not result.ok and result.meta.verification_status == "account_mismatch_or_unbound"
    provider.assert_not_called()


def test_new_unbound_operation_can_be_bound_once_by_owning_worker(monkeypatch, tmp_path):
    from src.infrastructure.account_identity import current_account_key

    monkeypatch.delenv("GEMINI_PSID", raising=False)
    store = OperationStore(tmp_path / "ops.sqlite")
    store.create("op_new", "media")
    assert store.get("op_new").account_binding_pending
    assert store.claim_worker("op_new", "owner")
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-a")
    account = current_account_key()
    assert not store.bind_pending_account("op_new", worker_token="other", account_key=account)
    assert store.bind_pending_account("op_new", worker_token="owner", account_key=account)
    assert not store.bind_pending_account("op_new", worker_token="owner", account_key=account)
    assert store.get("op_new").account_key == account
    assert not store.get("op_new").account_binding_pending


def test_v5_migration_keeps_existing_operations_unbound(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    path = tmp_path / "v5.sqlite"
    store = OperationStore(path)
    store.create("old", "research")
    store.update("old", state="running", phase="start_returned", upstream_chat_id="c_old")
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE operations DROP COLUMN account_key")
        db.execute("ALTER TABLE operations DROP COLUMN account_binding_pending")
        db.execute("PRAGMA user_version=5")
    monkeypatch.setenv("GEMINI_PSID", "synthetic-current-account")
    record = OperationStore(path).get("old")
    assert record.account_key is None and not record.account_binding_pending
    client = AsyncMock()
    service = OperationService(OperationStore(path), client, AsyncMock(), AsyncMock())
    result = asyncio.run(service.get_result("old"))
    assert not result.ok and result.meta.verification_status == "account_mismatch_or_unbound"
    client.assert_not_called()


def test_legacy_unbound_without_psid_never_runs_account_switching_initializer(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    store = OperationStore(tmp_path / "legacy.sqlite")
    store.create("legacy", "research")
    store.update("legacy", state="running", phase="start_returned", upstream_chat_id="c_legacy")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE operations SET account_binding_pending=0 WHERE operation_id='legacy'")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock())
    async def switching_initializer():
        monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
    initializer = AsyncMock(side_effect=switching_initializer)
    result = asyncio.run(OperationService(store, lambda: client, initializer).get_status("legacy"))
    assert not result.ok and result.meta.verification_status == "account_mismatch_or_unbound"
    initializer.assert_not_awaited()
    client.fetch_latest_chat_response.assert_not_awaited()
    assert store.get("legacy").cleanup_protected


def test_account_change_during_report_initialization_prevents_read(monkeypatch, tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    store.create("bound", "research")
    store.update("bound", state="running", phase="start_returned", upstream_chat_id="c_bound")
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock())
    async def switching_initializer():
        monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
    result = asyncio.run(OperationService(store, lambda: client, switching_initializer).get_status("bound"))
    assert not result.ok and result.meta.verification_status == "account_mismatch_or_unbound"
    client.fetch_latest_chat_response.assert_not_awaited()
    assert store.get("bound").cleanup_protected


def test_account_change_during_first_report_read_prevents_second_read(monkeypatch, tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    store.create("bound", "research")
    store.update("bound", state="running", phase="start_returned", upstream_chat_id="c_bound")
    async def switching_read(*args, **kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
        return None
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(side_effect=switching_read))
    result = asyncio.run(OperationService(store, lambda: client, AsyncMock()).get_result("bound"))
    assert not result.ok and result.meta.verification_status == "account_mismatch_or_unbound"
    assert client.fetch_latest_chat_response.await_count == 1
    assert store.get("bound").cleanup_protected
