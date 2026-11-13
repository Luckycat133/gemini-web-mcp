"""Offline persistence and fail-closed cleanup contracts for Task schedules."""

import asyncio
import json
import sqlite3
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.account_identity import current_account_key
from src.infrastructure.operation_store import OperationStore
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.task_schedule_store import TaskScheduleGuardStore
from src.services import web_context_transport
from src.services.web_context_transport import WebContextUnavailable, refresh_task_schedule_guard


@pytest.fixture(autouse=True)
def _synthetic_account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-schedule-account-a")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "false")


def _references(path):
    with sqlite3.connect(path) as connection:
        return set(connection.execute("SELECT account_key,schedule_id,chat_id FROM task_schedule_guards"))


def _goal():
    value = [None] * 14
    value[1], value[2], value[13] = "Synthetic Task", 3, "c_task"
    return value


def _reply(body):
    return SimpleNamespace(status_code=200, text=json.dumps([
        ["wrb.fr", get_contract("tasks.schedule_refs").rpc_id, json.dumps(body)],
    ]))


def test_schedule_guard_persists_only_account_scoped_identifier_metadata(tmp_path):
    path = tmp_path / "state" / "operations.sqlite3"
    owner = current_account_key()
    TaskScheduleGuardStore(path).replace_chat(owner, "c_task", ["schedule_one", "schedule_two"])

    reopened = TaskScheduleGuardStore(path)
    assert reopened.protects("c_task") and not reopened.protects("c_unrelated")
    assert _references(path) == {(owner, "schedule_one", "c_task"), (owner, "schedule_two", "c_task")}
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(task_schedule_guards)")}
    assert columns == {"account_key", "schedule_id", "chat_id", "observed_at"}
    assert b"synthetic-task-schedule-account-a" not in path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_same_chat_and_schedule_identifiers_remain_separate_across_accounts(tmp_path, monkeypatch):
    path = tmp_path / "operations.sqlite3"
    guards = TaskScheduleGuardStore(path)
    owner_a = current_account_key()
    guards.replace_chat(owner_a, "c_shared", ["same_schedule_id"])

    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-schedule-account-b")
    owner_b = current_account_key()
    assert not guards.protects("c_shared")
    with pytest.raises(ValueError):
        guards.forget_verified_absent(owner_a, "same_schedule_id")
    with pytest.raises(ValueError):
        guards.replace_chat(owner_a, "c_shared", [])
    guards.replace_chat(owner_b, "c_shared", ["same_schedule_id"])
    guards.forget_verified_absent(owner_b, "same_schedule_id")
    assert not guards.protects("c_shared")

    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-schedule-account-a")
    assert TaskScheduleGuardStore(path).protects("c_shared")
    assert _references(path) == {(owner_a, "same_schedule_id", "c_shared")}


def test_replacing_one_chat_preserves_other_chat_guards_and_persists_verified_empty(tmp_path):
    path = tmp_path / "operations.sqlite3"
    guards = TaskScheduleGuardStore(path)
    owner = current_account_key()
    guards.replace_chat(owner, "c_target", ["old", "kept"])
    guards.replace_chat(owner, "c_other", ["other"])
    guards.replace_chat(owner, "c_target", ["kept", "new"])
    assert _references(path) == {
        (owner, "kept", "c_target"), (owner, "new", "c_target"), (owner, "other", "c_other"),
    }

    guards.replace_chat(owner, "c_target", [])
    reopened = TaskScheduleGuardStore(path)
    assert not reopened.protects("c_target") and reopened.protects("c_other")
    assert _references(path) == {(owner, "other", "c_other")}


def test_forgetting_verified_absence_removes_only_the_exact_schedule(tmp_path):
    path = tmp_path / "operations.sqlite3"
    guards = TaskScheduleGuardStore(path)
    owner = current_account_key()
    guards.replace_chat(owner, "c_target", ["one", "two"])
    guards.replace_chat(owner, "c_other", ["other"])

    guards.forget_verified_absent(owner, "one")
    assert TaskScheduleGuardStore(path).protects("c_target")
    guards.forget_verified_absent(owner, "two")
    assert not TaskScheduleGuardStore(path).protects("c_target")
    assert TaskScheduleGuardStore(path).protects("c_other")
    assert _references(path) == {(owner, "other", "c_other")}


def test_replacing_account_snapshot_does_not_remove_other_accounts(tmp_path, monkeypatch):
    path = tmp_path / "operations.sqlite3"
    guards = TaskScheduleGuardStore(path)
    owner_a = current_account_key()
    guards.replace_account(owner_a, [("old", "c_old")])
    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-schedule-account-b")
    owner_b = current_account_key()
    guards.replace_account(owner_b, [("other", "c_other")])

    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-schedule-account-a")
    guards.replace_account(owner_a, [("new", "c_new")])
    assert _references(path) == {(owner_a, "new", "c_new"), (owner_b, "other", "c_other")}
    guards.replace_account(owner_a, [])
    assert _references(path) == {(owner_b, "other", "c_other")}


