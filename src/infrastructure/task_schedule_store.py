"""Account-scoped schedule references that prevent cascading chat cleanup."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from .account_identity import current_account_key
from .operation_store import default_operation_db_path


class TaskScheduleGuardStore:
    """Store identifiers only; a complete fresh listing replaces one account's references."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_operation_db_path()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("""CREATE TABLE IF NOT EXISTS task_schedule_guards (
            account_key TEXT NOT NULL, schedule_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            observed_at REAL NOT NULL, PRIMARY KEY(account_key,schedule_id))""")
        try:
            with db:
                yield db
        finally:
            db.close()

    def replace_chat(self, account_key: str, chat_id: str, schedule_ids: list[str]) -> None:
        if not account_key or current_account_key() != account_key or not chat_id:
            raise ValueError("Schedule references require the current account and Task.")
        if any(not isinstance(sid, str) or not sid for sid in schedule_ids) or len(set(schedule_ids)) != len(schedule_ids):
            raise ValueError("Invalid schedule references.")
        with self._connect() as db:
            db.execute("DELETE FROM task_schedule_guards WHERE account_key=? AND chat_id=?", (account_key, chat_id))
            db.executemany("INSERT OR REPLACE INTO task_schedule_guards VALUES (?,?,?,?)",
                           [(account_key, sid, chat_id, time.time()) for sid in schedule_ids])

    def forget_verified_absent(self, account_key: str, schedule_id: str) -> None:
        if not account_key or current_account_key() != account_key:
            raise ValueError("Schedule references require the current account.")
        with self._connect() as db:
            db.execute("DELETE FROM task_schedule_guards WHERE account_key=? AND schedule_id=?", (account_key, schedule_id))

    def chats_for_schedule(self, account_key: str, schedule_id: str) -> tuple[str, ...]:
        if not account_key or current_account_key() != account_key:
            raise ValueError("Schedule references require the current account.")
        with self._connect() as db:
            return tuple(row[0] for row in db.execute(
                'SELECT chat_id FROM task_schedule_guards WHERE account_key=? AND schedule_id=?',
                (account_key, schedule_id)))

    def replace_account(self, account_key: str, references: list[tuple[str, str]]) -> None:
        if not account_key or current_account_key() != account_key:
            raise ValueError("Schedule references require the current account.")
        if any(not sid or not cid for sid, cid in references) or len({sid for sid, _ in references}) != len(references):
            raise ValueError("Invalid schedule references.")
        with self._connect() as db:
            db.execute("DELETE FROM task_schedule_guards WHERE account_key=?", (account_key,))
            db.executemany("INSERT INTO task_schedule_guards VALUES (?,?,?,?)",
                           [(account_key, sid, cid, time.time()) for sid, cid in references])

    def protects(self, chat_id: str, account_key: str | None = None) -> bool:
        owner = account_key or current_account_key()
        with self._connect() as db:
            if not owner:
                # Losing identity must never discard an existing reference.
                # An empty guard table adds no protection to unrelated workflows.
                return db.execute('SELECT 1 FROM task_schedule_guards WHERE chat_id=? LIMIT 1',
                                  (chat_id,)).fetchone() is not None
            return db.execute("SELECT 1 FROM task_schedule_guards WHERE account_key=? AND chat_id=? LIMIT 1",
                              (owner, chat_id)).fetchone() is not None
