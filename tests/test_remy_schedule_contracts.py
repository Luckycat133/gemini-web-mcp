import json

import pytest

from src.infrastructure.rpc_contracts import build_remy_schedule_create, build_remy_schedule_update, get_contract
from src.infrastructure.rpc_parsers import parse_remy_schedule_list, parse_remy_schedule_object, parse_remy_schedule_reply


BASE = {'title': 'Synthetic schedule', 'instructions': 'Return marker', 'timezone': 'Asia/Shanghai'}


def raw_for(trigger):
    payload = json.loads(build_remy_schedule_create(spec={**BASE, 'trigger': trigger}))[0]
    payload[0] = 'schedule-fixture'
    return payload


@pytest.mark.parametrize('trigger', [
    {'frequency': 'daily', 'time': '09:15'},
    {'frequency': 'weekly', 'weekdays': [1, 5], 'time': '09:15', 'window': 'daytime'},
    {'frequency': 'hourly', 'minute': 15},
    {'frequency': 'monthly', 'days_of_month': [1, 15], 'time': '09:15'},
    {'frequency': 'yearly', 'month': 2, 'day': 29, 'time': '09:15'},
    {'frequency': 'once', 'at': '2026-11-03T09:00:00+08:00'},
])
def test_trigger_round_trip(trigger):
    result = parse_remy_schedule_object(raw_for(trigger))
    assert result.status == 'success' and result.value['editable']
    observed = result.value['trigger']
    assert observed['frequency'] == trigger['frequency']
    if trigger['frequency'] == 'once':
        assert observed['at'] == '2026-11-03T01:00:00+00:00'
    else:
        assert observed == trigger


def test_pause_keeps_complete_writable_projection_and_drops_readonly():
    raw = raw_for({'frequency': 'once', 'at': '2026-11-03T09:00:00+08:00'})
    raw.extend([None] * (27 - len(raw)))
    raw[8], raw[11], raw[21], raw[22], raw[23], raw[25], raw[26] = 'opaque9', 'cid', [123], 'opaque23', 'opaque24', 3, 'project'
    changed = json.loads(build_remy_schedule_update(raw_schedule=raw, patch={'status': 2}))[0]
    assert changed[9] == 2 and changed[20] == raw[20]
    assert changed[8] == 'opaque9' and changed[11] == 'cid' and changed[22:24] == ['opaque23', 'opaque24']
    assert changed[21] is None and changed[25] is None and changed[26] == 'project'
    assert parse_remy_schedule_object(changed).value['revision'] != parse_remy_schedule_object(raw).value['revision']


def test_unknown_and_ambiguous_triggers_and_future_fields_are_not_editable():
    raw = raw_for({'frequency': 'daily', 'time': '09:00'})
    raw[10] = [1]
    assert parse_remy_schedule_object(raw).value['trigger']['frequency'] == 'unknown'
    with pytest.raises(ValueError):
        build_remy_schedule_update(raw_schedule=raw, patch={'status': 2})
    raw[10] = None
    raw[24] = 77
    assert not parse_remy_schedule_object(raw).value['editable']
    raw[24] = 1
    raw.extend([None] * (27 - len(raw)))
    raw.append('future')
    assert not parse_remy_schedule_object(raw).value['editable']


def test_reply_and_list_fail_closed_on_invalid_shape_and_duplicates():
    raw = raw_for({'frequency': 'daily', 'time': '09:00'})
    assert parse_remy_schedule_reply([None]).status == 'empty'
    assert parse_remy_schedule_reply({}).status == 'changed_shape'
    assert parse_remy_schedule_list([[], False]).status == 'empty'
    assert parse_remy_schedule_list([[raw, raw], False]).status == 'changed_shape'
    assert parse_remy_schedule_list([[raw], 'yes']).status == 'changed_shape'
    raw[9] = True
    assert parse_remy_schedule_object(raw).status == 'changed_shape'


def test_recurrence_switch_removes_old_oneof_and_completed_cannot_edit():
    raw = raw_for({'frequency': 'daily', 'time': '09:00'})
    changed = json.loads(build_remy_schedule_update(raw_schedule=raw, patch={'trigger': {'frequency': 'hourly', 'minute': 12}}))[0]
    assert changed[3] is None and changed[17] == [12]
    raw[9] = 3
    with pytest.raises(ValueError):
        build_remy_schedule_update(raw_schedule=raw, patch={'status': 1})


def test_registered_rpc_contracts_and_mutation_modes():
    for name, rpcid, mode in [('list', 'Ey90Ac', 'read'), ('read', 'Mfr55e', 'read'), ('create', 'mDJkAd', 'mutation'), ('update', 'xrJXie', 'mutation'), ('delete', 'CK9lTc', 'mutation')]:
        contract = get_contract('task_schedules.' + name)
        assert (contract.rpc_id, contract.mode, contract.source_path) == (rpcid, mode, '/scheduled')


def test_full_normalized_patch_rejects_stale_revision_and_preserves_nanoseconds():
    raw = raw_for({'frequency': 'once', 'at': '2026-11-03T09:00:00.123456789+08:00'})
    observed = parse_remy_schedule_object(raw).value
    assert observed['trigger']['at'] == '2026-11-03T01:00:00.123456789+00:00'
    encoded = json.loads(build_remy_schedule_update(raw_schedule=raw, patch=observed))[0]
    assert parse_remy_schedule_object(encoded).value['trigger'] == observed['trigger']
    with pytest.raises(ValueError):
        build_remy_schedule_update(raw_schedule=raw, patch={**observed, 'revision': 'stale'})


def test_observed_opaque_false_read_field_is_omitted_like_native_write_projection():
    raw = raw_for({'frequency': 'daily', 'time': '09:00'})
    raw[2] = False
    assert parse_remy_schedule_object(raw).value['editable']
    written = json.loads(build_remy_schedule_update(raw_schedule=raw, patch={'status': 2}))[0]
    assert written[2] is None
    raw[2] = True
    assert parse_remy_schedule_object(raw).value['editable']
    raw[2] = 0
    assert parse_remy_schedule_object(raw).status == 'changed_shape'
