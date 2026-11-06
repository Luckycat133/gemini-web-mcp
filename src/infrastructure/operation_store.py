"""Local metadata-only persistence for explicitly addressed long operations."""

from __future__ import annotations

import os
import hashlib
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .account_identity import current_account_key, omni_video_api_key, omni_video_owner
from ..constants import OMNI_VIDEO_API_BACKEND
from ..domain.artifacts import Artifact

RETENTION_SECONDS = 7 * 24 * 60 * 60
SCHEMA_VERSION = 6
_V1_COLUMNS = {
    "operation_id", "operation", "state", "phase", "created_at", "updated_at",
    "upstream_chat_id", "upstream_operation_id", "verification_status",
}
_V2_COLUMNS = _V1_COLUMNS | {"retain_chat", "delete_after_seconds", "cleanup_state"}
_V3_COLUMNS = _V2_COLUMNS | {"cleanup_protected"}
_V4_COLUMNS = _V3_COLUMNS | {"requested_model", "thinking_level"}
_MEDIA_COLUMNS = {
    "media_type", "request_model", "effective_backend", "output_dir", "filename",
    "upstream_response_id", "upstream_candidate_id", "worker_token", "worker_pid",
    "heartbeat_at", "error_code", "artifact_state",
}
_V5_COLUMNS = _V4_COLUMNS | _MEDIA_COLUMNS
_COLUMNS = _V5_COLUMNS | {"account_key", "account_binding_pending"}
_MEDIA_TERMINAL = {"completed", "failed", "unavailable", "cancelled"}


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    operation: str
    state: str
    phase: str
    created_at: float
    updated_at: float
    upstream_chat_id: str | None = None
    upstream_operation_id: str | None = None
    verification_status: str = "not_observed"
    retain_chat: bool = True
    delete_after_seconds: int | None = None
    cleanup_state: str = "not_scheduled"
    cleanup_protected: bool = True
    requested_model: str | None = None
    thinking_level: str | None = None
    media_type: str | None = None
    request_model: str | None = None
    effective_backend: str | None = None
    output_dir: str | None = None
    filename: str | None = None
    upstream_response_id: str | None = None
    upstream_candidate_id: str | None = None
    worker_token: str | None = None
    worker_pid: int | None = None
    heartbeat_at: float | None = None
    error_code: str | None = None
    artifact_state: str | None = None
    account_key: str | None = None
    account_binding_pending: bool = False


def _record_from_row(row: sqlite3.Row) -> OperationRecord:
    data = dict(row)
    data["retain_chat"] = bool(data["retain_chat"])
    data["cleanup_protected"] = bool(data["cleanup_protected"])
    data["account_binding_pending"] = bool(data["account_binding_pending"])
    return OperationRecord(**data)