@pytest.mark.parametrize("schedule_ids", [[""], ["duplicate", "duplicate"], [None], [True]])
def test_invalid_schedule_references_cannot_clear_an_existing_guard(tmp_path, schedule_ids):
    path = tmp_path / "operations.sqlite3"
    guards = TaskScheduleGuardStore(path)
    owner = current_account_key()
    guards.replace_chat(owner, "c_task", ["original"])
    with pytest.raises(ValueError):
        guards.replace_chat(owner, "c_task", schedule_ids)
    assert _references(path) == {(owner, "original", "c_task")}
    assert TaskScheduleGuardStore(path).protects("c_task")


def test_unknown_account_never_authorizes_automatic_chat_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    TaskScheduleGuardStore(path).replace_chat(current_account_key(), 'c_task', ['schedule-one'])
    monkeypatch.delenv("GEMINI_PSID")
    assert TaskScheduleGuardStore(path).protects("c_task")
    assert store.is_cleanup_protected("c_task")
    with pytest.raises(ValueError):
        TaskScheduleGuardStore(path).replace_chat("", "c_task", [])


@pytest.mark.parametrize("value", [
    None,
    {"chat_id": "c_other", "schedule_ids": []},
    {"chat_id": "c_task", "schedule_ids": [None]},
    {"chat_id": "c_task", "schedule_ids": ["duplicate", "duplicate"]},
    RuntimeError("read unavailable"),
])
def test_unknown_or_failed_association_reads_never_remove_existing_protection(tmp_path, monkeypatch, value):
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    owner = current_account_key()
    TaskScheduleGuardStore(path).replace_chat(owner, "c_task", ["original"])
    batch = AsyncMock(side_effect=value) if isinstance(value, Exception) else AsyncMock(return_value=value)
    monkeypatch.setattr(web_context_transport, "_batch", batch)

    with pytest.raises((WebContextUnavailable, ValueError, RuntimeError)):
        asyncio.run(refresh_task_schedule_guard(object(), "c_task", store=store))

    assert _references(path) == {(owner, "original", "c_task")}
    assert OperationStore(path).is_cleanup_protected("c_task")
    batch.assert_awaited_once()


def test_account_switch_during_guard_read_cannot_apply_an_empty_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    owner = current_account_key()
    TaskScheduleGuardStore(path).replace_chat(owner, "c_task", ["original"])

    async def switched_read(*args, **kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-task-schedule-account-b")
        return {"chat_id": "c_task", "schedule_ids": []}

    monkeypatch.setattr(web_context_transport, "_batch", switched_read)
    with pytest.raises(WebContextUnavailable):
        asyncio.run(refresh_task_schedule_guard(object(), "c_task", store=store))

    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-schedule-account-a")
    assert _references(path) == {(owner, "original", "c_task")}
    assert TaskScheduleGuardStore(path).protects("c_task")


def test_valid_associated_schedule_of_unknown_kind_still_prevents_cascading_cleanup(tmp_path):
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    schedule = [None] * 25
    schedule[0], schedule[1], schedule[3] = "unknown_kind_schedule", "Synthetic schedule", [[9, 0]]
    schedule[6], schedule[7], schedule[9], schedule[11], schedule[24] = "UTC", "Synthetic plan", 1, "c_task", 0
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_reply([_goal(), [schedule]])))

    references = asyncio.run(refresh_task_schedule_guard(client, "c_task", store=store))

    assert references == ["unknown_kind_schedule"]
    assert TaskScheduleGuardStore(path).protects("c_task")
    assert OperationStore(path).is_cleanup_protected("c_task")
    assert client._batch_execute.await_count == 1


@pytest.mark.parametrize("case", ["changed_schedule", "missing_envelope", "http_rejected", "not_found"])
def test_unverified_schedule_reference_envelopes_do_not_clear_a_persisted_guard(tmp_path, case):
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    owner = current_account_key()
    TaskScheduleGuardStore(path).replace_chat(owner, "c_task", ["original"])
    if case == "changed_schedule":
        response = _reply([_goal(), [{"changed_shape": True}]])
    elif case == "missing_envelope":
        response = SimpleNamespace(status_code=200, text="[]")
    elif case == "http_rejected":
        response = SimpleNamespace(status_code=403, text="[]")
    else:
        response = SimpleNamespace(status_code=200, text=json.dumps([
            ["wrb.fr", get_contract("tasks.schedule_refs").rpc_id, None, None, None, [5]],
        ]))
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))

    with pytest.raises(WebContextUnavailable):
        asyncio.run(refresh_task_schedule_guard(client, "c_task", store=store))

    assert _references(path) == {(owner, "original", "c_task")}
    assert OperationStore(path).is_cleanup_protected("c_task")


def test_verified_empty_task_associations_clear_only_that_chat_guard(tmp_path):
    path = tmp_path / "operations.sqlite3"
    store = OperationStore(path)
    owner = current_account_key()
    guards = TaskScheduleGuardStore(path)
    guards.replace_chat(owner, "c_task", ["original"])
    guards.replace_chat(owner, "c_other", ["other"])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_reply([_goal(), []])))

    assert asyncio.run(refresh_task_schedule_guard(client, "c_task", store=store)) == []

    assert not OperationStore(path).is_cleanup_protected("c_task")
    assert OperationStore(path).is_cleanup_protected("c_other")
    assert _references(path) == {(owner, "other", "c_other")}
