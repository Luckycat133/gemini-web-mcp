"""Full report durability, privacy and release-boundary verification."""

import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import CleanupObservation, CleanupState, OperationState
from src.infrastructure.operation_store import OperationStore
from src.services.operations import OperationService
from src.services.research_result_storage import (
    RESEARCH_REPORT_PREVIEW_CHARS, ResearchResultUnavailable, load_research_result,
    research_destination, save_research_result,
)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-durable-research-owner")
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_report", "gemini_research")
    store.update("op_report", state="running", phase="plan_created", upstream_chat_id="c_owned")
    record = store.get("op_report")
    assert store.bind_research_turn("op_report", account_key=record.account_key,
        chat_id="c_owned", response_id="r_plan", candidate_id="rc_plan")
    store.update("op_report", state="running", phase="start_returned")
    return store


def document(text="# Synthetic report\n\nComplete report body."):
    return SimpleNamespace(ready=True, content=text, markdown=text, sources=[])


def test_each_read_of_cleaned_report_verifies_full_bytes_once(ledger, monkeypatch):
    from src.services import research_result_storage

    artifact = save_research_result(ledger, ledger.get("op_report"), document())
    ledger.release_cleanup("op_report")
    ledger.update_cleanup("op_report", CleanupState.COMPLETED.value)
    verify = research_result_storage._read_report_bytes
    calls = []

    def count(path, **kwargs):
        calls.append(path)
        return verify(path, **kwargs)

    monkeypatch.setattr(research_result_storage, "_read_report_bytes", count)
    initialize = AsyncMock()
    cleanup = AsyncMock()
    service = OperationService(ledger, lambda: None, initialize, cleanup)
    status = asyncio.run(service.get_status("op_report"))
    assert status.ok and status.data.report_available and status.data.report is None and not status.data.artifacts
    assert calls == [Path(artifact.local_path)]

    result = asyncio.run(service.get_result("op_report"))
    assert result.ok and result.data.artifacts[0].id == artifact.id
    assert result.data.report == document().content
    assert calls == [Path(artifact.local_path)] * 2

    path = Path(artifact.local_path)
    path.write_bytes(path.read_bytes()[:-1] + b"!")
    after_change = asyncio.run(service.get_status("op_report"))
    assert after_change.data.state == OperationState.PARTIAL and not after_change.data.report_available
    assert calls == [path] * 3
    initialize.assert_not_awaited()
    cleanup.assert_not_awaited()


