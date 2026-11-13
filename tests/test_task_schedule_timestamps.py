"""Nanosecond timestamp identity across domain and wire projections."""

import json

import pytest

from src.domain.task_schedules import TaskSchedule, build_update_spec, validate_create_spec
from src.infrastructure.rpc_contracts import build_remy_schedule_create, build_remy_schedule_update
from src.infrastructure.rpc_parsers import parse_remy_schedule_object

BASE = {'title': 'Synthetic schedule', 'instructions': 'Return marker', 'timezone': 'Asia/Shanghai'}


def spec(at, **values):
    return {**BASE, 'frequency': 'once', 'at': at, **values}


@pytest.mark.parametrize(('value', 'expected'), [
    ('2026-11-03T09:00:00.1234+08:00', '2026-11-03T01:00:00.1234+00:00'),
    ('2026-11-03T01:00:00.123400000+00:00', '2026-11-03T01:00:00.1234+00:00'),
    ('2026-11-03T01:00:00.123456789Z', '2026-11-03T01:00:00.123456789+00:00'),
    ('2026-11-03T01:00:00.000000000Z', '2026-11-03T01:00:00+00:00'),
    ('1969-12-31T23:59:59.999999999Z', '1969-12-31T23:59:59.999999999+00:00'),
])
def test_iso_round_trip_preserves_nanoseconds(value, expected):
    assert validate_create_spec(spec(value))['trigger']['at'] == expected


@pytest.mark.parametrize('value', [
    '2026-11-03T01:00:00.1234567890Z',
    '2026-11-03T01:00:00+08:00:00.5',
    '2026-11-03T01:00:00',
])
def test_unsupported_or_unaware_precision_rejected(value):
    with pytest.raises(ValueError):
        validate_create_spec(spec(value))


@pytest.mark.parametrize(('start', 'end', 'valid'), [
    ('2026-11-03T01:00:00Z', '2026-11-03T01:00:00.000000001Z', True),
    ('2026-11-03T01:00:00.1Z', '2026-11-03T01:00:00Z', False),
    ('2026-11-03T09:00:00.123456789+08:00', '2026-11-03T01:00:00.123456789Z', False),
    ('2026-11-03T01:00:00.123456789Z', '2026-11-03T01:00:00.123456788Z', False),
])
def test_boundaries_compared_by_nanosecond_instant(start, end, valid):
    arguments = spec('2026-11-03T01:00:00Z', starts_at=start, ends_at=end)
    if valid:
        validate_create_spec(arguments)
    else:
        with pytest.raises(ValueError):
            validate_create_spec(arguments)


def test_title_update_preserves_existing_once_and_bounds_exactly():
    trigger = {'frequency': 'once', 'at': '2026-11-03T01:00:00.123456789+00:00'}
    initial = {**BASE, 'trigger': trigger, 'starts_at': '2026-11-03T01:00:00.000000001+00:00',
               'ends_at': '2026-11-03T01:00:00.987654321+00:00'}
    raw = json.loads(build_remy_schedule_create(spec=initial))[0]
    raw[0] = 'schedule-fixture'
    before = TaskSchedule(**parse_remy_schedule_object(raw).value)
    patch = validate_create_spec(build_update_spec(before, {'title': 'Changed'}))
    after = json.loads(build_remy_schedule_update(raw_schedule=raw, patch=patch))[0]
    parsed = parse_remy_schedule_object(after).value
    assert parsed['trigger'] == before.trigger
    assert parsed['starts_at'] == before.starts_at and parsed['ends_at'] == before.ends_at
    assert parsed['title'] == 'Changed'
