"""Preflight failures and no-ops honor the caller's output limit."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.domain.task_schedules import TaskSchedule, TaskSchedulePage
from src.services.task_schedules import TaskScheduleDependencies, TaskScheduleService


@pytest.mark.parametrize(('operation', 'case'), [
    ('update', 'wrong_kind'), ('update', 'not_editable'), ('update', 'completed'), ('update', 'noop'),
    ('set_status', 'wrong_kind'), ('set_status', 'not_editable'), ('set_status', 'completed'), ('set_status', 'noop'),
    ('delete', 'wrong_kind'), ('delete', 'wrong_id'),
    ('update', 'readback_error'), ('set_status', 'readback_error'), ('delete', 'readback_error'),
])
def test_all_preflight_and_readback_results_obey_requested_cap(operation, case):
    before = TaskSchedule(schedule_id='schedule-fixture', title='Synthetic', instructions='long synthetic instruction' * 50,
                          timezone='UTC', status=1, kind=1, trigger={'frequency': 'daily', 'time': '09:00'},
                          revision='revision')
    if case == 'wrong_kind':
        before = replace(before, kind=2)
    elif case == 'not_editable':
        before = replace(before, editable=False)
    elif case == 'completed':
        before = replace(before, status=3)
    elif case == 'wrong_id':
        before = replace(before, schedule_id='different')
    read = AsyncMock(side_effect=[before, RuntimeError('synthetic')] if case == 'readback_error' else None,
                     return_value=before)
    mutation = AsyncMock(return_value=before)
    deps = TaskScheduleDependencies(
        client_provider=lambda: object(), client_initializer=AsyncMock(),
        list_schedules=AsyncMock(side_effect=RuntimeError('synthetic') if operation == 'delete' and case == 'readback_error' else None,
                                 return_value=TaskSchedulePage(())), get_schedule=read,
        create_schedule=mutation, update_schedule=mutation, delete_schedule=mutation,
        account_key_provider=lambda: 'synthetic-account',
    )
    service = TaskScheduleService(deps)
    if operation == 'update':
        coro = service.update_schedule('schedule-fixture', {'title': 'Synthetic' if case == 'noop' else 'New'}, max_chars=10)
    elif operation == 'set_status':
        coro = service.set_status('schedule-fixture', 1 if case == 'noop' else 2, max_chars=10)
    else:
        coro = service.delete_schedule('schedule-fixture', max_chars=10)
    result = asyncio.run(coro)
    assert result.data is not None
    bounded = [item for item in (result.data.before, result.data.after) if item is not None]
    assert bounded and all(len(item.instructions) <= 10 and item.instructions_truncated for item in bounded)
