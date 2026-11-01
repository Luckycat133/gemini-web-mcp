"""Typed contracts for Remy Task schedule management.

Gemini Web exposes Remy Task schedules (kind 1) distinct from Chat async
schedules (kind 2). This module declares only the domain boundary: the
observed schedule, page, summary, get, and mutation outcome types. It invents
no RPC ids, payload indices, endpoint URLs, browser selectors, or SDK methods.

Schedule status meaning: 1 active/resumed, 2 paused, 3 completed; 0 unknown.
Every non-1/2/3 status is unknown and the upstream integer is preserved
verbatim. ``revision`` is an opaque transport fingerprint; no raw wire fields
are modeled.

Trigger normalization converts a create/update spec into a frequency-based
dict. Unknown keys, inapplicable keys, bool ints, and ambiguous combinations
are rejected.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal


# --------------------------------------------------------------------------- status constants

TASK_SCHEDULE_STATUS_ACTIVE = 1
TASK_SCHEDULE_STATUS_PAUSED = 2
TASK_SCHEDULE_STATUS_COMPLETED = 3

KIND_REMY_TASK = 1
KIND_CHAT_ASYNC = 2
KIND_UNKNOWN = 0

# --------------------------------------------------------------------------- service-side limits

DEFAULT_SCHEDULE_LIST_LIMIT = 20
MAX_SCHEDULE_LIST_LIMIT = 100
MAX_SCHEDULE_LIST_OFFSET = 10000
DEFAULT_SCHEDULE_GET_CHARS = 800
MAX_SCHEDULE_GET_CHARS = 4000
MAX_SCHEDULE_TITLE_LENGTH = 256
MAX_SCHEDULE_INSTRUCTIONS_LENGTH = 16000
MAX_SCHEDULE_ID_LENGTH = 256

_TRUNCATION_INDICATOR = "…"  # keeps every preview within its limit


class TaskScheduleTransportError(Exception):
    """Transport callback reported a mutation dispatch outcome.

    ``mutation_sent`` records whether the dispatch was attempted:
    ``True`` = sent, ``False`` = known not sent, ``None`` = unknown.
    Root imports this class to type-check transport exceptions.
    """

    def __init__(self, message: str, mutation_sent: bool | None = False) -> None:
        super().__init__(message)
        self.message = message
        self.mutation_sent = mutation_sent


# Leap reference year for validating Feb 29 in yearly triggers.
_LEAP_REFERENCE_YEAR = 2024

SUPPORTED_FREQUENCIES = frozenset({"daily", "weekly", "hourly", "monthly", "yearly", "once"})
SUPPORTED_WINDOWS = frozenset({"overnight", "daytime", "evening"})

_TIME_OF_DAY_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

CREATE_SPEC_KEYS = frozenset({
    "title", "instructions", "timezone", "frequency",
    "time", "weekdays", "days_of_month", "month", "day", "minute", "at",
    "starts_at", "ends_at", "window",
})

FREQUENCY_TRIGGER_KEYS = {
    "daily": frozenset({"time", "window"}),
    "weekly": frozenset({"time", "weekdays", "window"}),
    "monthly": frozenset({"time", "days_of_month", "window"}),
    "yearly": frozenset({"time", "month", "day", "window"}),
    "hourly": frozenset({"minute"}),
    "once": frozenset({"at"}),
}

COMMON_SPEC_KEYS = frozenset({"starts_at", "ends_at"})

# Top-level spec keys that are not part of the trigger.
NON_TRIGGER_SPEC_KEYS = frozenset({"title", "instructions", "timezone"})

# Verification statuses consistent with src/domain/web_tasks.py.
STATUS_VERIFIED = "verified"
STATUS_NO_OP = "no_op"
STATUS_ACCOUNT_REQUIRED = "account_required"
STATUS_ACCOUNT_MISMATCH = "account_mismatch"
STATUS_INVALID_REQUEST = "invalid_request"
STATUS_ABSENT = "absent"
STATUS_WRONG_IDENTITY = "wrong_identity"
STATUS_READ_ERROR = "read_error"
STATUS_UNVERIFIED = "unverified"

TaskScheduleState = Literal["active", "paused", "completed", "unknown"]


def _is_exact_int(value: Any) -> bool:
    """Return True for ints that are not bools (bool is a subclass of int)."""
    return type(value) is int


def _parse_time_of_day(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("time must be an HH:MM string.")
    if not _TIME_OF_DAY_RE.match(value):
        raise ValueError("time must be an HH:MM string (e.g. 09:00).")
    return value


def _parse_unique_ints(values: Any, minimum: int, maximum: int, label: str) -> list[int]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a nonempty list.")
    result: list[int] = []
    for item in values:
        if not _is_exact_int(item):
            raise ValueError(f"{label} items must be exact integers, not bools.")
        if item < minimum or item > maximum:
            raise ValueError(f"{label} items must be between {minimum} and {maximum}.")
        if item in result:
            raise ValueError(f"{label} must not contain duplicates.")
        result.append(item)
    return result


_AWARE_TIMESTAMP = re.compile(
    r"(?P<local>\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2})?)"
    r"(?:[.,](?P<fraction>\d+))?(?P<offset>Z|[+-]\d{2}(?::?\d{2})?(?::?\d{2})?)\Z"
)


def _timestamp_parts(value: Any, label: str) -> tuple[datetime, int, int]:
    """Parse an aware instant into whole UTC seconds and nanoseconds without float rounding."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 string.")
    match = _AWARE_TIMESTAMP.fullmatch(value)
    if not match:
        raise ValueError(f"{label} must be a timezone-aware ISO-8601 timestamp with a whole-second offset.")
    fraction = match.group('fraction') or ''
    if len(fraction) > 9 or (fraction and len(match.group('local').split(':')) != 3):
        raise ValueError(f"{label} must use at most nine fractional-second digits.")
    nanos = int(fraction.ljust(9, '0')) if fraction else 0
    try:
        parsed = datetime.fromisoformat(match.group('local') + match.group('offset').replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('missing timezone')
        utc = parsed.astimezone(timezone.utc)
        seconds = calendar.timegm(utc.utctimetuple())
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a valid aware ISO-8601 timestamp.") from exc
    return utc, seconds, nanos


def _parse_aware_iso(value: Any, label: str) -> str:
    utc, _seconds, nanos = _timestamp_parts(value, label)
    base = utc.replace(microsecond=0).isoformat()[:-6]
    fraction = ('.' + f'{nanos:09d}'.rstrip('0')) if nanos else ''
    return base + fraction + '+00:00'


def _validate_yearly_month_day(month: Any, day: Any) -> tuple[int, int]:
    if not _is_exact_int(month):
        raise ValueError("yearly month must be an integer between 1 and 12.")
    assert isinstance(month, int)
    if not 1 <= month <= 12:
        raise ValueError("yearly month must be an integer between 1 and 12.")
    if not _is_exact_int(day):
        raise ValueError("yearly day must be an integer between 1 and 31.")
    assert isinstance(day, int)
    if not 1 <= day <= 31:
        raise ValueError("yearly day must be an integer between 1 and 31.")
    try:
        date(_LEAP_REFERENCE_YEAR, month, day)
    except ValueError as exc:
        raise ValueError(f"yearly month/day {month}/{day} is not a valid calendar date.") from exc
    return month, day


def normalize_trigger(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a trigger spec into a frequency-based dict.

    The returned dict always contains ``frequency`` plus the logical arguments
    for that frequency. ``starts_at``/``ends_at`` are top-level schedule
    fields and are not part of the trigger. Unknown keys, inapplicable keys,
    bool ints, and ambiguous combinations raise ValueError.
    """
    frequency = spec.get("frequency")
    if not isinstance(frequency, str) or frequency not in SUPPORTED_FREQUENCIES:
        raise ValueError(
            "frequency is required and must be one of: daily, weekly, hourly, monthly, yearly, once."
        )

    allowed = FREQUENCY_TRIGGER_KEYS[frequency]

    # Reject inapplicable / ambiguous trigger keys for this frequency.
    for key in spec:
        if key in COMMON_SPEC_KEYS or key in NON_TRIGGER_SPEC_KEYS or key == "frequency":
            continue
        if key not in allowed:
            raise ValueError(
                f"Trigger key {key!r} is not applicable to frequency {frequency!r}."
            )

    trigger: dict[str, Any] = {"frequency": frequency}

    if frequency in {"daily", "weekly", "monthly", "yearly"}:
        # HH:MM is mandatory; window is an optional additional field, not an
        # alternative to time.
        time_value = spec.get("time")
        if not isinstance(time_value, str) or not _TIME_OF_DAY_RE.match(time_value):
            raise ValueError("time must be an HH:MM string (e.g. 09:00).")
        trigger["time"] = time_value

        window = spec.get("window")
        if window is not None:
            if window not in SUPPORTED_WINDOWS:
                raise ValueError("window must be one of: overnight, daytime, evening.")
            trigger["window"] = window

        if frequency == "weekly":
            trigger["weekdays"] = sorted(_parse_unique_ints(spec.get("weekdays"), 1, 7, "weekdays"))
        elif frequency == "monthly":
            trigger["days_of_month"] = sorted(_parse_unique_ints(spec.get("days_of_month"), 1, 31, "days_of_month"))
        elif frequency == "yearly":
            month, day = _validate_yearly_month_day(spec.get("month"), spec.get("day"))
            trigger["month"] = month
            trigger["day"] = day

    elif frequency == "hourly":
        minute = spec.get("minute")
        if not _is_exact_int(minute):
            raise ValueError("hourly minute must be an exact integer between 0 and 59.")
        assert isinstance(minute, int)
        if not 0 <= minute <= 59:
            raise ValueError("hourly minute must be an exact integer between 0 and 59.")
        trigger["minute"] = minute

    elif frequency == "once":
        trigger["at"] = _parse_aware_iso(spec.get("at"), "at")

    return trigger


def validate_create_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a complete create spec and return normalized fields.

    Does not touch transport or auth. Raises ValueError on invalid input.
    Returns a dict with title, instructions, timezone, trigger, starts_at,
    ends_at suitable for dispatch or comparison.
    """
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary.")

    unknown_keys = set(spec) - CREATE_SPEC_KEYS
    if unknown_keys:
        raise ValueError(f"Unknown create spec keys: {sorted(unknown_keys)}.")

    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be nonblank.")
    if len(title) > MAX_SCHEDULE_TITLE_LENGTH:
        raise ValueError(f"title must not exceed {MAX_SCHEDULE_TITLE_LENGTH} characters.")

    instructions = spec.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("instructions must be nonblank.")
    if len(instructions) > MAX_SCHEDULE_INSTRUCTIONS_LENGTH:
        raise ValueError(f"instructions must not exceed {MAX_SCHEDULE_INSTRUCTIONS_LENGTH} characters.")

    timezone_value = spec.get("timezone")
    if not isinstance(timezone_value, str) or not timezone_value:
        raise ValueError("timezone is required.")
    try:
        import zoneinfo

        zoneinfo.ZoneInfo(timezone_value)
    except Exception as exc:
        raise ValueError(f"timezone {timezone_value!r} is not a valid IANA timezone.") from exc

    trigger = normalize_trigger(spec)

    starts_at_raw: Any = spec.get("starts_at")
    ends_at_raw: Any = spec.get("ends_at")
    starts_at: str | None = None
    ends_at: str | None = None
    if starts_at_raw is not None:
        starts_at = _parse_aware_iso(starts_at_raw, "starts_at")
    if ends_at_raw is not None:
        ends_at = _parse_aware_iso(ends_at_raw, "ends_at")
    if (starts_at is not None and ends_at is not None
            and _timestamp_parts(ends_at, "ends_at")[1:] <= _timestamp_parts(starts_at, "starts_at")[1:]):
        raise ValueError("ends_at must be after starts_at.")

    return {
        "title": title.strip(),
        "instructions": instructions.strip(),
        "timezone": timezone_value,
        "trigger": trigger,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def build_update_spec(
    before: "TaskSchedule", patch: dict[str, Any]
) -> dict[str, Any]:
    """Build a full update spec from the before schedule and a partial patch.

    Same-frequency preserves omitted recurrence args from ``before.trigger``;
    changed-frequency discards old frequency-specific keys. Null values for
    ``starts_at``/``ends_at`` clear the field.
    """
    before_trigger = dict(before.trigger or {})
    before_frequency = before_trigger.get("frequency")

    new_frequency = patch.get("frequency", before_frequency)
    if not isinstance(new_frequency, str) or new_frequency not in SUPPORTED_FREQUENCIES:
        raise ValueError("frequency must be one of: daily, weekly, hourly, monthly, yearly, once.")

    if new_frequency == before_frequency:
        trigger_args = {k: v for k, v in before_trigger.items() if k != "frequency"}
    else:
        trigger_args = {}

    for key in ("time", "weekdays", "days_of_month", "month", "day", "minute", "at", "window"):
        if key in patch:
            value = patch[key]
            if key == "window" and value is None:
                # Explicitly clearing the window: omit from normalized trigger.
                trigger_args.pop("window", None)
            else:
                trigger_args[key] = value

    starts_at = patch.get("starts_at", before.starts_at)
    ends_at = patch.get("ends_at", before.ends_at)

    return {
        "title": patch.get("title", before.title),
        "instructions": patch.get("instructions", before.instructions),
        "timezone": patch.get("timezone", before.timezone),
        "frequency": new_frequency,
        **trigger_args,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """Return ``text`` truncated to at most ``limit`` characters.

    When truncated, the truncation indicator is appended and the result never
    exceeds ``limit`` characters. The flag reports whether truncation
    happened.
    """
    if len(text) <= limit:
        return text, False
    keep = max(0, limit - len(_TRUNCATION_INDICATOR))
    return text[:keep] + _TRUNCATION_INDICATOR, True


# --------------------------------------------------------------------------- domain types

@dataclass(frozen=True)
class TaskSchedule:
    """Freshly observed Remy Task schedule; full instructions held transiently."""

    schedule_id: str
    title: str
    instructions: str
    timezone: str
    status: int
    kind: int | None
    trigger: dict[str, Any]
    conversation_id: str | None = None
    project_id: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    editable: bool = True
    revision: str | None = None
    instructions_truncated: bool = False
    state: TaskScheduleState = field(init=False)

    def __post_init__(self) -> None:
        if type(self.status) is int:
            if self.status == TASK_SCHEDULE_STATUS_ACTIVE:
                state: TaskScheduleState = "active"
            elif self.status == TASK_SCHEDULE_STATUS_PAUSED:
                state = "paused"
            elif self.status == TASK_SCHEDULE_STATUS_COMPLETED:
                state = "completed"
            else:
                state = "unknown"
        else:
            state = "unknown"
        object.__setattr__(self, "state", state)


@dataclass(frozen=True)
class TaskSchedulePage:
    """One page of schedules; items type depends on the caller (full or summary)."""

    items: tuple[Any, ...]
    active_limit_reached: bool | None = None
    unknown_kind_count: int = 0


@dataclass(frozen=True)
class TaskScheduleSummary:
    """One list page item with instructions preview bounded by max_chars."""

    schedule_id: str
    title: str
    instructions_preview: str
    instructions_truncated: bool
    timezone: str
    status: int
    kind: int | None
    state: TaskScheduleState
    trigger: dict[str, Any]
    conversation_id: str | None = None
    project_id: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None


@dataclass(frozen=True)
class TaskScheduleGetData:
    """Freshly observed schedule for one ID, with instructions bounded by max_chars."""

    schedule_id: str
    title: str
    instructions: str
    instructions_truncated: bool
    timezone: str
    status: int
    kind: int | None
    state: TaskScheduleState
    trigger: dict[str, Any]
    conversation_id: str | None = None
    project_id: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None


@dataclass(frozen=True)
class TaskScheduleMutationData:
    """Outcome of one schedule mutation request with read-back evidence.

    ``changed`` is evidence-based: False before a mutation is sent, None once
    a mutation was sent but its effect is not independently proven, and True
    only when a fresh read-back proves the requested change. ``accepted``
    records whether the upstream acknowledged the dispatch (ack alone is not
    success). ``mutation_sent`` records whether the dispatch was attempted.
    """

    action: str
    schedule_id: str
    changed: bool | None = False
    accepted: bool | None = None
    mutation_sent: bool | None = False
    verified: bool = False
    before: TaskSchedule | None = None
    after: TaskSchedule | None = None


__all__ = [
    "DEFAULT_SCHEDULE_GET_CHARS",
    "DEFAULT_SCHEDULE_LIST_LIMIT",
    "KIND_CHAT_ASYNC",
    "KIND_REMY_TASK",
    "KIND_UNKNOWN",
    "MAX_SCHEDULE_GET_CHARS",
    "MAX_SCHEDULE_ID_LENGTH",
    "MAX_SCHEDULE_INSTRUCTIONS_LENGTH",
    "MAX_SCHEDULE_LIST_LIMIT",
    "MAX_SCHEDULE_LIST_OFFSET",
    "MAX_SCHEDULE_TITLE_LENGTH",
    "STATUS_ABSENT",
    "STATUS_ACCOUNT_MISMATCH",
    "STATUS_ACCOUNT_REQUIRED",
    "STATUS_INVALID_REQUEST",
    "STATUS_NO_OP",
    "STATUS_READ_ERROR",
    "STATUS_UNVERIFIED",
    "STATUS_VERIFIED",
    "STATUS_WRONG_IDENTITY",
    "TASK_SCHEDULE_STATUS_ACTIVE",
    "TASK_SCHEDULE_STATUS_COMPLETED",
    "TASK_SCHEDULE_STATUS_PAUSED",
    "TaskSchedule",
    "TaskScheduleGetData",
    "TaskScheduleMutationData",
    "TaskSchedulePage",
    "TaskScheduleState",
    "TaskScheduleSummary",
    "TaskScheduleTransportError",
    "build_update_spec",
    "normalize_trigger",
    "truncate_text",
    "validate_create_spec",
]
