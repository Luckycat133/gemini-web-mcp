"""Task-local observation of SDK response and conversation metadata."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_observer: ContextVar[Callable[[Any], None] | None] = ContextVar("cleanup_frame_observer", default=None)


def observe_upstream_identity(value: Any) -> None:
    observer = _observer.get()
    if observer is not None:
        observer(value)


@contextmanager
def observe_cleanup_frames(callback: Callable[[Any], None]) -> Iterator[None]:
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)
