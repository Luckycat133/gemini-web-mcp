"""Account-scoped schedule references that prevent cascading chat cleanup."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from .account_identity import account_key_scope, account_matches, current_account_key
from .operation_store import default_operation_db_path


class TaskScheduleGuardStore:
    """Store identifiers only; a complete fresh listing replaces one account's references."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_operation_db_path()

    @staticmethod
    def _current_scope(account_key: str) -> tuple[str, tuple[str, ...]]:
        owner = current_account_key()
        if not owner or not account_matches(account_key, owner):
            raise ValueError("Schedule references require the current account.")
        return owner, account_key_scope(account_key)

    @staticmethod
    def _require_current(owner: str) -> None:
        if not account_matches(owner, current_account_key()):
            raise ValueError("The schedule reference account changed.")

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
        owner, scope = self._current_scope(account_key)
        if not chat_id:
            raise ValueError("Schedule references require the current account and Task.")
        if any(not isinstance(sid, str) or not sid for sid in schedule_ids) or len(set(schedule_ids)) != len(schedule_ids):
            raise ValueError("Invalid schedule references.")
        with self._connect() as db:
            self._require_current(owner)
            slots = ','.join('?' for _ in scope)
            db.execute(f"DELETE FROM task_schedule_guards WHERE account_key IN ({slots}) AND chat_id=?", (*scope, chat_id))
            if schedule_ids:
                ids = ','.join('?' for _ in schedule_ids)
                db.execute(f"DELETE FROM task_schedule_guards WHERE account_key IN ({slots}) AND schedule_id IN ({ids})",
                           (*scope, *schedule_ids))
            db.executemany("INSERT OR REPLACE INTO task_schedule_guards VALUES (?,?,?,?)",
                           [(owner, sid, chat_id, time.time()) for sid in schedule_ids])
            self._require_current(owner)

    def forget_verified_absent(self, account_key: str, schedule_id: str) -> None:
        owner, scope = self._current_scope(account_key)
        with self._connect() as db:
            self._require_current(owner)
            slots = ','.join('?' for _ in scope)
            db.execute(f"DELETE FROM task_schedule_guards WHERE account_key IN ({slots}) AND schedule_id=?", (*scope, schedule_id))
            self._require_current(owner)

    def chats_for_schedule(self, account_key: str, schedule_id: str) -> tuple[str, ...]:
        owner, scope = self._current_scope(account_key)
        with self._connect() as db:
            self._require_current(owner)
            slots = ','.join('?' for _ in scope)
            result = tuple(row[0] for row in db.execute(
                f'SELECT DISTINCT chat_id FROM task_schedule_guards WHERE account_key IN ({slots}) AND schedule_id=?',
                (*scope, schedule_id)))
            self._require_current(owner)
            return result

    def replace_account(self, account_key: str, references: list[tuple[str, str]]) -> None:
        owner, scope = self._current_scope(account_key)
        if any(not sid or not cid for sid, cid in references) or len({sid for sid, _ in references}) != len(references):
            raise ValueError("Invalid schedule references.")
        with self._connect() as db:
            self._require_current(owner)
            slots = ','.join('?' for _ in scope)
            db.execute(f"DELETE FROM task_schedule_guards WHERE account_key IN ({slots})", scope)
            db.executemany("INSERT INTO task_schedule_guards VALUES (?,?,?,?)",
                           [(owner, sid, cid, time.time()) for sid, cid in references])
            self._require_current(owner)

    def protects(self, chat_id: str, account_key: str | None = None) -> bool:
        owner = account_key or current_account_key()
        with self._connect() as db:
            if not owner:
                # Losing identity must never discard an existing reference.
                # An empty guard table adds no protection to unrelated workflows.
                return db.execute('SELECT 1 FROM task_schedule_guards WHERE chat_id=? LIMIT 1',
                                  (chat_id,)).fetchone() is not None
            scope = account_key_scope(owner)
            slots = ','.join('?' for _ in scope)
            return db.execute(f"SELECT 1 FROM task_schedule_guards WHERE account_key IN ({slots}) AND chat_id=? LIMIT 1",
                              (*scope, chat_id)).fetchone() is not None
