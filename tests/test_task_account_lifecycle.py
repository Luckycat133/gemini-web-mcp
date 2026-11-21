"""Explicit verified Task deletion reconciles only owned local metadata."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.domain import DomainErrorCode, DomainResult
from src.domain.conversations import CleanupState
from src.domain.web_tasks import WebTask, WebTaskDeleteData
from src.infrastructure.account_identity import current_account_key
from src.infrastructure.operation_store import OperationStore
from src.infrastructure.task_schedule_store import TaskScheduleGuardStore
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.task_account_lifecycle import delete_task_and_reconcile
from src.services.task_result_storage import save_task_result

CID = 'c_0123456789abcdef'
OTHER = 'c_fedcba9876543210'


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-account-a')
    store = OperationStore(tmp_path / 'operations.sqlite3')
    cleanup = RemoteChatCleanupManager(store_path=tmp_path / 'cleanup.sqlite3', account_key_provider=current_account_key,
                                       cleanup_guard=lambda _: False)
    return store, cleanup, monkeypatch


def task(store, handle, cid, *, operation='gemini_run_task', retained=False, artifact=False):
    store.create(handle, operation, retain_chat=retained)
    store.update(handle, state='running', phase='observed', upstream_chat_id=cid)
    if artifact:
        if operation == 'gemini_run_task':
            assert store.claim_worker(handle, 'test-owner')
            assert store.checkpoint_media(handle, worker_token='test-owner', chat_id=cid,
                                          response_id='r_task', candidate_id='rc_task')
        save_task_result(store, store.get(handle), 'Synthetic cached result')
        if operation == 'gemini_run_task':
            store.commit_task_files(handle, response_id='r_task', candidate_id='rc_task', artifacts=())
        assert store.release_task_result_cleanup(handle)


def verified(*, cid=CID, changed=True):
    return DomainResult.success(WebTaskDeleteData(chat_id=cid, verified=True, changed=changed))


def execute(service, store, callback=None):
    return asyncio.run(delete_task_and_reconcile(service, CID, store=store, cleanup_observer=callback))


def test_verified_exact_deletion_reconciles_owned_records_and_retained_ledger(environment):
    store, cleanup, monkeypatch = environment
    task(store, 'op_cached', CID, retained=True, artifact=True)
    task(store, 'op_missing', CID)
    task(store, 'op_notebook', CID, operation='gemini_notebook_chat')
    task(store, 'op_other', OTHER)
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-account-b')
    task(store, 'op_foreign', CID)
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-account-a')
    artifact_path = store.get_media_artifacts('op_cached')[0]['local_path']
    owner = current_account_key()
    guards = TaskScheduleGuardStore(store.path)
    guards.replace_chat(owner, CID, ['synthetic-plan'])
    assert cleanup.schedule_cleanup_result(CID, retain_chat=True).state == CleanupState.RETAINED
    service = AsyncMock()
    service.delete_task.return_value = verified()
    result = execute(service, store, cleanup.record_explicit_verified_absence)
    assert result.ok and result.data.verified
    assert store.get('op_cached').state == 'completed' and store.get('op_cached').cleanup_state == 'completed'
    assert store.get('op_missing').state == 'unavailable' and store.get('op_missing').phase == 'task_deleted'
    assert not store.get('op_missing').cleanup_protected and not guards.protects(CID, owner)
    assert store.get('op_notebook').cleanup_state != 'completed' and store.get('op_other').cleanup_protected
    assert store.get('op_foreign').cleanup_protected
    assert cleanup.get_cleanup_observation(CID).state == CleanupState.COMPLETED
    with open(artifact_path, encoding='utf-8') as stream:
        assert stream.read() == 'Synthetic cached result'


@pytest.mark.parametrize('changed', [True, False])
def test_verified_change_and_noop_reconcile(environment, changed):
    store, _, _ = environment
    task(store, 'op_missing', CID)
    service = AsyncMock()
    service.delete_task.return_value = verified(changed=changed)
    assert execute(service, store).ok
    assert store.get('op_missing').phase == 'task_deleted'


@pytest.mark.parametrize('mode', ['unverified', 'wrong_id', 'failure', 'exception'])
def test_unverified_or_lost_deletion_never_changes_local_record(environment, mode):
    store, cleanup, _ = environment
    task(store, 'op_missing', CID)
    service = AsyncMock()
    if mode == 'unverified':
        service.delete_task.return_value = DomainResult.success(WebTaskDeleteData(chat_id=CID, verified=False))
    elif mode == 'wrong_id':
        service.delete_task.return_value = verified(cid=OTHER)
    elif mode == 'failure':
        service.delete_task.return_value = DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'unverified')
    else:
        service.delete_task.side_effect = RuntimeError('PRIVATE ERROR')
    callback = AsyncMock()
    result = execute(service, store, callback)
    assert store.get('op_missing').cleanup_protected and store.get('op_missing').phase == 'observed'
    assert callback.call_count == 0 and 'PRIVATE ERROR' not in str(result)
    assert cleanup.get_cleanup_observation(CID) is None
    service.delete_task.assert_awaited_once()


@pytest.mark.parametrize('raise_error', [False, True])
def test_account_switch_during_delete_scrubs_all_data(environment, raise_error):
    store, _, monkeypatch = environment
    task(store, 'op_missing', CID)
    async def switch(_cid):
        monkeypatch.setenv('GEMINI_PSID', 'synthetic-account-b')
        if raise_error:
            raise RuntimeError('PRIVATE ERROR')
        return verified()
    service = AsyncMock()
    service.delete_task.side_effect = switch
    result = execute(service, store)
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED and result.data is None
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-account-a')
    assert store.get('op_missing').cleanup_protected


def test_observer_switch_or_failure_is_safe(environment):
    store, _, monkeypatch = environment
    task(store, 'op_missing', CID)
    service = AsyncMock()
    service.delete_task.return_value = verified()
    def switched(_cid):
        monkeypatch.setenv('GEMINI_PSID', 'synthetic-account-b')
        raise ValueError('PRIVATE ERROR')
    result = execute(service, store, switched)
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED and result.data is None
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-account-a')
    def failed(_cid):
        raise ValueError('PRIVATE ERROR')
    result = execute(service, store, failed)
    assert result.ok and any(w.code == 'local_cleanup_unverified' for w in result.warnings)
    assert 'PRIVATE ERROR' not in str(result)


def test_old_pre_post_task_details_are_removed(environment):
    store, _, _ = environment
    details = WebTask(chat_id=CID, description='PRIVATE DESCRIPTION', upstream_status=3,
                      is_pinned=False, created_at=None, updated_at=None)
    service = AsyncMock()
    service.delete_task.return_value = replace(verified(), data=WebTaskDeleteData(
        chat_id=CID, verified=True, pre_read_task=details, post_read_task=details))
    result = execute(service, store)
    assert result.ok and result.data.pre_read_task is None and result.data.post_read_task is None
    assert 'PRIVATE DESCRIPTION' not in str(result)
