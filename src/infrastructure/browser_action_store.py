"""Account-bound, metadata-only claims for one-shot browser input."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from .account_identity import current_account_key
from .operation_store import default_operation_db_path

_ACTION_ID = re.compile(r'[A-Za-z0-9_-]{20,128}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_ARTIFACT_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_ACTIONS = frozenset({'click', 'scroll', 'key', 'type'})
_STATES = frozenset({'claimed', 'dispatched', 'verified', 'outcome_unverified'})


def _account(account_key: str) -> None:
    if not isinstance(account_key, str) or not account_key or current_account_key() != account_key:
        raise ValueError('Account identity could not be verified')


def _action_id(value: str) -> None:
    if not isinstance(value, str) or not _ACTION_ID.fullmatch(value):
        raise ValueError('Invalid action ID')


def _identity(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


class BrowserActionStore:
    """Retain action identity and outcome only, never interaction parameters."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_operation_db_path()

    def _connect(self, account_key: str) -> sqlite3.Connection:
        _account(account_key)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            _account(account_key)
            db.execute('''CREATE TABLE IF NOT EXISTS browser_actions (
                account_key TEXT NOT NULL, action_id TEXT NOT NULL,
                chat_id TEXT NOT NULL, response_id TEXT NOT NULL, action TEXT NOT NULL,
                expected_frame_sha256 TEXT NOT NULL, state TEXT NOT NULL,
                artifact_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                candidate_id TEXT, PRIMARY KEY (account_key, action_id))''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(browser_actions)')}
            if 'candidate_id' not in columns:
                db.execute('ALTER TABLE browser_actions ADD COLUMN candidate_id TEXT')
            return db
        except BaseException:
            db.close()
            raise

    def claim(self, *, account_key: str, chat_id: str, response_id: str, action_id: str,
              action: str, expected_frame_sha256: str, candidate_id: str | None = None) -> dict[str, Any]:
        _account(account_key)
        _action_id(action_id)
        if not _identity(chat_id) or not _identity(response_id):
            raise ValueError('Invalid response identity')
        if candidate_id is not None and (not _identity(candidate_id) or not candidate_id.startswith('rc_')):
            raise ValueError('Invalid candidate identity')
        if not isinstance(action, str) or action not in _ACTIONS or not isinstance(expected_frame_sha256, str) or not _HASH.fullmatch(expected_frame_sha256):
            raise ValueError('Invalid action metadata')
        db = self._connect(account_key)
        try:
            _account(account_key)
            row = db.execute('SELECT * FROM browser_actions WHERE account_key=? AND action_id=?',
                             (account_key, action_id)).fetchone()
            if row is not None:
                if (row['chat_id'], row['response_id'], row['action'], row['expected_frame_sha256'], row['candidate_id']) != (
                    chat_id, response_id, action, expected_frame_sha256, candidate_id
                ):
                    raise ValueError('Action ID already belongs to different metadata')
                db.commit()
                return {**dict(row), 'new': False}
            _account(account_key)
            now = time.time()
            db.execute('INSERT INTO browser_actions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (account_key, action_id, chat_id, response_id, action, expected_frame_sha256,
                        'claimed', None, now, now, candidate_id))
            _account(account_key)
            row = db.execute('SELECT * FROM browser_actions WHERE account_key=? AND action_id=?',
                             (account_key, action_id)).fetchone()
            db.commit()
            assert row is not None
            return {**dict(row), 'new': True}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def finish(self, account_key: str, action_id: str, *, state: str, artifact_id: str | None = None) -> bool:
        _account(account_key)
        _action_id(action_id)
        if not isinstance(state, str) or state not in _STATES or state == 'claimed':
            raise ValueError('Invalid action state')
        if artifact_id is not None and (not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id)):
            raise ValueError('Invalid artifact ID')
        if artifact_id is not None and state != 'verified':
            raise ValueError('Artifact requires verified outcome')
        db = self._connect(account_key)
        try:
            _account(account_key)
            row = db.execute('SELECT state, artifact_id FROM browser_actions WHERE account_key=? AND action_id=?',
                             (account_key, action_id)).fetchone()
            if row is None:
                db.commit()
                return False
            current = row['state']
            allowed = {'claimed': {'dispatched', 'outcome_unverified', 'verified'},
                       'dispatched': {'verified', 'outcome_unverified'},
                       'outcome_unverified': {'verified'}, 'verified': set()}
            if current == state:
                if artifact_id is not None and artifact_id != row['artifact_id']:
                    db.commit()
                    return False
                db.commit()
                return True
            if state not in allowed.get(current, set()):
                db.commit()
                return False
            _account(account_key)
            db.execute('UPDATE browser_actions SET state=?, artifact_id=?, updated_at=? WHERE account_key=? AND action_id=?',
                       (state, artifact_id, time.time(), account_key, action_id))
            _account(account_key)
            db.commit()
            return True
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read(self, account_key: str, action_id: str) -> dict[str, Any] | None:
        _account(account_key)
        _action_id(action_id)
        db = self._connect(account_key)
        try:
            _account(account_key)
            row = db.execute('SELECT * FROM browser_actions WHERE account_key=? AND action_id=?',
                             (account_key, action_id)).fetchone()
            _account(account_key)
            db.commit()
            return dict(row) if row is not None else None
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
