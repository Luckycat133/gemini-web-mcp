import asyncio
from dataclasses import asdict

import pytest

from src.services.task_progress import MAX_ACTIVITIES, TaskProgressService, parse_task_progress


def candidate():
    result = [None] * 38
    result[0] = 'rc_fixture'
    result[37] = [['unused'], [[['Opening page'], None, 'browser', None, [['PRIVATE OUTPUT']], None, 'opaque', [['SECRET URL']]]]]
    return result


def parse(value):
    return parse_task_progress(value, chat_id='c_fixture', response_id='r_fixture', candidate_id='rc_fixture')


def test_activity_exposes_only_labels_and_counts():
    data = parse(candidate())
    assert data is not None and data.activities[0].label == 'Opening page'
    assert data.activities[0].tool_name == 'browser'
    assert data.activities[0].output_node_count == 1 and data.activities[0].source_count == 1
    assert 'PRIVATE OUTPUT' not in str(asdict(data)) and 'SECRET URL' not in str(asdict(data))


def test_count_depth_and_labels_are_bounded():
    raw = candidate()
    raw[37][1] = [[['A' * 300]]] * (MAX_ACTIVITIES + 1)
    result = parse(raw)
    assert result is not None and result.truncated and len(result.activities) == MAX_ACTIVITIES
    assert len(result.activities[0].label) == 256


def test_legacy_tree_unknown_state_and_depth_limit():
    node = [None, 'nested']
    for _ in range(8):
        node = [None, 'step', None, None, None, None, [1, 2, 3], [node]]
    raw = [None] * 46
    raw[0], raw[45] = 'rc_fixture', [None, None, [[node]]]
    data = parse(raw)
    assert data is not None and data.truncated and all(a.status == 'unknown' for a in data.activities)
    assert max(a.depth for a in data.activities) == 4


def test_missing_and_malformed_are_distinct_from_empty():
    raw = ['rc_fixture']
    assert parse(raw) is None
    raw.extend([None] * 37)
    raw[37] = [None, []]
    assert parse(raw).activities == ()
    raw[37] = [None, 'malformed']
    with pytest.raises(ValueError):
        parse(raw)
    with pytest.raises(ValueError):
        parse(['wrong'])


def service(*, changed=False, latest_verified=True):
    state = {'account': 'account_a', 'calls': 0}
    async def init():
        pass
    async def read(cid, rid):
        state['calls'] += 1
        if changed:
            state['account'] = 'account_b'
        return {'chat_id': cid, 'response_id': rid, 'candidate': candidate()}
    async def latest(cid):
        return {'chat_id': cid, 'response_id': 'r_fixture', 'candidate': candidate(), 'latest_verified': latest_verified}
    return TaskProgressService(init, read, latest, lambda: state['account'], timeout_seconds=0.02), state


def test_exact_read_account_fence_and_no_identity_substitution():
    svc, state = service(changed=True)
    result = asyncio.run(svc.read_exact_turn('c_fixture', 'r_fixture', 'rc_fixture'))
    assert not result.ok and result.error.code.value == 'CAPABILITY_UNAVAILABLE'
    assert state['calls'] == 1
    svc, _ = service()
    assert not asyncio.run(svc.read_exact_turn('c_fixture', 'r_fixture', 'wrong')).ok


def test_latest_requires_explicit_transport_evidence():
    svc, _ = service(latest_verified=False)
    assert not asyncio.run(svc.read_latest_task('c_fixture')).ok
    svc, _ = service()
    assert asyncio.run(svc.read_latest_task('c_fixture')).ok


def test_initializer_timeout_does_not_read():
    svc, state = service()
    async def stuck():
        await asyncio.Event().wait()
    svc.initializer = stuck
    result = asyncio.run(svc.read_exact_turn('c_fixture', 'r_fixture', 'rc_fixture'))
    assert result.error.code.value == 'TIMED_OUT' and state['calls'] == 0
