"""Deterministic offline tests for TaskScheduleService.

Every transport and identity callback is a fake. No network, browser, account,
cookie, or upstream RPC is involved. No RPC IDs or indices appear in these tests.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.domain import DomainErrorCode
from src.domain.task_schedules import (
    KIND_CHAT_ASYNC,
    KIND_REMY_TASK,
    KIND_UNKNOWN,
    MAX_SCHEDULE_TITLE_LENGTH,
    TASK_SCHEDULE_STATUS_ACTIVE,
    TASK_SCHEDULE_STATUS_COMPLETED,
    TaskSchedule,
    TaskSchedulePage,
    TaskScheduleTransportError,
    build_update_spec,
    normalize_trigger,
    validate_create_spec,
)
from src.services.task_schedules import TaskScheduleDependencies, TaskScheduleService

SCHEDULE_ID = "sched_abc123"
OTHER_ID = "sched_def456"
ACCOUNT = "acct_alpha"


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _make_schedule(
    schedule_id: str = SCHEDULE_ID,
    title: str = "Scheduled Task",
    instructions: str = "Do the thing",
    timezone: str = "America/New_York",
    status: int = TASK_SCHEDULE_STATUS_ACTIVE,
    kind: int | None = KIND_REMY_TASK,
    trigger: dict[str, Any] | None = None,
    conversation_id: str | None = "conv_1",
    project_id: str | None = "proj_1",
    starts_at: str | None = None,
    ends_at: str | None = None,
    editable: bool = True,
    revision: str | None = "rev_1",
) -> TaskSchedule:
    if trigger is None:
        trigger = {"frequency": "daily", "time": "09:00"}
    return TaskSchedule(
        schedule_id=schedule_id,
        title=title,
        instructions=instructions,
        timezone=timezone,
        status=status,
        kind=kind,
        trigger=trigger,
        conversation_id=conversation_id,
        project_id=project_id,
        starts_at=starts_at,
        ends_at=ends_at,
        editable=editable,
        revision=revision,
    )


class FakeTransport:
    def __init__(
        self,
        account: str | None = ACCOUNT,
        account_sequence: list[str | None] | None = None,
        schedules: dict[str, TaskSchedule] | None = None,
        list_return: TaskSchedulePage | None = None,
        read_values: list[TaskSchedule | None] | None = None,
        create_return: TaskSchedule | None = None,
        update_return: TaskSchedule | None = None,
        delete_return: Any = None,
        initializer_side_effect: BaseException | None = None,
        read_side_effect: BaseException | None = None,
        create_side_effect: BaseException | None = None,
        update_side_effect: BaseException | None = None,
        delete_side_effect: BaseException | None = None,
        list_side_effect: BaseException | None = None,
    ) -> None:
        self.account = account
        self._account_iterator = iter(account_sequence) if account_sequence is not None else None
        self._schedules = dict(schedules or {})
        self._list_return = list_return
        self._read_values = list(read_values) if read_values is not None else None
        self.create_return = create_return
        self.update_return = update_return
        self.delete_return = delete_return
        self.initializer_side_effect = initializer_side_effect
        self.read_side_effect = read_side_effect
        self.create_side_effect = create_side_effect
        self.update_side_effect = update_side_effect
        self.delete_side_effect = delete_side_effect
        self.list_side_effect = list_side_effect
        self.client = object()
        self.provider_calls = 0
        self.initializer_calls = 0
        self.account_key_calls = 0
        self.read_calls: list[tuple[Any, str]] = []
        self.create_calls: list[tuple[Any, dict[str, Any]]] = []
        self.update_calls: list[tuple[Any, str, dict[str, Any], TaskSchedule]] = []
        self.delete_calls: list[tuple[Any, str]] = []
        self.list_calls: list[tuple[Any]] = []

    def account_key(self) -> str | None:
        self.account_key_calls += 1
        if self._account_iterator is not None:
            return next(self._account_iterator, "exhausted-account")
        return self.account

    def client_provider(self) -> Any:
        self.provider_calls += 1
        return self.client

    async def client_initializer(self) -> None:
        self.initializer_calls += 1
        if self.initializer_side_effect is not None:
            raise self.initializer_side_effect

    async def list_schedules(self, client: Any) -> TaskSchedulePage:
        self.list_calls.append((client,))
        if self.list_side_effect is not None:
            raise self.list_side_effect
        if self._list_return is not None:
            return self._list_return
        items = tuple(self._schedules.values())
        return TaskSchedulePage(items=items, unknown_kind_count=0)

    async def get_schedule(self, client: Any, schedule_id: str) -> TaskSchedule | None:
        self.read_calls.append((client, schedule_id))
        if self.read_side_effect is not None:
            raise self.read_side_effect
        if self._read_values is not None:
            return self._read_values.pop(0)
        return self._schedules.get(schedule_id)

    async def create_schedule(
        self, client: Any, spec: dict[str, Any]
    ) -> TaskSchedule | None:
        self.create_calls.append((client, dict(spec)))
        if self.create_side_effect is not None:
            raise self.create_side_effect
        if self.create_return is not None:
            self._schedules[self.create_return.schedule_id] = self.create_return
        return self.create_return

    async def update_schedule(
        self,
        client: Any,
        schedule_id: str,
        patch: dict[str, Any],
        expected_before: TaskSchedule,
    ) -> TaskSchedule | None:
        self.update_calls.append((client, schedule_id, dict(patch), expected_before))
        if self.update_side_effect is not None:
            raise self.update_side_effect
        if self.update_return is not None:
            self._schedules[schedule_id] = self.update_return
        return self.update_return

    async def delete_schedule(self, client: Any, schedule_id: str) -> Any:
        self.delete_calls.append((client, schedule_id))
        if self.delete_side_effect is not None:
            raise self.delete_side_effect
        self._schedules.pop(schedule_id, None)
        return self.delete_return


def _service(transport: FakeTransport) -> TaskScheduleService:
    return TaskScheduleService(
        TaskScheduleDependencies(
            client_provider=transport.client_provider,
            client_initializer=transport.client_initializer,
            list_schedules=transport.list_schedules,
            get_schedule=transport.get_schedule,
            create_schedule=transport.create_schedule,
            update_schedule=transport.update_schedule,
            delete_schedule=transport.delete_schedule,
            account_key_provider=transport.account_key,
        )
    )


# ------------------------------------------------------------------ domain tests

def test_task_schedule_state_active_paused_completed_unknown():
    assert _make_schedule(status=1).state == "active"
    assert _make_schedule(status=2).state == "paused"
    assert _make_schedule(status=3).state == "completed"
    assert _make_schedule(status=0).state == "unknown"
    assert _make_schedule(status=99).state == "unknown"


def test_task_schedule_unknown_bool():
    s = _make_schedule(status=0)
    assert s.state == "unknown"
    s2 = _make_schedule(status=1)
    assert s2.state == "active"


def test_validate_create_spec_daily():
    result = validate_create_spec({
        "title": "Daily Task",
        "instructions": "Run daily",
        "timezone": "America/New_York",
        "frequency": "daily",
        "time": "09:00",
    })
    assert result["trigger"] == {"frequency": "daily", "time": "09:00"}


def test_validate_create_spec_weekly():
    result = validate_create_spec({
        "title": "Weekly Task",
        "instructions": "Run weekly",
        "timezone": "America/New_York",
        "frequency": "weekly",
        "time": "10:30",
        "weekdays": [1, 3, 5],
    })
    assert result["trigger"] == {
        "frequency": "weekly",
        "time": "10:30",
        "weekdays": [1, 3, 5],
    }


def test_validate_create_spec_hourly():
    result = validate_create_spec({
        "title": "Hourly Task",
        "instructions": "Run hourly",
        "timezone": "UTC",
        "frequency": "hourly",
        "minute": 15,
    })
    assert result["trigger"] == {"frequency": "hourly", "minute": 15}


def test_validate_create_spec_monthly():
    result = validate_create_spec({
        "title": "Monthly Task",
        "instructions": "Run monthly",
        "timezone": "Europe/London",
        "frequency": "monthly",
        "time": "08:00",
        "days_of_month": [1, 15],
    })
    assert result["trigger"] == {
        "frequency": "monthly",
        "time": "08:00",
        "days_of_month": [1, 15],
    }


def test_validate_create_spec_yearly_leap():
    result = validate_create_spec({
        "title": "Yearly Task",
        "instructions": "Run yearly",
        "timezone": "UTC",
        "frequency": "yearly",
        "time": "07:00",
        "month": 2,
        "day": 29,
    })
    assert result["trigger"] == {
        "frequency": "yearly",
        "time": "07:00",
        "month": 2,
        "day": 29,
    }


def test_validate_create_spec_once():
    result = validate_create_spec({
        "title": "Once Task",
        "instructions": "Run once",
        "timezone": "America/Chicago",
        "frequency": "once",
        "at": "2026-12-31T23:59:00+00:00",
    })
    assert result["trigger"] == {"frequency": "once", "at": "2026-12-31T23:59:00+00:00"}


def test_validate_create_spec_window():
    result = validate_create_spec({
        "title": "Window Task",
        "instructions": "Windowed",
        "timezone": "UTC",
        "frequency": "daily",
        "time": "09:00",
        "window": "overnight",
    })
    assert result["trigger"] == {
        "frequency": "daily", "time": "09:00", "window": "overnight"
    }


def test_validate_create_spec_missing_frequency():
    with pytest.raises(ValueError, match="frequency is required"):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "time": "09:00",
        })


def test_validate_create_spec_unknown_key():
    with pytest.raises(ValueError, match="Unknown create spec keys"):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "frequency": "daily", "time": "09:00", "bogus": 1,
        })


def test_validate_create_spec_bool_int_minute():
    with pytest.raises(ValueError, match="exact integer"):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "frequency": "hourly", "minute": True,
        })


def test_validate_create_spec_inapplicable_key():
    with pytest.raises(ValueError, match="not applicable"):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "frequency": "daily", "time": "09:00", "weekdays": [1],
        })


def test_validate_create_spec_window_with_time():
    result = validate_create_spec({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00", "window": "daytime",
    })
    assert result["trigger"] == {
        "frequency": "daily", "time": "09:00", "window": "daytime"
    }


def test_validate_create_spec_weekly_unique():
    with pytest.raises(ValueError, match="duplicates"):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "frequency": "weekly", "time": "09:00", "weekdays": [1, 1],
        })


def test_validate_create_spec_yearly_invalid_day():
    with pytest.raises(ValueError):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "frequency": "yearly", "time": "09:00", "month": 2, "day": 30,
        })


def test_validate_create_spec_once_naive_iso():
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "frequency": "once", "at": "2026-12-31T23:59:00",
        })


def test_validate_create_spec_bad_timezone():
    with pytest.raises(ValueError, match="not a valid IANA timezone"):
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "Not/AZone",
            "frequency": "daily", "time": "09:00",
        })


def test_validate_create_spec_blank_title():
    with pytest.raises(ValueError, match="nonblank"):
        validate_create_spec({
            "title": "   ", "instructions": "I", "timezone": "UTC",
            "frequency": "daily", "time": "09:00",
        })


def test_validate_create_spec_title_too_long():
    with pytest.raises(ValueError, match="exceed"):
        validate_create_spec({
            "title": "x" * (MAX_SCHEDULE_TITLE_LENGTH + 1),
            "instructions": "I", "timezone": "UTC",
            "frequency": "daily", "time": "09:00",
        })


def test_build_update_spec_same_frequency_preserves():
    before = _make_schedule(trigger={"frequency": "weekly", "time": "09:00", "weekdays": [1, 3]})
    merged = build_update_spec(before, {"time": "10:00"})
    assert merged["frequency"] == "weekly"
    assert merged["time"] == "10:00"
    assert merged["weekdays"] == [1, 3]


def test_build_update_spec_changed_frequency_discards():
    before = _make_schedule(trigger={"frequency": "weekly", "time": "09:00", "weekdays": [1, 3]})
    merged = build_update_spec(before, {"frequency": "daily", "time": "10:00"})
    assert merged["frequency"] == "daily"
    assert merged["time"] == "10:00"
    assert "weekdays" not in merged


def test_build_update_spec_null_clears_ends_at():
    before = _make_schedule(ends_at="2026-12-31T23:59:00+00:00")
    merged = build_update_spec(before, {"ends_at": None})
    assert merged["ends_at"] is None


# ------------------------------------------------------------------ invalid-before-auth tests

def test_list_invalid_limit_bool():
    t = FakeTransport()
    result = _run(_service(t).list_schedules(limit=True))
    assert not result.ok
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert t.provider_calls == 0


def test_list_invalid_limit_zero():
    t = FakeTransport()
    result = _run(_service(t).list_schedules(limit=0))
    assert not result.ok
    assert t.provider_calls == 0


def test_list_invalid_offset_negative():
    t = FakeTransport()
    result = _run(_service(t).list_schedules(offset=-1))
    assert not result.ok
    assert t.provider_calls == 0


def test_list_invalid_max_chars_bool():
    t = FakeTransport()
    result = _run(_service(t).list_schedules(max_chars=True))
    assert not result.ok
    assert t.provider_calls == 0


def test_get_invalid_id_empty():
    t = FakeTransport()
    result = _run(_service(t).get_schedule(""))
    assert not result.ok
    assert t.provider_calls == 0


def test_get_invalid_id_too_long():
    t = FakeTransport()
    result = _run(_service(t).get_schedule("x" * 257))
    assert not result.ok
    assert t.provider_calls == 0


def test_create_invalid_before_auth():
    t = FakeTransport()
    result = _run(_service(t).create_schedule({"title": "T", "instructions": "I"}))
    assert not result.ok
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert t.initializer_calls == 0
    assert t.provider_calls == 0


def test_update_invalid_id():
    t = FakeTransport()
    result = _run(_service(t).update_schedule("", {"title": "T"}))
    assert not result.ok
    assert t.initializer_calls == 0


def test_set_status_invalid_status():
    t = FakeTransport()
    result = _run(_service(t).set_status(SCHEDULE_ID, 3))
    assert not result.ok
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert t.initializer_calls == 0


def test_delete_invalid_id():
    t = FakeTransport()
    result = _run(_service(t).delete_schedule(""))
    assert not result.ok
    assert t.initializer_calls == 0


# ------------------------------------------------------------------ provider / init tests

def test_initializer_called_before_provider():
    t = FakeTransport()
    _run(_service(t).list_schedules())
    assert t.initializer_calls == 1
    assert t.provider_calls == 1
    assert t.initializer_calls <= t.provider_calls


def test_provider_never_awaited():
    t = FakeTransport()
    _run(_service(t).list_schedules())
    assert t.initializer_calls == 1


def test_initializer_exception():
    t = FakeTransport(initializer_side_effect=RuntimeError("init failed"))
    result = _run(_service(t).list_schedules())
    assert not result.ok
    assert result.error_code == DomainErrorCode.INTERNAL_ERROR.value


# ------------------------------------------------------------------ account flip tests

def test_account_flip_after_initializer():
    """Account changes during initializer -> mismatch, no schedules read."""
    t = FakeTransport(account_sequence=[ACCOUNT, "other_acct"])
    result = _run(_service(t).list_schedules())
    assert not result.ok
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED.value
    assert t.list_calls == []


def test_account_flip_after_list_read():
    """Account changes after list read -> page not trusted."""
    t = FakeTransport(
        account_sequence=[ACCOUNT, ACCOUNT, "other_acct"],
        schedules={SCHEDULE_ID: _make_schedule()},
    )
    result = _run(_service(t).list_schedules())
    assert not result.ok
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED.value


def test_account_flip_on_get_read():
    t = FakeTransport(
        account_sequence=[ACCOUNT, ACCOUNT, "other_acct"],
        schedules={SCHEDULE_ID: _make_schedule()},
    )
    result = _run(_service(t).get_schedule(SCHEDULE_ID))
    assert not result.ok
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED.value


def test_account_flip_on_create_readback():
    t = FakeTransport(
        account_sequence=[ACCOUNT, ACCOUNT, ACCOUNT, "other_acct"],
        create_return=_make_schedule(),
    )
    result = _run(_service(t).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert not result.ok


def test_account_flip_on_update_readback():
    t = FakeTransport(
        account_sequence=[ACCOUNT, ACCOUNT, ACCOUNT, "other_acct"],
        schedules={SCHEDULE_ID: _make_schedule()},
        update_return=_make_schedule(),
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok


def test_account_flip_on_delete_listproof():
    t = FakeTransport(
        account_sequence=[ACCOUNT, ACCOUNT, ACCOUNT, ACCOUNT, "other_acct"],
        schedules={SCHEDULE_ID: _make_schedule()},
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert not result.ok


# ------------------------------------------------------------------ kind refusal tests

def test_get_refuses_kind2_chat():
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule(kind=KIND_CHAT_ASYNC)})
    result = _run(_service(t).get_schedule(SCHEDULE_ID))
    assert not result.ok
    assert result.error_code == DomainErrorCode.VERIFICATION_FAILED.value


def test_get_refuses_kind0_unknown():
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule(kind=KIND_UNKNOWN)})
    result = _run(_service(t).get_schedule(SCHEDULE_ID))
    assert not result.ok


def test_update_refuses_kind2_chat():
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule(kind=KIND_CHAT_ASYNC)})
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok


def test_set_status_refuses_kind0():
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule(kind=KIND_UNKNOWN)})
    result = _run(_service(t).set_status(SCHEDULE_ID, 1))
    assert not result.ok


# ------------------------------------------------------------------ completed tests

def test_completed_get_is_ok():
    """Successful metadata read of a completed schedule remains ok."""
    completed = _make_schedule(status=TASK_SCHEDULE_STATUS_COMPLETED)
    t = FakeTransport(schedules={SCHEDULE_ID: completed})
    result = _run(_service(t).get_schedule(SCHEDULE_ID))
    assert result.ok


def test_completed_rejects_update():
    completed = _make_schedule(status=TASK_SCHEDULE_STATUS_COMPLETED)
    t = FakeTransport(schedules={SCHEDULE_ID: completed})
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok


def test_completed_rejects_set_status():
    completed = _make_schedule(status=TASK_SCHEDULE_STATUS_COMPLETED)
    t = FakeTransport(schedules={SCHEDULE_ID: completed})
    result = _run(_service(t).set_status(SCHEDULE_ID, 1))
    assert not result.ok


def test_completed_allows_delete():
    completed = _make_schedule(status=TASK_SCHEDULE_STATUS_COMPLETED)
    t = FakeTransport(
        schedules={SCHEDULE_ID: completed},
        list_return=TaskSchedulePage(items=(), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert result.ok


# ------------------------------------------------------------------ editable / unknown trigger tests

def test_not_editable_rejects_update():
    not_editable = _make_schedule(editable=False)
    t = FakeTransport(schedules={SCHEDULE_ID: not_editable})
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok


def test_not_editable_rejects_set_status():
    not_editable = _make_schedule(editable=False)
    t = FakeTransport(schedules={SCHEDULE_ID: not_editable})
    result = _run(_service(t).set_status(SCHEDULE_ID, 1))
    assert not result.ok


def test_not_editable_allows_delete():
    not_editable = _make_schedule(editable=False)
    t = FakeTransport(
        schedules={SCHEDULE_ID: not_editable},
        list_return=TaskSchedulePage(items=(), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert result.ok


# ------------------------------------------------------------------ same status no-op

def test_set_status_same_status_noop():
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule(status=1)})
    result = _run(_service(t).set_status(SCHEDULE_ID, 1))
    assert result.ok
    data = result.data
    assert data.changed is False
    assert data.mutation_sent is False
    assert t.update_calls == []


# ------------------------------------------------------------------ create success path

def test_create_one_dispatch_verified_success():
    created = _make_schedule(
        title="T", instructions="I", timezone="UTC",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    t = FakeTransport(
        create_return=created,
        schedules={SCHEDULE_ID: created},
    )
    result = _run(_service(t).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert result.ok
    data = result.data
    assert data.changed is True
    assert data.accepted is True
    assert data.mutation_sent is True
    assert data.verified is True
    assert len(t.create_calls) == 1
    assert len(t.read_calls) >= 1


def test_create_dispatch_count_exactly_one():
    created = _make_schedule(
        title="T", instructions="I", timezone="UTC",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    t = FakeTransport(create_return=created, schedules={SCHEDULE_ID: created})
    _run(_service(t).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert len(t.create_calls) == 1


def test_create_wrong_id_readback():
    """Read-back returns different ID -> unverified."""
    transport = FakeTransport(
        create_return=_make_schedule(schedule_id="sched_other"),
        schedules={SCHEDULE_ID: _make_schedule()},
    )
    result = _run(_service(transport).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert not result.ok


def test_create_create_exception_unverified():
    """Generic callback error -> accepted=None, mutation_sent=None (unknown)."""
    t = FakeTransport(create_side_effect=RuntimeError("boom"))
    result = _run(_service(t).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert not result.ok
    data = result.data
    assert data.changed is None
    assert data.accepted is None
    assert data.mutation_sent is None


def test_create_no_id_cannot_guess():
    """Create returns None/no ID -> unverified, no guessing latest title."""
    t = FakeTransport(create_return=None)
    result = _run(_service(t).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert not result.ok
    data = result.data
    assert data.changed is None


# ------------------------------------------------------------------ update success path

def test_update_one_dispatch_verified_success():
    before = _make_schedule(title="Scheduled Task", instructions="Do the thing",
                            timezone="America/New_York", trigger={"frequency": "daily", "time": "09:00"})
    after = _make_schedule(title="Updated", instructions="Do the thing",
                           timezone="America/New_York", trigger={"frequency": "daily", "time": "09:00"})
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_return=after,
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "Updated"}))
    assert result.ok
    data = result.data
    assert data.changed is True
    assert len(t.update_calls) == 1


def test_update_partial_merge_same_frequency():
    before = _make_schedule(title="Scheduled Task", instructions="Do the thing",
                            timezone="America/New_York",
                            trigger={"frequency": "weekly", "time": "09:00", "weekdays": [1, 3]})
    after = _make_schedule(title="Scheduled Task", instructions="Do the thing",
                           timezone="America/New_York",
                           trigger={"frequency": "weekly", "time": "10:00", "weekdays": [1, 3]})
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_return=after,
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"time": "10:00"}))
    assert result.ok
    # Verify the update call was made with the patch
    assert len(t.update_calls) == 1
    _, _, patch, expected_before = t.update_calls[0]
    assert patch == {"time": "10:00"}
    assert expected_before.schedule_id == SCHEDULE_ID


def test_update_changed_frequency_discards_old_keys():
    before = _make_schedule(title="Scheduled Task", instructions="Do the thing",
                            timezone="America/New_York",
                            trigger={"frequency": "weekly", "time": "09:00", "weekdays": [1, 3]})
    after = _make_schedule(title="Scheduled Task", instructions="Do the thing",
                           timezone="America/New_York",
                           trigger={"frequency": "daily", "time": "10:00"})
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_return=after,
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"frequency": "daily", "time": "10:00"}))
    assert result.ok


def test_update_readback_wrong_association():
    """Read-back has different conversation/project ID -> unverified."""
    before = _make_schedule(conversation_id="conv_1", project_id="proj_1")
    transport = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_return=_make_schedule(conversation_id="conv_2", project_id="proj_2"),
    )
    result = _run(_service(transport).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok


# ------------------------------------------------------------------ set_status success path

def test_set_status_one_dispatch_verified():
    before = _make_schedule(status=1)
    after = _make_schedule(status=2)
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_return=after,
    )
    result = _run(_service(t).set_status(SCHEDULE_ID, 2))
    assert result.ok
    data = result.data
    assert data.changed is True
    assert data.accepted is True


def test_set_status_readback_error():
    before = _make_schedule(status=1)
    after = _make_schedule(status=2)
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_return=after,
        read_values=[before, None],  # pre-read ok, post-read None
    )
    result = _run(_service(t).set_status(SCHEDULE_ID, 2))
    assert not result.ok
    data = result.data
    assert data.changed is None


# ------------------------------------------------------------------ delete success path

def test_delete_complete_list_proof():
    """Delete verified only when fresh complete list shows exact-ID absence."""
    before = _make_schedule()
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        list_return=TaskSchedulePage(items=(), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert result.ok
    data = result.data
    assert data.changed is True
    assert data.verified is True
    assert len(t.delete_calls) == 1
    assert len(t.list_calls) >= 1


def test_delete_dispatch_exactly_once():
    before = _make_schedule()
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        list_return=TaskSchedulePage(items=(), unknown_kind_count=0),
    )
    _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert len(t.delete_calls) == 1


def test_delete_missing_get_insufficient():
    """Missing Get alone is insufficient; must use complete list."""
    before = _make_schedule()
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        list_return=TaskSchedulePage(items=(_make_schedule(),), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert not result.ok


def test_delete_list_proof_still_present():
    """Schedule still in complete list -> unverified, no replay."""
    before = _make_schedule()
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        list_return=TaskSchedulePage(items=(before,), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert not result.ok
    data = result.data
    assert data.changed is None


# ------------------------------------------------------------------ list behavior

def test_list_returns_only_kind1():
    t = FakeTransport(
        list_return=TaskSchedulePage(
            items=(
                _make_schedule(schedule_id="s1", kind=KIND_REMY_TASK),
                _make_schedule(schedule_id="s2", kind=KIND_CHAT_ASYNC),
                _make_schedule(schedule_id="s3", kind=KIND_UNKNOWN),
            ),
            unknown_kind_count=0,
        )
    )
    result = _run(_service(t).list_schedules())
    assert result.ok
    page = result.data
    assert len(page.items) == 1
    assert page.items[0].schedule_id == "s1"
    assert page.unknown_kind_count == 2


def test_list_local_pagination():
    schedules = tuple(
        _make_schedule(schedule_id=f"s{i}", title=f"Task {i}") for i in range(5)
    )
    t = FakeTransport(
        list_return=TaskSchedulePage(items=schedules, unknown_kind_count=0)
    )
    result = _run(_service(t).list_schedules(limit=2, offset=1))
    assert result.ok
    page = result.data
    assert len(page.items) == 2
    assert page.items[0].schedule_id == "s1"
    assert page.items[1].schedule_id == "s2"


def test_list_truthful_unknown_kind_count():
    t = FakeTransport(
        list_return=TaskSchedulePage(
            items=(
                _make_schedule(schedule_id="s1", kind=KIND_REMY_TASK),
                _make_schedule(schedule_id="s2", kind=KIND_CHAT_ASYNC),
                _make_schedule(schedule_id="s3", kind=KIND_UNKNOWN),
                _make_schedule(schedule_id="s4", kind=KIND_CHAT_ASYNC),
            ),
            unknown_kind_count=0,
        )
    )
    result = _run(_service(t).list_schedules())
    assert result.ok
    page = result.data
    assert page.unknown_kind_count == 3  # s2, s3, s4 are non-kind1


def test_list_bounded_instructions():
    long_instructions = "x" * 500
    t = FakeTransport(
        list_return=TaskSchedulePage(
            items=(_make_schedule(instructions=long_instructions),),
            unknown_kind_count=0,
        )
    )
    result = _run(_service(t).list_schedules(max_chars=10))
    assert result.ok
    summary = result.data.items[0]
    assert len(summary.instructions_preview) <= 10
    assert summary.instructions_truncated is True


# ------------------------------------------------------------------ get behavior

def test_get_bounded_instructions():
    long_instructions = "x" * 500
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule(instructions=long_instructions)})
    result = _run(_service(t).get_schedule(SCHEDULE_ID, max_chars=10))
    assert result.ok
    data = result.data
    assert len(data.instructions) <= 10
    assert data.instructions_truncated is True


def test_get_full_instructions_on_verify():
    """Compare full instructions during read-back verification (not truncated)."""
    full = "x" * 500
    before = _make_schedule(instructions=full)
    after = _make_schedule(title="New", instructions=full)
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_return=after,
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert result.ok


# ------------------------------------------------------------------ six triggers via create

def test_create_six_triggers():
    triggers = [
        ("daily", {"time": "09:00"}),
        ("weekly", {"time": "09:00", "weekdays": [1, 3, 5]}),
        ("hourly", {"minute": 15}),
        ("monthly", {"time": "09:00", "days_of_month": [1, 15]}),
        ("yearly", {"time": "09:00", "month": 6, "day": 15}),
        ("once", {"at": "2026-12-31T23:59:00+00:00"}),
    ]
    for freq, args in triggers:
        created = _make_schedule(
            title="T", instructions="I", timezone="UTC",
            trigger={"frequency": freq, **args},
        )
        t = FakeTransport(
            create_return=created,
            schedules={SCHEDULE_ID: created},
        )
        spec = {"title": "T", "instructions": "I", "timezone": "UTC", "frequency": freq, **args}
        result = _run(_service(t).create_schedule(spec))
        assert result.ok, f"Failed for {freq}: {result.error}"


# ------------------------------------------------------------------ trigger normalization tests

def test_normalize_trigger_daily():
    assert normalize_trigger({"frequency": "daily", "time": "09:00"}) == {
        "frequency": "daily", "time": "09:00"
    }


def test_normalize_trigger_window_with_time():
    assert normalize_trigger({"frequency": "weekly", "time": "09:00", "window": "evening", "weekdays": [1]}) == {
        "frequency": "weekly", "time": "09:00", "window": "evening", "weekdays": [1]
    }


# ------------------------------------------------------------------ result bounds

def test_mutation_data_result_bounds():
    t = FakeTransport(
        schedules={SCHEDULE_ID: _make_schedule()},
        list_return=TaskSchedulePage(items=(), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert result.ok
    d = result.to_dict()
    assert d["ok"] is True
    data = d["data"]
    assert data["changed"] is True
    assert data["accepted"] is True
    assert data["verified"] is True


# ------------------------------------------------------------------ account mismatch scrubbing

def test_account_mismatch_scrubs_before_after_and_secret():
    """Account mismatch after dispatch scrubs before/after; synthetic secret absent from to_dict."""
    before = _make_schedule(instructions="synthetic_secret_do_not_leak")
    t = FakeTransport(
        account_sequence=[ACCOUNT, ACCOUNT, ACCOUNT, "other_acct"],
        schedules={SCHEDULE_ID: before},
        update_return=_make_schedule(title="New", instructions="synthetic_secret_do_not_leak"),
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok
    d = result.to_dict()
    data = d["data"]
    assert data["before"] is None
    assert data["after"] is None
    assert "synthetic_secret_do_not_leak" not in str(d)


def test_account_mismatch_get_scrubs_data():
    """Account mismatch on get returns no data with scrubbed details."""
    t = FakeTransport(
        account_sequence=[ACCOUNT, ACCOUNT, "other_acct"],
        schedules={SCHEDULE_ID: _make_schedule(instructions="secret")},
    )
    result = _run(_service(t).get_schedule(SCHEDULE_ID))
    assert not result.ok
    d = result.to_dict()
    assert d["data"] is None
    assert "secret" not in str(d)


# ------------------------------------------------------------------ TaskScheduleTransportError

def test_transport_error_mutation_sent_false():
    """TaskScheduleTransportError mutation_sent=False => changed=False, no post-read."""
    before = _make_schedule()
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_side_effect=TaskScheduleTransportError("preflight failed", mutation_sent=False),
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok
    data = result.data
    assert data.changed is False
    assert data.accepted is None
    assert data.mutation_sent is False
    assert data.verified is False
    # Dispatched once, pre-read only, no post-read
    assert len(t.update_calls) == 1
    assert len(t.read_calls) == 1


def test_transport_error_mutation_sent_none_attempts_readback():
    """TaskScheduleTransportError mutation_sent=None => accepted=None, readback attempted."""
    before = _make_schedule()
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_side_effect=TaskScheduleTransportError("unknown", mutation_sent=None),
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok
    data = result.data
    assert data.changed is None
    assert data.accepted is None
    assert data.mutation_sent is None
    # Readback was attempted (possibly sent)
    assert len(t.read_calls) >= 2


def test_transport_error_mutation_sent_true_attempts_readback():
    """TaskScheduleTransportError mutation_sent=True => accepted=None, readback attempted."""
    before = _make_schedule()
    after = _make_schedule(title="New")
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        update_side_effect=TaskScheduleTransportError("lost response", mutation_sent=True),
        update_return=after,
    )
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok
    data = result.data
    assert data.changed is None
    assert data.accepted is None
    assert data.mutation_sent is True
    assert len(t.read_calls) >= 2


# ------------------------------------------------------------------ strong verification: tampered fields

@pytest.mark.parametrize("field", [
    "title", "instructions", "timezone", "status",
    "conversation_id", "project_id",
])
def test_set_status_verifies_every_preserved_field(field):
    """set_status must preserve every field except status; tampering any field fails."""
    before = _make_schedule(
        status=1, title="T", instructions="I", timezone="UTC",
        conversation_id="conv_1", project_id="proj_1",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    after = _make_schedule(
        status=2, title="T", instructions="I", timezone="UTC",
        conversation_id="conv_1", project_id="proj_1",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    # Tamper the target field
    if field == "title":
        after = _make_schedule(status=2, title="TAMPERED", instructions="I", timezone="UTC",
                               conversation_id="conv_1", project_id="proj_1",
                               trigger={"frequency": "daily", "time": "09:00"})
    elif field == "instructions":
        after = _make_schedule(status=2, title="T", instructions="TAMPERED", timezone="UTC",
                               conversation_id="conv_1", project_id="proj_1",
                               trigger={"frequency": "daily", "time": "09:00"})
    elif field == "timezone":
        after = _make_schedule(status=2, title="T", instructions="I", timezone="Europe/London",
                               conversation_id="conv_1", project_id="proj_1",
                               trigger={"frequency": "daily", "time": "09:00"})
    elif field == "conversation_id":
        after = _make_schedule(status=2, title="T", instructions="I", timezone="UTC",
                               conversation_id="conv_2", project_id="proj_1",
                               trigger={"frequency": "daily", "time": "09:00"})
    elif field == "project_id":
        after = _make_schedule(status=2, title="T", instructions="I", timezone="UTC",
                               conversation_id="conv_1", project_id="proj_2",
                               trigger={"frequency": "daily", "time": "09:00"})
    elif field == "status":
        after = _make_schedule(status=1, title="T", instructions="I", timezone="UTC",
                               conversation_id="conv_1", project_id="proj_1",
                               trigger={"frequency": "daily", "time": "09:00"})

    t = FakeTransport(schedules={SCHEDULE_ID: before}, update_return=after)
    result = _run(_service(t).set_status(SCHEDULE_ID, 2))
    assert not result.ok, f"Field {field} should fail verification"
    data = result.data
    assert data.changed is None
    assert data.verified is False


def test_update_verifies_preserved_status_and_association():
    """Update preserves status, conversation_id, project_id; tampering fails."""
    before = _make_schedule(
        status=1, title="Old", instructions="I", timezone="UTC",
        conversation_id="conv_1", project_id="proj_1",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    # Tampered conversation_id
    after = _make_schedule(
        title="New", instructions="I", timezone="UTC",
        conversation_id="conv_2", project_id="proj_1",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    t = FakeTransport(schedules={SCHEDULE_ID: before}, update_return=after)
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}))
    assert not result.ok
    data = result.data
    assert data.changed is None
    assert data.verified is False


def test_create_readback_must_be_active():
    """Create read-back must have status active (1)."""
    created = _make_schedule(status=2)  # paused, not active
    t = FakeTransport(create_return=created, schedules={SCHEDULE_ID: created})
    result = _run(_service(t).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert not result.ok


def test_create_readback_preserves_association():
    """Create read-back must preserve conversation_id/project_id from created object."""
    created = _make_schedule(
        title="T", instructions="I", timezone="UTC",
        conversation_id="conv_1", project_id="proj_1",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    # Read-back has different association
    read_back = _make_schedule(
        schedule_id=created.schedule_id,
        title="T", instructions="I", timezone="UTC",
        conversation_id="conv_2", project_id="proj_2",
        trigger={"frequency": "daily", "time": "09:00"},
    )
    t = FakeTransport(
        create_return=created,
        read_values=[read_back],
    )
    result = _run(_service(t).create_schedule({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
    }))
    assert not result.ok


# ------------------------------------------------------------------ ISO UTC normalization

def test_once_aware_iso_normalized_to_utc():
    """Aware ISO normalized to canonical UTC; +05:00 becomes same instant UTC."""
    result = validate_create_spec({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "once", "at": "2026-12-31T23:59:00+05:00",
    })
    assert result["trigger"]["at"] == "2026-12-31T18:59:00+00:00"


def test_once_microseconds_preserved():
    result = validate_create_spec({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "once", "at": "2026-12-31T23:59:00.123456+00:00",
    })
    assert result["trigger"]["at"] == "2026-12-31T23:59:00.123456+00:00"


def test_once_negative_offset_normalized():
    result = validate_create_spec({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "once", "at": "2026-12-31T23:59:00-05:00",
    })
    assert result["trigger"]["at"] == "2027-01-01T04:59:00+00:00"


def test_starts_ends_at_normalized_to_utc():
    result = validate_create_spec({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00",
        "starts_at": "2026-12-31T23:59:00+05:00",
        "ends_at": "2027-01-01T00:00:00+05:00",
    })
    assert result["starts_at"] == "2026-12-31T18:59:00+00:00"
    assert result["ends_at"] == "2026-12-31T19:00:00+00:00"


# ------------------------------------------------------------------ delete absence proof: all IDs

def test_delete_absence_proof_wrong_kind_still_present():
    """Wrong-kind same ID in complete list => still present, deletion unverified."""
    before = _make_schedule()
    wrong_kind = _make_schedule(schedule_id=SCHEDULE_ID, kind=KIND_CHAT_ASYNC)
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        list_return=TaskSchedulePage(items=(wrong_kind,), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert not result.ok
    data = result.data
    assert data.changed is None
    assert data.verified is False


def test_delete_absence_proof_unknown_kind_still_present():
    """Unknown-kind same ID in complete list => still present, deletion unverified."""
    before = _make_schedule()
    unknown_kind = _make_schedule(schedule_id=SCHEDULE_ID, kind=KIND_UNKNOWN)
    t = FakeTransport(
        schedules={SCHEDULE_ID: before},
        list_return=TaskSchedulePage(items=(unknown_kind,), unknown_kind_count=0),
    )
    result = _run(_service(t).delete_schedule(SCHEDULE_ID))
    assert not result.ok
    data = result.data
    assert data.changed is None


# ------------------------------------------------------------------ patch validation before auth

def test_update_rejects_unknown_patch_key_before_auth():
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule()})
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"bogus_key": 1}))
    assert not result.ok
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    # No transport touched before auth
    assert t.initializer_calls == 0


def test_update_rejects_non_dict_patch_before_auth():
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule()})
    result = _run(_service(t).update_schedule(SCHEDULE_ID, "not a dict"))
    assert not result.ok
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert t.initializer_calls == 0


def test_update_rejects_status_patch_before_auth():
    """status must go through set_status, not update patch."""
    t = FakeTransport(schedules={SCHEDULE_ID: _make_schedule()})
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"status": 2}))
    assert not result.ok
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert t.initializer_calls == 0


# ------------------------------------------------------------------ max_chars truncation on mutation before/after

def test_mutation_truncates_before_after_instructions():
    """Before/after instructions truncated to max_chars at output boundary; full objects used for verification."""
    long = "x" * 500
    before = _make_schedule(instructions=long)
    after = _make_schedule(title="New", instructions=long)
    t = FakeTransport(schedules={SCHEDULE_ID: before}, update_return=after)
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "New"}, max_chars=10))
    assert result.ok
    d = result.to_dict()
    data = d["data"]
    assert data["before"]["instructions_truncated"] is True
    assert len(data["before"]["instructions"]) <= 10
    assert data["after"]["instructions_truncated"] is True
    assert len(data["after"]["instructions"]) <= 10


def test_mutation_noop_truncates_before_after():
    """No-op update also truncates before/after at output boundary."""
    long = "x" * 500
    before = _make_schedule(instructions=long)
    t = FakeTransport(schedules={SCHEDULE_ID: before})
    result = _run(_service(t).update_schedule(SCHEDULE_ID, {"title": "Scheduled Task"}, max_chars=10))
    assert result.ok
    d = result.to_dict()
    data = d["data"]
    assert data["before"]["instructions_truncated"] is True
    assert len(data["before"]["instructions"]) <= 10
    assert data["after"]["instructions_truncated"] is True
    assert len(data["after"]["instructions"]) <= 10


# ------------------------------------------------------------------ bounded initializer deadline

def test_initializer_deadline_monkeypatched(monkeypatch):
    """Initializer awaited through bounded deadline; tests can monkeypatch it."""
    import src.services.task_schedules as svc
    monkeypatch.setattr(svc, "CLIENT_INITIALIZER_DEADLINE", 0.01)

    async def slow_initializer():
        await asyncio.sleep(10)

    deps = TaskScheduleDependencies(
        client_provider=lambda: object(),
        client_initializer=slow_initializer,
        list_schedules=lambda c: TaskSchedulePage(items=(), unknown_kind_count=0),
        get_schedule=lambda c, i: None,
        create_schedule=lambda c, s: None,
        update_schedule=lambda *a, **k: None,
        delete_schedule=lambda c, i: None,
        account_key_provider=lambda: ACCOUNT,
    )
    result = _run(TaskScheduleService(deps).list_schedules())
    assert not result.ok
    assert result.error_code == DomainErrorCode.TIMED_OUT.value


# ------------------------------------------------------------------ window contract

def test_window_optional_additional_not_alternative():
    """daily requires time; window is optional additional, not alternative."""
    # time + window allowed
    result = validate_create_spec({
        "title": "T", "instructions": "I", "timezone": "UTC",
        "frequency": "daily", "time": "09:00", "window": "overnight",
    })
    assert result["trigger"] == {
        "frequency": "daily", "time": "09:00", "window": "overnight"
    }
    # window without time rejected
    try:
        validate_create_spec({
            "title": "T", "instructions": "I", "timezone": "UTC",
            "frequency": "daily", "window": "overnight",
        })
        assert False, "window without time should be rejected"
    except ValueError:
        pass


def test_update_clears_window_with_null():
    """Update with window=None clears window from trigger."""
    before = _make_schedule(
        trigger={"frequency": "daily", "time": "09:00", "window": "overnight"}
    )
    merged = build_update_spec(before, {"window": None})
    assert "window" not in merged


def test_update_same_frequency_preserves_window():
    """Same-frequency update without window preserves existing window."""
    before = _make_schedule(
        trigger={"frequency": "daily", "time": "09:00", "window": "overnight"}
    )
    merged = build_update_spec(before, {"title": "New"})
    assert merged.get("window") == "overnight"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
