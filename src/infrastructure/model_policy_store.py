"""Account-scoped, short-lived evidence of Flash text quota exhaustion."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from collections.abc import Callable
from contextlib import contextmanager
from typing import Iterator

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
            with db:
                yield db
        finally:
            db.close()

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
