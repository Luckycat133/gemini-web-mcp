"""Read and mutate exact Gemini Web Task (Remy goal) metadata.

The service validates every request before touching transport or client state,
guards account identity across each await, fresh-reads task metadata before
and after the deletion, sends the deletion at most once, and claims deletion
success only when an independent fresh read-back proves the task is absent.
Transport and identity callbacks are injected; Root connects them to observed
contracts (such as DeleteRemyGoal). No ordinary chat deletion is performed
and no mutation is ever replayed automatically.

List and get are fresh reads and never read conversation text. Task status
meaning is partial: verified statuses 3/4/5 mean completed/failed/input-required;
other values remain unknown and the upstream integer is preserved. Activity
and schedules use separate, evidence-backed contracts.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from ..domain import (
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    is_valid_remote_chat_id,
    result_from_exception,
)
from ..domain.web_tasks import (
    DEFAULT_TASK_GET_CHARS,
    DEFAULT_TASK_LIST_LIMIT,
    MAX_TASK_DESCRIPTION_PREVIEW,
    MAX_TASK_GET_CHARS,
    MAX_TASK_TITLE_LENGTH,
    STATUS_ACCOUNT_MISMATCH,
    STATUS_ACCOUNT_REQUIRED,
    STATUS_ABSENT,
    STATUS_INVALID_REQUEST,
    STATUS_NO_OP,
    STATUS_READ_ERROR,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    STATUS_WRONG_IDENTITY,
    TaskMetadataAction,
    TaskMetadataField,
    WebTask,
    WebTaskDeleteData,
    WebTaskGetData,
    WebTaskMetadataUpdateData,
    WebTaskPage,
    WebTaskSummary,
    truncate_text,
)
from ..infrastructure.account_identity import current_account_key
from .deadlines import await_before_deadline

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebTaskDependencies:
    """Injectable transport and account callbacks; Root connects observed contracts."""

    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    list_tasks: Callable[[Any, int, str | None], Awaitable[WebTaskPage[WebTask]]]
    get_task: Callable[[Any, str], Awaitable[WebTask | None]]
    delete_task: Callable[[Any, str], Awaitable[Any]]
    account_key_provider: Callable[[], str | None] = current_account_key
    readback_delays: tuple[float, ...] = ()
    update_metadata: Callable[[Any, str, TaskMetadataField, str | bool], Awaitable[None]] | None = None


class WebTaskService:
    """Fresh Task metadata reads and independently verified mutations."""

    def __init__(self, dependencies: WebTaskDependencies) -> None:
        delays = dependencies.readback_delays
        if (not isinstance(delays, tuple) or len(delays) > 5
                or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 5
                       for value in delays)):
            raise ValueError("Invalid Task deletion read-back delays.")
        self._dependencies = dependencies

    # ------------------------------------------------------------------ list

    async def list_tasks(
        self, limit: int = DEFAULT_TASK_LIST_LIMIT, page_token: str | None = None
    ) -> DomainResult[WebTaskPage[WebTaskSummary]]:
        rejection = self._reject_invalid_list(limit, page_token)
        if rejection is not None:
            return rejection

        original_owner = self._account_identity()
        client: Any = None
        try:
            # Automatic browser authentication may bind the account during
            # initialization. Existing project clients are synchronous objects,
            # so the provider is called synchronously and never awaited.
            await self._dependencies.client_initializer()
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="web_tasks.initialize_client"
            )

        owner = self._account_identity()
        if owner is None:
            return self._list_failure(
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before tasks can be listed.",
                STATUS_ACCOUNT_REQUIRED,
            )
        if original_owner is not None and original_owner != owner:
            return self._list_failure(
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no tasks were read.",
                STATUS_ACCOUNT_MISMATCH,
            )

        try:
            page = await self._dependencies.list_tasks(client, limit, page_token)
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="web_tasks.list_tasks"
            )
        # Fence the account before trusting the upstream page.
        if self._account_identity() != owner:
            return self._list_failure(
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading tasks; the page is not trusted.",
                STATUS_ACCOUNT_MISMATCH,
            )

        summaries: list[WebTaskSummary] = []
        for task in page.items:
            if not is_valid_remote_chat_id(task.chat_id):
                return DomainResult.failure(
                    DomainErrorCode.VERIFICATION_FAILED,
                    "A listed task has a malformed chat identity; the page is not trusted.",
                    verification_status=STATUS_READ_ERROR,
                    details={"service": "web_tasks", "operation": "list_tasks"},
                )
            summaries.append(self._to_summary(task))

        warnings = (DomainWarning(code="unaddressable_task_goals",
                                  message="Some Task entries lack a supported conversation ID."),) if page.unaddressable_count else ()
        return DomainResult.success(
            WebTaskPage(items=tuple(summaries), next_page_token=page.next_page_token,
                        unaddressable_count=page.unaddressable_count),
            operation_state=OperationState.PARTIAL if warnings else OperationState.COMPLETED,
            verification_status="partial_task_page" if warnings else STATUS_VERIFIED,
            warnings=warnings,
            details={"service": "web_tasks", "operation": "list_tasks"},
        )

    # ------------------------------------------------------------------ get

    async def get_task(
        self, chat_id: str, max_chars: int = DEFAULT_TASK_GET_CHARS
    ) -> DomainResult[WebTaskGetData]:
        rejection = self._reject_invalid_get(chat_id, max_chars)
        if rejection is not None:
            return rejection

        original_owner = self._account_identity()
        client: Any = None
        try:
            await self._dependencies.client_initializer()
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="web_tasks.initialize_client"
            )

        owner = self._account_identity()
        if owner is None:
            return self._get_failure(
                chat_id,
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before a task can be read.",
                STATUS_ACCOUNT_REQUIRED,
            )
        if original_owner is not None and original_owner != owner:
            return self._get_failure(
                chat_id,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no task was read.",
                STATUS_ACCOUNT_MISMATCH,
            )

        try:
            task = await self._dependencies.get_task(client, chat_id)
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="web_tasks.get_task"
            )
        # Fence the account before trusting the fetched task: a switch during
        # a None-returning read is an account mismatch, not trusted absence.
        if self._account_identity() != owner:
            return self._get_failure(
                chat_id,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading the task; it is not trusted.",
                STATUS_ACCOUNT_MISMATCH,
            )

        if task is None:
            # Authoritative absence: the task is not visible to this account.
            return DomainResult.failure(
                DomainErrorCode.SESSION_NOT_FOUND,
                f"The task {chat_id} is not visible to this account.",
                verification_status=STATUS_ABSENT,
                details={
                    "service": "web_tasks",
                    "operation": "get_task",
                    "chat_id": chat_id,
                },
            )

        if task.chat_id != chat_id:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The fetched task identifies a different chat; no task data is returned.",
                verification_status=STATUS_WRONG_IDENTITY,
                details={
                    "service": "web_tasks",
                    "operation": "get_task",
                    "chat_id": chat_id,
                },
            )

        description, description_truncated = self._truncate_description(
            task.description, max_chars
        )
        return DomainResult.success(
            WebTaskGetData(
                chat_id=task.chat_id,
                description=description,
                description_truncated=description_truncated,
                upstream_status=task.upstream_status,
                is_pinned=task.is_pinned,
                created_at=task.created_at,
                updated_at=task.updated_at,
            ),
            verification_status=STATUS_VERIFIED,
            details={"service": "web_tasks", "operation": "get_task", "chat_id": chat_id},
        )

    async def update_task(self, action: TaskMetadataAction, chat_id: str,
                          title: str | None = None) -> DomainResult[WebTaskMetadataUpdateData]:
        data = WebTaskMetadataUpdateData(action, chat_id)
        details = {"service": "web_tasks", "operation": "update_task", "action": action}

        def failure(code: DomainErrorCode, message: str, status: str) -> DomainResult[WebTaskMetadataUpdateData]:
            return DomainResult.failure(code, message, data=data, verification_status=status, details=details)

        if action not in ("rename", "pin", "unpin") or not is_valid_remote_chat_id(chat_id):
            return failure(DomainErrorCode.INVALID_ARGUMENT, "Provide a supported action and exact Task chat_id.",
                           STATUS_INVALID_REQUEST)
        field: TaskMetadataField = "pinned"
        value: str | bool = action == "pin"
        if action == "rename":
            normalized = title.strip() if isinstance(title, str) else ""
            if not normalized or len(normalized) > MAX_TASK_TITLE_LENGTH:
                return failure(DomainErrorCode.INVALID_ARGUMENT,
                               f"Provide a nonblank title of at most {MAX_TASK_TITLE_LENGTH} characters.",
                               STATUS_INVALID_REQUEST)
            field, value = "description", normalized
        elif title is not None:
            return failure(DomainErrorCode.INVALID_ARGUMENT, "title is only valid for rename.", STATUS_INVALID_REQUEST)

        update = self._dependencies.update_metadata
        if update is None:
            return failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Task metadata updates are unavailable.",
                           STATUS_UNVERIFIED)

        def matches(task: WebTask) -> bool:
            observed = task.description if field == "description" else task.is_pinned
            return type(observed) is type(value) and observed == value

        def success(changed: bool) -> DomainResult[WebTaskMetadataUpdateData]:
            return DomainResult.success(replace(
                data, changed=changed, verified=True,
                description=value if isinstance(value, str) else None,
                is_pinned=value if isinstance(value, bool) else None,
            ), verification_status=STATUS_VERIFIED if changed else STATUS_NO_OP, details=details)

        def account_mismatch() -> DomainResult[WebTaskMetadataUpdateData]:
            return failure(DomainErrorCode.AUTH_REQUIRED, "The Task account changed. The result is unverified.",
                           STATUS_ACCOUNT_MISMATCH)

        original_owner = self._account_identity()
        try:
            await self._dependencies.client_initializer()
            client = self._dependencies.client_provider()
        except Exception as exc:
            return replace(result_from_exception(exc, logger=_logger, operation="web_tasks.initialize_client"),
                           data=data)
        owner = self._account_identity()
        if not owner:
            return failure(DomainErrorCode.AUTH_REQUIRED, "An account is required for Task metadata updates.",
                           STATUS_ACCOUNT_REQUIRED)
        if original_owner is not None and original_owner != owner:
            return account_mismatch()

        try:
            before = await await_before_deadline(self._dependencies.get_task(client, chat_id), timeout=30)
        except Exception:
            if self._account_identity() != owner:
                return account_mismatch()
            return failure(DomainErrorCode.VERIFICATION_FAILED, "Task metadata could not be read before the update.",
                           STATUS_READ_ERROR)
        if self._account_identity() != owner:
            return account_mismatch()
        if before is None:
            return failure(DomainErrorCode.SESSION_NOT_FOUND, "The Task is absent from this account.", STATUS_ABSENT)
        if before.chat_id != chat_id:
            return failure(DomainErrorCode.VERIFICATION_FAILED, "The pre-read returned a different Task.",
                           STATUS_WRONG_IDENTITY)
        if matches(before):
            return success(False)

        from ..thinking_client import mutation_scope

        try:
            with mutation_scope(owner=owner, account_key_provider=self._account_identity):
                data = replace(data, changed=None, mutation_sent=None)
                await await_before_deadline(update(client, chat_id, field, value), timeout=30)
                data = replace(data, mutation_sent=True)
        except Exception:
            if data.mutation_sent is False:
                return account_mismatch()
            # The outcome may have been applied. Only independent reads can verify it.

        for delay in (0, *self._dependencies.readback_delays):
            if delay:
                await asyncio.sleep(delay)
            if self._account_identity() != owner:
                return account_mismatch()
            try:
                observed = await await_before_deadline(self._dependencies.get_task(client, chat_id), timeout=10)
            except Exception:
                if self._account_identity() != owner:
                    return account_mismatch()
                return failure(DomainErrorCode.VERIFICATION_FAILED, "Task metadata read-back failed.", STATUS_READ_ERROR)
            if self._account_identity() != owner:
                return account_mismatch()
            if observed is None:
                return failure(DomainErrorCode.VERIFICATION_FAILED, "The Task was absent on read-back.", STATUS_ABSENT)
            if observed.chat_id != chat_id:
                return failure(DomainErrorCode.VERIFICATION_FAILED, "Read-back returned a different Task.",
                               STATUS_WRONG_IDENTITY)
            if matches(observed):
                return success(True)

        return failure(DomainErrorCode.VERIFICATION_FAILED, "Read-back did not verify the requested Task field.",
                       STATUS_UNVERIFIED)

    # ----------------------------------------------------------------- delete

    async def delete_task(self, chat_id: str) -> DomainResult[WebTaskDeleteData]:
        if not is_valid_remote_chat_id(chat_id):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "chat_id must be a Gemini Web chat identifier starting with c_.",
                verification_status=STATUS_INVALID_REQUEST,
                data=WebTaskDeleteData(chat_id=chat_id, changed=False, verified=False),
                details={"service": "web_tasks", "operation": "delete_task"},
            )

        original_owner = self._account_identity()
        client: Any = None
        try:
            await self._dependencies.client_initializer()
            client = self._dependencies.client_provider()
        except Exception:
            return self._delete_failure(
                chat_id,
                WebTaskDeleteData(chat_id=chat_id, changed=False, verified=False),
                DomainErrorCode.INTERNAL_ERROR,
                "The client could not be initialized; no deletion was sent.",
                STATUS_READ_ERROR,
            )

        owner = self._account_identity()
        if owner is None:
            return self._delete_failure(
                chat_id,
                WebTaskDeleteData(chat_id=chat_id, changed=False, verified=False),
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before a task can be deleted.",
                STATUS_ACCOUNT_REQUIRED,
            )
        if original_owner is not None and original_owner != owner:
            return self._delete_failure(
                chat_id,
                WebTaskDeleteData(chat_id=chat_id, changed=False, verified=False),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no deletion was sent.",
                STATUS_ACCOUNT_MISMATCH,
            )

        # Pre-read the exact task to delete.
        try:
            pre_task = await self._dependencies.get_task(client, chat_id)
        except Exception:
            return self._delete_failure(
                chat_id,
                WebTaskDeleteData(chat_id=chat_id, changed=False, verified=False),
                DomainErrorCode.VERIFICATION_FAILED,
                "The task could not be read before deletion; no deletion was sent.",
                STATUS_READ_ERROR,
            )
        # Fence before trusting a None pre-read: a switch during the read is a
        # mismatch, not trusted absence evidence.
        if self._account_identity() != owner:
            return self._delete_failure(
                chat_id,
                WebTaskDeleteData(chat_id=chat_id, changed=False, verified=False),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading the task; no deletion was sent.",
                STATUS_ACCOUNT_MISMATCH,
            )

        if pre_task is None:
            # Authoritative absence: verified no-op, nothing sent.
            return DomainResult.success(
                WebTaskDeleteData(
                    chat_id=chat_id,
                    changed=False,
                    mutation_sent=False,
                    verified=True,
                ),
                verification_status=STATUS_NO_OP,
                details={
                    "service": "web_tasks",
                    "operation": "delete_task",
                    "chat_id": chat_id,
                },
            )

        if pre_task.chat_id != chat_id:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The pre-read task identifies a different chat; no deletion was sent.",
                verification_status=STATUS_WRONG_IDENTITY,
                data=WebTaskDeleteData(
                    chat_id=chat_id,
                    changed=False,
                    mutation_sent=False,
                    verified=False,
                    pre_read_task=pre_task,
                ),
                details={
                    "service": "web_tasks",
                    "operation": "delete_task",
                    "chat_id": chat_id,
                },
            )

        if self._account_identity() != owner:
            return self._delete_failure(
                chat_id,
                WebTaskDeleteData(
                    chat_id=chat_id, changed=False, verified=False, pre_read_task=pre_task
                ),
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed before the deletion; the deletion was not sent.",
                STATUS_ACCOUNT_MISMATCH,
            )

        # Send the deletion at most once. A lost response is still a sent
        # deletion: read-back below decides the outcome, with no replay.
        try:
            await self._dependencies.delete_task(client, chat_id)
        except Exception:
            pass

        delete_data = WebTaskDeleteData(
            chat_id=chat_id,
            changed=None,
            mutation_sent=True,
            verified=False,
            pre_read_task=pre_task,
        )

        # Fence the account before trusting the read-back.
        if self._account_identity() != owner:
            return self._delete_failure(
                chat_id,
                delete_data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the deletion; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
            )

        # Only the read-back is polled; the mutation above is never repeated.
        post_task: WebTask | None = None
        for delay in (None, *self._dependencies.readback_delays):
            if delay is not None and self._account_identity() != owner:
                return self._delete_failure(
                    chat_id, replace(delete_data, pre_read_task=None, post_read_task=None),
                    DomainErrorCode.AUTH_REQUIRED, "The account changed after the deletion; read-back was not trusted.",
                    STATUS_ACCOUNT_MISMATCH,
                )
            try:
                if delay is not None:
                    await await_before_deadline(asyncio.sleep(delay), timeout=delay + 1)
                    if self._account_identity() != owner:
                        return self._delete_failure(
                            chat_id, replace(delete_data, pre_read_task=None, post_read_task=None),
                            DomainErrorCode.AUTH_REQUIRED, "The account changed during read-back; no further read was sent.",
                            STATUS_ACCOUNT_MISMATCH,
                        )
                post_task = await await_before_deadline(self._dependencies.get_task(client, chat_id), timeout=10)
            except Exception:
                if self._account_identity() != owner:
                    return self._delete_failure(
                        chat_id, replace(delete_data, pre_read_task=None, post_read_task=None),
                        DomainErrorCode.AUTH_REQUIRED, "The account changed during read-back; no result was trusted.",
                        STATUS_ACCOUNT_MISMATCH,
                    )
                return self._delete_failure(
                    chat_id, delete_data, DomainErrorCode.VERIFICATION_FAILED,
                    "The post-deletion read-back failed; the deletion is unverified.", STATUS_READ_ERROR,
                )
            if self._account_identity() != owner:
                return self._delete_failure(
                    chat_id, replace(delete_data, pre_read_task=None, post_read_task=None),
                    DomainErrorCode.AUTH_REQUIRED, "The account changed after the deletion; read-back was not trusted.",
                    STATUS_ACCOUNT_MISMATCH,
                )
            if post_task is None or post_task.chat_id != chat_id:
                break

        if post_task is None:
            # Absence proven by an independent fresh read-back.
            return DomainResult.success(
                replace(
                    delete_data,
                    changed=True,
                    verified=True,
                    post_read_task=None,
                ),
                verification_status=STATUS_VERIFIED,
                details={
                    "service": "web_tasks",
                    "operation": "delete_task",
                    "chat_id": chat_id,
                },
            )

        if post_task.chat_id != chat_id:
            return DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED,
                "The post-deletion read-back identifies a different chat; the deletion is unverified.",
                verification_status=STATUS_WRONG_IDENTITY,
                data=replace(delete_data, post_read_task=post_task),
                details={
                    "service": "web_tasks",
                    "operation": "delete_task",
                    "chat_id": chat_id,
                },
            )

        # The task is still present: sent but unverified, no automatic replay.
        return DomainResult.failure(
            DomainErrorCode.VERIFICATION_FAILED,
            "The post-deletion read-back still shows the task; the deletion is unverified.",
            verification_status=STATUS_UNVERIFIED,
            data=replace(delete_data, post_read_task=post_task),
            details={
                "service": "web_tasks",
                "operation": "delete_task",
                "chat_id": chat_id,
            },
        )

    # ------------------------------------------------------------- helpers

    def _account_identity(self) -> str | None:
        try:
            return self._dependencies.account_key_provider()
        except Exception:
            return None

    @staticmethod
    def _valid_limit(value: Any) -> bool:
        return type(value) is int and 1 <= value <= 100

    @staticmethod
    def _valid_page_token(value: Any) -> bool:
        return value is None or (isinstance(value, str) and len(value) <= 4096)

    @staticmethod
    def _valid_max_chars(value: Any) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= MAX_TASK_GET_CHARS
        )

    def _reject_invalid_list(
        self, limit: int, page_token: str | None
    ) -> DomainResult[WebTaskPage] | None:
        if not self._valid_limit(limit):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "limit must be an integer between 1 and 100.",
                verification_status=STATUS_INVALID_REQUEST,
                details={"service": "web_tasks", "operation": "list_tasks"},
            )
        if not self._valid_page_token(page_token):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "page_token must be a string or None.",
                verification_status=STATUS_INVALID_REQUEST,
                details={"service": "web_tasks", "operation": "list_tasks"},
            )
        return None

    def _reject_invalid_get(
        self, chat_id: str, max_chars: int
    ) -> DomainResult[WebTaskGetData] | None:
        if not is_valid_remote_chat_id(chat_id):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "chat_id must be a Gemini Web chat identifier starting with c_.",
                verification_status=STATUS_INVALID_REQUEST,
                details={"service": "web_tasks", "operation": "get_task"},
            )
        if not self._valid_max_chars(max_chars):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"max_chars must be an integer between 1 and {MAX_TASK_GET_CHARS}.",
                verification_status=STATUS_INVALID_REQUEST,
                details={"service": "web_tasks", "operation": "get_task"},
            )
        return None

    @staticmethod
    def _truncate_description(
        description: str | None, max_chars: int
    ) -> tuple[str | None, bool]:
        if description is None:
            return None, False
        return truncate_text(description, max_chars)

    @staticmethod
    def _to_summary(task: WebTask) -> WebTaskSummary:
        description = task.description or ""
        preview, truncated = truncate_text(description, MAX_TASK_DESCRIPTION_PREVIEW)
        return WebTaskSummary(
            chat_id=task.chat_id,
            description_preview=preview,
            description_truncated=truncated,
            upstream_status=task.upstream_status,
            is_pinned=task.is_pinned,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def _list_failure(
        self, code: DomainErrorCode, message: str, status: str
    ) -> DomainResult[WebTaskPage]:
        return DomainResult.failure(
            code,
            message,
            verification_status=status,
            details={"service": "web_tasks", "operation": "list_tasks"},
        )

    def _get_failure(
        self, chat_id: str, code: DomainErrorCode, message: str, status: str
    ) -> DomainResult[WebTaskGetData]:
        return DomainResult.failure(
            code,
            message,
            verification_status=status,
            details={"service": "web_tasks", "operation": "get_task", "chat_id": chat_id},
        )

    def _delete_failure(
        self,
        chat_id: str,
        data: WebTaskDeleteData,
        code: DomainErrorCode,
        message: str,
        status: str,
    ) -> DomainResult[WebTaskDeleteData]:
        if status == STATUS_ACCOUNT_MISMATCH:
            data = replace(data, pre_read_task=None, post_read_task=None)
        return DomainResult.failure(
            code,
            message,
            verification_status=status,
            data=data,
            details={"service": "web_tasks", "operation": "delete_task", "chat_id": chat_id},
        )


__all__ = ["WebTaskDependencies", "WebTaskService"]
