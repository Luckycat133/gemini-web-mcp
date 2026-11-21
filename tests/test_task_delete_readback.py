"""Task deletion is dispatched once and only bounded read-back is retried."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.domain import DomainErrorCode
from src.domain.web_tasks import WebTask, WebTaskPage
from src.services import web_tasks
from src.services.web_tasks import WebTaskDependencies, WebTaskService

CID = 'c_0123456789abcdef'
OTHER = 'c_fedcba9876543210'


def task(cid=CID):
    return WebTask(chat_id=cid, description='PRIVATE SYNTHETIC DESCRIPTION', upstream_status=5,
                   is_pinned=False, created_at=None, updated_at=None)


@pytest.fixture
def setup(monkeypatch):
    state = {'account': 'a'}
    read = AsyncMock()
    delete = AsyncMock()
    deps = WebTaskDependencies(client_provider=lambda: object(), client_initializer=AsyncMock(),
        list_tasks=AsyncMock(return_value=WebTaskPage(items=(), next_page_token=None)), get_task=read, delete_task=delete,
        account_key_provider=lambda: state['account'], readback_delays=(0.25, 0.75, 1.5, 2.5))
    original_sleep = asyncio.sleep
    class FastAsyncio:
        def __getattr__(self, name):
            return getattr(asyncio, name)
        async def sleep(self, delay):
            await original_sleep(0)
    monkeypatch.setattr(web_tasks, 'asyncio', FastAsyncio())
    return state, deps, read, delete


def test_eventual_absence_is_verified_and_mutation_sent_once(setup):
    _, deps, read, delete = setup
    read.side_effect = [task(), task(), task(), None]
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert result.ok and result.data.verified and result.data.changed
    assert read.await_count == 4 and delete.await_count == 1


def test_lost_reply_followed_by_absence_is_verified_without_replay(setup):
    _, deps, read, delete = setup
    read.side_effect = [task(), task(), None]
    delete.side_effect = TimeoutError()
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert result.ok and result.data.verified and delete.await_count == 1


def test_all_present_returns_unknown_and_never_replays(setup):
    _, deps, read, delete = setup
    read.return_value = task()
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert not result.ok and result.data.changed is None and not result.data.verified
    assert read.await_count == 6 and delete.await_count == 1


@pytest.mark.parametrize('outcome', ['wrong_identity', 'error'])
def test_wrong_identity_or_failed_read_stops_polling(setup, outcome):
    _, deps, read, delete = setup
    read.side_effect = [task(), task(), task(OTHER) if outcome == 'wrong_identity' else TimeoutError()]
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert not result.ok and result.data.changed is None
    assert read.await_count == 3 and delete.await_count == 1


def test_account_switch_during_delay_stops_before_new_read(setup, monkeypatch):
    state, deps, read, delete = setup
    read.side_effect = [task(), task(), None]
    original_sleep = asyncio.sleep
    class SwitchAsyncio:
        def __getattr__(self, name):
            return getattr(asyncio, name)
        async def sleep(self, delay):
            state['account'] = 'b'
            await original_sleep(0)
    monkeypatch.setattr(web_tasks, 'asyncio', SwitchAsyncio())
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED
    assert result.data.pre_read_task is None and result.data.post_read_task is None
    assert read.await_count == 2 and delete.await_count == 1


def test_account_switch_during_read_error_scrubs_old_metadata(setup):
    state, deps, read, delete = setup
    calls = 0
    async def lookup(*_):
        nonlocal calls
        calls += 1
        if calls == 3:
            state['account'] = 'b'
            raise OSError('private')
        return task()
    read.side_effect = lookup
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED
    assert result.data.pre_read_task is None and result.data.post_read_task is None
    assert 'PRIVATE SYNTHETIC DESCRIPTION' not in str(result) and delete.await_count == 1


@pytest.mark.parametrize('delays', [[1], (float('inf'),), (-1,), (6,), (True,), (0, 0, 0, 0, 0, 0)])
def test_invalid_delay_configuration_rejected(setup, delays):
    _, deps, _, _ = setup
    with pytest.raises(ValueError):
        WebTaskService(replace(deps, readback_delays=delays))


def test_readback_has_ten_second_deadline(setup, monkeypatch):
    _, deps, read, delete = setup
    calls = 0
    async def lookup(*_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return task()
        await asyncio.Event().wait()
    read.side_effect = lookup
    real_deadline = web_tasks.await_before_deadline
    observed = []
    async def shorten(awaitable, *, timeout):
        observed.append(timeout)
        return await real_deadline(awaitable, timeout=0.01 if timeout == 10 else timeout)
    monkeypatch.setattr(web_tasks, 'await_before_deadline', shorten)
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert not result.ok and result.meta.verification_status == 'read_error'
    assert 10 in observed and read.await_count == 2 and delete.await_count == 1


def test_default_has_no_extra_reads(setup):
    _, deps, read, delete = setup
    read.side_effect = [task(), task()]
    result = asyncio.run(WebTaskService(replace(deps, readback_delays=())).delete_task(CID))
    assert not result.ok and read.await_count == 2 and delete.await_count == 1


def test_account_switch_during_successful_read_scrubs_old_metadata(setup):
    state, deps, read, delete = setup
    calls = 0
    async def lookup(*_):
        nonlocal calls
        calls += 1
        if calls == 3:
            state['account'] = 'b'
        return task()
    read.side_effect = lookup
    result = asyncio.run(WebTaskService(deps).delete_task(CID))
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED
    assert result.data.pre_read_task is None and result.data.post_read_task is None
    assert read.await_count == 3 and delete.await_count == 1
