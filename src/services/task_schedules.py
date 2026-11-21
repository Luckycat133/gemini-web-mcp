"""Shared service for Remy Task schedule management: list, get, create, update, status, delete.

The service validates every request before touching transport or client state,
guards account identity across each await, fresh-reads schedules before and
after mutations, sends each mutation at most once, and claims success only
when an independent fresh read-back proves the requested change. Transport and
identity callbacks are injected; Root connects them to observed contracts.
No mutation is ever replayed automatically.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from ..domain import (
    DomainErrorCode,
    DomainResult,
    result_from_exception,
)
from ..domain.task_schedules import (
    DEFAULT_SCHEDULE_GET_CHARS,
    DEFAULT_SCHEDULE_LIST_LIMIT,
    KIND_REMY_TASK,
    MAX_SCHEDULE_GET_CHARS,
    MAX_SCHEDULE_ID_LENGTH,
    MAX_SCHEDULE_LIST_LIMIT,
    MAX_SCHEDULE_LIST_OFFSET,
    STATUS_ACCOUNT_MISMATCH,
    STATUS_ACCOUNT_REQUIRED,
    STATUS_ABSENT,
    STATUS_INVALID_REQUEST,
    STATUS_NO_OP,
    STATUS_READ_ERROR,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    STATUS_WRONG_IDENTITY,
    TASK_SCHEDULE_STATUS_ACTIVE,
    TaskSchedule,
    TaskScheduleGetData,
    TaskScheduleMutationData,
    TaskSchedulePage,
    TaskScheduleSummary,
    TaskScheduleTransportError,
    build_update_spec,
    truncate_text,
    validate_create_spec,
)
from ..infrastructure.account_identity import current_account_key
from .deadlines import await_before_deadline

_logger = logging.getLogger(__name__)

# Bounded deadline for the client initializer await; tests may monkeypatch
# this to a small value to keep timeout-path tests fast.
CLIENT_INITIALIZER_DEADLINE = 30.0


@dataclass(frozen=True)
class TaskScheduleDependencies:
    """Injectable transport and account callbacks; Root connects observed contracts.

    ``client_provider`` is a synchronous object accessed after
    ``client_initializer`` has been awaited; it is never awaited itself.
    ``update_schedule`` accepts an ``expected_before`` keyword argument.
    """

    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    list_schedules: Callable[[Any], Awaitable[TaskSchedulePage]]
    get_schedule: Callable[[Any, str], Awaitable[TaskSchedule | None]]
    create_schedule: Callable[[Any, dict[str, Any]], Awaitable[TaskSchedule | None]]
    update_schedule: Callable[..., Awaitable[TaskSchedule | None]]
    delete_schedule: Callable[[Any, str], Awaitable[Any]]
    account_key_provider: Callable[[], str | None] = current_account_key


class TaskScheduleService:
    """Fresh Remy Task schedule reads and read-back-verified mutations."""

    def __init__(self, dependencies: TaskScheduleDependencies) -> None:
        self._dependencies = dependencies

    # ------------------------------------------------------------------ list

    async def list_schedules(
        self,
        limit: int = DEFAULT_SCHEDULE_LIST_LIMIT,
        offset: int = 0,
        max_chars: int = DEFAULT_SCHEDULE_GET_CHARS,
    ) -> DomainResult[TaskSchedulePage]:
        if not self._valid_limit(limit):
            return self._list_failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "limit must be an integer between 1 and 100.",
                STATUS_INVALID_REQUEST,
            )
        if not self._valid_offset(offset):
            return self._list_failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "offset must be an integer between 0 and 10000.",
                STATUS_INVALID_REQUEST,
            )
        if not self._valid_max_chars(max_chars):
            return self._list_failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"max_chars must be an integer between 1 and {MAX_SCHEDULE_GET_CHARS}.",
                STATUS_INVALID_REQUEST,
            )

        original_owner = self._account_identity()
        client: Any = None
        try:
            await await_before_deadline(
                self._dependencies.client_initializer(), timeout=CLIENT_INITIALIZER_DEADLINE
            )
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="task_schedules.initialize_client"
            )

        owner = self._account_identity()
        if owner is None:
            return self._list_failure(
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before schedules can be listed.",
                STATUS_ACCOUNT_REQUIRED,
            )
        if original_owner is not None and original_owner != owner:
            return self._list_failure(
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no schedules were read.",
                STATUS_ACCOUNT_MISMATCH,
            )

        try:
            page = await self._dependencies.list_schedules(client)
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="task_schedules.list_schedules"
            )
        # Fence the account before trusting the upstream page.
        if self._account_identity() != owner:
            return self._list_failure(
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading schedules; the page is not trusted.",
                STATUS_ACCOUNT_MISMATCH,
            )

        # Keep only kind 1 (Remy Task). Count all non-kind-1 schedules as
        # other/unknown (kind 0 unknown, kind 2 Chat async, etc.).
        task_items: list[TaskSchedule] = []
        unknown_kind_count = 0
        for item in page.items:
            if type(item.kind) is int and item.kind == KIND_REMY_TASK:
                task_items.append(item)
            else:
                unknown_kind_count += 1

        # Local pagination only; the remote result is complete.
        paginated = task_items[offset : offset + limit]

        summaries: list[TaskScheduleSummary] = []
        for schedule in paginated:
            preview, truncated = truncate_text(schedule.instructions, max_chars)
            summaries.append(
                TaskScheduleSummary(
                    schedule_id=schedule.schedule_id,
                    title=schedule.title,
                    instructions_preview=preview,
                    instructions_truncated=truncated,
                    timezone=schedule.timezone,
                    status=schedule.status,
                    kind=schedule.kind,
                    state=schedule.state,
                    trigger=schedule.trigger,
                    conversation_id=schedule.conversation_id,
                    project_id=schedule.project_id,
                    starts_at=schedule.starts_at,
                    ends_at=schedule.ends_at,
                )
            )

        return DomainResult.success(
            TaskSchedulePage(
                items=tuple(summaries),
                active_limit_reached=page.active_limit_reached,
                unknown_kind_count=unknown_kind_count,
            ),
            verification_status=STATUS_VERIFIED,
            details={"service": "task_schedules", "operation": "list_schedules"},
        )

    # ------------------------------------------------------------------ get

    async def get_schedule(
        self, schedule_id: str, max_chars: int = DEFAULT_SCHEDULE_GET_CHARS
    ) -> DomainResult[TaskScheduleGetData]:
        if not self._valid_schedule_id(schedule_id):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "schedule_id must be a nonempty string up to 256 characters.",
                verification_status=STATUS_INVALID_REQUEST,
                details={"service": "task_schedules", "operation": "get_schedule", "schedule_id": schedule_id},
            )
        if not self._valid_max_chars(max_chars):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"max_chars must be an integer between 1 and {MAX_SCHEDULE_GET_CHARS}.",
                verification_status=STATUS_INVALID_REQUEST,
                details={"service": "task_schedules", "operation": "get_schedule", "schedule_id": schedule_id},
            )

        original_owner = self._account_identity()
        client: Any = None
        try:
            await await_before_deadline(
                self._dependencies.client_initializer(), timeout=CLIENT_INITIALIZER_DEADLINE
            )
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="task_schedules.initialize_client"
            )

        owner = self._account_identity()
        if owner is None:
            return DomainResult.failure(
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before a schedule can be read.",
                verification_status=STATUS_ACCOUNT_REQUIRED,
                details={"service": "task_schedules", "operation": "get_schedule", "schedule_id": schedule_id},
            )
        if original_owner is not None and original_owner != owner:
            return DomainResult.failure(
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no schedule was read.",
                verification_status=STATUS_ACCOUNT_MISMATCH,
                details={"service": "task_schedules", "operation": "get_schedule", "schedule_id": schedule_id},
            )

        try:
            schedule = await self._dependencies.get_schedule(client, schedule_id)
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="task_schedules.get_schedule"
            )
        # Fence the account before trusting the fetched schedule: a switch
        # during a None-returning read is a mismatch, not trusted absence.
        if self._account_identity() != owner:
            return DomainResult.failure(
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading the schedule; it is not trusted.",
                verification_status=STATUS_ACCOUNT_MISMATCH,
                details={"service": "task_schedules", "operation": "get_schedule", "schedule_id": schedule_id},
            )

        if schedule is None:
            # Authoritative absence: the schedule is not visible to this account.
            return DomainResult.failure(
                DomainErrorCode.SESSION_NOT_FOUND,
                f"The schedule {schedule_id} is not visible to this account.",
                verification_status=STATUS_ABSENT,
                details={
                    "service": "task_schedules",
                    "operation": "get_schedule",
                    "schedule_id": schedule_id,
                },
            )

        if schedule.schedule_id != schedule_id:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The fetched schedule identifies a different schedule; no schedule data is returned.",
                verification_status=STATUS_WRONG_IDENTITY,
                details={
                    "service": "task_schedules",
                    "operation": "get_schedule",
                    "schedule_id": schedule_id,
                },
            )

        # Only kind 1 (Remy Task) is managed here; kind 0/2 are refused.
        if type(schedule.kind) is not int or schedule.kind != KIND_REMY_TASK:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not a Remy Task schedule (kind={schedule.kind!r}); access refused.",
                verification_status=STATUS_WRONG_IDENTITY,
                details={
                    "service": "task_schedules",
                    "operation": "get_schedule",
                    "schedule_id": schedule_id,
                },
            )

        instructions, instructions_truncated = truncate_text(schedule.instructions, max_chars)
        return DomainResult.success(
            TaskScheduleGetData(
                schedule_id=schedule.schedule_id,
                title=schedule.title,
                instructions=instructions,
                instructions_truncated=instructions_truncated,
                timezone=schedule.timezone,
                status=schedule.status,
                kind=schedule.kind,
                state=schedule.state,
                trigger=schedule.trigger,
                conversation_id=schedule.conversation_id,
                project_id=schedule.project_id,
                starts_at=schedule.starts_at,
                ends_at=schedule.ends_at,
            ),
            verification_status=STATUS_VERIFIED,
            details={"service": "task_schedules", "operation": "get_schedule", "schedule_id": schedule_id},
        )

    # ------------------------------------------------------------------ create

    async def create_schedule(
        self, spec: dict[str, Any], max_chars: int = DEFAULT_SCHEDULE_GET_CHARS
    ) -> DomainResult[TaskScheduleMutationData]:
        if not self._valid_max_chars(max_chars):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"max_chars must be an integer between 1 and {MAX_SCHEDULE_GET_CHARS}.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="create", schedule_id="", changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "create_schedule"},
            )

        # Validate before auth: no transport, no client, no account touch.
        try:
            normalized = validate_create_spec(spec)
        except ValueError as exc:
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                str(exc),
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="create", schedule_id="", changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "create_schedule"},
            )

        data = TaskScheduleMutationData(
            action="create",
            schedule_id="",
            changed=False,
            accepted=None,
        )

        original_owner = self._account_identity()
        client: Any = None
        try:
            await await_before_deadline(
                self._dependencies.client_initializer(), timeout=CLIENT_INITIALIZER_DEADLINE
            )
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="task_schedules.initialize_client"
            )

        owner = self._account_identity()
        if owner is None:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before a schedule can be created.",
                STATUS_ACCOUNT_REQUIRED,
                max_chars=max_chars,
            )
        if original_owner is not None and original_owner != owner:
            return self._mutation_failure(
                self._scrub_data(data),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no schedule was created.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Dispatch the create at most once.
        data, created, possibly_sent = await self._dispatch_mutation(
            data, lambda: self._dependencies.create_schedule(client, normalized)
        )

        # Known not sent: no read-back, changed=False.
        if not possibly_sent:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The create was not dispatched; no schedule was created.",
                verification_status=STATUS_UNVERIFIED,
                data=self._output_boundary(data, max_chars),
                details={"service": "task_schedules", "operation": "create_schedule"},
            )

        # Fence before trusting the create response.
        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(data),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the create; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # The create response must supply a nonempty ID; accepted text alone
        # is not proof. A lost response / no ID cannot guess the latest title.
        if created is None or not created.schedule_id:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The create response supplied no schedule ID; the creation is unverified.",
                verification_status=STATUS_UNVERIFIED,
                data=self._output_boundary(
                    replace(data, verified=False), max_chars
                ),
                details={"service": "task_schedules", "operation": "create_schedule"},
            )

        schedule_id = created.schedule_id

        # Independent read-back for the exact returned ID.
        try:
            read_back = await self._dependencies.get_schedule(client, schedule_id)
        except Exception:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                f"The post-create read-back for {schedule_id} failed; the creation is unverified.",
                verification_status=STATUS_READ_ERROR,
                data=self._output_boundary(
                    replace(data, schedule_id=schedule_id, verified=False), max_chars
                ),
                details={"service": "task_schedules", "operation": "create_schedule"},
            )
        # Fence again before interpreting the read-back.
        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, schedule_id=schedule_id)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the create; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        verified, status = self._verify_create(normalized, created, read_back)
        if not verified:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The post-create read-back did not match the requested schedule.",
                verification_status=status,
                data=self._output_boundary(
                    replace(
                        data,
                        schedule_id=schedule_id,
                        verified=False,
                        after=read_back,
                    ),
                    max_chars,
                ),
                details={"service": "task_schedules", "operation": "create_schedule"},
            )

        final_data = replace(
            data,
            schedule_id=schedule_id,
            changed=True,
            verified=True,
            after=read_back,
        )
        # An explicit read-back proving the change establishes acceptance.
        if final_data.accepted is None:
            final_data = replace(final_data, accepted=True)

        return DomainResult.success(
            self._output_boundary(final_data, max_chars),
            verification_status=STATUS_VERIFIED,
            details={"service": "task_schedules", "operation": "create_schedule"},
        )

    # ------------------------------------------------------------------ update

    async def update_schedule(
        self, schedule_id: str, patch: dict[str, Any], max_chars: int = DEFAULT_SCHEDULE_GET_CHARS
    ) -> DomainResult[TaskScheduleMutationData]:
        if not self._valid_schedule_id(schedule_id):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "schedule_id must be a nonempty string up to 256 characters.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="update", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "update_schedule"},
            )
        if not self._valid_max_chars(max_chars):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"max_chars must be an integer between 1 and {MAX_SCHEDULE_GET_CHARS}.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="update", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "update_schedule"},
            )

        # Reject unknown patch keys and non-dict patch before any auth.
        if not isinstance(patch, dict):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "patch must be a dictionary.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="update", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "update_schedule"},
            )
        unknown_keys = set(patch) - _PATCH_KEYS
        if unknown_keys:
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"Unknown patch keys: {sorted(unknown_keys)}.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="update", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "update_schedule"},
            )

        data = TaskScheduleMutationData(
            action="update",
            schedule_id=schedule_id,
            changed=False,
            accepted=None,
        )

        original_owner = self._account_identity()
        client: Any = None
        try:
            await await_before_deadline(
                self._dependencies.client_initializer(), timeout=CLIENT_INITIALIZER_DEADLINE
            )
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="task_schedules.initialize_client"
            )

        owner = self._account_identity()
        if owner is None:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before a schedule can be updated.",
                STATUS_ACCOUNT_REQUIRED,
                max_chars=max_chars,
            )
        if original_owner is not None and original_owner != owner:
            return self._mutation_failure(
                self._scrub_data(data),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no schedule was updated.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Fresh exact-ID kind-1 get before any mutation.
        try:
            before = await self._dependencies.get_schedule(client, schedule_id)
        except Exception:
            return self._mutation_failure(
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} could not be read before update; no update was sent.",
                STATUS_READ_ERROR,
                max_chars=max_chars,
            )
        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(data),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading the schedule; no update was sent.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        if before is None:
            return self._mutation_failure(
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not visible to this account; no update was sent.",
                STATUS_ABSENT,
                max_chars=max_chars,
            )
        if before.schedule_id != schedule_id:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                "The pre-read schedule identifies a different schedule; no update was sent.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # Only kind 1 (Remy Task) is managed here.
        if type(before.kind) is not int or before.kind != KIND_REMY_TASK:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not a Remy Task schedule (kind={before.kind!r}); update refused.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # Completed schedules only allow delete.
        if before.status == 3:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is completed; only delete is allowed.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # editable=False or unknown triggers reject update; delete still allowed.
        if not before.editable:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not editable; update refused.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # Build the full merged spec and validate before dispatch.
        try:
            merged = build_update_spec(before, patch)
            normalized = validate_create_spec(merged)
        except ValueError as exc:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.INVALID_ARGUMENT,
                str(exc),
                STATUS_INVALID_REQUEST,
                max_chars=max_chars,
            )

        # Same verified status / no change => no-op zero mutation.
        if self._no_update_change(before, normalized):
            return DomainResult.success(
                self._output_boundary(
                    replace(data, verified=True, before=before, after=before), max_chars
                ),
                verification_status=STATUS_NO_OP,
                details={"service": "task_schedules", "operation": "update_schedule", "schedule_id": schedule_id},
            )

        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed before the update; the update was not sent.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Dispatch the update at most once.
        data, updated, possibly_sent = await self._dispatch_mutation(
            data,
            lambda: self._dependencies.update_schedule(
                client, schedule_id, patch, expected_before=before
            ),
        )

        # Known not sent: no read-back, changed=False.
        if not possibly_sent:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The update was not dispatched; no change was made.",
                verification_status=STATUS_UNVERIFIED,
                data=self._output_boundary(
                    replace(data, before=before), max_chars
                ),
                details={"service": "task_schedules", "operation": "update_schedule"},
            )

        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the update; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Independent read-back for the exact ID.
        try:
            read_back = await self._dependencies.get_schedule(client, schedule_id)
        except Exception:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                f"The post-update read-back for {schedule_id} failed; the change is unverified.",
                verification_status=STATUS_READ_ERROR,
                data=self._output_boundary(
                    replace(data, verified=False, before=before), max_chars
                ),
                details={"service": "task_schedules", "operation": "update_schedule"},
            )
        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the update; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        verified, status = self._verify_update(normalized, before, read_back)
        if not verified:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The post-update read-back did not match the requested update.",
                verification_status=status,
                data=self._output_boundary(
                    replace(
                        data,
                        verified=False,
                        after=read_back,
                        before=before,
                    ),
                    max_chars,
                ),
                details={"service": "task_schedules", "operation": "update_schedule"},
            )

        final_data = replace(
            data,
            changed=True,
            verified=True,
            after=read_back,
            before=before,
        )
        # An explicit read-back proving the change establishes acceptance.
        if final_data.accepted is None:
            final_data = replace(final_data, accepted=True)

        return DomainResult.success(
            self._output_boundary(final_data, max_chars),
            verification_status=STATUS_VERIFIED,
            details={"service": "task_schedules", "operation": "update_schedule"},
        )

    # ------------------------------------------------------------------ set_status

    async def set_status(
        self, schedule_id: str, status: int, max_chars: int = DEFAULT_SCHEDULE_GET_CHARS
    ) -> DomainResult[TaskScheduleMutationData]:
        if not self._valid_schedule_id(schedule_id):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "schedule_id must be a nonempty string up to 256 characters.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="set_status", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "set_status"},
            )
        if not _is_exact_status(status):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "set_status only accepts status 1 (active) or 2 (paused).",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="set_status", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "set_status"},
            )
        if not self._valid_max_chars(max_chars):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"max_chars must be an integer between 1 and {MAX_SCHEDULE_GET_CHARS}.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="set_status", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "set_status"},
            )

        data = TaskScheduleMutationData(
            action="set_status",
            schedule_id=schedule_id,
            changed=False,
            accepted=None,
        )

        original_owner = self._account_identity()
        client: Any = None
        try:
            await await_before_deadline(
                self._dependencies.client_initializer(), timeout=CLIENT_INITIALIZER_DEADLINE
            )
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="task_schedules.initialize_client"
            )

        owner = self._account_identity()
        if owner is None:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before a schedule status can be changed.",
                STATUS_ACCOUNT_REQUIRED,
                max_chars=max_chars,
            )
        if original_owner is not None and original_owner != owner:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no status was changed.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Fresh exact-ID kind-1 get before any mutation.
        try:
            before = await self._dependencies.get_schedule(client, schedule_id)
        except Exception:
            return self._mutation_failure(
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} could not be read before status change; no status change was sent.",
                STATUS_READ_ERROR,
                max_chars=max_chars,
            )
        if self._account_identity() != owner:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading the schedule; no status change was sent.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        if before is None:
            return self._mutation_failure(
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not visible to this account; no status change was sent.",
                STATUS_ABSENT,
                max_chars=max_chars,
            )
        if before.schedule_id != schedule_id:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                "The pre-read schedule identifies a different schedule; no status change was sent.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # Only kind 1 (Remy Task) is managed here.
        if type(before.kind) is not int or before.kind != KIND_REMY_TASK:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not a Remy Task schedule (kind={before.kind!r}); status change refused.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # Completed schedules only allow delete; no status change.
        if before.status == 3:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is completed; only delete is allowed.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # editable=False or unknown triggers reject status change; delete still allowed.
        if not before.editable:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not editable; status change refused.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        # Same verified status => no-op zero mutation.
        if before.status == status:
            return DomainResult.success(
                self._output_boundary(
                    replace(data, verified=True, before=before, after=before), max_chars
                ),
                verification_status=STATUS_NO_OP,
                details={"service": "task_schedules", "operation": "set_status", "schedule_id": schedule_id},
            )

        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed before the status change; it was not sent.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Dispatch the status change at most once via update_schedule.
        data, updated, possibly_sent = await self._dispatch_mutation(
            data,
            lambda: self._dependencies.update_schedule(
                client, schedule_id, {"status": status}, expected_before=before
            ),
        )

        # Known not sent: no read-back, changed=False.
        if not possibly_sent:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The status change was not dispatched; no change was made.",
                verification_status=STATUS_UNVERIFIED,
                data=self._output_boundary(
                    replace(data, before=before), max_chars
                ),
                details={"service": "task_schedules", "operation": "set_status"},
            )

        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the status change; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Independent read-back for the exact ID.
        try:
            read_back = await self._dependencies.get_schedule(client, schedule_id)
        except Exception:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                f"The post-status-change read-back for {schedule_id} failed; the change is unverified.",
                verification_status=STATUS_READ_ERROR,
                data=self._output_boundary(
                    replace(data, verified=False, before=before), max_chars
                ),
                details={"service": "task_schedules", "operation": "set_status"},
            )
        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the status change; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        verified, status_code = self._verify_status(status, before, read_back)
        if not verified:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The post-status-change read-back did not match the requested status.",
                verification_status=status_code,
                data=self._output_boundary(
                    replace(
                        data,
                        verified=False,
                        after=read_back,
                        before=before,
                    ),
                    max_chars,
                ),
                details={"service": "task_schedules", "operation": "set_status"},
            )

        final_data = replace(
            data,
            changed=True,
            verified=True,
            after=read_back,
            before=before,
        )
        # An explicit read-back proving the change establishes acceptance.
        if final_data.accepted is None:
            final_data = replace(final_data, accepted=True)

        return DomainResult.success(
            self._output_boundary(final_data, max_chars),
            verification_status=STATUS_VERIFIED,
            details={"service": "task_schedules", "operation": "set_status"},
        )

    # ------------------------------------------------------------------ delete

    async def delete_schedule(
        self, schedule_id: str, max_chars: int = DEFAULT_SCHEDULE_GET_CHARS
    ) -> DomainResult[TaskScheduleMutationData]:
        if not self._valid_schedule_id(schedule_id):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "schedule_id must be a nonempty string up to 256 characters.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="delete", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "delete_schedule"},
            )
        if not self._valid_max_chars(max_chars):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"max_chars must be an integer between 1 and {MAX_SCHEDULE_GET_CHARS}.",
                verification_status=STATUS_INVALID_REQUEST,
                data=TaskScheduleMutationData(action="delete", schedule_id=schedule_id, changed=False, accepted=None),
                details={"service": "task_schedules", "operation": "delete_schedule"},
            )

        data = TaskScheduleMutationData(
            action="delete",
            schedule_id=schedule_id,
            changed=False,
            accepted=None,
        )

        original_owner = self._account_identity()
        client: Any = None
        try:
            await await_before_deadline(
                self._dependencies.client_initializer(), timeout=CLIENT_INITIALIZER_DEADLINE
            )
            client = self._dependencies.client_provider()
        except Exception:
            return self._mutation_failure(
                data,
                DomainErrorCode.INTERNAL_ERROR,
                "The client could not be initialized; no deletion was sent.",
                STATUS_READ_ERROR,
                max_chars=max_chars,
            )

        owner = self._account_identity()
        if owner is None:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before a schedule can be deleted.",
                STATUS_ACCOUNT_REQUIRED,
                max_chars=max_chars,
            )
        if original_owner is not None and original_owner != owner:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no deletion was sent.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Fresh exact-ID kind-1 get before deletion (delete still allowed for
        # completed, editable=False, and unknown-trigger schedules).
        try:
            before = await self._dependencies.get_schedule(client, schedule_id)
        except Exception:
            return self._mutation_failure(
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} could not be read before deletion; no deletion was sent.",
                STATUS_READ_ERROR,
                max_chars=max_chars,
            )
        if self._account_identity() != owner:
            return self._mutation_failure(
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading the schedule; no deletion was sent.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        if before is None:
            # Authoritative absence: verified no-op, nothing sent.
            return DomainResult.success(
                replace(data, changed=False, verified=True),
                verification_status=STATUS_NO_OP,
                details={"service": "task_schedules", "operation": "delete_schedule", "schedule_id": schedule_id},
            )
        if before.schedule_id != schedule_id:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                "The pre-read schedule identifies a different schedule; no deletion was sent.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )
        if type(before.kind) is not int or before.kind != KIND_REMY_TASK:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The schedule {schedule_id} is not a Remy Task schedule (kind={before.kind!r}); delete refused.",
                STATUS_WRONG_IDENTITY,
                max_chars=max_chars,
            )

        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed before the deletion; the deletion was not sent.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Send the deletion at most once. A lost response is still a sent
        # deletion; read-back below decides the outcome, with no replay.
        data, _result, possibly_sent = await self._dispatch_mutation(
            data, lambda: self._dependencies.delete_schedule(client, schedule_id)
        )

        # Known not sent: no read-back, changed=False.
        if not possibly_sent:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The deletion was not dispatched; no change was made.",
                verification_status=STATUS_UNVERIFIED,
                data=self._output_boundary(
                    replace(data, before=before), max_chars
                ),
                details={"service": "task_schedules", "operation": "delete_schedule"},
            )

        # Fence before trusting the read-back.
        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the deletion; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        # Delete verification requires a fresh complete list and exact-ID
        # absence: a missing Get alone is insufficient. Check ALL items with
        # the same ID, regardless of kind (a wrong-kind same-ID is still
        # present).
        try:
            page = await self._dependencies.list_schedules(client)
        except Exception:
            return self._mutation_failure(
                replace(data, before=before),
                DomainErrorCode.VERIFICATION_FAILED,
                f"The post-deletion list read-back for {schedule_id} failed; the deletion is unverified.",
                STATUS_READ_ERROR,
                max_chars=max_chars,
            )
        if self._account_identity() != owner:
            return self._mutation_failure(
                self._scrub_data(replace(data, before=before)),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the deletion; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
                max_chars=max_chars,
            )

        still_present = any(
            item.schedule_id == schedule_id
            for item in page.items
        )

        if not still_present:
            # Absence proven by an independent fresh complete list.
            return DomainResult.success(
                self._output_boundary(
                    replace(
                        data,
                        changed=True,
                        accepted=True,
                        verified=True,
                    ),
                    max_chars,
                ),
                verification_status=STATUS_VERIFIED,
                details={"service": "task_schedules", "operation": "delete_schedule", "schedule_id": schedule_id},
            )

        # The schedule is still present: sent but unverified, no automatic replay.
        return DomainResult.failure(
            DomainErrorCode.VERIFICATION_FAILED,
            f"The post-deletion list still shows schedule {schedule_id}; the deletion is unverified.",
            verification_status=STATUS_UNVERIFIED,
            data=self._output_boundary(
                replace(data, verified=False, before=before), max_chars
            ),
            details={"service": "task_schedules", "operation": "delete_schedule", "schedule_id": schedule_id},
        )

    # ------------------------------------------------------------- helpers

    def _account_identity(self) -> str | None:
        try:
            return self._dependencies.account_key_provider()
        except Exception:
            return None

    @staticmethod
    def _valid_schedule_id(value: Any) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= MAX_SCHEDULE_ID_LENGTH

    @staticmethod
    def _valid_limit(value: Any) -> bool:
        return type(value) is int and 1 <= value <= MAX_SCHEDULE_LIST_LIMIT

    @staticmethod
    def _valid_offset(value: Any) -> bool:
        return type(value) is int and 0 <= value <= MAX_SCHEDULE_LIST_OFFSET

    @staticmethod
    def _valid_max_chars(value: Any) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= MAX_SCHEDULE_GET_CHARS
        )

    @staticmethod
    def _scrub_data(data: TaskScheduleMutationData) -> TaskScheduleMutationData:
        """Scrub before/after (old account instructions) on account mismatch."""
        return replace(data, before=None, after=None)

    @staticmethod
    def _truncate_for_output(
        schedule: TaskSchedule | None, max_chars: int
    ) -> TaskSchedule | None:
        """Truncate instructions on a TaskSchedule for output (never before verification)."""
        if schedule is None:
            return None
        instructions, truncated = truncate_text(schedule.instructions, max_chars)
        return replace(schedule, instructions=instructions, instructions_truncated=truncated)

    def _output_boundary(
        self, data: TaskScheduleMutationData, max_chars: int
    ) -> TaskScheduleMutationData:
        """Single output boundary: truncate before/after instructions for return."""
        return replace(
            data,
            before=self._truncate_for_output(data.before, max_chars),
            after=self._truncate_for_output(data.after, max_chars),
        )

    async def _dispatch_mutation(
        self,
        data: TaskScheduleMutationData,
        call: Callable[[], Awaitable[Any]],
    ) -> tuple[TaskScheduleMutationData, Any, bool]:
        """Dispatch one mutation callback.

        Returns ``(data, result, possibly_sent)``:

        * Normal return: ``accepted=True``, ``mutation_sent=True``, ``changed=None``.
        * ``TaskScheduleTransportError``: ``mutation_sent`` from the typed
          exception, ``accepted=None``.
        * Any other exception: ``mutation_sent=None`` (unknown),
          ``accepted=None``.

        ``possibly_sent`` is ``False`` only when the mutation is known not to
        have been sent (``mutation_sent is False``); in that case no read-back
        should be attempted and ``changed`` is definitively ``False``.
        """
        try:
            result = await call()
            return replace(data, changed=None, accepted=True, mutation_sent=True), result, True
        except TaskScheduleTransportError as exc:
            sent = exc.mutation_sent
            return (
                replace(
                    data,
                    changed=False if sent is False else None,
                    accepted=None,
                    mutation_sent=sent,
                ),
                None,
                sent is not False,
            )
        except Exception:
            return replace(data, changed=None, accepted=None, mutation_sent=None), None, True

    @staticmethod
    def _no_update_change(before: TaskSchedule, normalized: dict[str, Any]) -> bool:
        """Return True if the merged spec matches the before schedule exactly."""
        if normalized["title"] != before.title:
            return False
        if normalized["instructions"] != before.instructions:
            return False
        if normalized["timezone"] != before.timezone:
            return False
        if normalized["starts_at"] != before.starts_at:
            return False
        if normalized["ends_at"] != before.ends_at:
            return False
        # Compare triggers: same frequency and same logical args.
        before_trigger = dict(before.trigger or {})
        if normalized["trigger"] != before_trigger:
            return False
        return True

    @staticmethod
    def _verify_create(
        normalized: dict[str, Any], created: TaskSchedule, read_back: TaskSchedule | None
    ) -> tuple[bool, str]:
        if read_back is None:
            return False, STATUS_ABSENT
        if read_back.schedule_id != created.schedule_id:
            return False, STATUS_WRONG_IDENTITY
        if type(read_back.kind) is not int or read_back.kind != KIND_REMY_TASK:
            return False, STATUS_WRONG_IDENTITY
        # Create status must be active.
        if read_back.status != TASK_SCHEDULE_STATUS_ACTIVE:
            return False, STATUS_READ_ERROR
        if read_back.title != normalized["title"]:
            return False, STATUS_READ_ERROR
        if read_back.instructions != normalized["instructions"]:
            return False, STATUS_READ_ERROR
        if read_back.timezone != normalized["timezone"]:
            return False, STATUS_READ_ERROR
        if read_back.trigger != normalized["trigger"]:
            return False, STATUS_READ_ERROR
        if read_back.starts_at != normalized["starts_at"]:
            return False, STATUS_READ_ERROR
        if read_back.ends_at != normalized["ends_at"]:
            return False, STATUS_READ_ERROR
        # Preserved association to the acknowledged (created) object.
        if read_back.conversation_id != created.conversation_id:
            return False, STATUS_READ_ERROR
        if read_back.project_id != created.project_id:
            return False, STATUS_READ_ERROR
        return True, STATUS_VERIFIED

    @staticmethod
    def _verify_update(
        normalized: dict[str, Any], before: TaskSchedule, read_back: TaskSchedule | None
    ) -> tuple[bool, str]:
        if read_back is None:
            return False, STATUS_ABSENT
        if read_back.schedule_id != before.schedule_id:
            return False, STATUS_WRONG_IDENTITY
        if type(read_back.kind) is not int or read_back.kind != KIND_REMY_TASK:
            return False, STATUS_WRONG_IDENTITY
        # Status is preserved by update (status changes go through set_status).
        if read_back.status != before.status:
            return False, STATUS_READ_ERROR
        if read_back.title != normalized["title"]:
            return False, STATUS_READ_ERROR
        if read_back.instructions != normalized["instructions"]:
            return False, STATUS_READ_ERROR
        if read_back.timezone != normalized["timezone"]:
            return False, STATUS_READ_ERROR
        if read_back.trigger != normalized["trigger"]:
            return False, STATUS_READ_ERROR
        if read_back.starts_at != normalized["starts_at"]:
            return False, STATUS_READ_ERROR
        if read_back.ends_at != normalized["ends_at"]:
            return False, STATUS_READ_ERROR
        # Preserved conversation/project IDs must match the pre-read.
        if read_back.conversation_id != before.conversation_id:
            return False, STATUS_READ_ERROR
        if read_back.project_id != before.project_id:
            return False, STATUS_READ_ERROR
        return True, STATUS_VERIFIED

    @staticmethod
    def _verify_status(
        status: int, before: TaskSchedule, read_back: TaskSchedule | None
    ) -> tuple[bool, str]:
        if read_back is None:
            return False, STATUS_ABSENT
        if read_back.schedule_id != before.schedule_id:
            return False, STATUS_WRONG_IDENTITY
        if type(read_back.kind) is not int or read_back.kind != KIND_REMY_TASK:
            return False, STATUS_WRONG_IDENTITY
        if read_back.status != status:
            return False, STATUS_READ_ERROR
        # Preserve every other field incl IDs/kind/CID/project.
        if read_back.title != before.title:
            return False, STATUS_READ_ERROR
        if read_back.instructions != before.instructions:
            return False, STATUS_READ_ERROR
        if read_back.timezone != before.timezone:
            return False, STATUS_READ_ERROR
        if read_back.trigger != before.trigger:
            return False, STATUS_READ_ERROR
        if read_back.starts_at != before.starts_at:
            return False, STATUS_READ_ERROR
        if read_back.ends_at != before.ends_at:
            return False, STATUS_READ_ERROR
        if read_back.conversation_id != before.conversation_id:
            return False, STATUS_READ_ERROR
        if read_back.project_id != before.project_id:
            return False, STATUS_READ_ERROR
        return True, STATUS_VERIFIED

    def _mutation_failure(
        self,
        data: TaskScheduleMutationData,
        code: DomainErrorCode,
        message: str,
        status: str,
        max_chars: int = DEFAULT_SCHEDULE_GET_CHARS,
    ) -> DomainResult[TaskScheduleMutationData]:
        return DomainResult.failure(
            code,
            message,
            verification_status=status,
            data=self._output_boundary(data, max_chars),
            details={"service": "task_schedules"},
        )

    def _list_failure(
        self, code: DomainErrorCode, message: str, status: str
    ) -> DomainResult[TaskSchedulePage]:
        return DomainResult.failure(
            code,
            message,
            verification_status=status,
            details={"service": "task_schedules", "operation": "list_schedules"},
        )


def _is_exact_status(value: Any) -> bool:
    return type(value) is int and value in (1, 2)


# Patch keys accepted by update_schedule (everything in a create spec except
# read-only/system fields). "status" is intentionally excluded: status changes
# go through set_status only.
_PATCH_KEYS = frozenset({
    "title", "instructions", "timezone", "frequency",
    "time", "weekdays", "days_of_month", "month", "day", "minute", "at",
    "starts_at", "ends_at", "window",
})


__all__ = ["TaskScheduleDependencies", "TaskScheduleService"]
