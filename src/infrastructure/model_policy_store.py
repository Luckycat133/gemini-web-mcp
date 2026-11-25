"""Account-scoped, short-lived evidence of Flash text quota exhaustion."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
import time
from typing import Any, Iterator

from .operation_store import default_operation_db_path


class ModelPolicyStore:
    def __init__(self, path: Path | str | None = None, *, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path is not None else default_operation_db_path()
        self.clock = clock

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS flash_quota_evidence (account_key TEXT PRIMARY KEY, observed_at REAL NOT NULL, expires_at REAL NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS quota_reset_snapshots ("
                "account_key TEXT PRIMARY KEY, quota_window TEXT NOT NULL, reset_at TEXT NOT NULL, "
                "reset_timestamp REAL NOT NULL, remaining_credits INTEGER, observed_at REAL NOT NULL)"
            )
            with db:
                yield db
        finally:
            db.close()

    def record_quota_snapshot(
        self,
        account_key: str,
        *,
        quota_window: str,
        reset_at: str,
        reset_timestamp: float,
        remaining_credits: int | None = None,
    ) -> None:
        if not account_key or not reset_at or reset_timestamp <= 0:
            return
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO quota_reset_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (account_key, quota_window, reset_at, float(reset_timestamp), remaining_credits, self.clock()),
            )

    def get_quota_snapshot(self, account_key: str | None) -> dict[str, Any] | None:
        if not account_key:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT quota_window, reset_at, reset_timestamp, remaining_credits, observed_at "
                "FROM quota_reset_snapshots WHERE account_key=?",
                (account_key,),
            ).fetchone()
            if not row:
                return None
            return {
                "quota_window": row[0],
                "reset_at": row[1],
                "reset_timestamp": float(row[2]),
                "remaining_credits": row[3],
                "observed_at": float(row[4]),
            }

    def record_flash_exhausted(self, account_key: str) -> None:
        if not account_key:
            return
        now = self.clock()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO flash_quota_evidence VALUES (?, ?, ?)", (account_key, now, now + 60))

    def flash_exhausted(self, account_key: str | None) -> bool:
        if not account_key:
            return False
        now = self.clock()
        with self._connect() as db:
            row = db.execute("SELECT observed_at, expires_at FROM flash_quota_evidence WHERE account_key=?", (account_key,)).fetchone()
            return bool(row and row[0] <= now < row[1])

    def clear_flash_exhausted(self, account_key: str | None, *, observed_before: float | None = None) -> None:
        if account_key:
            with self._connect() as db:
                if observed_before is None:
                    db.execute("DELETE FROM flash_quota_evidence WHERE account_key=?", (account_key,))
                else:
                    db.execute("DELETE FROM flash_quota_evidence WHERE account_key=? AND observed_at<=?",
                               (account_key, observed_before))
