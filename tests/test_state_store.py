"""Metadata-only persistence, migrations, identity isolation, and atomic claims."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import multiprocessing
import os
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import src.infrastructure.state_store as state_module
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


def _create_in_process_after_busy(path, index, ready, busy, results):
    """Independent spawned SQLite client; every other I/O path is absent."""
    original_connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            try:
                return super().execute(statement, *args, **kwargs)
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY:
                    busy.set()
                raise

    state_module.sqlite3.connect = lambda *args, **kwargs: original_connect(*args, **kwargs, factory=ObservedConnection)
    try:
        ready.wait(timeout=10)
        record, created = _store(path).operations.create(_operation(operation_id=f"op_{index}", idempotency_key="opaque_same_key"))
        results.put(("ok", record.operation_id, created))
    except Exception as error:
        results.put(("error", type(error).__name__, False))
    finally:
        state_module.sqlite3.connect = original_connect


def _sqlite_error(code):
    error = sqlite3.OperationalError("private-sql-argument-must-not-be-exposed")
    error.sqlite_errorcode = code
    return error


def _virtual_initialization_clock(monkeypatch):
    now, waits = [0.0], []

    def sleep(duration):
        waits.append(duration)
        now[0] += duration

    monkeypatch.setattr(state_module, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    return now, waits


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


@pytest.mark.parametrize("existing_wal", [False, True])
def test_initialization_waits_for_real_sqlite_lock_before_body(tmp_path, monkeypatch, existing_wal):
    store = _store(tmp_path / "state.sqlite3")
    store._prepare()
    original_connect = sqlite3.connect
    blocker = original_connect(store.path, timeout=0, isolation_level=None)
    if existing_wal:
        blocker.execute("PRAGMA journal_mode=WAL").close()
    blocker.execute("BEGIN IMMEDIATE")
    busy, errors, body_calls = threading.Event(), [], []

    class ObservedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            try:
                return super().execute(statement, *args, **kwargs)
            except sqlite3.OperationalError as error:
                errors.append((statement, error.sqlite_errorcode))
                busy.set()
                raise

    monkeypatch.setattr(state_module.sqlite3, "connect", lambda *args, **kwargs: original_connect(*args, **kwargs, factory=ObservedConnection))

    def enter():
        with store.transaction() as connection:
            body_calls.append("entered")
            assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 3000

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(enter)
            try:
                assert busy.wait(timeout=5)
                assert body_calls == []
                assert not future.done()
            finally:
                blocker.execute("COMMIT")
            future.result(timeout=5)
    finally:
        blocker.close()
    expected = "BEGIN IMMEDIATE" if existing_wal else "PRAGMA journal_mode=WAL"
    assert errors and {statement for statement, _code in errors} == {expected}
    assert all(code & 0xFF == sqlite3.SQLITE_BUSY for _statement, code in errors)
    assert body_calls == ["entered"]


def test_first_initialization_and_idempotency_across_spawned_processes(tmp_path):
    store = _store(tmp_path / "state.sqlite3")
    store._prepare()
    blocker = sqlite3.connect(store.path, timeout=0, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    context = multiprocessing.get_context("spawn")
    ready, busy = context.Barrier(3), [context.Event(), context.Event()]
    results = context.Queue()
    workers = [context.Process(target=_create_in_process_after_busy,
                               args=(store.path, index, ready, busy[index], results)) for index in range(2)]
    try:
        for worker in workers:
            worker.start()
        ready.wait(timeout=10)
        try:
            assert all(event.wait(timeout=5) for event in busy)
        finally:
            blocker.execute("COMMIT")
        received = [results.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert all(status == "ok" for status, _operation_id, _created in received)
        assert sum(created for _status, _operation_id, created in received) == 1
        assert len({operation_id for _status, operation_id, _created in received}) == 1
        with store.transaction() as connection:
            assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
    finally:
        blocker.close()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(timeout=10)
        results.close()
        results.join_thread()


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_BUSY_RECOVERY])
def test_initialization_retries_only_busy_family_before_body(tmp_path, monkeypatch, code):
    _now, waits = _virtual_initialization_clock(monkeypatch)
    original_connect, attempts, bodies = sqlite3.connect, [], []

    class BusyOnceConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            attempts.append(statement)
            if statement == "PRAGMA journal_mode=WAL" and attempts.count(statement) == 1:
                raise _sqlite_error(code)
            return super().execute(statement, *args, **kwargs)

    monkeypatch.setattr(state_module.sqlite3, "connect", lambda *args, **kwargs: original_connect(*args, **kwargs, factory=BusyOnceConnection))
    with _store(tmp_path / "state.sqlite3").transaction():
        bodies.append("entered")
    assert attempts.count("PRAGMA journal_mode=WAL") == 2
    assert attempts.count("PRAGMA secure_delete=ON") == 1
    assert attempts.count("BEGIN IMMEDIATE") == 1
    assert attempts.count("COMMIT") == 1
    assert waits and bodies == ["entered"]


@pytest.mark.parametrize("code", [sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_CORRUPT])
def test_nonbusy_initialization_errors_fail_immediately_and_safely(tmp_path, monkeypatch, code):
    _now, waits = _virtual_initialization_clock(monkeypatch)
    original_connect, attempts, bodies = sqlite3.connect, [], []

    class BrokenConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement == "PRAGMA journal_mode=WAL":
                attempts.append(statement)
                raise _sqlite_error(code)
            return super().execute(statement, *args, **kwargs)

    monkeypatch.setattr(state_module.sqlite3, "connect", lambda *args, **kwargs: original_connect(*args, **kwargs, factory=BrokenConnection))
    with pytest.raises(StateStoreError) as raised, _store(tmp_path / "state.sqlite3").transaction():
        bodies.append("entered")
    assert len(attempts) == 1
    assert waits == [] and bodies == []
    assert "private-sql" not in str(raised.value)


@pytest.mark.parametrize("statement", ["PRAGMA secure_delete=ON", "PRAGMA journal_mode=WAL", "BEGIN IMMEDIATE"])
def test_initialization_busy_wait_has_one_bounded_budget(tmp_path, monkeypatch, statement):
    now, waits = _virtual_initialization_clock(monkeypatch)
    original_connect, attempts, bodies = sqlite3.connect, [], []

    class AlwaysBusyConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == statement:
                attempts.append(sql)
                raise _sqlite_error(sqlite3.SQLITE_BUSY)
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(state_module.sqlite3, "connect", lambda *args, **kwargs: original_connect(*args, **kwargs, factory=AlwaysBusyConnection))
    with pytest.raises(StateStoreError) as raised, _store(tmp_path / "state.sqlite3").transaction():
        bodies.append("entered")
    assert len(attempts) > 1
    assert now[0] == pytest.approx(state_module._INITIALIZATION_TIMEOUT_SECONDS)
    assert sum(waits) == pytest.approx(state_module._INITIALIZATION_TIMEOUT_SECONDS)
    assert bodies == []
    assert "private-sql" not in str(raised.value)


def test_initialization_budget_is_shared_across_journal_and_begin(tmp_path, monkeypatch):
    now, waits = _virtual_initialization_clock(monkeypatch)
    original_connect, begin_attempts, bodies = sqlite3.connect, [], []

    class DelayedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement == "PRAGMA journal_mode=WAL" and now[0] < 2:
                raise _sqlite_error(sqlite3.SQLITE_BUSY)
            if statement == "BEGIN IMMEDIATE":
                begin_attempts.append(statement)
                raise _sqlite_error(sqlite3.SQLITE_BUSY)
            return super().execute(statement, *args, **kwargs)

    monkeypatch.setattr(state_module.sqlite3, "connect", lambda *args, **kwargs: original_connect(*args, **kwargs, factory=DelayedConnection))
    with pytest.raises(StateStoreError), _store(tmp_path / "state.sqlite3").transaction():
        bodies.append("entered")
    assert begin_attempts and bodies == []
    assert sum(waits) == pytest.approx(state_module._INITIALIZATION_TIMEOUT_SECONDS)


@pytest.mark.parametrize("failure_phase", ["body", "commit"])
def test_busy_after_transaction_entry_never_replays_body_and_rolls_back(tmp_path, monkeypatch, failure_phase):
    store = _store(tmp_path / "state.sqlite3")
    store.operations.create(_operation())
    _now, waits = _virtual_initialization_clock(monkeypatch)
    original_connect, connections, body_calls = sqlite3.connect, [], []

    class CommitBusyConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement == "COMMIT" and failure_phase == "commit":
                raise _sqlite_error(sqlite3.SQLITE_BUSY)
            return super().execute(statement, *args, **kwargs)

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=CommitBusyConnection)
        connections.append(connection)
        return connection

    with monkeypatch.context() as context:
        context.setattr(state_module.sqlite3, "connect", connect)
        with pytest.raises(StateStoreError), store.transaction() as connection:
            body_calls.append("entered")
            connection.execute("UPDATE operations SET attempt_count=99")
            if failure_phase == "body":
                raise _sqlite_error(sqlite3.SQLITE_BUSY)
    assert len(connections) == 1 and body_calls == ["entered"]
    assert waits == []
    assert store.operations.get("scope_a", "op_test").attempt_count == 0


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
