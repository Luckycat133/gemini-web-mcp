"""Bound local observation waits without accepting late upstream results."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any

logger = logging.getLogger(__name__)


def consume_finished_task(task: asyncio.Future[Any]) -> None:
    """Observe detached task results without leaking late exceptions."""
    try:
        task.result()
    except BaseException:
        pass


async def await_before_deadline(
    awaitable: Awaitable[Any], *, timeout: float,
    on_late: Callable[[asyncio.Future[Any]], None] | None = None,
) -> Any:
    """Stop waiting at the deadline even when a child suppresses cancellation."""
    task = asyncio.ensure_future(awaitable)

    def consume_late(done: asyncio.Future[Any]) -> None:
        try:
            if on_late is not None:
                on_late(done)
        except Exception:
            logger.exception("Late operation checkpoint failed")
        finally:
            consume_finished_task(done)

    try:
        done, _pending = await asyncio.wait({task}, timeout=max(0.0, float(timeout)))
    except BaseException:
        if not task.done():
            task.cancel()
        task.add_done_callback(consume_late)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(consume_late)
        raise asyncio.TimeoutError
    return await task
