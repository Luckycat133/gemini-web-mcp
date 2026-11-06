"""Deterministic offline tests for WebTaskService: list, get, delete.

Every transport and identity callback is a fake. No network, browser, account,
cookie, or upstream RPC is involved.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain import is_valid_remote_chat_id
from src.domain.web_tasks import (
    DEFAULT_TASK_GET_CHARS,
    DEFAULT_TASK_LIST_LIMIT,
    MAX_TASK_DESCRIPTION_PREVIEW,
    MAX_TASK_GET_CHARS,
    STATUS_ACCOUNT_MISMATCH,
    STATUS_ACCOUNT_REQUIRED,
    STATUS_ABSENT,
    STATUS_INVALID_REQUEST,
    STATUS_NO_OP,
    STATUS_READ_ERROR,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    STATUS_WRONG_IDENTITY,
    TASK_STATUS_COMPLETED,
    WebTask,
    WebTaskPage,
    truncate_text,
)
from src.services.web_tasks import WebTaskDependencies, WebTaskService

CHAT_ID = "c_123abc"
WRONG_CHAT_ID = "c_999zzz"
LONG_DESCRIPTION = "x" * 300


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _task(
    chat_id: str = CHAT_ID,
    description: str | None = "Finish the review",
    upstream_status: int | None = TASK_STATUS_COMPLETED,
    is_pinned: bool | None = False,
    created_at: str | int | float | None = "2024-01-01T00:00:00Z",
    updated_at: str | int | float | None = 1704067200,
) -> WebTask:
    return WebTask(
        chat_id=chat_id,
        description=description,
        upstream_status=upstream_status,
        is_pinned=is_pinned,
        created_at=created_at,
        updated_at=updated_at,
    )


class FakeTransport:
    def __init__(
        self,
        account: str | None = "acct",
        account_sequence: list[str | None] | None = None,
        page: WebTaskPage | None = None,
        pages: list[WebTaskPage] | None = None,
        tasks: dict[str, WebTask] | None = None,
        get_values: list[WebTask | None] | None = None,
        delete_side_effect: BaseException | None = None,
        get_side_effect: BaseException | None = None,
        list_side_effect: BaseException | None = None,
        initializer_side_effect: BaseException | None = None,
        raise_get_on_call: int | None = None,
        raise_delete_on_call: int | None = None,
    ) -> None:
        self.account = account
        self._account_iterator = iter(account_sequence) if account_sequence is not None else None
        self._page = page
        self._pages = list(pages or [])
        self._tasks = dict(tasks or {})
        self._get_values = list(get_values or [])
        self.delete_side_effect = delete_side_effect
        self.get_side_effect = get_side_effect
        self.list_side_effect = list_side_effect
        self.initializer_side_effect = initializer_side_effect
        self._raise_get_on_call = raise_get_on_call
        self._raise_delete_on_call = raise_delete_on_call
        self.client = object()
        self.provider_calls = 0
        self.initializer_calls = 0
        self.account_key_calls = 0
        self._get_count = 0
        self._delete_count = 0
        self.list_calls: list[tuple[Any, int, str | None]] = []
        self.get_calls: list[tuple[Any, str]] = []
        self.delete_calls: list[tuple[Any, str]] = []

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

    async def list_tasks(self, client: Any, limit: int, page_token: str | None) -> WebTaskPage:
        self.list_calls.append((client, limit, page_token))
        if self.list_side_effect is not None:
            raise self.list_side_effect
        if self._pages:
            return self._pages.pop(0)
        if self._page is not None:
            return self._page
        return WebTaskPage(items=(), next_page_token=None)

    async def get_task(self, client: Any, chat_id: str) -> WebTask | None:
        self.get_calls.append((client, chat_id))
        self._get_count += 1
        if self._get_count == self._raise_get_on_call:
            raise RuntimeError("get failed")
        if self.get_side_effect is not None:
            raise self.get_side_effect
        if self._get_values:
            return self._get_values.pop(0)
        return self._tasks.get(chat_id)

    async def delete_task(self, client: Any, chat_id: str) -> Any:
        self.delete_calls.append((client, chat_id))
        self._delete_count += 1
        if self._delete_count == self._raise_delete_on_call:
            raise RuntimeError("delete failed")
        if self.delete_side_effect is not None:
            raise self.delete_side_effect
        return None


def _service(transport: FakeTransport, *, update_metadata=None, readback_delays=()) -> WebTaskService:
    return WebTaskService(
        WebTaskDependencies(
            client_provider=transport.client_provider,
            client_initializer=transport.client_initializer,
            list_tasks=transport.list_tasks,
            get_task=transport.get_task,
            delete_task=transport.delete_task,
            account_key_provider=transport.account_key,
            update_metadata=update_metadata,
            readback_delays=readback_delays,
        )
    )


@pytest.mark.parametrize(("action", "title", "before", "after", "field", "value"), [
    ("rename", " New title ", _task(), _task(description="New title"), "description", "New title"),
    ("pin", None, _task(), _task(is_pinned=True), "pinned", True),
    ("unpin", None, _task(is_pinned=True), _task(), "pinned", False),
])
def test_task_metadata_update_verifies_the_one_requested_field(action, title, before, after, field, value):
    transport = FakeTransport(get_values=[before, after])
    write = AsyncMock()

    result = _run(_service(transport, update_metadata=write).update_task(action, CHAT_ID, title))

    assert result.ok and result.meta.verification_status == STATUS_VERIFIED
    assert result.data.changed is True and result.data.mutation_sent is True and result.data.verified
    assert result.data.description == (value if field == "description" else None)
    assert result.data.is_pinned is (value if field == "pinned" else None)
    assert transport.get_calls == [(transport.client, CHAT_ID)] * 2
    write.assert_awaited_once_with(transport.client, CHAT_ID, field, value)
    assert transport.delete_calls == []


@pytest.mark.parametrize(("action", "title", "before"), [
    ("rename", " Same title ", _task(description="Same title")),
    ("pin", None, _task(is_pinned=True)),
    ("unpin", None, _task(is_pinned=False)),
])
def test_task_metadata_update_noop_requires_a_matching_pre_read(action, title, before):
    transport = FakeTransport(get_values=[before])
    write = AsyncMock()

    result = _run(_service(transport, update_metadata=write).update_task(action, CHAT_ID, title))

    assert result.ok and result.meta.verification_status == STATUS_NO_OP
    assert result.data.changed is False and result.data.mutation_sent is False and result.data.verified
    assert len(transport.get_calls) == 1
    write.assert_not_awaited()


@pytest.mark.parametrize(("action", "chat_id", "title"), [
    ("stop", CHAT_ID, None), ("pin", "wrong-id", None), ("pin", CHAT_ID, "unused"),
    ("rename", CHAT_ID, None), ("rename", CHAT_ID, " "), ("rename", CHAT_ID, "x" * 201),
])
def test_task_metadata_update_validates_before_reading_or_writing(action, chat_id, title):
    transport = FakeTransport()
    write = AsyncMock()

    result = _run(_service(transport, update_metadata=write).update_task(action, chat_id, title))

    assert not result.ok and result.meta.verification_status == STATUS_INVALID_REQUEST
    assert result.data.changed is False and result.data.mutation_sent is False
    assert not transport.get_calls and not transport.initializer_calls
    write.assert_not_awaited()


@pytest.mark.parametrize(("before", "expected"), [(None, STATUS_ABSENT), (_task(WRONG_CHAT_ID), STATUS_WRONG_IDENTITY)])
def test_task_metadata_update_does_not_write_after_missing_or_wrong_pre_read(before, expected):
    transport = FakeTransport(get_values=[before])
    write = AsyncMock()

    result = _run(_service(transport, update_metadata=write).update_task("pin", CHAT_ID))

    assert not result.ok and result.meta.verification_status == expected
    assert result.data.changed is False and not result.data.verified
    write.assert_not_awaited()


@pytest.mark.parametrize(("post", "read_failure", "expected"), [
    (None, None, STATUS_ABSENT), (_task(WRONG_CHAT_ID, is_pinned=True), None, STATUS_WRONG_IDENTITY),
    (_task(), None, STATUS_UNVERIFIED), (_task(), 2, STATUS_READ_ERROR),
])
def test_task_metadata_update_never_verifies_an_echo_or_failed_read_back(post, read_failure, expected):
    transport = FakeTransport(get_values=[_task(), post], raise_get_on_call=read_failure)
    write = AsyncMock(return_value=_task(is_pinned=True))

    result = _run(_service(transport, update_metadata=write).update_task("pin", CHAT_ID))

    assert not result.ok and result.meta.verification_status == expected
    assert result.data.changed is None and result.data.mutation_sent is True and not result.data.verified
    assert result.data.description is None and result.data.is_pinned is None
    write.assert_awaited_once()


@pytest.mark.parametrize("changed", [True, False])
def test_task_metadata_update_reconciles_a_lost_ack_without_replaying(changed):
    transport = FakeTransport(get_values=[_task(), _task(), _task(is_pinned=changed)])
    write = AsyncMock(side_effect=TimeoutError)

    result = _run(_service(transport, update_metadata=write, readback_delays=(0,)).update_task("pin", CHAT_ID))

    assert result.ok is changed and result.data.verified is changed
    assert result.data.changed is (True if changed else None) and result.data.mutation_sent is None
    assert len(transport.get_calls) == 3
    write.assert_awaited_once()


def test_task_metadata_update_stops_read_back_after_account_switch_during_write():
    transport = FakeTransport(get_values=[_task()])

    async def switch(*_args):
        transport.account = "other"

    write = AsyncMock(side_effect=switch)
    result = _run(_service(transport, update_metadata=write).update_task("pin", CHAT_ID))

    assert not result.ok and result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert result.data.changed is None and not result.data.verified
    assert len(transport.get_calls) == 1
    write.assert_awaited_once()


@pytest.mark.parametrize("switch_on", [1, 2])
def test_task_metadata_update_rejects_reads_from_a_changed_account(switch_on):
    transport = FakeTransport(get_values=[_task(), _task(is_pinned=True)])
    get_task = transport.get_task

    async def switch(client, chat_id):
        task = await get_task(client, chat_id)
        if len(transport.get_calls) == switch_on:
            transport.account = "other"
        return task

    transport.get_task = switch
    write = AsyncMock()
    result = _run(_service(transport, update_metadata=write).update_task("pin", CHAT_ID))

    assert not result.ok and result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert result.data.changed is (False if switch_on == 1 else None)
    assert result.data.is_pinned is None and not result.data.verified
    assert write.await_count == switch_on - 1


# --------------------------------------------------------------- domain

def test_domain_state_is_completed_only_for_status_three():
    assert _task(upstream_status=3).state == "completed"
    # Broad unknown coverage uses only truly unknown statuses: 4 and 5 are now
    # verified states (failed, input_required) and belong in focused tests.
    for status in (None, 0, 1, 2, 99):
        assert _task(upstream_status=status).state == "unknown"


def test_domain_state_maps_failed_and_input_required():
    assert _task(upstream_status=4).state == "failed"
    assert _task(upstream_status=5).state == "input_required"


def test_domain_state_unknown_for_bool_not_treated_as_int():
    assert _task(upstream_status=True).state == "unknown"
    assert _task(upstream_status=False).state == "unknown"
    assert _task(upstream_status=True).upstream_status is True
    assert _task(upstream_status=False).upstream_status is False


def test_domain_state_unknown_for_none_preserves_none():
    assert _task(upstream_status=None).state == "unknown"
    assert _task(upstream_status=None).upstream_status is None


def test_domain_upstream_status_is_preserved_verbatim():
    assert _task(upstream_status=7).upstream_status == 7
    assert _task(upstream_status=None).upstream_status is None


def test_domain_missing_pin_stays_none_and_is_not_coerced():
    assert _task(is_pinned=None).is_pinned is None
    assert _task(is_pinned=False).is_pinned is False
    assert _task(is_pinned=True).is_pinned is True


def test_domain_created_at_keeps_opaque_str_and_numeric():
    assert _task(created_at="2024-01-01T00:00:00Z").created_at == "2024-01-01T00:00:00Z"
    assert _task(created_at=1704067200).created_at == 1704067200
    assert _task().created_at == "2024-01-01T00:00:00Z"


def test_truncate_text_stays_within_limit():
    preview, truncated = truncate_text("x" * 200, MAX_TASK_DESCRIPTION_PREVIEW)
    assert len(preview) == MAX_TASK_DESCRIPTION_PREVIEW
    assert preview.endswith("…")
    assert truncated is True
    preview, truncated = truncate_text("short", MAX_TASK_DESCRIPTION_PREVIEW)
    assert preview == "short"
    assert truncated is False


# --------------------------------------------------------------- list

def test_list_returns_typed_page_with_truncated_previews_and_derived_state():
    transport = FakeTransport(
        page=WebTaskPage(
            items=(
                _task(chat_id="c_aaa", description="short", upstream_status=3, is_pinned=True),
                _task(chat_id="c_bbb", description=LONG_DESCRIPTION, upstream_status=None, is_pinned=None),
                _task(chat_id="c_ccc", description=None, upstream_status=1, is_pinned=False),
            ),
            next_page_token="page-2",
        )
    )
    result = _run(_service(transport).list_tasks(limit=5, page_token="page-1"))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert isinstance(data, WebTaskPage)
    assert data.next_page_token == "page-2"
    assert len(data.items) == 3

    first = data.items[0]
    assert first.chat_id == "c_aaa"
    assert first.description_preview == "short"
    assert first.description_truncated is False
    assert first.upstream_status == 3
    assert first.is_pinned is True
    assert first.state == "completed"

    second = data.items[1]
    assert len(second.description_preview) <= MAX_TASK_DESCRIPTION_PREVIEW
    assert second.description_preview.endswith("…")
    assert second.description_truncated is True
    assert second.upstream_status is None
    assert second.is_pinned is None
    assert second.state == "unknown"

    third = data.items[2]
    assert third.description_preview == ""
    assert third.description_truncated is False
    assert third.state == "unknown"

    assert transport.list_calls == [(transport.client, 5, "page-1")]
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1


def test_list_defaults_to_limit_20_and_no_page_token():
    transport = FakeTransport(page=WebTaskPage(items=(), next_page_token=None))
    result = _run(_service(transport).list_tasks())
    assert result.ok is True
    assert result.data is not None
    assert result.data.items == ()
    assert result.data.next_page_token is None
    assert transport.list_calls == [(transport.client, DEFAULT_TASK_LIST_LIMIT, None)]


def test_list_empty_page_is_success_with_no_items():
    transport = FakeTransport(page=WebTaskPage(items=(), next_page_token=None))
    result = _run(_service(transport).list_tasks())
    assert result.ok is True
    assert result.data is not None
    assert result.data.items == ()


def test_list_marks_a_page_with_unaddressable_goals_partial():
    transport = FakeTransport(page=WebTaskPage(
        items=(_task(),), next_page_token=None, unaddressable_count=1,
    ))
    result = _run(_service(transport).list_tasks())
    assert result.ok and result.data is not None
    assert len(result.data.items) == 1 and result.data.unaddressable_count == 1
    assert result.meta.operation_state.value == "partial"
    assert result.meta.verification_status == "partial_task_page"
    assert [warning.code for warning in result.warnings] == ["unaddressable_task_goals"]


def test_list_description_preview_never_exceeds_160_chars():
    transport = FakeTransport(
        page=WebTaskPage(items=(_task(description=LONG_DESCRIPTION),), next_page_token=None)
    )
    result = _run(_service(transport).list_tasks())
    assert result.ok is True
    assert result.data is not None
    preview = result.data.items[0].description_preview
    assert len(preview) <= MAX_TASK_DESCRIPTION_PREVIEW
    assert preview.endswith("…")
    assert result.data.items[0].description_truncated is True


def test_list_description_exactly_at_limit_is_not_truncated():
    transport = FakeTransport(
        page=WebTaskPage(items=(_task(description="y" * MAX_TASK_DESCRIPTION_PREVIEW),), next_page_token=None)
    )
    result = _run(_service(transport).list_tasks())
    assert result.ok is True
    assert result.data is not None
    assert result.data.items[0].description_preview == "y" * MAX_TASK_DESCRIPTION_PREVIEW
    assert result.data.items[0].description_truncated is False


@pytest.mark.parametrize("limit", [0, -1, 2.5, "20", True, None])
@pytest.mark.parametrize("page_token", [123, True, b"token"])
def test_list_validates_before_any_transport_call(limit: Any, page_token: Any):
    transport = FakeTransport()
    result = _run(_service(transport).list_tasks(limit=limit, page_token=page_token))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert result.meta.verification_status == STATUS_INVALID_REQUEST
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0
    assert transport.list_calls == []


def test_list_fences_after_await_and_switch_is_refused():
    transport = FakeTransport(
        page=WebTaskPage(items=(_task(),), next_page_token=None),
        account_sequence=["acct", "acct", "other"],
    )
    result = _run(_service(transport).list_tasks())
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.list_calls == [(transport.client, DEFAULT_TASK_LIST_LIMIT, None)]


def test_list_malformed_fetched_identity_fails():
    transport = FakeTransport(
        page=WebTaskPage(items=(_task(chat_id="not-a-chat"),), next_page_token=None)
    )
    result = _run(_service(transport).list_tasks())
    assert result.ok is False
    assert result.error_code == "VERIFICATION_FAILED"
    assert result.meta.verification_status == STATUS_READ_ERROR
    assert transport.list_calls == [(transport.client, DEFAULT_TASK_LIST_LIMIT, None)]


# --------------------------------------------------------------- get

def test_get_returns_full_observed_task_up_to_max_chars():
    transport = FakeTransport(tasks={CHAT_ID: _task()})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.chat_id == CHAT_ID
    assert data.description == "Finish the review"
    assert data.description_truncated is False
    assert data.upstream_status == 3
    assert data.state == "completed"
    assert data.is_pinned is False
    assert data.created_at == "2024-01-01T00:00:00Z"
    assert data.updated_at == 1704067200
    assert transport.get_calls == [(transport.client, CHAT_ID)]


def test_get_default_max_chars_is_800_and_truncates_with_indicator():
    transport = FakeTransport(tasks={CHAT_ID: _task(description="y" * 1000)})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is True
    assert result.data is not None
    assert len(result.data.description) <= DEFAULT_TASK_GET_CHARS
    assert result.data.description.endswith("…")
    assert result.data.description_truncated is True


def test_get_description_exactly_at_max_chars_is_not_truncated():
    transport = FakeTransport(tasks={CHAT_ID: _task(description="z" * DEFAULT_TASK_GET_CHARS)})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is True
    assert result.data is not None
    assert len(result.data.description) == DEFAULT_TASK_GET_CHARS
    assert result.data.description_truncated is False


def test_get_max_chars_up_to_4000_is_accepted():
    transport = FakeTransport(tasks={CHAT_ID: _task()})
    result = _run(_service(transport).get_task(CHAT_ID, max_chars=MAX_TASK_GET_CHARS))
    assert result.ok is True


@pytest.mark.parametrize("max_chars", [0, -1, 4001, 8000, "800", True, None])
def test_get_max_chars_outside_bounds_is_invalid(max_chars: Any):
    transport = FakeTransport()
    result = _run(_service(transport).get_task(CHAT_ID, max_chars=max_chars))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert result.meta.verification_status == STATUS_INVALID_REQUEST
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0
    assert transport.get_calls == []


@pytest.mark.parametrize("chat_id", ["not-a-chat", "c_", "", "CHAT_123", None, 123])
def test_get_validates_chat_id_before_any_transport_call(chat_id: Any):
    transport = FakeTransport()
    result = _run(_service(transport).get_task(chat_id))  # type: ignore[arg-type]
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert result.meta.verification_status == STATUS_INVALID_REQUEST
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0
    assert transport.get_calls == []
    assert transport.delete_calls == []


def test_get_absent_is_authoritative_session_not_found():
    transport = FakeTransport(tasks={})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is False
    assert result.error_code == "SESSION_NOT_FOUND"
    assert result.meta.verification_status == STATUS_ABSENT
    assert result.data is None
    assert transport.get_calls == [(transport.client, CHAT_ID)]


def test_get_wrong_fetched_identity_fails():
    transport = FakeTransport(get_values=[_task(chat_id=WRONG_CHAT_ID)])
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is False
    assert result.error_code == "VERIFICATION_FAILED"
    assert result.meta.verification_status == STATUS_WRONG_IDENTITY
    assert result.data is None
    assert transport.get_calls == [(transport.client, CHAT_ID)]


def test_get_unknown_status_preserves_upstream_integer_and_unknown_state():
    transport = FakeTransport(tasks={CHAT_ID: _task(upstream_status=7)})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is True
    assert result.data is not None
    assert result.data.upstream_status == 7
    assert result.data.state == "unknown"


@pytest.mark.parametrize("upstream_status,expected_state", [(3, "completed"), (4, "failed"), (5, "input_required")])
def test_get_verified_goal_state_is_successful_read(upstream_status: int, expected_state: str):
    # Successful account metadata reads are ok even when the upstream goal
    # state is failed or input_required; the read is not a failed operation.
    transport = FakeTransport(tasks={CHAT_ID: _task(upstream_status=upstream_status)})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is True
    assert result.data is not None
    assert result.data.upstream_status == upstream_status
    assert result.data.state == expected_state


def test_get_missing_pin_stays_none():
    transport = FakeTransport(tasks={CHAT_ID: _task(is_pinned=None)})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is True
    assert result.data is not None
    assert result.data.is_pinned is None


def test_get_fences_after_await_and_switch_is_refused():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()}, account_sequence=["acct", "acct", "other"]
    )
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.get_calls == [(transport.client, CHAT_ID)]


# ------------------------------------------------------------- delete

def test_delete_absent_pre_read_is_verified_no_op_and_sends_nothing():
    transport = FakeTransport(tasks={})
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.chat_id == CHAT_ID
    assert data.changed is False
    assert data.mutation_sent is False
    assert data.verified is True
    assert data.pre_read_task is None
    assert data.post_read_task is None
    assert result.meta.verification_status == STATUS_NO_OP
    assert transport.get_calls == [(transport.client, CHAT_ID)]
    assert transport.delete_calls == []


def test_delete_wrong_fetched_identity_fails_without_sending():
    transport = FakeTransport(get_values=[_task(chat_id=WRONG_CHAT_ID)])
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.error_code == "VERIFICATION_FAILED"
    assert result.meta.verification_status == STATUS_WRONG_IDENTITY
    data = result.data
    assert data is not None
    assert data.changed is False
    assert data.mutation_sent is False
    assert data.pre_read_task is not None
    assert data.pre_read_task.chat_id == WRONG_CHAT_ID
    assert transport.delete_calls == []


def test_delete_success_proven_by_independent_post_get_none_without_replay():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()}, get_values=[_task(), None]
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.chat_id == CHAT_ID
    assert data.changed is True
    assert data.mutation_sent is True
    assert data.verified is True
    assert data.pre_read_task is not None
    assert data.pre_read_task.chat_id == CHAT_ID
    assert data.post_read_task is None
    assert result.meta.verification_status == STATUS_VERIFIED
    assert transport.delete_calls == [(transport.client, CHAT_ID)]
    assert transport.get_calls == [(transport.client, CHAT_ID), (transport.client, CHAT_ID)]


def test_delete_lost_reply_but_post_get_none_is_verified_without_replay():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()},
        get_values=[_task(), None],
        delete_side_effect=RuntimeError("response lost"),
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.changed is True
    assert data.mutation_sent is True
    assert data.verified is True
    assert result.meta.verification_status == STATUS_VERIFIED
    assert transport.delete_calls == [(transport.client, CHAT_ID)]


def test_delete_lost_reply_and_task_still_present_is_unverified_without_replay():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()},
        get_values=[_task(), _task()],
        delete_side_effect=RuntimeError("response lost"),
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_UNVERIFIED
    data = result.data
    assert data is not None
    assert data.changed is None
    assert data.mutation_sent is True
    assert data.verified is False
    assert data.post_read_task is not None
    assert transport.delete_calls == [(transport.client, CHAT_ID)]


def test_delete_successful_mutation_but_task_still_present_is_unverified():
    transport = FakeTransport(tasks={CHAT_ID: _task()}, get_values=[_task(), _task()])
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_UNVERIFIED
    data = result.data
    assert data is not None
    assert data.changed is None
    assert data.mutation_sent is True
    assert data.verified is False
    assert data.post_read_task is not None
    assert transport.delete_calls == [(transport.client, CHAT_ID)]


def test_delete_post_read_error_is_unverified_and_sent_once():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()}, get_values=[_task()], raise_get_on_call=2
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_ERROR
    data = result.data
    assert data is not None
    assert data.changed is None
    assert data.mutation_sent is True
    assert data.verified is False
    assert transport.delete_calls == [(transport.client, CHAT_ID)]
    assert len(transport.get_calls) == 2


def test_delete_invalid_chat_id_validates_before_init():
    transport = FakeTransport()
    result = _run(_service(transport).delete_task("not-a-chat"))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert result.meta.verification_status == STATUS_INVALID_REQUEST
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0
    assert transport.get_calls == []
    assert transport.delete_calls == []


def test_missing_account_is_refused_after_initialization_still_runs():
    transport = FakeTransport(account=None, account_sequence=[None, None])
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_REQUIRED
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1
    assert transport.get_calls == []
    assert transport.delete_calls == []


def test_account_switch_after_initialization_is_refused():
    transport = FakeTransport(account_sequence=["acct", "other"])
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1
    assert transport.get_calls == []
    assert transport.delete_calls == []


def test_account_switch_after_pre_read_is_refused_for_delete():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()}, account_sequence=["acct", "acct", "other"]
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.get_calls == [(transport.client, CHAT_ID)]
    assert transport.delete_calls == []


def test_account_switch_before_mutation_is_refused():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()},
        account_sequence=["acct", "acct", "acct", "other"],
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.get_calls == [(transport.client, CHAT_ID)]
    assert transport.delete_calls == []


def test_account_switch_after_mutation_is_refused_without_trusted_read_back():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()},
        get_values=[_task(), None],
        account_sequence=["acct", "acct", "acct", "acct", "other"],
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    data = result.data
    assert data is not None
    assert data.changed is None
    assert data.mutation_sent is True
    assert data.verified is False
    assert transport.delete_calls == [(transport.client, CHAT_ID)]
    assert len(transport.get_calls) == 1


def test_account_switch_after_read_back_is_refused():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()},
        get_values=[_task(), None],
        account_sequence=["acct", "acct", "acct", "acct", "acct", "other"],
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    data = result.data
    assert data is not None
    assert data.changed is None
    assert data.mutation_sent is True
    assert data.verified is False
    assert transport.delete_calls == [(transport.client, CHAT_ID)]
    assert len(transport.get_calls) == 2


def test_auto_auth_binds_unbound_owner_during_init_then_delete_succeeds():
    transport = FakeTransport(
        account=None,
        account_sequence=[None, "bound-acct", "bound-acct", "bound-acct", "bound-acct", "bound-acct"],
        tasks={CHAT_ID: _task()},
        get_values=[_task(), None],
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is True
    data = result.data
    assert data is not None and data.verified is True
    assert data.changed is True
    assert data.mutation_sent is True
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1


def test_client_initializer_failure_blocks_before_mutation():
    transport = FakeTransport(initializer_side_effect=RuntimeError("down"))
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 1
    assert transport.get_calls == []
    assert transport.delete_calls == []


def test_delete_sends_callback_at_most_once_on_every_path():
    cases = [
        FakeTransport(tasks={CHAT_ID: _task()}, get_values=[_task(), None]),
        FakeTransport(
            tasks={CHAT_ID: _task()},
            get_values=[_task(), _task()],
            delete_side_effect=RuntimeError("lost"),
        ),
        FakeTransport(tasks={}, ),
    ]
    for transport in cases:
        _run(_service(transport).delete_task(CHAT_ID))
        assert len(transport.delete_calls) <= 1


def test_no_account_keys_or_raw_exception_text_in_results():
    transport = FakeTransport(
        tasks={CHAT_ID: _task()},
        get_values=[_task(), None],
        delete_side_effect=RuntimeError("boom-secret-token-42"),
    )
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is True
    serialized = str(result.to_dict())
    assert "boom-secret-token-42" not in serialized
    assert result.data is not None
    assert result.data.changed is True


def test_delete_pre_read_failure_does_not_send_mutation():
    transport = FakeTransport(raise_get_on_call=1)
    result = _run(_service(transport).delete_task(CHAT_ID))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_ERROR
    data = result.data
    assert data is not None
    assert data.changed is False
    assert data.mutation_sent is False
    assert transport.delete_calls == []


def test_get_result_contains_no_account_or_cookie_material():
    transport = FakeTransport(tasks={CHAT_ID: _task()})
    result = _run(_service(transport).get_task(CHAT_ID))
    assert result.ok is True
    serialized = str(result.to_dict())
    assert "acct" not in serialized
    assert "secret" not in serialized
    assert result.data is not None
    assert result.data.chat_id == CHAT_ID


def test_is_valid_remote_chat_id_accepts_observed_ids():
    assert is_valid_remote_chat_id("c_123abc") is True
    assert is_valid_remote_chat_id("c_") is False
    assert is_valid_remote_chat_id("not-a-chat") is False
