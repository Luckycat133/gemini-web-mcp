"""Account-scoped, metadata-only durable queue for remote chat cleanup."""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .account_identity import account_key_scope, canonical_account_key


def _retry_delay(attempts: int) -> int:
    return int(min(300, 30 * (2 ** min(max(0, attempts - 1), 4))))


def default_cleanup_db_path() -> Path:
    configured = os.environ.get("GEMINI_CLEANUP_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "gemini-web-mcp" / "cleanup.sqlite3"


class CleanupStore:
    """Only store account hash, opaque chat IDs and cleanup state."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_cleanup_db_path()

    @staticmethod
    def _scoped_rows(db: sqlite3.Connection, account: str, cid: str | None = None) -> list[sqlite3.Row]:
        scope = account_key_scope(account)
        if not scope:
            return []
        placeholders = ",".join("?" for _ in scope)
        query = f"SELECT * FROM cleanup WHERE account_key IN ({placeholders})"
        parameters: tuple[object, ...] = scope
        if cid is not None:
            query += " AND chat_id=?"
            parameters += (cid,)
        return db.execute(query, parameters).fetchall()

    @staticmethod
    def _preferred_row(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
        # Conflicting aliases never authorize a new deletion. Preserve the most
        # conservative real row for reads; mutations separately reject ambiguity.
        def priority(row: sqlite3.Row) -> tuple[int, str]:
            state = row["state"]
            leased = row["lease_token"] and (row["lease_until"] or 0) > time.time()
            return (0 if state == "retained" else 1 if leased else 2 if state == "completed" else 3, row["account_key"])

        return min(rows, key=priority) if rows else None

    @classmethod
    def _resolve_account(
        cls, db: sqlite3.Connection, account: str, cid: str, *, token: str | None = None,
    ) -> str | None:
        # Serialize resolving an existing alias and writing its exact row, so
        # concurrent new builds cannot create a second alias for the same chat.
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        rows = cls._scoped_rows(db, account, cid)
        if token is not None:
            rows = [row for row in rows if row["lease_token"] == token]
            return str(rows[0]["account_key"]) if len(rows) == 1 else None
        if len(rows) > 1:
            return None
        if rows:
            return str(rows[0]["account_key"])
        return canonical_account_key(account) or account or None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS cleanup (
            account_key TEXT NOT NULL, chat_id TEXT NOT NULL, state TEXT NOT NULL,
            delete_at REAL NOT NULL, source TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            diagnostic_id TEXT, lease_token TEXT, lease_until REAL,
            PRIMARY KEY (account_key, chat_id))""")
        try:
            with db:
                yield db
        finally:
            db.close()

    def schedule(
        self, account: str, cid: str, delete_at: float, source: str, *, retain: bool,
        override_retained: bool = False,
        reschedule_pending: bool = False,
    ) -> None:
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid)
            if resolved is None:
                return
            account = resolved
            if retain:
                db.execute("""INSERT INTO cleanup (account_key,chat_id,state,delete_at,source)
                    VALUES (?,?,'retained',?,?) ON CONFLICT(account_key,chat_id) DO UPDATE SET
                    state='retained', lease_token=NULL, lease_until=NULL
                    WHERE cleanup.state != 'completed'
                    AND (cleanup.lease_token IS NULL OR cleanup.lease_until <= ?)""",
                    (account, cid, delete_at, source, time.time()))
            elif override_retained and reschedule_pending:
                db.execute("""INSERT INTO cleanup
                    (account_key,chat_id,state,delete_at,source) VALUES (?,?,'pending',?,?)
                    ON CONFLICT(account_key,chat_id) DO UPDATE SET state='pending',
                    delete_at=CASE WHEN cleanup.state='pending' AND cleanup.diagnostic_id IS NOT NULL
                        THEN MAX(cleanup.delete_at, excluded.delete_at) ELSE excluded.delete_at END,
                    source=excluded.source,
                    diagnostic_id=CASE WHEN cleanup.state='retained' THEN NULL ELSE cleanup.diagnostic_id END,
                    lease_token=CASE WHEN cleanup.state='retained' THEN NULL ELSE cleanup.lease_token END,
                    lease_until=CASE WHEN cleanup.state='retained' THEN NULL ELSE cleanup.lease_until END
                    WHERE cleanup.state IN ('retained','pending')""",
                    (account, cid, delete_at, source))
            elif override_retained:
                db.execute("""INSERT INTO cleanup
                    (account_key,chat_id,state,delete_at,source) VALUES (?,?,'pending',?,?)
                    ON CONFLICT(account_key,chat_id) DO UPDATE SET state='pending',
                    delete_at=excluded.delete_at, source=excluded.source, diagnostic_id=NULL,
                    lease_token=NULL, lease_until=NULL WHERE cleanup.state='retained'""",
                    (account, cid, delete_at, source))
            else:
                db.execute("""INSERT INTO cleanup
                    (account_key,chat_id,state,delete_at,source) VALUES (?,?,'pending',?,?)
                    ON CONFLICT(account_key,chat_id) DO UPDATE SET delete_at=excluded.delete_at, source=excluded.source
                    WHERE cleanup.state='pending'
                    AND (cleanup.lease_token IS NULL OR cleanup.lease_until <= ?)
                    AND cleanup.delete_at > excluded.delete_at""",
                    (account, cid, delete_at, source, time.time()))

    def get(self, account: str, cid: str) -> dict | None:
        with self._connect() as db:
            row = self._preferred_row(self._scoped_rows(db, account, cid))
        return dict(row) if row else None

    def reopen_verified_present(self, account: str, cid: str) -> None:
        """Only a caller with fresh positive presence evidence may reopen a completed record."""
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid)
            if resolved is None:
                return
            account = resolved
            db.execute("""UPDATE cleanup SET state='pending',delete_at=?,diagnostic_id=NULL,
                lease_token=NULL,lease_until=NULL WHERE account_key=? AND chat_id=? AND state='completed'""",
                (time.time(), account, cid))

    def record_verified_absence(self, account: str, cid: str) -> bool:
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid)
            if resolved is None:
                return False
            account = resolved
            updated = db.execute("""UPDATE cleanup SET state='completed',diagnostic_id=NULL,lease_token=NULL,lease_until=NULL
                WHERE account_key=? AND chat_id=? AND state!='retained'
                AND (lease_token IS NULL OR lease_until<=?)""", (account, cid, time.time()))
            return updated.rowcount > 0

    def record_explicit_verified_absence(self, account: str, cid: str) -> bool:
        """An explicit, independently verified deletion supersedes prior retention."""
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid)
            if resolved is None:
                return False
            account = resolved
            return db.execute("""UPDATE cleanup SET state='completed',diagnostic_id=NULL,
                lease_token=NULL,lease_until=NULL WHERE account_key=? AND chat_id=?""", (account, cid)).rowcount > 0

    def pending(self, account: str) -> list[dict]:
        return [row for row in self.all_records(account) if row["state"] == "pending"]

    def expedite_pending(self, account: str, cid: str) -> None:
        """Make an explicit immediate deletion recoverable without disturbing a lease."""
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid)
            if resolved is None:
                return
            account = resolved
            db.execute("""UPDATE cleanup SET delete_at=MIN(delete_at, ?)
                WHERE account_key=? AND chat_id=? AND state='pending'""", (time.time(), account, cid))

    def all_records(self, account: str) -> list[dict]:
        with self._connect() as db:
            rows = self._scoped_rows(db, account)
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["chat_id"], []).append(row)
        return [dict(preferred) for group in grouped.values() if (preferred := self._preferred_row(group)) is not None]

    def record_failure(self, account: str, cid: str, diagnostic_id: str) -> None:
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid)
            if resolved is None:
                return
            account = resolved
            row = db.execute("""SELECT attempts FROM cleanup WHERE account_key=? AND chat_id=?
                AND state='pending' AND (lease_token IS NULL OR lease_until <= ?)""",
                (account, cid, time.time())).fetchone()
            if row is None:
                return
            retry_at = time.time() + _retry_delay(row["attempts"] + 1)
            db.execute("""UPDATE cleanup SET diagnostic_id=?
                , delete_at=MAX(delete_at, ?) WHERE account_key=? AND chat_id=?
                AND state='pending' AND (lease_token IS NULL OR lease_until <= ?)""",
                (diagnostic_id, retry_at, account, cid, time.time()))

    def claim(self, account: str, cid: str, *, force: bool = False, lease_seconds: int = 600) -> str | None:
        now = time.time()
        token = uuid.uuid4().hex
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid)
            if resolved is None:
                return None
            account = resolved
            result = db.execute("""UPDATE cleanup SET lease_token=?, lease_until=?, attempts=attempts+1
                WHERE account_key=? AND chat_id=? AND state='pending'
                AND (lease_token IS NULL OR lease_until <= ?) AND (delete_at <= ? OR ?)""",
                (token, now + lease_seconds, account, cid, now, now, force))
            return token if result.rowcount == 1 else None

    def defer_protected(self, account: str, cid: str, *, token: str | None = None) -> None:
        """Defer a protected operation without recording a failed delete attempt."""
        now = time.time()
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid, token=token)
            if resolved is None:
                return
            account = resolved
            if token is None:
                db.execute("""UPDATE cleanup SET delete_at=MAX(delete_at, ?)
                    WHERE account_key=? AND chat_id=? AND state='pending'
                    AND (lease_token IS NULL OR lease_until<=?)""", (now + 60, account, cid, now))
            else:
                db.execute("""UPDATE cleanup SET delete_at=MAX(delete_at, ?),
                    attempts=MAX(attempts-1, 0), lease_token=NULL, lease_until=NULL
                    WHERE account_key=? AND chat_id=? AND state='pending' AND lease_token=?""",
                    (now + 60, account, cid, token))

    def renew(self, account: str, cid: str, token: str, *, lease_seconds: int = 600) -> bool:
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid, token=token)
            if resolved is None:
                return False
            account = resolved
            result = db.execute("""UPDATE cleanup SET lease_until=? WHERE account_key=? AND chat_id=?
                AND state='pending' AND lease_token=?""", (time.time()+lease_seconds, account, cid, token))
            return result.rowcount == 1

    def finish(self, account: str, cid: str, token: str, *, verified: bool, diagnostic_id: str | None) -> None:
        with self._connect() as db:
            resolved = self._resolve_account(db, account, cid, token=token)
            if resolved is None:
                return
            account = resolved
            row = db.execute("SELECT attempts FROM cleanup WHERE account_key=? AND chat_id=? AND lease_token=?",
                             (account, cid, token)).fetchone()
            if row is None:
                return
            retry_at = 0.0 if verified else time.time() + _retry_delay(row["attempts"])
            db.execute("""UPDATE cleanup SET state=?, diagnostic_id=?, lease_token=NULL, lease_until=NULL,
                delete_at=MAX(delete_at, ?)
                WHERE account_key=? AND chat_id=? AND lease_token=?""",
                ("completed" if verified else "pending", diagnostic_id, retry_at, account, cid, token))
