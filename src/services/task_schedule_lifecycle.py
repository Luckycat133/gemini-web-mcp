"""Release only owned disposable Task threads after verified schedule deletion."""

from dataclasses import replace
from typing import Any

from ..domain import DomainErrorCode, DomainResult, DomainWarning
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.operation_store import OperationStore
from ..infrastructure.task_schedule_store import TaskScheduleGuardStore
from .deadlines import await_before_deadline


async def delete_schedule_and_release(service: Any, schedule_id: str, *, max_chars: int,
                                      client_provider: Any, finalize_cleanup: Any,
                                      store: OperationStore | None = None) -> DomainResult[Any]:
    owner = current_account_key()
    try:
        result: DomainResult[Any] = await service.delete_schedule(schedule_id, max_chars=max_chars)
    except Exception:
        if current_account_key() != owner:
            return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, 'The schedule account changed.',
                                        verification_status='account_mismatch')
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'Schedule deletion could not be verified.',
                                    verification_status='unverified')
    if not owner:
        owner = current_account_key()
    if current_account_key() != owner:
        return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, 'The schedule account changed.',
                                    verification_status='account_mismatch')
    if not result.ok or result.data is None or not result.data.verified or not owner:
        return result
    operations = store or OperationStore()
    guards = TaskScheduleGuardStore(operations.path)
    try:
        chats = guards.chats_for_schedule(owner, schedule_id)
        guards.forget_verified_absent(owner, schedule_id)
        for cid in chats:
            if guards.protects(cid, owner):
                continue
            for record in operations.disposable_task_records(cid):
                observation = await await_before_deadline(finalize_cleanup(
                    cid, retain_chat=False, delete_after_seconds=record.delete_after_seconds,
                    source=record.operation, client=client_provider()), timeout=30)
                if current_account_key() != owner:
                    return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, 'The schedule account changed.',
                                                verification_status='account_mismatch')
                operations.update_cleanup(record.operation_id, observation.state.value)
    except Exception:
        if current_account_key() != owner:
            return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, 'The schedule account changed.',
                                        verification_status='account_mismatch')
        return replace(result, warnings=(*result.warnings, DomainWarning(code='task_cleanup_unverified',
                       message='The schedule was deleted; cleanup of its owned Task thread is not yet verified.')))
    return result