def test_full_report_saved_and_independently_verified_before_cleanup_then_restart_needs_no_auth(ledger):
    text = "# Report\n\n" + "Long research evidence.\n" * 400
    doc = document(text)
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=doc)))
    async def finalize(cid, **kwargs):
        record = ledger.get("op_report")
        rows = ledger.get_media_artifacts("op_report")
        assert record.phase == "research_report_saved" and not record.cleanup_protected
        assert len(rows) == 1 and rows[0]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
        assert Path(rows[0]["local_path"]).read_text() == text
        return CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=cid)
    original = asyncio.run(OperationService(ledger, lambda: client, AsyncMock(), finalize).get_result("op_report"))
    assert original.ok and original.data.state is OperationState.COMPLETED
    assert original.data.report == text[:RESEARCH_REPORT_PREVIEW_CHARS]
    artifact = original.data.artifacts[0]
    assert artifact.size_bytes == len(text.encode())
    assert original.data.artifact.sha256 == hashlib.sha256(original.data.report.encode()).hexdigest()
    assert original.data.artifact.size_bytes == len(original.data.report.encode())
    assert artifact.verification.details["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert artifact.size_bytes > original.data.artifact.size_bytes
    initializer = AsyncMock(side_effect=AssertionError("Cached report must not authenticate"))
    def provider():
        raise AssertionError("Cached report must not read Gemini")
    reopened = OperationStore(ledger.path)
    second = asyncio.run(OperationService(reopened, provider, initializer, AsyncMock()).get_result("op_report"))
    assert second.ok and second.data.artifacts == original.data.artifacts
    assert second.data.report == original.data.report
    initializer.assert_not_awaited()
    assert artifact.local_path and Path(artifact.local_path).stat().st_mode & 0o077 == 0
    assert Path(artifact.local_path).parent.stat().st_mode & 0o077 == 0
    assert "Long research evidence".encode() not in ledger.path.read_bytes()
    with sqlite3.connect(ledger.path) as db:
        assert len(db.execute("SELECT * FROM operation_artifacts").fetchall()) == 1


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink", "directory", "parent_symlink"])
def test_invalid_saved_report_is_partial_without_remote_read_or_cleanup(ledger, tmp_path, damage):
    record = ledger.get("op_report")
    artifact = save_research_result(ledger, record, document())
    path = Path(artifact.local_path)
    if damage == "parent_symlink":
        moved = tmp_path / "moved"
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
    else:
        path.unlink()
        if damage == "changed":
            path.write_text("Changed report")
        elif damage == "symlink":
            target = tmp_path / "outside.md"
            target.write_text("Outside report")
            path.symlink_to(target)
        elif damage == "directory":
            path.mkdir()
    init, cleanup = AsyncMock(), AsyncMock()
    def provider():
        raise AssertionError("Corruption must not fall back to remote")
    result = asyncio.run(OperationService(ledger, provider, init, cleanup).get_result("op_report"))
    assert result.ok and result.data.state is OperationState.PARTIAL
    assert not result.data.report_available and not result.data.artifacts and result.data.report is None
    assert ledger.get("op_report").cleanup_protected
    init.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.parametrize("damage", ["delete", "replace", "change_account", "lose_lease"])
def test_cleanup_rehashes_saved_report_after_await_and_preserves_guard_on_changed_scope(ledger, monkeypatch, damage):
    artifact = save_research_result(ledger, ledger.get("op_report"), document())
    path = Path(artifact.local_path)
    if damage == "lose_lease":
        with ledger._connect() as db:
            db.execute("UPDATE operations SET worker_token='original' WHERE operation_id='op_report'")
    async def initialize():
        if damage == "delete":
            path.unlink()
        elif damage == "replace":
            path.write_text("Altered during initialization")
        elif damage == "change_account":
            monkeypatch.setenv("GEMINI_PSID", "another-synthetic-account")
        else:
            with ledger._connect() as db:
                db.execute("UPDATE operations SET worker_token='replacement' WHERE operation_id='op_report'")
    cleanup = AsyncMock()
    result = asyncio.run(OperationService(ledger, lambda: None, initialize, cleanup,
        worker_token="original" if damage == "lose_lease" else None).get_result("op_report"))
    assert ledger.get("op_report").cleanup_protected
    assert result.data is None or not result.data.report_available
    cleanup.assert_not_awaited()


@pytest.mark.parametrize("field", ["upstream_chat_id", "upstream_response_id", "upstream_candidate_id", "account_key"])
def test_report_metadata_cannot_be_rebound_to_a_different_original_identity(ledger, field):
    record = ledger.get("op_report")
    save_research_result(ledger, record, document())
    with ledger._connect() as db:
        db.execute(f"UPDATE operations SET {field}=? WHERE operation_id=?", ("different", record.operation_id))
    with pytest.raises(ResearchResultUnavailable):
        load_research_result(ledger, record, include_text=True)


def test_saved_report_account_isolation_does_not_read_file(ledger, monkeypatch):
    save_research_result(ledger, ledger.get("op_report"), document("PRIVATE_SYNTHETIC_MARKER"))
    monkeypatch.setenv("GEMINI_PSID", "another-owner")
    read = AsyncMock()
    result = asyncio.run(OperationService(ledger, lambda: None, read, read).get_result("op_report"))
    assert not result.ok and result.data is None
    assert "PRIVATE_SYNTHETIC_MARKER" not in str(result.to_dict())
    read.assert_not_awaited()


@pytest.mark.parametrize("ready,content", [(False, "A plan"), (True, ""), (True, "   "), (1, "Plan")])
def test_saver_requires_an_actual_ready_nonempty_report_not_a_completed_plan(ledger, ready, content):
    with pytest.raises(ResearchResultUnavailable):
        save_research_result(ledger, ledger.get("op_report"), SimpleNamespace(
            ready=ready, content=content, markdown="# A plan"))
    assert not ledger.get_media_artifacts("op_report")
    assert ledger.get("op_report").cleanup_protected


def test_report_cannot_be_replaced_or_published_under_an_unrelated_locator(ledger, tmp_path):
    record = ledger.get("op_report")
    original = save_research_result(ledger, record, document())
    with pytest.raises(ResearchResultUnavailable):
        save_research_result(ledger, ledger.get("op_report"), document("Replacement report"))
    with pytest.raises(ValueError):
        ledger.save_research_result_artifact(ledger.get("op_report"), replace(original, local_path=str(tmp_path / "other.md")))
    assert Path(original.local_path).read_text() == document().content


def test_status_does_not_save_or_release_a_fresh_report(ledger):
    client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
        return_value=SimpleNamespace(deep_research_document=document())))
    cleanup = AsyncMock()
    result = asyncio.run(OperationService(ledger, lambda: client, AsyncMock(), cleanup).get_status("op_report"))
    assert result.ok and not result.data.report_available
    assert result.data.report is None and not result.data.artifacts
    assert not ledger.get_media_artifacts("op_report")
    assert not research_destination(ledger, ledger.get("op_report")).exists()
    assert ledger.get("op_report").cleanup_protected
    cleanup.assert_not_awaited()
    client.fetch_latest_chat_response.assert_not_awaited()