def default_operation_db_path() -> Path:
    configured = os.environ.get("GEMINI_OPERATION_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    state_root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state_root / "gemini-web-mcp" / "operations.sqlite3"


class OperationStore:
    """Store identifiers and state only, never prompts, reports or credentials."""

    def __init__(self, path: Path | str | None = None, *, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path is not None else default_operation_db_path()
        self.clock = clock

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Creation mode is restrictive even when the process umask is permissive.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            connection.close()
            raise RuntimeError("operation database schema is newer than supported")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
                state TEXT NOT NULL, phase TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                upstream_chat_id TEXT, upstream_operation_id TEXT,
                verification_status TEXT NOT NULL,
                retain_chat INTEGER NOT NULL DEFAULT 1, delete_after_seconds INTEGER,
                cleanup_state TEXT NOT NULL DEFAULT 'not_scheduled',
                cleanup_protected INTEGER NOT NULL DEFAULT 1,
                requested_model TEXT, thinking_level TEXT
            )"""
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(operations)")}
        if version < 2 and columns == _V1_COLUMNS:
            connection.execute("ALTER TABLE operations ADD COLUMN retain_chat INTEGER NOT NULL DEFAULT 1")
            connection.execute("ALTER TABLE operations ADD COLUMN delete_after_seconds INTEGER")
            connection.execute("ALTER TABLE operations ADD COLUMN cleanup_state TEXT NOT NULL DEFAULT 'not_scheduled'")
            columns = _V2_COLUMNS
        if version < 3 and columns == _V2_COLUMNS:
            connection.execute("ALTER TABLE operations ADD COLUMN cleanup_protected INTEGER NOT NULL DEFAULT 1")
            columns = _V3_COLUMNS
        if version < 4 and columns == _V3_COLUMNS:
            connection.execute("ALTER TABLE operations ADD COLUMN requested_model TEXT")
            connection.execute("ALTER TABLE operations ADD COLUMN thinking_level TEXT")
            columns = _V4_COLUMNS
        if version < 5 and columns == _V4_COLUMNS:
            for column in sorted(_MEDIA_COLUMNS):
                sql_type = "INTEGER" if column == "worker_pid" else "REAL" if column == "heartbeat_at" else "TEXT"
                connection.execute(f"ALTER TABLE operations ADD COLUMN {column} {sql_type}")
            columns = _V5_COLUMNS
        if version < 6 and columns == _V5_COLUMNS:
            connection.execute("ALTER TABLE operations ADD COLUMN account_key TEXT")
            connection.execute("ALTER TABLE operations ADD COLUMN account_binding_pending INTEGER NOT NULL DEFAULT 0")
            columns = _COLUMNS
        if columns != _COLUMNS:
            connection.close()
            raise RuntimeError("operation database schema is incompatible")
        if version < SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_schedule_guards (
                account_key TEXT NOT NULL, schedule_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                observed_at REAL NOT NULL, PRIMARY KEY(account_key,schedule_id))"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS operation_artifacts (
                operation_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
                kind TEXT NOT NULL, mime_type TEXT NOT NULL, local_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY (operation_id, artifact_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_file_manifests (
                operation_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
                response_id TEXT NOT NULL, candidate_id TEXT NOT NULL, file_count INTEGER NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_inputs (
                operation_id TEXT NOT NULL, input_id TEXT NOT NULL, account_key TEXT NOT NULL,
                previous_response_id TEXT NOT NULL, previous_candidate_id TEXT NOT NULL,
                created_at REAL NOT NULL, PRIMARY KEY (operation_id,input_id)
            )"""
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, operation_id: str, operation: str, *, retain_chat: bool = False,
               delete_after_seconds: int | None = None, requested_model: str | None = None,
               thinking_level: str | None = None) -> None:
        now = self.clock()
        account = current_account_key()
        with self._connect() as db:
            db.execute("DELETE FROM operations WHERE created_at <= ? AND cleanup_protected=0", (now - RETENTION_SECONDS,))
            db.execute("DELETE FROM operation_artifacts WHERE operation_id NOT IN (SELECT operation_id FROM operations)")
            db.execute(
                """INSERT INTO operations
                (operation_id, operation, state, phase, created_at, updated_at, verification_status,
                 retain_chat, delete_after_seconds, cleanup_state, cleanup_protected, requested_model, thinking_level,
                 account_key, account_binding_pending)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (operation_id, operation, "accepted", "created", now, now, "not_observed",
                 int(retain_chat), delete_after_seconds, "not_scheduled", 1, requested_model, thinking_level,
                 account, int(account is None)),
            )

    def create_or_get(self, operation_id: str, operation: str, *, retain_chat: bool = False,
                      delete_after_seconds: int | None = None, requested_model: str | None = None,
                      thinking_level: str | None = None, media_type: str | None = None,
                      output_dir: str | None = None, filename: str | None = None,
                      request_model: str | None = None, effective_backend: str | None = None) -> tuple[OperationRecord, bool]:
        """Atomically claim a caller handle; never replace an existing claim."""
        now = self.clock()
        account = (omni_video_owner(omni_video_api_key()) if effective_backend == OMNI_VIDEO_API_BACKEND
                   else current_account_key())
        with self._connect() as db:
            db.execute("DELETE FROM operations WHERE created_at <= ? AND cleanup_protected=0 AND operation_id <> ?", (
                now - RETENTION_SECONDS, operation_id,
            ))
            db.execute("DELETE FROM operation_artifacts WHERE operation_id NOT IN (SELECT operation_id FROM operations)")
            cursor = db.execute(
                """INSERT OR IGNORE INTO operations
                (operation_id, operation, state, phase, created_at, updated_at, verification_status,
                 retain_chat, delete_after_seconds, cleanup_state, cleanup_protected, requested_model, thinking_level,
                 media_type, output_dir, filename, request_model, effective_backend, account_key, account_binding_pending)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (operation_id, operation, "accepted", "created", now, now, "not_observed",
                 int(retain_chat), delete_after_seconds, "not_scheduled", 1, requested_model, thinking_level,
                 media_type, output_dir, filename, request_model, effective_backend, account, int(account is None)),
            )
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            assert row is not None
            return _record_from_row(row), cursor.rowcount == 1

    def get(self, operation_id: str) -> OperationRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return _record_from_row(row) if row is not None else None

    def task_input_dispatched(self, operation_id: str, input_id: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM task_inputs WHERE operation_id=? AND input_id=? AND account_key=?",
                              (operation_id, input_id, current_account_key())).fetchone() is not None

    def has_task_input(self, operation_id: str) -> bool:
        """Whether this account has consumed any exact card on this operation."""
        account = current_account_key()
        if not account:
            return False
        with self._connect() as db:
            return db.execute("SELECT 1 FROM task_inputs WHERE operation_id=? AND account_key=? LIMIT 1",
                              (operation_id, account)).fetchone() is not None

    def begin_task_input(self, operation_id: str, input_id: str, *, response_id: str,
                         candidate_id: str, worker_token: str) -> bool:
        """Claim one explicit reply to one observed card; never store or replay its prompt."""
        account = current_account_key()
        if not account or not all((input_id, response_id, candidate_id, worker_token)):
            return False
        with self._connect() as db:
            if db.execute("SELECT 1 FROM task_inputs WHERE operation_id=? AND input_id=?",
                          (operation_id, input_id)).fetchone():
                return False
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if (row is None or row["account_key"] != account or row["operation"] != "gemini_run_task"
                    or not row["cleanup_protected"] or row["upstream_response_id"] != response_id
                    or row["upstream_candidate_id"] != candidate_id or not row["upstream_chat_id"]
                    or row["state"] in {"completed", "failed", "unavailable", "cancelled"}
                    or db.execute("SELECT 1 FROM operation_artifacts WHERE operation_id=?", (operation_id,)).fetchone()):
                return False
            db.execute("INSERT INTO task_inputs VALUES (?,?,?,?,?,?)",
                       (operation_id, input_id, account, response_id, candidate_id, self.clock()))
            db.execute("""UPDATE operations SET state='queued',phase='task_input_dispatched',
                          upstream_response_id=NULL,upstream_candidate_id=NULL,worker_token=?,worker_pid=NULL,
                          heartbeat_at=?,updated_at=?,verification_status='explicit_input_dispatched',error_code=NULL
                          WHERE operation_id=?""", (worker_token, self.clock(), self.clock(), operation_id))
            return True

    def bind_pending_account(self, operation_id: str, *, worker_token: str, account_key: str) -> bool:
        """Bind only a newly created, unobserved operation held by this worker."""
        if not re.fullmatch(r"[0-9a-f]{64}", account_key):
            raise ValueError("invalid account key")
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET account_key=?, account_binding_pending=0, updated_at=?
                   WHERE operation_id=? AND worker_token=? AND account_key IS NULL
                     AND account_binding_pending=1 AND upstream_chat_id IS NULL AND phase='created'""",
                (account_key, self.clock(), operation_id, worker_token),
            ).rowcount == 1

    def delete_expired(self, operation_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM operations WHERE operation_id=? AND created_at <= ? AND cleanup_protected=0",
                (operation_id, self.clock() - RETENTION_SECONDS),
            )
            db.execute("DELETE FROM operation_artifacts WHERE operation_id=? AND NOT EXISTS (SELECT 1 FROM operations WHERE operation_id=?)",
                       (operation_id, operation_id))
            db.execute("DELETE FROM task_inputs WHERE operation_id=? AND NOT EXISTS (SELECT 1 FROM operations WHERE operation_id=?)",
                       (operation_id, operation_id))

    def is_cleanup_protected(self, chat_id: str) -> bool:
        """Do not automatically delete any chat with a protected research operation."""
        from .task_schedule_store import TaskScheduleGuardStore

        if TaskScheduleGuardStore(self.path).protects(chat_id):
            return True
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM operations WHERE upstream_chat_id=? AND cleanup_protected=1 LIMIT 1",
                (chat_id,),
            ).fetchone() is not None

    def attach_late_chat(self, operation_id: str, chat_id: str) -> bool:
        """Atomically attach a late ID without changing status or reopening cleanup."""
        if not chat_id:
            raise ValueError("chat ID must not be empty")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE operations SET upstream_chat_id=COALESCE(upstream_chat_id, ?)
                   WHERE operation_id=? AND cleanup_protected=1
                     AND cleanup_state NOT IN ('completed', 'already_completed')
                     AND (upstream_chat_id IS NULL OR upstream_chat_id=?)""",
                (chat_id, operation_id, chat_id),
            )
            if cursor.rowcount:
                return True
            row = db.execute(
                "SELECT upstream_chat_id FROM operations WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if row is None:
                raise LookupError("operation not found")
            if row["upstream_chat_id"] is not None and row["upstream_chat_id"] != chat_id:
                raise ValueError("late chat ID conflicts with the saved operation")
            return False

    def attach_research_observation(
        self, operation_id: str, chat_id: str, *, account_key: str,
        response_id: str | None = None, candidate_id: str | None = None,
    ) -> bool:
        """Bind an early/late research identity without reopening or replacing work.

        Observation belongs to the recorded account even after its worker loses
        the lease or the caller switches accounts. This only stores metadata;
        the caller must verify the current account before scheduling cleanup.
        """
        if (not all(isinstance(value, str) and value.strip() for value in (operation_id, chat_id, account_key))
                or any(value is not None and (not isinstance(value, str) or not value.strip())
                       for value in (response_id, candidate_id))):
            return False
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET upstream_chat_id=COALESCE(upstream_chat_id,?),
                   upstream_response_id=COALESCE(upstream_response_id,?),
                   upstream_candidate_id=COALESCE(upstream_candidate_id,?),
                   state=CASE WHEN phase IN ('created','chat_observed') THEN 'partial' ELSE state END,
                   verification_status=CASE WHEN phase IN ('created','chat_observed')
                     THEN 'start_outcome_unverified' ELSE verification_status END,
                   phase=CASE WHEN phase IN ('created','chat_observed')
                     THEN 'plan_outcome_unverified' ELSE phase END, updated_at=?
                   WHERE operation_id=? AND account_key=? AND media_type IS NULL AND cleanup_protected=1
                     AND cleanup_state NOT IN ('completed','already_completed')
                     AND (upstream_chat_id IS NULL OR upstream_chat_id=?)
                     AND (? IS NULL OR upstream_response_id IS NULL OR upstream_response_id=?)
                     AND (? IS NULL OR upstream_candidate_id IS NULL OR upstream_candidate_id=?)""",
                (chat_id, response_id, candidate_id, self.clock(), operation_id, account_key, chat_id,
                 response_id, response_id, candidate_id, candidate_id),
            ).rowcount == 1

    def bind_research_turn(self, operation_id: str, *, account_key: str, chat_id: str,
                           response_id: str, candidate_id: str) -> bool:
        """Bind an observed plan turn once, without replacing a different identity."""
        if not all((account_key, chat_id, response_id, candidate_id)):
            return False
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET upstream_response_id=?, upstream_candidate_id=?, updated_at=?
                   WHERE operation_id=? AND account_key=? AND upstream_chat_id=? AND cleanup_protected=1
                     AND phase IN ('created','chat_observed','plan_created','plan_outcome_unverified')
                     AND (upstream_response_id IS NULL OR upstream_response_id=?)
                     AND (upstream_candidate_id IS NULL OR upstream_candidate_id=?)""",
                (response_id, candidate_id, self.clock(), operation_id, account_key, chat_id, response_id, candidate_id),
            ).rowcount == 1

    def reject_research_turn(self, operation_id: str, *, account_key: str, chat_id: str,
                             response_id: str, candidate_id: str) -> bool:
        """Commit definite rejection only while confirmation remains unclaimed."""
        if not all((account_key, chat_id, response_id, candidate_id)):
            return False
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET state='failed',phase='plan_rejected',cleanup_protected=0,
                   error_code='UPSTREAM_REJECTED',verification_status='research_rejection_observed',updated_at=?
                   WHERE operation_id=? AND account_key=? AND upstream_chat_id=? AND upstream_response_id=?
                     AND upstream_candidate_id=? AND phase='plan_outcome_unverified' AND cleanup_protected=1""",
                (self.clock(), operation_id, account_key, chat_id, response_id, candidate_id),
            ).rowcount == 1

    def claim_research_confirmation(self, operation_id: str, *, expected_phase: str) -> bool:
        """Claim exactly one confirmation before any upstream confirmation request."""
        if expected_phase not in {"plan_created", "plan_outcome_unverified"}:
            raise ValueError("invalid confirmation claim phase")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE operations SET phase='confirmation_requested', updated_at=?
                   WHERE operation_id=? AND phase=? AND cleanup_protected=1
                     AND upstream_chat_id IS NOT NULL
                     AND cleanup_state NOT IN ('completed', 'already_completed')""",
                (self.clock(), operation_id, expected_phase),
            )
            return bool(cursor.rowcount)

    def release_cleanup(self, operation_id: str) -> None:
        """Release only after a report was obtained or a definite pre-start failure."""
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE operations SET cleanup_protected=0, updated_at=? WHERE operation_id=?",
                (self.clock(), operation_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("operation not found")

    def update_cleanup(self, operation_id: str, state: str) -> None:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE operations SET cleanup_state=?, updated_at=? WHERE operation_id=?",
                (state, self.clock(), operation_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("operation not found")

    def update(
        self, operation_id: str, *, state: str, phase: str,
        upstream_chat_id: str | None = None, upstream_operation_id: str | None = None,
        verification_status: str = "not_observed",
    ) -> None:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE operations SET state=?, phase=?, updated_at=?,
                upstream_chat_id=COALESCE(?, upstream_chat_id),
                upstream_operation_id=COALESCE(?, upstream_operation_id),
                verification_status=? WHERE operation_id=? AND (state != 'completed' OR ? = 'completed')""",
                (state, phase, self.clock(), upstream_chat_id, upstream_operation_id,
                 verification_status, operation_id, state),
            )
            if cursor.rowcount != 1:
                if db.execute("SELECT 1 FROM operations WHERE operation_id=?", (operation_id,)).fetchone() is None:
                    raise LookupError("operation not found")

    def configure_media(self, operation_id: str, media_type: str, output_dir: str, filename: str,
                        request_model: str | None, effective_backend: str | None,
                        *, worker_token: str | None = None) -> bool:
        """Persist only modality, model metadata and the requested Artifact destination."""
        if media_type not in {"image", "video", "music"}:
            raise ValueError("unsupported media type")
        if not filename or Path(filename).name != filename or filename in {".", ".."}:
            raise ValueError("invalid artifact filename")
        destination = str(Path(output_dir).expanduser().resolve())
        values = (media_type, destination, filename, request_model, effective_backend)
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                raise LookupError("operation not found")
            if worker_token is not None and row["worker_token"] != worker_token:
                return False
            if row["media_type"] is not None:
                saved = tuple(row[key] for key in ("media_type", "output_dir", "filename", "request_model", "effective_backend"))
                if saved != values:
                    raise ValueError("media operation configuration cannot be replaced")
                return True
            db.execute(
                """UPDATE operations SET media_type=?, output_dir=?, filename=?, request_model=?,
                   effective_backend=?, updated_at=? WHERE operation_id=?""",
                (*values, self.clock(), operation_id),
            )
            return True

    def claim_worker(self, operation_id: str, worker_token: str, *, stale_after_seconds: float = 60) -> bool:
        """Claim a worker lease without changing the saved upstream workflow phase."""
        now = self.clock()
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE operations SET worker_token=?, worker_pid=NULL, heartbeat_at=?, updated_at=?
                   WHERE operation_id=? AND (state NOT IN ('completed','failed','unavailable','cancelled')
                     OR (state='completed' AND media_type IS NOT NULL
                         AND (retain_chat=0 OR (effective_backend=? AND cleanup_protected=1))
                         AND cleanup_state NOT IN ('completed','already_completed')
                         AND EXISTS (SELECT 1 FROM operation_artifacts WHERE operation_id=?))
                     OR (state='failed' AND effective_backend=? AND phase='omni_rejected'
                         AND (retain_chat=0 OR cleanup_protected=1) AND upstream_operation_id IS NOT NULL
                         AND cleanup_state NOT IN ('completed','already_completed'))
                     OR (state='completed' AND operation IN ('gemini_research','gemini_deep_research')
                         AND retain_chat=0 AND cleanup_state NOT IN ('completed','already_completed')
                         AND (cleanup_protected=1 OR verification_status='research_artifact_verified'))
                     OR (state IN ('completed','failed') AND operation IN ('gemini_run_task','gemini_notebook_chat')
                         AND (state != 'failed' OR phase IN ('task_failed','task_failure_saved'))
                         AND (cleanup_protected=1 OR (retain_chat=0 AND cleanup_state NOT IN ('completed','already_completed')))))
                     AND (worker_token IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)""",
                (worker_token, now, now, operation_id, OMNI_VIDEO_API_BACKEND, operation_id, OMNI_VIDEO_API_BACKEND,
                 now - stale_after_seconds),
            )
            return cursor.rowcount == 1

    def bind_worker(self, operation_id: str, worker_token: str, pid: int) -> bool:
        with self._connect() as db:
            return db.execute(
                "UPDATE operations SET worker_pid=? WHERE operation_id=? AND worker_token=?",
                (pid, operation_id, worker_token),
            ).rowcount == 1

    def heartbeat_worker(self, operation_id: str, worker_token: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "UPDATE operations SET heartbeat_at=? WHERE operation_id=? AND worker_token=?",
                (self.clock(), operation_id, worker_token),
            ).rowcount == 1

    def is_worker_owner(self, operation_id: str, worker_token: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM operations WHERE operation_id=? AND worker_token=?",
                (operation_id, worker_token),
            ).fetchone() is not None

    def retire_worker(self, operation_id: str, worker_token: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE operations SET worker_token=NULL, worker_pid=NULL, heartbeat_at=NULL
                   WHERE operation_id=? AND worker_token=?""", (operation_id, worker_token),
            )

    def begin_omni_video(self, operation_id: str, *, worker_token: str) -> bool:
        """Durably claim the one paid submission before sending it."""
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET state='running',phase='omni_dispatching',updated_at=?
                   WHERE operation_id=? AND worker_token=? AND account_key=? AND effective_backend=?
                     AND phase='created' AND upstream_operation_id IS NULL""",
                (self.clock(), operation_id, worker_token, omni_video_owner(omni_video_api_key()),
                 OMNI_VIDEO_API_BACKEND),
            ).rowcount == 1

    def checkpoint_omni_interaction(self, operation_id: str, interaction_id: str, *, worker_token: str) -> bool:
        """Save the first accepted interaction, so recovery can only read it."""
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET upstream_operation_id=?,state='queued',phase='omni_observing',
                   verification_status='upstream_identity_observed',updated_at=?
                   WHERE operation_id=? AND worker_token=? AND account_key=? AND effective_backend=?
                     AND phase='omni_dispatching' AND upstream_operation_id IS NULL""",
                (interaction_id, self.clock(), operation_id, worker_token,
                 omni_video_owner(omni_video_api_key()), OMNI_VIDEO_API_BACKEND),
            ).rowcount == 1

    def finish_omni_cleanup(self, operation_id: str, state: str, *, worker_token: str) -> bool:
        """Record an explicit retention decision or verified interaction absence."""
        if state not in {"pending", "completed", "retained"}:
            raise ValueError("unsupported Omni cleanup state")
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET cleanup_state=?,cleanup_protected=?,updated_at=?
                   WHERE operation_id=? AND worker_token=? AND account_key=? AND effective_backend=?
                     AND (state='completed' OR (state='failed' AND phase='omni_rejected'))""",
                (state, int(state == "pending"), self.clock(), operation_id, worker_token,
                 omni_video_owner(omni_video_api_key()), OMNI_VIDEO_API_BACKEND),
            ).rowcount == 1

    def checkpoint_media(self, operation_id: str, *, worker_token: str, chat_id: str,
                         response_id: str | None = None, candidate_id: str | None = None) -> bool:
        """Protect the first observed identity; never silently switch generation turns."""
        if not chat_id:
            raise ValueError("chat ID must not be blank")
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None or row["worker_token"] != worker_token or not row["cleanup_protected"]:
                return False
            for field, value in (("upstream_chat_id", chat_id), ("upstream_response_id", response_id),
                                 ("upstream_candidate_id", candidate_id)):
                if value and row[field] and row[field] != value:
                    raise ValueError("media response identity conflicts with saved operation")
            db.execute(
                """UPDATE operations SET upstream_chat_id=?,
                   upstream_response_id=COALESCE(upstream_response_id, ?),
                   upstream_candidate_id=COALESCE(upstream_candidate_id, ?),
                   state='running', phase='generation_observed', verification_status='upstream_identity_observed',
                   updated_at=? WHERE operation_id=? AND worker_token=?""",
                (chat_id, response_id, candidate_id, self.clock(), operation_id, worker_token),
            )
            return True

    def save_media_artifacts(self, operation_id: str, artifacts: tuple, *, worker_token: str,
                             artifact_state: str, verification_status: str = "local_artifacts_verified") -> bool:
        """Atomically checkpoint completed local files, excluding URLs and response text."""
        record = self.get(operation_id)
        if record is None or not record.output_dir:
            raise ValueError("missing media destination")
        destination = Path(record.output_dir).resolve()
        rows = []
        for artifact in artifacts:
            if not artifact.local_path:
                continue
            path = Path(artifact.local_path).resolve()
            if not path.is_relative_to(destination) or not path.is_file() or path.stat().st_size <= 0:
                raise ValueError("artifact is not a nonempty file in the requested destination")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            kind = getattr(artifact.kind, "value", artifact.kind)
            rows.append((operation_id, artifact.id, kind, artifact.mime_type, str(path), path.stat().st_size, digest))
        if not rows:
            raise ValueError("completed media requires a saved local artifact")
        with self._connect() as db:
            if db.execute("SELECT 1 FROM operations WHERE operation_id=? AND worker_token=?",
                          (operation_id, worker_token)).fetchone() is None:
                return False
            db.execute("DELETE FROM operation_artifacts WHERE operation_id=?", (operation_id,))
            db.executemany("INSERT INTO operation_artifacts VALUES (?,?,?,?,?,?,?)", rows)
            db.execute(
                """UPDATE operations SET state='completed',phase='artifacts_saved',artifact_state=?,error_code=NULL,
                   verification_status=?,updated_at=? WHERE operation_id=?""",
                (artifact_state, verification_status, self.clock(), operation_id),
            )
            return True

    def get_media_artifacts(self, operation_id: str) -> tuple[dict, ...]:
        with self._connect() as db:
            return tuple(dict(row) for row in db.execute(
                "SELECT artifact_id,kind,mime_type,local_path,size_bytes,sha256 FROM operation_artifacts WHERE operation_id=?",
                (operation_id,),
            ))

    def save_research_result_artifact(self, expected: OperationRecord, artifact: Artifact) -> None:
        """Checkpoint one full Research report without storing its body or URLs."""
        import stat

        if (expected.operation not in {"gemini_research", "gemini_deep_research"}
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", expected.operation_id)
                or not expected.upstream_chat_id or not expected.account_key
                or expected.account_key != current_account_key()):
            raise ValueError("Research report ownership could not be verified")
        root = self.path.resolve().parent / "artifacts"
        directory = root / expected.operation_id
        destination = directory / "research-report.md"
        if (root.is_symlink() or directory.is_symlink() or destination.is_symlink()
                or artifact.local_path != str(destination) or artifact.kind.value != "report"
                or artifact.mime_type != "text/markdown"):
            raise ValueError("Invalid research report location")
        fd = os.open(destination, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            content = stream.read(50_000_001)
        if (not stat.S_ISREG(info.st_mode) or not content or len(content) > 50_000_000
                or len(content) != artifact.size_bytes or not content.decode("utf-8").strip()):
            raise ValueError("Research report bytes were not saved")
        digest = hashlib.sha256(content).hexdigest()
        identity_material = "\0".join((str(destination), expected.account_key, expected.upstream_chat_id,
                                      expected.upstream_response_id or "", expected.upstream_candidate_id or ""))
        identity = "artifact_" + hashlib.sha256(identity_material.encode()).hexdigest()[:24]
        if digest != artifact.verification.details.get("sha256") or artifact.id != identity:
            raise ValueError("Research report digest changed")
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (expected.operation_id,)).fetchone()
            identity_fields = ("operation", "account_key", "upstream_chat_id", "upstream_response_id", "upstream_candidate_id")
            if (row is None or current_account_key() != expected.account_key
                    or any(row[key] != getattr(expected, key) for key in identity_fields)
                    or row["state"] in {"failed", "unavailable", "cancelled"}):
                raise ValueError("Research report identity changed")
            previous = db.execute("SELECT artifact_id,sha256 FROM operation_artifacts WHERE operation_id=?",
                                  (expected.operation_id,)).fetchall()
            if previous and (len(previous) != 1 or previous[0]["artifact_id"] != artifact.id
                             or previous[0]["sha256"] != digest):
                raise ValueError("The original report cannot be replaced")
            db.execute("INSERT OR REPLACE INTO operation_artifacts VALUES (?,?,?,?,?,?,?)",
                       (expected.operation_id, artifact.id, "report", "text/markdown", str(destination), len(content), digest))
            db.execute("""UPDATE operations SET state='completed',phase='research_report_saved',
                verification_status='research_artifact_verified',artifact_state='local',error_code=NULL,updated_at=?
                WHERE operation_id=?""", (self.clock(), expected.operation_id))

    def save_task_result_artifact(self, operation_id: str, artifact: Artifact) -> None:
        """Save a verified Task output locator and digest; never its text."""
        if not re.fullmatch(r"op_[A-Za-z0-9_-]{1,128}", operation_id):
            raise ValueError("invalid Task handle")
        destination = (self.path.resolve().parent / "artifacts" / operation_id / "task-result.md").resolve()
        if not artifact.local_path or Path(artifact.local_path).resolve() != destination:
            raise ValueError("Task result is outside its operation directory")
        content = destination.read_bytes()
        if not content or len(content) != artifact.size_bytes:
            raise ValueError("Task result was not saved")
        digest = hashlib.sha256(content).hexdigest()
        with self._connect() as db:
            record = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if (record is None or record["operation"] not in {"gemini_run_task", "gemini_notebook_chat"}
                    or record["account_key"] != current_account_key() or not record["cleanup_protected"]):
                raise ValueError("Task result ownership could not be verified")
            previous = db.execute("SELECT sha256 FROM operation_artifacts WHERE operation_id=? AND kind='report'", (operation_id,)).fetchone()
            if previous is not None and previous["sha256"] != digest:
                raise ValueError("The saved Task result changed")
            db.execute(
                """INSERT OR REPLACE INTO operation_artifacts
                   (operation_id,artifact_id,kind,mime_type,local_path,size_bytes,sha256) VALUES (?,?,?,?,?,?,?)""",
                (operation_id, artifact.id, "report", "text/markdown", str(destination), len(content), digest),
            )
            db.execute("UPDATE operations SET output_dir=?,artifact_state='local',updated_at=? WHERE operation_id=?",
                       (str(destination.parent), self.clock(), operation_id))

    def commit_task_files(self, operation_id: str, *, response_id: str, candidate_id: str,
                          artifacts: tuple[Artifact, ...]) -> None:
        """Atomically publish the complete same-turn file set after verifying local bytes."""
        root = (self.path.resolve().parent / "artifacts" / operation_id).resolve()
        rows = []
        for artifact in artifacts:
            if not artifact.local_path or artifact.kind.value != 'file':
                raise ValueError('Invalid Task file artifact')
            path = Path(artifact.local_path).resolve()
            if path.parent != root or not path.name.startswith('task-file-'):
                raise ValueError('Invalid Task file location')
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if (not path.is_file() or path.stat().st_size != artifact.size_bytes
                    or digest != artifact.verification.details.get('sha256')):
                raise ValueError('Task file failed readback')
            rows.append((operation_id, artifact.id, 'file', artifact.mime_type, str(path), artifact.size_bytes, digest))
        if len({r[1] for r in rows}) != len(rows):
            raise ValueError('Duplicate Task file identity')
        with self._connect() as db:
            record = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
            if (record is None or record['operation'] != 'gemini_run_task' or not record['cleanup_protected']
                    or not current_account_key() or record['account_key'] != current_account_key()
                    or record['upstream_response_id'] != response_id or record['upstream_candidate_id'] != candidate_id
                    or not db.execute("SELECT 1 FROM operation_artifacts WHERE operation_id=? AND kind='report'",
                                      (operation_id,)).fetchone()):
                raise ValueError('Task file ownership or identity changed')
            db.execute("DELETE FROM operation_artifacts WHERE operation_id=? AND kind='file'", (operation_id,))
            db.executemany('INSERT INTO operation_artifacts VALUES (?,?,?,?,?,?,?)', rows)
            db.execute('INSERT OR REPLACE INTO task_file_manifests VALUES (?,?,?,?,?)',
                       (operation_id, record['account_key'], response_id, candidate_id, len(rows)))

    def task_files_committed(self, operation_id: str) -> bool:
        with self._connect() as db:
            row = db.execute('''SELECT m.file_count FROM task_file_manifests m JOIN operations o USING(operation_id)
                WHERE m.operation_id=? AND m.account_key=? AND m.account_key=o.account_key
                  AND m.response_id=o.upstream_response_id AND m.candidate_id=o.upstream_candidate_id''',
                             (operation_id, current_account_key())).fetchone()
            if row is None:
                return False
            count = db.execute("SELECT count(*) FROM operation_artifacts WHERE operation_id=? AND kind='file'",
                               (operation_id,)).fetchone()[0]
            return bool(count == row['file_count'])

    def checkpoint_task_result_state(self, operation_id: str, *, terminal_state: str) -> bool:
        """Persist verified answer state independently from the later cleanup decision."""
        if terminal_state not in {'completed', 'failed'}:
            raise ValueError('Unsupported Task result state')
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET state=?,phase=?,verification_status='task_artifact_verified',
                   artifact_state='local',error_code=?,updated_at=? WHERE operation_id=? AND account_key=?
                   AND operation IN ('gemini_run_task','gemini_notebook_chat')
                   AND EXISTS (SELECT 1 FROM operation_artifacts WHERE operation_id=? AND kind='report')
                   AND (operation='gemini_notebook_chat' OR EXISTS (
                        SELECT 1 FROM task_file_manifests m WHERE m.operation_id=operations.operation_id
                          AND m.account_key=operations.account_key AND m.response_id=operations.upstream_response_id
                          AND m.candidate_id=operations.upstream_candidate_id
                          AND m.file_count=(SELECT count(*) FROM operation_artifacts a
                                            WHERE a.operation_id=operations.operation_id AND a.kind='file')))""",
                (terminal_state, 'task_failure_saved' if terminal_state == 'failed' else 'task_result_saved',
                 'UPSTREAM_REJECTED' if terminal_state == 'failed' else None, self.clock(), operation_id,
                 current_account_key(), operation_id),
            ).rowcount == 1

    def release_task_result_cleanup(self, operation_id: str, *, terminal_state: str = "completed") -> bool:
        """Publish verified terminal Task output without turning failure into success."""
        if terminal_state not in {"completed", "failed"}:
            raise ValueError("Task result must have an observed terminal state")
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET state=?,phase=?,
                   verification_status='task_artifact_verified',cleanup_protected=0,updated_at=?,error_code=?
                   WHERE operation_id=? AND operation IN ('gemini_run_task','gemini_notebook_chat') AND account_key=?
                   AND EXISTS (SELECT 1 FROM operation_artifacts WHERE operation_id=? AND kind='report')
                   AND (operation='gemini_notebook_chat' OR EXISTS (
                        SELECT 1 FROM task_file_manifests m WHERE m.operation_id=operations.operation_id
                          AND m.account_key=operations.account_key AND m.response_id=operations.upstream_response_id
                          AND m.candidate_id=operations.upstream_candidate_id
                          AND m.file_count=(SELECT count(*) FROM operation_artifacts a
                                            WHERE a.operation_id=operations.operation_id AND a.kind='file')))""",
                (terminal_state, "task_failure_saved" if terminal_state == "failed" else "task_answer_saved",
                 self.clock(), "UPSTREAM_REJECTED" if terminal_state == "failed" else None,
                 operation_id, current_account_key(), operation_id),
            ).rowcount == 1

    def release_media_cleanup(self, operation_id: str, *, worker_token: str) -> bool:
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET cleanup_protected=0,updated_at=?
                   WHERE operation_id=? AND worker_token=? AND state='completed'
                     AND EXISTS (SELECT 1 FROM operation_artifacts WHERE operation_id=?)""",
                (self.clock(), operation_id, worker_token, operation_id),
            ).rowcount == 1

    def release_completed_media_cleanup(self, operation_id: str) -> bool:
        """Allow an explicit verified result read to recover an interrupted finalization."""
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET cleanup_protected=0,updated_at=? WHERE operation_id=?
                   AND media_type IS NOT NULL AND state='completed'
                   AND EXISTS (SELECT 1 FROM operation_artifacts WHERE operation_id=?)""",
                (self.clock(), operation_id, operation_id),
            ).rowcount == 1

    def release_failed_media_cleanup(self, operation_id: str, *, worker_token: str) -> bool:
        """Release only after the worker recorded an explicitly observed rejection."""
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET cleanup_protected=0,updated_at=? WHERE operation_id=?
                   AND worker_token=? AND media_type IS NOT NULL AND phase='media_rejected'
                   AND error_code IN ('UPSTREAM_LIMIT_EXCEEDED','ARTIFACT_NOT_RETURNED','UPSTREAM_REJECTED')""",
                (self.clock(), operation_id, worker_token),
            ).rowcount == 1

    def cleanup_repair_candidates(self, account_key: str) -> tuple[OperationRecord, ...]:
        """Find observed conversations belonging to this account for queue reconciliation."""
        if not account_key:
            return ()
        with self._connect() as db:
            return tuple(_record_from_row(row) for row in db.execute(
                """SELECT * FROM operations WHERE account_key=? AND upstream_chat_id IS NOT NULL
                   AND cleanup_state NOT IN ('completed','already_completed')""", (account_key,),
            ))

    def recoverable_media(self, *, limit: int = 20) -> tuple[OperationRecord, ...]:
        api_owner = omni_video_owner(omni_video_api_key())
        web_owner = current_account_key()
        with self._connect() as db:
            return tuple(_record_from_row(row) for row in db.execute(
                """SELECT * FROM operations WHERE media_type IS NOT NULL
                   AND ((effective_backend=? AND account_key=?) OR (effective_backend IS NOT ? AND account_key=?))
                   AND (worker_token IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)
                   AND updated_at <= ?
                   AND ((state NOT IN ('completed','failed','unavailable','cancelled') AND upstream_chat_id IS NOT NULL
                         AND upstream_response_id IS NOT NULL AND upstream_candidate_id IS NOT NULL)
                     OR (effective_backend=? AND upstream_operation_id IS NOT NULL
                         AND (state NOT IN ('completed','failed','unavailable','cancelled')
                              OR (state='failed' AND phase='omni_rejected' AND (retain_chat=0 OR cleanup_protected=1)
                                  AND cleanup_state NOT IN ('completed','already_completed'))))
                     OR (state='completed' AND (retain_chat=0 OR (effective_backend=? AND cleanup_protected=1))
                         AND cleanup_state NOT IN ('completed','already_completed')))
                   ORDER BY updated_at LIMIT ?""",
                (OMNI_VIDEO_API_BACKEND, api_owner, OMNI_VIDEO_API_BACKEND, web_owner,
                 self.clock() - 60, self.clock() - 60, OMNI_VIDEO_API_BACKEND, OMNI_VIDEO_API_BACKEND, limit),
            ))

    def recoverable_web_workflows(self, *, limit: int = 20) -> tuple[OperationRecord, ...]:
        """Resume exact reply observation or result cleanup; prompts are never replayed."""
        with self._connect() as db:
            return tuple(_record_from_row(row) for row in db.execute(
                """SELECT * FROM operations WHERE operation IN ('gemini_run_task','gemini_notebook_chat')
                   AND (worker_token IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)
                   AND updated_at <= ? AND upstream_chat_id IS NOT NULL
                   AND upstream_response_id IS NOT NULL
                   AND state NOT IN ('unavailable','cancelled')
                   AND (state != 'failed' OR phase IN ('task_failed','task_failure_saved'))
                   AND NOT EXISTS (SELECT 1 FROM task_schedule_guards AS g
                                   WHERE g.account_key=operations.account_key AND g.chat_id=operations.upstream_chat_id)
                   AND (cleanup_protected=1 OR (retain_chat=0 AND cleanup_state NOT IN ('completed','already_completed')))
                   ORDER BY updated_at LIMIT ?""", (self.clock() - 60, self.clock() - 60, limit),
            ))

    def recoverable_research(self, *, limit: int = 20) -> tuple[OperationRecord, ...]:
        """Original owned CIDs eligible for read-only report observation, never submission."""
        account = current_account_key()
        if not account:
            return ()
        with self._connect() as db:
            return tuple(_record_from_row(row) for row in db.execute(
                """SELECT * FROM operations WHERE operation IN ('gemini_research','gemini_deep_research')
                   AND account_key=? AND upstream_chat_id IS NOT NULL
                   AND (worker_token IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)
                   AND updated_at <= ? AND state NOT IN ('failed','unavailable','cancelled')
                   AND cleanup_state NOT IN ('completed','already_completed')
                   AND (cleanup_protected=1 OR (state='completed' AND retain_chat=0
                        AND verification_status='research_artifact_verified'))
                   AND NOT (state='completed' AND retain_chat=1 AND verification_status='research_artifact_verified')
                   ORDER BY updated_at LIMIT ?""", (account, self.clock() - 60, self.clock() - 60, limit),
            ))

    def disposable_task_records(self, chat_id: str) -> tuple[OperationRecord, ...]:
        """Only MCP-owned Tasks whose verified answer already permits cleanup."""
        with self._connect() as db:
            return tuple(_record_from_row(row) for row in db.execute(
                """SELECT * FROM operations WHERE upstream_chat_id=? AND account_key=?
                   AND operation='gemini_run_task' AND retain_chat=0 AND cleanup_protected=0
                   AND verification_status='task_artifact_verified'
                   AND cleanup_state NOT IN ('completed','already_completed')""",
                (chat_id, current_account_key())))

    def observe_task_deletion(self, chat_id: str) -> int:
        """Reconcile exact verified Task absence without removing saved answer files."""
        owner = current_account_key()
        if not owner:
            raise ValueError('Task deletion reconciliation requires an account.')
        with self._connect() as db:
            db.execute('DELETE FROM task_schedule_guards WHERE account_key=? AND chat_id=?', (owner, chat_id))
            return db.execute(
                """UPDATE operations SET cleanup_state='completed',cleanup_protected=0,worker_token=NULL,
                   worker_pid=NULL,heartbeat_at=NULL,updated_at=?,
                   state=CASE WHEN EXISTS(SELECT 1 FROM operation_artifacts a WHERE a.operation_id=operations.operation_id)
                              THEN state ELSE 'unavailable' END,
                   phase=CASE WHEN EXISTS(SELECT 1 FROM operation_artifacts a WHERE a.operation_id=operations.operation_id)
                              THEN phase ELSE 'task_deleted' END,
                   error_code=CASE WHEN EXISTS(SELECT 1 FROM operation_artifacts a WHERE a.operation_id=operations.operation_id)
                              THEN error_code ELSE 'CAPABILITY_UNAVAILABLE' END
                   WHERE operation='gemini_run_task' AND account_key=? AND upstream_chat_id=?""",
                (self.clock(), owner, chat_id),
            ).rowcount

    def finish_worker(self, operation_id: str, *, worker_token: str, state: str, phase: str,
                      verification_status: str, error_code: str | None = None) -> bool:
        if state not in {"accepted", "queued", "running", "partial", "timed_out", "completed", "failed", "unavailable"}:
            raise ValueError("unsupported worker state")
        if error_code is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code):
            raise ValueError("error code must be a stable identifier")
        with self._connect() as db:
            return db.execute(
                """UPDATE operations SET state=?,phase=?,verification_status=?,error_code=?,updated_at=?
                   WHERE operation_id=? AND worker_token=?
                     AND (state NOT IN ('completed','failed','unavailable','cancelled') OR state=?)
                     AND verification_status != 'task_artifact_verified'""",
                (state, phase, verification_status, error_code, self.clock(), operation_id, worker_token, state),
            ).rowcount == 1
