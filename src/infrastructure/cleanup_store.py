"""Account-scoped, metadata-only durable queue for remote chat cleanup."""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


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
                db.execute("""INSERT OR IGNORE INTO cleanup
                    (account_key,chat_id,state,delete_at,source) VALUES (?,?,'pending',?,?)""",
                    (account, cid, delete_at, source))

    def get(self, account: str, cid: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM cleanup WHERE account_key=? AND chat_id=?", (account, cid)).fetchone()
        return dict(row) if row else None

    def reopen_verified_present(self, account: str, cid: str) -> None:
        """Only a caller with fresh positive presence evidence may reopen a completed record."""
        with self._connect() as db:
            db.execute("""UPDATE cleanup SET state='pending',delete_at=?,diagnostic_id=NULL,
                lease_token=NULL,lease_until=NULL WHERE account_key=? AND chat_id=? AND state='completed'""",
                (time.time(), account, cid))

    def record_verified_absence(self, account: str, cid: str) -> bool:
        with self._connect() as db:
            updated = db.execute("""UPDATE cleanup SET state='completed',diagnostic_id=NULL,lease_token=NULL,lease_until=NULL
                WHERE account_key=? AND chat_id=? AND state!='retained'
                AND (lease_token IS NULL OR lease_until<=?)""", (account, cid, time.time()))
            return updated.rowcount > 0

    def record_explicit_verified_absence(self, account: str, cid: str) -> bool:
        """An explicit, independently verified deletion supersedes prior retention."""
        with self._connect() as db:
            return db.execute("""UPDATE cleanup SET state='completed',diagnostic_id=NULL,
                lease_token=NULL,lease_until=NULL WHERE account_key=? AND chat_id=?""", (account, cid)).rowcount > 0

    def pending(self, account: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cleanup WHERE account_key=? AND state='pending'", (account,)).fetchall()
        return [dict(row) for row in rows]

    def expedite_pending(self, account: str, cid: str) -> None:
        """Make an explicit immediate deletion recoverable without disturbing a lease."""
        with self._connect() as db:
            db.execute("""UPDATE cleanup SET delete_at=MIN(delete_at, ?)
                WHERE account_key=? AND chat_id=? AND state='pending'""", (time.time(), account, cid))

    def all_records(self, account: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cleanup WHERE account_key=?", (account,)).fetchall()
        return [dict(row) for row in rows]

    def record_failure(self, account: str, cid: str, diagnostic_id: str) -> None:
        with self._connect() as db:
            row = db.execute("""SELECT attempts FROM cleanup WHERE account_key=? AND chat_id=?
                AND state='pending' AND (lease_token IS NULL OR lease_until <= ?)""",
                (account, cid, time.time())).fetchone()
            if row is None:
                return
            retry_at = time.time() + _retry_delay(row["attempts"] + 1)
            db.execute("""UPDATE cleanup SET attempts=attempts+1, diagnostic_id=?
                , delete_at=MAX(delete_at, ?) WHERE account_key=? AND chat_id=?
                AND state='pending' AND (lease_token IS NULL OR lease_until <= ?)""",
                (diagnostic_id, retry_at, account, cid, time.time()))

    def claim(self, account: str, cid: str, *, force: bool = False, lease_seconds: int = 600) -> str | None:
        now = time.time()
        token = uuid.uuid4().hex
        with self._connect() as db:
            result = db.execute("""UPDATE cleanup SET lease_token=?, lease_until=?, attempts=attempts+1
                WHERE account_key=? AND chat_id=? AND state='pending'
                AND (lease_token IS NULL OR lease_until <= ?) AND (delete_at <= ? OR ?)""",
                (token, now + lease_seconds, account, cid, now, now, force))
            return token if result.rowcount == 1 else None

    def defer_protected(self, account: str, cid: str, *, token: str | None = None) -> None:
        """Defer a protected operation without recording a failed delete attempt."""
        now = time.time()
        with self._connect() as db:
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
            result = db.execute("""UPDATE cleanup SET lease_until=? WHERE account_key=? AND chat_id=?
                AND state='pending' AND lease_token=?""", (time.time()+lease_seconds, account, cid, token))
            return result.rowcount == 1

    def finish(self, account: str, cid: str, token: str, *, verified: bool, diagnostic_id: str | None) -> None:
        with self._connect() as db:
            row = db.execute("SELECT attempts FROM cleanup WHERE account_key=? AND chat_id=? AND lease_token=?",
                             (account, cid, token)).fetchone()
            if row is None:
                return
            retry_at = 0.0 if verified else time.time() + _retry_delay(row["attempts"])
            db.execute("""UPDATE cleanup SET state=?, diagnostic_id=?, lease_token=NULL, lease_until=NULL,
                delete_at=MAX(delete_at, ?)
                WHERE account_key=? AND chat_id=? AND lease_token=?""",
                ("completed" if verified else "pending", diagnostic_id, retry_at, account, cid, token))
