"""Metadata-only persistence, migrations, identity isolation, and atomic claims."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os

import pytest

from src.domain import Artifact, ArtifactKind, ArtifactState, OperationState
from src.infrastructure.state_store import (
    RETENTION_SECONDS, SCHEMA_VERSION, ArtifactLocator, CleanupJobRecord,
    OperationRecord, StateStore, StateStoreError, get_default_state_store,
)


def _store(path, *, clock=lambda: 10):
    return StateStore(path, clock=clock)


def _operation(**changes):
    return replace(OperationRecord("op_test", "scope_a", "research", OperationState.RUNNING, 10, 10, 10 + RETENTION_SECONDS), **changes)


def _cleanup(**changes):
    return replace(CleanupJobRecord("job_test", "scope_a", "c_test", "pending", 10, 10, 10, 10 + RETENTION_SECONDS), **changes)


def test_lazy_private_store_and_stable_anonymous_identity(tmp_path):
    path = tmp_path / "private" / "state.sqlite3"
    store = _store(path)
    assert not path.parent.exists()
    scope = store.scope_for("private-test-cookie-material")
    assert scope == _store(path).scope_for("private-test-cookie-material")
    assert scope != store.scope_for("other-cookie-material")
    store.operations.create(_operation(scope_id=scope))
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_suffix(".sqlite3.key").stat().st_mode & 0o777 == 0o600
    assert b"private-test-cookie-material" not in path.read_bytes()


@pytest.mark.parametrize("missing_api", ["O_NOFOLLOW", "getuid"])
def test_unsupported_file_protections_fail_before_creating_private_state(tmp_path, monkeypatch, missing_api):
    store = _store(tmp_path / "private" / "state.sqlite3")
    with monkeypatch.context() as context:
        context.delattr(os, missing_api)
        with pytest.raises(StateStoreError, match="Windows authenticated runtime is not supported"):
            store.scope_for("offline-test-material")
        with pytest.raises(StateStoreError, match="POSIX file protections"):
            store.operations.get("scope_a", "op_test")
    assert not store.path.parent.exists()


def test_default_store_follows_current_environment_without_import_side_effects(tmp_path, monkeypatch):
    first = tmp_path / "first.sqlite3"
    second = tmp_path / "second.sqlite3"
    monkeypatch.setenv("GEMINI_STATE_DB_PATH", str(first))
    a = get_default_state_store()
    assert not first.exists()
    monkeypatch.setenv("GEMINI_STATE_DB_PATH", str(second))
    b = get_default_state_store()
    assert b is not a
    assert not second.exists()


def test_private_content_is_dropped_before_serialization(tmp_path):
    store = _store(tmp_path / "state.sqlite3")
    artifact = Artifact(id="artifact_test", kind=ArtifactKind.REPORT, state=ArtifactState.REMOTE,
                        uri="https://example.invalid/report", title="private-report-title",
                        source_chat_id="c_test")
    store.operations.create(_operation(artifacts=(ArtifactLocator.from_artifact(artifact),)))
    stored = _store(store.path).operations.get("scope_a", "op_test")
    assert stored.artifacts[0].artifact().title is None
    assert b"private-report-title" not in store.path.read_bytes()
    with pytest.raises(ValueError, match="persistent locator"):
        ArtifactLocator.from_artifact(replace(artifact, uri="data:text/plain,private-report-body"))
    with pytest.raises(ValueError, match="Unsupported"):
        store.operations.update("scope_a", "op_test", prompt="private prompt")


def test_schema_migrates_old_metadata_and_rejects_future_version(tmp_path):
    store = _store(tmp_path / "state.sqlite3")
    store.operations.create(_operation(output_dir=str(tmp_path), retain_chat=False, delete_after_seconds=90))
    with store.transaction() as conn:
        # Remove the v2/v3 columns to simulate the prior metadata-only schema.
        for column in ("output_dir", "retain_chat", "delete_after_seconds", "lease_id", "lease_until"):
            conn.execute(f"ALTER TABLE operations DROP COLUMN {column}")
        conn.execute("PRAGMA user_version=1")
    restored = _store(store.path).operations.get("scope_a", "op_test")
    assert restored.retain_chat is True
    assert restored.output_dir is None
    assert restored.lease_id is None
    with store.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(StateStoreError, match="newer"):
        _store(store.path).operations.get("scope_a", "op_test")


def test_two_connections_create_one_idempotent_operation(tmp_path):
    path = tmp_path / "state.sqlite3"
    def create(index):
        return _store(path).operations.create(_operation(operation_id=f"op_{index}", idempotency_key="opaque_same_key"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sum(created for _record, created in results) == 1
    assert len({record.operation_id for record, _created in results}) == 1
    with pytest.raises(ValueError, match="another operation kind"):
        _store(path).operations.create(_operation(operation_id="op_other", operation_type="video", idempotency_key="opaque_same_key"))


def test_scope_filters_handles_and_cleanup_job_ids(tmp_path):
    store = _store(tmp_path / "state.sqlite3")
    store.operations.create(_operation())
    store.cleanup_jobs.upsert(_cleanup())
    assert store.operations.get("scope_b", "op_test") is None
    assert store.cleanup_jobs.get("scope_b", "c_test") is None
    assert store.cleanup_jobs.get_by_job_id("scope_b", "job_test") is None
    assert store.cleanup_jobs.list("scope_b") == ()


def test_cleanup_claim_renew_and_late_finish_cannot_revive_cancelled_job(tmp_path):
    store = _store(tmp_path / "state.sqlite3", clock=lambda: 10)
    job = store.cleanup_jobs.upsert(_cleanup())
    claimed = store.cleanup_jobs.claim("scope_a", "c_test", expected_version=job.version,
                                       lease_id="lease_a", lease_until=20, now=10)
    assert claimed.attempts == 1
    other = _store(store.path, clock=lambda: 10)
    assert other.cleanup_jobs.claim("scope_a", "c_test", expected_version=claimed.version,
                                    lease_id="lease_b", lease_until=30, now=11) is None
    assert store.cleanup_jobs.renew("scope_a", "c_test", lease_id="lease_a", lease_until=40)
    assert other.cleanup_jobs.claim("scope_a", "c_test", expected_version=store.cleanup_jobs.get("scope_a", "c_test").version,
                                    lease_id="lease_b", lease_until=50, now=21) is None
    current = store.cleanup_jobs.get("scope_a", "c_test")
    assert store.cleanup_jobs.update("scope_a", "c_test", expected_version=current.version, state="cancelled")
    assert store.cleanup_jobs.update("scope_a", "c_test", lease_id="lease_a", state="completed",
                                     verification_status="verified_absent") is None
    assert store.cleanup_jobs.get("scope_a", "c_test").state == "cancelled"


def test_cleanup_expired_lease_can_be_reclaimed_but_old_lease_cannot_finish(tmp_path):
    store = _store(tmp_path / "state.sqlite3", clock=lambda: 10)
    job = store.cleanup_jobs.upsert(_cleanup())
    first = store.cleanup_jobs.claim("scope_a", "c_test", expected_version=job.version,
                                     lease_id="lease_a", lease_until=20, now=10)
    second = _store(store.path).cleanup_jobs.claim("scope_a", "c_test", expected_version=first.version,
                                                       lease_id="lease_b", lease_until=40, now=21)
    assert second.attempts == 2
    assert store.cleanup_jobs.update("scope_a", "c_test", lease_id="lease_a", state="completed",
                                     verification_status="verified_absent") is None
    with pytest.raises(ValueError, match="observed absence"):
        store.cleanup_jobs.update("scope_a", "c_test", lease_id="lease_b", state="completed")
    assert store.cleanup_jobs.update("scope_a", "c_test", lease_id="lease_b", state="completed",
                                     verification_status="verified_absent").state == "completed"


def test_seven_day_pruning_preserves_pending_failed_and_running_cleanup(tmp_path):
    store = _store(tmp_path / "state.sqlite3", clock=lambda: RETENTION_SECONDS * 3)
    store.operations.create(_operation())
    for index, state in enumerate(("pending", "failed", "running", "completed", "retained", "cancelled")):
        store.cleanup_jobs.upsert(_cleanup(job_id=f"job_{index}", resource_id=f"c_{index}", state=state,
                                          verification_status="verified_absent" if state == "completed" else "not_observed"))
    assert store.operations.prune() == 1
    assert store.cleanup_jobs.prune() == 3
    assert {job.state for job in store.cleanup_jobs.list("scope_a")} == {"pending", "failed", "running"}


def test_unsafe_database_symlink_or_permissions_are_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("private")
    link = tmp_path / "state.sqlite3"
    link.symlink_to(target)
    with pytest.raises(StateStoreError, match="unsafe"):
        _store(link).operations.get("scope_a", "op_test")
    link.unlink()
    link.touch(mode=0o644)
    with pytest.raises(StateStoreError, match="unsafe"):
        _store(link).operations.get("scope_a", "op_test")
    assert target.read_text() == "private"


def test_lazy_pruning_removes_expired_locator_bytes_but_preserves_active_cleanup(tmp_path):
    now = [10]
    store = _store(tmp_path / "state.sqlite3", clock=lambda: now[0])
    locator = ArtifactLocator("artifact_expiring", "report", "remote", uri="https://example.invalid/private-expiring-locator")
    store.operations.create(_operation(artifacts=(locator,)))
    store.cleanup_jobs.upsert(_cleanup())
    now[0] += RETENTION_SECONDS
    assert store.operations.get("scope_a", "op_test") is None
    assert store.cleanup_jobs.get("scope_a", "c_test") is not None
    assert b"private-expiring-locator" not in store.path.read_bytes()
