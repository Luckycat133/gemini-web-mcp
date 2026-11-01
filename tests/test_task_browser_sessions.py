"""Process-local Task browser reuse without live connections."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.services import task_browser_sessions as sessions


def observed(*, chat='c_fixture', rid='r_fixture', candidate='rc_fixture', vm='projects/p', nonce='nonce'):
    fields = [None] * 12
    fields[4], fields[5], fields[11] = vm, 'private-host', nonce
    response = [None] * 13
    response[0], response[12] = candidate, [{'62': fields}]
    response[1] = ['PRIVATE RESPONSE CONTENT']
    return {'chat_id': chat, 'response_id': rid, 'candidate_id': candidate, 'candidate': response}


@pytest.fixture
def harness(monkeypatch):
    state = {'account': 'synthetic-account', 'entered': 0, 'closed': 0, 'snapshots': []}
    monkeypatch.setattr(sessions, 'current_account_key', lambda: state['account'])
    @asynccontextmanager
    async def connection(client, value):
        state['entered'] += 1
        state['snapshots'].append(str(value))
        try:
            yield object()
        finally:
            state['closed'] += 1
    monkeypatch.setattr(sessions, 'task_browser_connection', connection)
    return state


def test_same_key_reuses_session_and_discards_response(harness):
    async def run():
        client = object()
        async with sessions.pooled_task_browser_connection(client, observed()) as first:
            pass
        async with sessions.pooled_task_browser_connection(client, observed(nonce='refreshed')) as second:
            assert first is second
        assert harness['entered'] == 1 and harness['closed'] == 0
        assert 'PRIVATE RESPONSE CONTENT' not in harness['snapshots'][0]
        await sessions.close_task_browser_sessions()
        assert harness['closed'] == 1
    asyncio.run(run())


@pytest.mark.parametrize('change', ['vm', 'rid', 'client', 'account', 'candidate'])
def test_different_identity_never_reuses(harness, change):
    async def run():
        client = object()
        async with sessions.pooled_task_browser_connection(client, observed()) as first:
            pass
        next_client = object() if change == 'client' else client
        if change == 'account':
            harness['account'] = 'other-account'
        options = {'vm': 'projects/other'} if change == 'vm' else {'rid': 'r_other'} if change == 'rid' else {'candidate': 'rc_other'} if change == 'candidate' else {}
        async with sessions.pooled_task_browser_connection(next_client, observed(**options)) as second:
            assert first is not second
        assert harness['entered'] == 2
        await sessions.close_task_browser_sessions()
    asyncio.run(run())


def test_borrowers_serialize_and_error_invalidates(harness):
    async def run():
        client = object()
        first_entered, release = asyncio.Event(), asyncio.Event()
        order = []
        async def first():
            async with sessions.pooled_task_browser_connection(client, observed()):
                order.append('first')
                first_entered.set()
                await release.wait()
        async def second():
            await first_entered.wait()
            async with sessions.pooled_task_browser_connection(client, observed()):
                order.append('second')
        a, b = asyncio.create_task(first()), asyncio.create_task(second())
        await first_entered.wait()
        await asyncio.sleep(0)
        assert order == ['first']
        release.set()
        await asyncio.gather(a, b)
        assert order == ['first', 'second'] and harness['entered'] == 1
        with pytest.raises(RuntimeError):
            async with sessions.pooled_task_browser_connection(client, observed()):
                raise RuntimeError('synthetic')
        assert harness['closed'] == 1
        async with sessions.pooled_task_browser_connection(client, observed()):
            pass
        assert harness['entered'] == 2
        await sessions.close_task_browser_sessions()
    asyncio.run(run())


def test_idle_timeout_closes_and_reopens(harness, monkeypatch):
    monkeypatch.setattr(sessions, 'IDLE_TTL_SECONDS', 0.01)
    async def run():
        client = object()
        async with sessions.pooled_task_browser_connection(client, observed()):
            pass
        await asyncio.sleep(0.05)
        assert harness['closed'] == 1
        async with sessions.pooled_task_browser_connection(client, observed()):
            pass
        assert harness['entered'] == 2
        await sessions.close_task_browser_sessions()
    asyncio.run(run())


def test_all_busy_rejected_and_shutdown_waits_for_borrow_exit(harness):
    async def run():
        client = object()
        gate, entered = asyncio.Event(), asyncio.Event()
        count = 0
        async def worker(i):
            nonlocal count
            async with sessions.pooled_task_browser_connection(client, observed(rid=f'r_{i}')):
                count += 1
                if count == 4:
                    entered.set()
                await gate.wait()
        tasks = [asyncio.create_task(worker(i)) for i in range(4)]
        await entered.wait()
        with pytest.raises(RuntimeError):
            async with sessions.pooled_task_browser_connection(client, observed(rid='r_extra')):
                pass
        await sessions.close_task_browser_sessions()
        assert harness['closed'] == 0
        gate.set()
        await asyncio.gather(*tasks)
        assert harness['closed'] == 4
    asyncio.run(run())


def test_account_switch_inside_borrow_invalidates(harness):
    async def run():
        with pytest.raises(ValueError):
            async with sessions.pooled_task_browser_connection(object(), observed()):
                harness['account'] = 'other-account'
        assert harness['closed'] == 1
        await sessions.close_task_browser_sessions()
    asyncio.run(run())


def test_event_loop_isolation(harness):
    client = object()
    async def run():
        async with sessions.pooled_task_browser_connection(client, observed()):
            pass
        await sessions.close_task_browser_sessions()
    asyncio.run(run())
    asyncio.run(run())
    assert harness['entered'] == 2 and harness['closed'] == 2
