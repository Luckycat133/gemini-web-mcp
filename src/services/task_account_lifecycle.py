"""Reconcile local ownership only after verified explicit Task deletion."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from ..domain import DomainErrorCode, DomainResult, DomainWarning
from ..domain.web_tasks import WebTaskDeleteData
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.operation_store import OperationStore


def _scrub(result: DomainResult[Any]) -> DomainResult[Any]:
    data = result.data
    if isinstance(data, WebTaskDeleteData):
        return replace(result, data=replace(data, pre_read_task=None, post_read_task=None))
    return replace(result, data=None) if data is not None else result


def _account_failure() -> DomainResult[Any]:
    return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, 'The Task account changed or is unavailable.',
                                verification_status='account_mismatch')


async def delete_task_and_reconcile(service: Any, chat_id: str, *, store: OperationStore | None = None,
                                    cleanup_observer: Callable[[str], Any] | None = None) -> DomainResult[Any]:
    """Reconcile exact verified absence; a sent but uncertain deletion is never retried."""
    initial_owner = current_account_key()
    try:
        result: DomainResult[Any] = await service.delete_task(chat_id)
    except Exception:
        if initial_owner is not None and current_account_key() != initial_owner:
            return _account_failure()
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED,
                                    'Task deletion could not be verified; do not repeat an uncertain dispatch.',
                                    verification_status='task_delete_unverified')
    owner = current_account_key()
    if not owner or initial_owner is not None and initial_owner != owner:
        return _account_failure()
    if not result.ok or result.data is None:
        return _scrub(result)
    if getattr(result.data, 'verified', None) is not True:
        return _scrub(result)
    if getattr(result.data, 'chat_id', None) != chat_id:
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'The verified Task identity did not match.',
                                    verification_status='task_identity_unverified')
    try:
        if current_account_key() != owner:
            return _account_failure()
        (store or OperationStore()).observe_task_deletion(chat_id)
        if current_account_key() != owner:
            return _account_failure()
        if cleanup_observer is not None:
            cleanup_observer(chat_id)
        if current_account_key() != owner:
            return _account_failure()
    except Exception:
        if current_account_key() != owner:
            return _account_failure()
        return replace(_scrub(result), warnings=(*result.warnings, DomainWarning(
            code='local_cleanup_unverified', message='Task deletion was verified; local ownership reconciliation is unverified.',
        )))
    return _scrub(result)
