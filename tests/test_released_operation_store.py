"""Offline upgrade/rollback boundary for media handles issued by v0.2.2."""

import asyncio
import sqlite3

import pytest

from src.infrastructure.operation_store import OperationStore
from src.services.operations import OperationService

_HANDLE = "op_" + "0" * 32
# operations schema from the released StateStore (362a368f), reduced to the
# operation table; the separate cleanup table does not participate in lookup.
_RELEASED_OPERATIONS = """
CREATE TABLE operations (
    operation_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, operation_type TEXT NOT NULL,
    state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    expires_at REAL NOT NULL, provider_operation_id TEXT, upstream_chat_id TEXT,
    attempt_count INTEGER NOT NULL, error_code TEXT, verification_status TEXT NOT NULL,
    artifacts TEXT NOT NULL, version INTEGER NOT NULL, idempotency_key TEXT,
    output_dir TEXT, retain_chat INTEGER NOT NULL DEFAULT 1, delete_after_seconds INTEGER,
    lease_id TEXT, lease_until REAL, filename TEXT, UNIQUE(scope_id, idempotency_key)
);
PRAGMA user_version = 4;
"""


@pytest.fixture
def released_ledger(tmp_path, monkeypatch):
    path = tmp_path / "state.sqlite3"
    path.touch(mode=0o600)
    with sqlite3.connect(path) as db:
        db.executescript(_RELEASED_OPERATIONS)
        db.execute(
            """INSERT INTO operations (
                operation_id, scope_id, operation_type, state, created_at, updated_at, expires_at,
                upstream_chat_id, attempt_count, verification_status, artifacts, version, idempotency_key
            ) VALUES (?, 'scope_synthetic', 'video', 'accepted', 1000, 1000, 2000,
                'c_synthetic', 1, 'not_observed', '[]', 1, 'synthetic-retry')""",
            (_HANDLE,),
        )
    monkeypatch.setenv("GEMINI_STATE_DB_PATH", str(path))
    return path


def test_function_reads_do_not_resume_a_released_media_handle(released_ledger):
    before = released_ledger.read_bytes()

    def no_remote_access(*_args, **_kwargs):
        pytest.fail("Unknown handle must not contact Gemini or run cleanup")

    for read in ("get_status", "get_result"):
        store = OperationStore(clock=lambda: 1100)
        assert store.path != released_ledger
        service = OperationService(store, no_remote_access, no_remote_access, no_remote_access)
        result = asyncio.run(getattr(service, read)(_HANDLE))
        assert not result.ok
        assert result.meta.verification_status == "operation_not_found"
    assert released_ledger.read_bytes() == before


def test_pointing_function_store_at_released_ledger_is_not_a_migration(released_ledger, monkeypatch):
    before = released_ledger.read_bytes()
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(released_ledger))
    with pytest.raises(RuntimeError, match="operation database schema is incompatible"):
        OperationStore().get(_HANDLE)
    assert released_ledger.read_bytes() == before
    with sqlite3.connect(released_ledger.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (4,)
        assert db.execute(
            "SELECT state, upstream_chat_id FROM operations WHERE operation_id = ?", (_HANDLE,)
        ).fetchone() == ("accepted", "c_synthetic")
