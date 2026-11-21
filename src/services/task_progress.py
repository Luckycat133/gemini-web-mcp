"""Read bounded Task activity for an explicitly identified turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..domain.results import DomainErrorCode, DomainResult, OperationState
from ..domain.task_progress import TaskProgress
from ..infrastructure.task_progress import MAX_ACTIVITIES as MAX_ACTIVITIES
from ..infrastructure.task_progress import _id, parse_task_progress as parse_task_progress
from .deadlines import await_before_deadline



class TaskProgressService:
    """Injected reads never initiate or resume generation."""

    def __init__(self, initializer: Callable[[], Awaitable[Any]],
                 read_exact: Callable[[str, str], Awaitable[Any]],
                 read_latest: Callable[[str], Awaitable[Any]],
                 account_key: Callable[[], str | None], *, timeout_seconds: float = 30):
        self.initializer = initializer
        self.read_exact = read_exact
        self.read_latest = read_latest
        self.account_key = account_key
        if timeout_seconds <= 0:
            raise ValueError('Timeout must be positive')
        self.timeout_seconds = timeout_seconds

    async def read_exact_turn(self, chat_id: str, response_id: str, candidate_id: str) -> DomainResult[TaskProgress]:
        if not all(_id(v) for v in (chat_id, response_id, candidate_id)):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Exact Task identity required.')
        return await self._read(chat_id, response_id, candidate_id)

    async def read_latest_task(self, chat_id: str) -> DomainResult[TaskProgress]:
        if not _id(chat_id):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, 'Task identity required.')
        return await self._read(chat_id, None, None)

    async def _read(self, chat_id: str, response_id: str | None,
                    candidate_id: str | None) -> DomainResult[TaskProgress]:
        account = self.account_key()
        if not _id(account):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, 'Task account could not be verified.')
        try:
            await await_before_deadline(self.initializer(), timeout=self.timeout_seconds)
            if self.account_key() != account:
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, 'Task account changed.')
            payload = await await_before_deadline(
                self.read_exact(chat_id, response_id) if response_id else self.read_latest(chat_id),
                timeout=self.timeout_seconds,
            )
            if self.account_key() != account:
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, 'Task account changed.')
        except asyncio.TimeoutError:
            return DomainResult.failure(DomainErrorCode.TIMED_OUT, 'Task progress read timed out.')
        except Exception:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, 'Task progress read could not be verified.')
        if not isinstance(payload, dict) or payload.get('chat_id') != chat_id:
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'Task response identity could not be verified.')
        if response_id is None and payload.get('latest_verified') is not True:
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'Latest Task turn could not be verified.')
        rid = payload.get('response_id')
        candidate = payload.get('candidate')
        cid = candidate[0] if isinstance(candidate, list) and candidate else None
        if not _id(rid) or not _id(cid) or (response_id is not None and rid != response_id) or (candidate_id is not None and cid != candidate_id):
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'Task response identity could not be verified.')
        try:
            progress = parse_task_progress(candidate, chat_id=chat_id, response_id=rid, candidate_id=cid)
        except ValueError:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, 'Task progress shape changed.')
        if progress is None:
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, 'Task progress was not observed.',
                                        verification_status='task_progress_not_observed')
        return DomainResult.success(progress, operation_state=OperationState.PARTIAL,
                                    verification_status=progress.verification)
