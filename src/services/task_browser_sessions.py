"""Short-lived, process-local reuse of exact Task browser connections."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import time
from typing import Any, AsyncIterator
from weakref import WeakKeyDictionary

from ..infrastructure.account_identity import current_account_key
from ..infrastructure.task_browser import browser_identity
from .task_browser_transport import task_browser_connection

MAX_CONNECTIONS = 4
IDLE_TTL_SECONDS = 120.0
_CLOSE_TIMEOUT = 5.0
_CONNECT_TIMEOUT = 60.0


@dataclass(eq=False)
class _Entry:
    key: tuple[str, int, str, str, str, str]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    context: Any = None
    rfb: Any = None
    last_used: float = field(default_factory=time.monotonic)
    borrowers: int = 0
    invalid: bool = False
    timer: asyncio.TimerHandle | None = None


class _Pool:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.entries: dict[tuple[str, int, str, str, str, str], _Entry] = {}


_pools: WeakKeyDictionary[asyncio.AbstractEventLoop, _Pool] = WeakKeyDictionary()


def _pool() -> _Pool:
    loop = asyncio.get_running_loop()
    if loop not in _pools:
        _pools[loop] = _Pool()
    return _pools[loop]


def _identity(client: Any, observed: dict[str, Any]) -> tuple[str, int, str, str, str, str]:
    account = current_account_key()
    if not account:
        raise ValueError('Task browser account is unavailable.')
    metadata = browser_identity(observed)
    candidate = observed.get('candidate')
    rcid = candidate[0] if isinstance(candidate, list) and candidate else None
    if not isinstance(rcid, str) or not rcid.startswith('rc_') or not rcid[3:].strip() or len(rcid) > 256:
        raise ValueError('Task browser response identity is unavailable.')
    # The opaque connection token is intentionally omitted: the same VM can
    # advertise a refreshed token without becoming a different browser.
    digest = hashlib.sha256((metadata[0] + '\0' + metadata[1]).encode()).hexdigest()
    return account, id(client), metadata[2], metadata[3], rcid, digest


def _check(client: Any, observed: dict[str, Any], key: tuple[str, int, str, str, str, str]) -> None:
    if _identity(client, observed) != key:
        raise ValueError('Task browser identity or account changed.')


async def _close(entry: _Entry) -> None:
    if entry.timer is not None:
        entry.timer.cancel()
        entry.timer = None
    context, entry.context, entry.rfb = entry.context, None, None
    if context is not None:
        try:
            await asyncio.wait_for(context.__aexit__(None, None, None), timeout=_CLOSE_TIMEOUT)
        except Exception:
            pass


async def _expire(pool: _Pool, entry: _Entry) -> None:
    async with pool.lock:
        if pool.entries.get(entry.key) is not entry or entry.borrowers or entry.lock.locked():
            return
        if time.monotonic() - entry.last_used < IDLE_TTL_SECONDS:
            _arm(pool, entry)
            return
        del pool.entries[entry.key]
        entry.invalid = True
    await _close(entry)


def _arm(pool: _Pool, entry: _Entry) -> None:
    if entry.timer is not None:
        entry.timer.cancel()
    delay = max(0.0, IDLE_TTL_SECONDS - (time.monotonic() - entry.last_used))
    loop = asyncio.get_running_loop()
    entry.timer = loop.call_later(delay, lambda: loop.create_task(_expire(pool, entry)))


@asynccontextmanager
async def pooled_task_browser_connection(client: Any, observed: dict[str, Any]) -> AsyncIterator[Any]:
    """Borrow one exact browser session; never replay an input or retain its response."""
    key = _identity(client, observed)
    pool = _pool()
    to_close: list[_Entry] = []
    async with pool.lock:
        _check(client, observed, key)
        # A different account or client invalidates previous connections; a new
        # VM identity for the same turn invalidates that stale connection too.
        for old_key, old in list(pool.entries.items()):
            if (old_key[0] != key[0] or old_key[1] != key[1]
                    or old_key[2:5] == key[2:5] and old_key[5] != key[5]):
                old.invalid = True
                if not old.borrowers:
                    del pool.entries[old_key]
                    to_close.append(old)
        entry = pool.entries.get(key)
        if entry is None:
            if len(pool.entries) >= MAX_CONNECTIONS:
                idle = [item for item in pool.entries.values() if not item.borrowers and not item.lock.locked()]
                if not idle:
                    raise RuntimeError('Task browser connection capacity is busy.')
                oldest = min(idle, key=lambda item: item.last_used)
                del pool.entries[oldest.key]
                oldest.invalid = True
                to_close.append(oldest)
            entry = _Entry(key)
            pool.entries[key] = entry
        entry.borrowers += 1
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None
    try:
        for old in to_close:
            await _close(old)
            _check(client, observed, key)
        async with entry.lock:
            _check(client, observed, key)
            if entry.invalid:
                raise ValueError('Task browser connection is no longer current.')
            if entry.context is None:
                identity = browser_identity(observed)
                metadata: list[Any] = [None] * 12
                metadata[4], metadata[5], metadata[11] = identity[0], identity[1], identity[4]
                candidate: list[Any] = [None] * 13
                candidate[0], candidate[12] = key[4], [{'62': metadata}]
                reduced = {'chat_id': key[2], 'response_id': key[3], 'candidate_id': key[4], 'candidate': candidate}
                context = task_browser_connection(client, reduced)
                try:
                    rfb = await asyncio.wait_for(context.__aenter__(), timeout=_CONNECT_TIMEOUT)
                except BaseException:
                    entry.invalid = True
                    raise
                entry.context, entry.rfb = context, rfb
                _check(client, observed, key)
            try:
                yield entry.rfb
                _check(client, observed, key)
            except BaseException:
                entry.invalid = True
                raise
    except BaseException:
        entry.invalid = True
        raise
    finally:
        async with pool.lock:
            entry.borrowers -= 1
            entry.last_used = time.monotonic()
            if entry.invalid:
                if pool.entries.get(key) is entry:
                    del pool.entries[key]
                should_close = entry.borrowers == 0
            else:
                should_close = False
                if entry.borrowers == 0:
                    _arm(pool, entry)
        if should_close:
            await _close(entry)


async def close_task_browser_sessions() -> None:
    """Invalidate and close sessions belonging to the current event loop."""
    loop = asyncio.get_running_loop()
    pool = _pools.get(loop)
    if pool is None:
        return
    async with pool.lock:
        entries = list(pool.entries.values())
        pool.entries.clear()
        for entry in entries:
            entry.invalid = True
    # In-use entries close after their borrower exits; never interrupt input.
    for entry in entries:
        if not entry.borrowers:
            await _close(entry)
    _pools.pop(loop, None)
