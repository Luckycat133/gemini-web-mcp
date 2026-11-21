"""Read an authenticated v0.2.2 media receipt without upgrading its ledger."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import sqlite3
import stat
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..domain.results import OperationState

_HANDLE = re.compile(r"op_[0-9a-f]{32}")


@dataclass(frozen=True)
class ReleasedMediaReceipt:
    kind: str
    recorded_state: OperationState
    expires_at: float


def _ledger_path() -> Path:
    configured = os.environ.get("GEMINI_STATE_DB_PATH")
    if configured:
        return Path(configured).expanduser().absolute()
    base = Path.home() / ("Library/Application Support" if sys.platform == "darwin" else ".local/state")
    return base / "gemini-web-mcp" / "state.sqlite3"


def _private(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not (info.st_mode & 0o077)


def _unchanged(path: Path, original: os.stat_result) -> bool:
    current = path.lstat()
    return _private(current) and all(getattr(current, field) == getattr(original, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _journal_present(path: Path) -> bool:
    # Immutable SQLite access avoids creating or writing sidecars. Do not use
    # it over an active WAL or rollback journal, as it could miss newer rows.
    return any(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-journal"))


def lookup_released_media(operation_id: str) -> ReleasedMediaReceipt | None:
    credential = os.environ.get("GEMINI_PSID", "")
    if not credential or not _HANDLE.fullmatch(operation_id) or not hasattr(os, "O_NOFOLLOW"):
        return None
    try:
        path = _ledger_path()
        db_stat = path.lstat()
        if not _private(db_stat) or _journal_present(path):
            return None
        key_path = path.with_suffix(path.suffix + ".key")
        # Reject FIFOs and non-owner files both before and after opening.
        key_stat = key_path.lstat()
        if not _private(key_stat):
            return None
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as key_file:
            if not _private(os.fstat(key_file.fileno())):
                return None
            key = key_file.read(33)
        if len(key) != 32:
            return None
        scope = "scope_" + hmac.new(key, credential.encode(), hashlib.sha256).hexdigest()

        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
            connection.execute("PRAGMA trusted_schema=OFF")
            if connection.execute("PRAGMA user_version").fetchone() != (4,):
                return None
            if connection.execute("SELECT type FROM sqlite_master WHERE name='operations'").fetchone() != ("table",):
                return None
            row = connection.execute(
                "SELECT operation_type, state, expires_at FROM operations "
                "WHERE operation_id=? AND scope_id=? AND operation_type IN ('video','music')",
                (operation_id, scope),
            ).fetchone()
        if (_journal_present(path) or not _unchanged(path, db_stat) or not _unchanged(key_path, key_stat)
                or os.environ.get("GEMINI_PSID") != credential):
            return None
        if row and isinstance(row[2], (float, int)) and math.isfinite(row[2]):
            return ReleasedMediaReceipt(row[0], OperationState(row[1]), float(row[2]))
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        return None
    return None
