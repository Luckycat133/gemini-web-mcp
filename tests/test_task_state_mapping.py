"""Focused tests for the shared goal-state mapping.

Upstream Task (Remy goal) statuses verified in the Gemini legacy frontend:
3 = completed, 4 = could-not-complete (failed), 5 = needs-input (input_required).
The mapping is pure, shared by WebTask, WebTaskSummary and WebTaskGetData, and
every other status (including None and bools) is unknown.
"""

from __future__ import annotations

import pytest

from src.domain.web_tasks import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_INPUT_REQUIRED,
    WebTask,
    WebTaskGetData,
    WebTaskSummary,
    goal_state_from_upstream_status,
)

VERIFIED_STATUSES = [
    (TASK_STATUS_COMPLETED, "completed"),
    (TASK_STATUS_FAILED, "failed"),
    (TASK_STATUS_INPUT_REQUIRED, "input_required"),
]

UNKNOWN_STATUSES = [None, 0, 1, 2, True, False, 99, -1]


def _web_task(upstream_status: int | None) -> WebTask:
    return WebTask(
        chat_id="c_123abc",
        description="Finish the review",
        upstream_status=upstream_status,
        is_pinned=False,
        created_at="2024-01-01T00:00:00Z",
        updated_at=1704067200,
    )


def _web_task_summary(upstream_status: int | None) -> WebTaskSummary:
    return WebTaskSummary(
        chat_id="c_123abc",
        description_preview="Finish the review",
        description_truncated=False,
        upstream_status=upstream_status,
        is_pinned=False,
        created_at="2024-01-01T00:00:00Z",
        updated_at=1704067200,
    )


def _web_task_get_data(upstream_status: int | None) -> WebTaskGetData:
    return WebTaskGetData(
        chat_id="c_123abc",
        description="Finish the review",
        description_truncated=False,
        upstream_status=upstream_status,
        is_pinned=False,
        created_at="2024-01-01T00:00:00Z",
        updated_at=1704067200,
    )


_TASK_CLASSES = (_web_task, _web_task_summary, _web_task_get_data)


@pytest.mark.parametrize("upstream_status,expected_state", VERIFIED_STATUSES)
def test_goal_state_maps_verified_statuses(upstream_status: int, expected_state: str):
    assert goal_state_from_upstream_status(upstream_status) == expected_state


@pytest.mark.parametrize("upstream_status", UNKNOWN_STATUSES)
def test_goal_state_unknown_for_unverified_statuses(upstream_status: int | None):
    assert goal_state_from_upstream_status(upstream_status) == "unknown"


@pytest.mark.parametrize("upstream_status", UNKNOWN_STATUSES)
def test_goal_state_bool_is_not_treated_as_int(upstream_status: int | None):
    if isinstance(upstream_status, bool):
        assert type(upstream_status) is not int
        assert goal_state_from_upstream_status(upstream_status) == "unknown"


@pytest.mark.parametrize("factory", _TASK_CLASSES)
@pytest.mark.parametrize("upstream_status,expected_state", VERIFIED_STATUSES)
def test_task_dataclasses_map_verified_statuses(factory, upstream_status: int, expected_state: str):
    task = factory(upstream_status)
    assert task.state == expected_state
    # Raw upstream status is preserved verbatim, including for mapped states.
    assert task.upstream_status == upstream_status


@pytest.mark.parametrize("factory", _TASK_CLASSES)
@pytest.mark.parametrize("upstream_status", UNKNOWN_STATUSES)
def test_task_dataclasses_unknown_for_unverified_statuses(factory, upstream_status: int | None):
    task = factory(upstream_status)
    assert task.state == "unknown"
    assert task.upstream_status is upstream_status


def test_task_dataclasses_agree_on_state_for_every_status():
    statuses = [status for status, _ in VERIFIED_STATUSES] + UNKNOWN_STATUSES
    for upstream_status in statuses:
        expected = goal_state_from_upstream_status(upstream_status)
        assert _web_task(upstream_status).state == expected
        assert _web_task_summary(upstream_status).state == expected
        assert _web_task_get_data(upstream_status).state == expected
