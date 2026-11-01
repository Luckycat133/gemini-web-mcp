"""Only verified schedule deletion may release owned disposable Tasks."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain import DomainErrorCode, DomainResult
from src.domain.conversations import CleanupObservation, CleanupState
from src.domain.task_schedules import TaskScheduleMutationData
from src.infrastructure import account_identity, operation_store, task_schedule_store
from src.infrastructure.operation_store import OperationStore
from src.infrastructure.task_schedule_store import TaskScheduleGuardStore
from src.services import task_schedule_lifecycle
from src.services.task_result_storage import save_task_result


@pytest.fixture
def environment(tmp_path, monkeypatch):
    state = {'account': 'synthetic-account'}
    for module in (account_identity, operation_store, task_schedule_store, task_schedule_lifecycle):
        monkeypatch.setattr(module, 'current_account_key', lambda: state['account'])
    store = OperationStore(tmp_path / 'operations.sqlite3')
    guards = TaskScheduleGuardStore(store.path)
    return state, store, guards


def owned_task(store, *, handle, chat_id, retained=False, artifact=True):
    store.create(handle, 'gemini_run_task', retain_chat=retained)
    store.update(handle, state='running', phase='observed', upstream_chat_id=chat_id)
    if artifact:
        assert store.claim_worker(handle, 'test-owner')
        assert store.checkpoint_media(handle, worker_token='test-owner', chat_id=chat_id,
                                      response_id='r_task', candidate_id='rc_task')
        save_task_result(store, store.get(handle), 'Synthetic result')
        store.commit_task_files(handle, response_id='r_task', candidate_id='rc_task', artifacts=())
        assert store.release_task_result_cleanup(handle)


def deletion(verified=True):
    return DomainResult.success(TaskScheduleMutationData(action='delete', schedule_id='schedule-fixture',
                                                          verified=verified, changed=verified))


def execute(service, store, callback):
    return asyncio.run(task_schedule_lifecycle.delete_schedule_and_release(
        service, 'schedule-fixture', max_chars=10, client_provider=lambda: object(),
        finalize_cleanup=callback, store=store))


def test_verified_last_plan_deletes_only_owned_disposable_task(environment):
    _, store, guards = environment
    owned_task(store, handle='op_owned', chat_id='c_owned')
    owned_task(store, handle='op_retained', chat_id='c_retained', retained=True)
    owned_task(store, handle='op_unsaved', chat_id='c_unsaved', artifact=False)
    for cid in ('c_owned', 'c_retained', 'c_unsaved', 'c_user'):
        guards.replace_chat('synthetic-account', cid, ['schedule-' + cid])
    guards.replace_chat('synthetic-account', 'c_owned', ['schedule-fixture'])
    service = AsyncMock()
    service.delete_schedule.return_value = deletion()
    cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED))
    result = execute(service, store, cleanup)
    assert result.ok and not guards.protects('c_owned', 'synthetic-account')
    assert cleanup.await_count == 1 and cleanup.await_args.args[0] == 'c_owned'
    assert store.get('op_owned').cleanup_state == 'completed'
    assert all(guards.protects(cid, 'synthetic-account') for cid in ('c_retained', 'c_unsaved', 'c_user'))


def test_multiple_schedules_keep_thread_protected(environment):
    _, store, guards = environment
    owned_task(store, handle='op_owned', chat_id='c_owned')
    guards.replace_chat('synthetic-account', 'c_owned', ['schedule-fixture', 'other-schedule'])
    service = AsyncMock()
    service.delete_schedule.return_value = deletion()
    cleanup = AsyncMock()
    assert execute(service, store, cleanup).ok
    assert guards.protects('c_owned', 'synthetic-account') and cleanup.await_count == 0


def test_unverified_deletion_never_releases_guard(environment):
    _, store, guards = environment
    guards.replace_chat('synthetic-account', 'c_owned', ['schedule-fixture'])
    service = AsyncMock()
    service.delete_schedule.return_value = DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'unverified', data=deletion(False).data)
    cleanup = AsyncMock()
    assert not execute(service, store, cleanup).ok
    assert guards.protects('c_owned', 'synthetic-account') and cleanup.await_count == 0


@pytest.mark.parametrize('raises', [False, True])
def test_account_change_during_delete_never_exposes_old_result(environment, raises):
    state, store, guards = environment
    guards.replace_chat('synthetic-account', 'c_owned', ['schedule-fixture'])
    async def change(*args, **kwargs):
        state['account'] = 'other-account'
        if raises:
            raise RuntimeError('PRIVATE OLD ACCOUNT DATA')
        return deletion()
    service = AsyncMock()
    service.delete_schedule.side_effect = change
    cleanup = AsyncMock()
    result = execute(service, store, cleanup)
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED and result.data is None
    assert 'PRIVATE OLD ACCOUNT DATA' not in str(result) and cleanup.await_count == 0
    state['account'] = 'synthetic-account'
    assert guards.protects('c_owned', 'synthetic-account')


def test_account_change_during_cleanup_scrubs_result(environment):
    state, store, guards = environment
    owned_task(store, handle='op_owned', chat_id='c_owned')
    guards.replace_chat('synthetic-account', 'c_owned', ['schedule-fixture'])
    service = AsyncMock()
    service.delete_schedule.return_value = deletion()
    async def change(*args, **kwargs):
        state['account'] = 'other-account'
        return CleanupObservation(state=CleanupState.COMPLETED)
    result = execute(service, store, AsyncMock(side_effect=change))
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED and result.data is None
    state['account'] = 'synthetic-account'
    assert store.get('op_owned').cleanup_state != 'completed'


@pytest.mark.parametrize('kind', ['retained', 'unsaved', 'unowned'])
def test_verified_last_plan_never_cleans_retained_unsaved_or_unowned_thread(environment, kind):
    _, store, guards = environment
    if kind != 'unowned':
        owned_task(store, handle='op_fixture', chat_id='c_fixture', retained=kind == 'retained',
                   artifact=kind != 'unsaved')
    guards.replace_chat('synthetic-account', 'c_fixture', ['schedule-fixture'])
    service = AsyncMock()
    service.delete_schedule.return_value = deletion()
    cleanup = AsyncMock()
    assert execute(service, store, cleanup).ok
    assert not guards.protects('c_fixture', 'synthetic-account')
    cleanup.assert_not_awaited()