def test_legacy_deleted_report_is_partial_without_authentication_or_regeneration(ledger):
    ledger.update("op_report", state="completed", phase="report_observed")
    ledger.update_cleanup("op_report", "completed")
    initialize = AsyncMock(side_effect=AssertionError("Deleted legacy report cannot be fetched"))
    result = asyncio.run(OperationService(ledger, lambda: None, initialize).get_result("op_report"))
    assert result.ok and result.data.state is OperationState.PARTIAL
    assert result.data.verification_status == "legacy_report_artifact_unavailable"
    assert not result.data.report_available
    initialize.assert_not_awaited()


@pytest.mark.parametrize("changed", [False, True])
def test_cleanup_transport_failure_keeps_valid_report_but_never_claims_missing_artifact(ledger, changed):
    artifact = save_research_result(ledger, ledger.get("op_report"), document())
    async def cleanup(*args, **kwargs):
        if changed:
            Path(artifact.local_path).unlink()
        raise OSError("Synthetic cleanup transport interruption")
    result = asyncio.run(OperationService(ledger, lambda: None, AsyncMock(), cleanup).get_result("op_report"))
    if changed:
        assert result.data.state is OperationState.PARTIAL and not result.data.report_available
        assert not result.data.artifacts
    else:
        assert result.data.state is OperationState.COMPLETED and result.data.report_available
        assert result.data.artifacts[0].id == artifact.id
        assert any(w.code == "cleanup_unverified" for w in result.warnings)


def test_cached_report_remains_usable_when_cleanup_authentication_fails(ledger):
    artifact = save_research_result(ledger, ledger.get("op_report"), document())
    cleanup = AsyncMock()
    initialize = AsyncMock(side_effect=OSError("Synthetic authentication transport unavailable"))
    result = asyncio.run(OperationService(ledger, lambda: None, initialize, cleanup).get_result("op_report"))
    assert result.ok and result.data.report_available and result.data.artifacts[0].id == artifact.id
    assert ledger.get("op_report").cleanup_protected
    assert any(w.code == "cleanup_unverified" for w in result.warnings)
    cleanup.assert_not_awaited()
