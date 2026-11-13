"""Synthetic RPC envelopes for schedule transport; no account or network."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.task_schedules import TaskSchedule, TaskSchedulePage
from src.infrastructure.rpc_contracts import build_remy_schedule_create
from src.services import task_schedule_transport as transport


def raw_schedule(*, kind=1, window=False):
    trigger = {'frequency': 'daily', 'time': '09:15'}
    if window:
        trigger['window'] = 'daytime'
    raw = json.loads(build_remy_schedule_create(spec={
        'title': 'Synthetic schedule', 'instructions': 'Return marker',
        'timezone': 'Asia/Shanghai', 'trigger': trigger,
    }))[0]
    raw[0] = 'schedule-fixture'
    raw.extend([None] * (27 - len(raw)))
    raw[8], raw[11], raw[22], raw[23], raw[24], raw[26] = 'opaque9', 'c_fixture', 'opaque23', 'opaque24', kind, 'project'
    return raw


def response(rpcid, body, *, status=200, reject=None):
    return SimpleNamespace(status_code=status, text=json.dumps([
        ['wrb.fr', rpcid, json.dumps(body), None, None, [reject] if reject is not None else None, 'generic']
    ]))


def fake_client(*responses):
    return SimpleNamespace(_batch_execute=AsyncMock(side_effect=list(responses)))


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setattr(transport, 'current_account_key', lambda: 'account-a')


def test_list_retains_all_kinds_and_read_returns_typed_object():
    first, second = raw_schedule(), raw_schedule(kind=2)
    second[0] = 'schedule-other'
    client = fake_client(response('Ey90Ac', [[first, second], True]), response('Mfr55e', [first]))
    async def run():
        return await transport.list_task_schedules(client), await transport.get_task_schedule(client, first[0])
    page, item = asyncio.run(run())
    assert isinstance(page, TaskSchedulePage) and [item.kind for item in page.items] == [1, 2]
    assert page.active_limit_reached is True and isinstance(item, TaskSchedule)
    assert item.schedule_id == first[0] and item.trigger['frequency'] == 'daily'
    assert all(call.kwargs['current_retry'] == 0 for call in client._batch_execute.await_args_list)


def test_read_empty_is_unverified_not_absent():
    client = fake_client(response('Mfr55e', [None]))
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(transport.get_task_schedule(client, 'schedule-fixture'))
    assert error.value.mutation_sent is False


@pytest.mark.parametrize('bad', [
    SimpleNamespace(status_code=503, text=''),
    SimpleNamespace(status_code=200, text='not json'),
    response('Ey90Ac', [[raw_schedule()], False], reject=7),
    response('Ey90Ac', [None]),
    response('wrong', [[raw_schedule()], False]),
])
def test_list_fails_closed_for_http_envelope_rejection_and_shape(bad):
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(transport.list_task_schedules(fake_client(bad)))
    assert error.value.mutation_sent is False


def test_update_fresh_cas_preserves_wire_and_one_dispatch():
    raw = raw_schedule()
    client = fake_client(response('Mfr55e', [raw]), response('Mfr55e', [raw]), response('xrJXie', [raw]))
    async def run():
        before = await transport.get_task_schedule(client, raw[0])
        return await transport.update_task_schedule(client, raw[0], {'status': 2}, expected_before=before)
    asyncio.run(run())
    assert client._batch_execute.await_count == 3
    mutation = client._batch_execute.await_args_list[-1]
    assert mutation.kwargs['current_retry'] == 0
    payload = json.loads(mutation.args[0][0].payload)[0]
    assert payload[9] == 2 and payload[3] == raw[3]
    assert (payload[8], payload[11], payload[22], payload[23], payload[26]) == ('opaque9', 'c_fixture', 'opaque23', 'opaque24', 'project')


def test_stale_revision_rejected_before_update():
    raw = raw_schedule()
    client = fake_client(response('Mfr55e', [raw]), response('Mfr55e', [raw]))
    async def run():
        before = await transport.get_task_schedule(client, raw[0])
        return await transport.update_task_schedule(client, raw[0], {'status': 2}, expected_before=replace(before, revision='stale'))
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(run())
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 2


def test_frequency_change_reaches_wire_builder():
    raw = raw_schedule()
    client = fake_client(response('Mfr55e', [raw]), response('Mfr55e', [raw]), response('xrJXie', [raw]))
    async def run():
        before = await transport.get_task_schedule(client, raw[0])
        return await transport.update_task_schedule(client, raw[0], {'frequency': 'hourly', 'minute': 12}, expected_before=before)
    asyncio.run(run())
    payload = json.loads(client._batch_execute.await_args_list[-1].args[0][0].payload)[0]
    assert payload[3] is None and payload[17] == [12]


def test_window_schedule_title_update_does_not_discard_or_reject_window():
    raw = raw_schedule(window=True)
    client = fake_client(response('Mfr55e', [raw]), response('Mfr55e', [raw]), response('xrJXie', [raw]))
    async def run():
        before = await transport.get_task_schedule(client, raw[0])
        return await transport.update_task_schedule(client, raw[0], {'title': 'New title'}, expected_before=before)
    asyncio.run(run())
    payload = json.loads(client._batch_execute.await_args_list[-1].args[0][0].payload)[0]
    assert payload[3] == raw[3] and payload[1] == 'New title'


def test_account_switch_after_await_blocks_and_fences_write(monkeypatch):
    raw = raw_schedule()
    state = {'account': 'account-a'}
    monkeypatch.setattr(transport, 'current_account_key', lambda: state['account'])
    async def switch(*args, **kwargs):
        state['account'] = 'account-b'
        return response('Mfr55e', [raw])
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=switch))
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(transport.get_task_schedule(client, raw[0]))
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 1


def test_lost_mutation_reply_is_uncertain_and_never_replayed():
    client = fake_client(TimeoutError())
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(transport.delete_task_schedule(client, 'schedule-fixture'))
    assert error.value.mutation_sent is None and client._batch_execute.await_count == 1
    assert client._batch_execute.await_args.kwargs['current_retry'] == 0


def test_invalid_preflight_never_dispatches():
    client = fake_client()
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(transport.delete_task_schedule(client, ''))
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 0


def test_account_switch_after_mutation_reply_is_uncertain(monkeypatch):
    state = {'account': 'account-a'}
    monkeypatch.setattr(transport, 'current_account_key', lambda: state['account'])
    async def switch(*args, **kwargs):
        state['account'] = 'account-b'
        return response('CK9lTc', [])
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=switch))
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(transport.delete_task_schedule(client, 'schedule-fixture'))
    assert error.value.mutation_sent is None and client._batch_execute.await_count == 1


def test_malformed_mutation_reply_is_uncertain():
    client = fake_client(SimpleNamespace(status_code=200, text='bad response'))
    with pytest.raises(transport.ScheduleTransportError) as error:
        asyncio.run(transport.delete_task_schedule(client, 'schedule-fixture'))
    assert error.value.mutation_sent is None and client._batch_execute.await_count == 1


def test_create_returns_typed_data_and_dispatches_once():
    raw = raw_schedule()
    client = fake_client(response('mDJkAd', [raw]))
    spec = {'title': 'Synthetic schedule', 'instructions': 'Return marker', 'timezone': 'Asia/Shanghai',
            'trigger': {'frequency': 'daily', 'time': '09:15'}}
    result = asyncio.run(transport.create_task_schedule(client, spec))
    assert isinstance(result, TaskSchedule) and result.schedule_id == 'schedule-fixture'
    assert client._batch_execute.await_count == 1
    assert client._batch_execute.await_args.kwargs['current_retry'] == 0
