"""Coordinate temporary timeout budgets for shared SDK clients."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
from typing import Any


@dataclass
class _TimeoutLeases:
    client: Any
    base_timeout: Any
    base_watchdog: Any
    applied_timeout: Any
    applied_watchdog: Any
    leases: dict[object, tuple[float, float]] = field(default_factory=dict)


_timeout_lock = threading.RLock()
_timeout_leases: dict[int, _TimeoutLeases] = {}


def _refresh_timeout_baseline(state: _TimeoutLeases) -> None:
    """Preserve deliberate changes made by another client user during a lease."""
    current_timeout = getattr(state.client, "timeout", None)
    current_watchdog = getattr(state.client, "watchdog_timeout", None)
    if current_timeout != state.applied_timeout:
        state.base_timeout = current_timeout
    if current_watchdog != state.applied_watchdog:
        state.base_watchdog = current_watchdog


def _apply_timeout_leases(state: _TimeoutLeases) -> None:
    if state.base_timeout is not None:
        value = max(float(state.base_timeout), *(timeout for timeout, _ in state.leases.values()))
        state.client.timeout = value
        state.applied_timeout = value
    if state.base_watchdog is not None:
        # Never lower a shared client's watchdog while non-media calls run.
        value = max(float(state.base_watchdog), *(watchdog for _, watchdog in state.leases.values()))
        state.client.watchdog_timeout = value
        state.applied_watchdog = value


@contextmanager
def client_timeout_scope(
    client: Any, timeout_seconds: int, *, watchdog_seconds: float | None = None,
) -> Iterator[None]:
    """Keep overlapping client timeout leases active and restore the client once last exits."""
    token = object()
    key = id(client)
    with _timeout_lock:
        state = _timeout_leases.get(key)
        if state is None:
            original_timeout = getattr(client, "timeout", None)
            original_watchdog = getattr(client, "watchdog_timeout", None)
            state = _TimeoutLeases(client, original_timeout, original_watchdog, original_timeout, original_watchdog)
            _timeout_leases[key] = state
        else:
            _refresh_timeout_baseline(state)
        state.leases[token] = (float(timeout_seconds), float(timeout_seconds if watchdog_seconds is None else watchdog_seconds))
        _apply_timeout_leases(state)
    try:
        yield
    finally:
        with _timeout_lock:
            _refresh_timeout_baseline(state)
            state.leases.pop(token, None)
            if state.leases:
                _apply_timeout_leases(state)
            else:
                if state.base_timeout is not None:
                    client.timeout = state.base_timeout
                if state.base_watchdog is not None:
                    client.watchdog_timeout = state.base_watchdog
                _timeout_leases.pop(key, None)
