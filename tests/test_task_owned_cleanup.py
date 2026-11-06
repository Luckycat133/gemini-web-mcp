"""Exact Remy goals need their own read-back before owned Task cleanup is verified."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import CleanupState
from src.infrastructure.account_identity import current_account_key
from src.infrastructure.operation_store import OperationStore
from src.infrastructure.rpc_contracts import get_contract
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager


CID = "c_task_fixture"


class Client:
    def __init__(self, *, goal=None, history=False, malformed=False, retain_history=False, on_goal_read=None):
        self.goal = goal
        self.history = history
        self.malformed = malformed
        self.retain_history = retain_history
        self.on_goal_read = on_goal_read
        self.goal_reads = 0
        self.history_reads = 0
        self.mutations = []

    async def delete_chat(self, cid):
        assert cid == CID
        self.mutations.append("conversation")
        self.history = False

    async def _batch_execute(self, requests, **options):
        request, = requests
        rpc_id = request.rpcid
        rejection = None
        if rpc_id == get_contract("tasks.get").rpc_id:
            assert json.loads(request.payload) == [CID]
            self.goal_reads += 1
            if self.on_goal_read is not None:
                self.on_goal_read(self.goal_reads)
            if self.malformed:
                body = []
            elif self.goal is None:
                body, rejection = None, [5]
            else:
                goal = [None] * 14
                goal[2], goal[13] = 8, self.goal
                body = [goal, []]
        elif rpc_id == get_contract("tasks.delete").rpc_id:
            assert json.loads(request.payload) == [CID] and options["current_retry"] == 0
            self.mutations.append("goal")
            self.goal = None
            if not self.retain_history:
                self.history = False
            body = []
        else:
            assert rpc_id == get_contract("history.page").rpc_id
            self.history_reads += 1
            body = [None, None, [[CID, "Synthetic"]] if self.history else []]
        envelope = ["wrb.fr", rpc_id, json.dumps(body) if body is not None else None, None, None, rejection]
        return SimpleNamespace(status_code=200, text=json.dumps([envelope]))


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-cleanup-owner")
    return RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite3", account_key_provider=current_account_key,
    )


def register(owned, *, source="gemini_run_task", completed=False):
    account = current_account_key()
    owned._store.schedule(account, CID, 0, source, retain=False)
    if completed:
        token = owned._store.claim(account, CID, force=True)
        owned._store.finish(account, CID, token, verified=True, diagnostic_id=None)
    return account


def bind_operation(operation):
    store = OperationStore()
    store.create("fixture", operation)
    store.update("fixture", state="completed", phase="completed", upstream_chat_id=CID)
    store.release_cleanup("fixture")


@pytest.mark.parametrize("action", ["recheck", "retry"])
def test_history_absence_cannot_hide_a_known_task_goal(owned, action):
    # Older in-progress sources were shared by Task and Notebook Chat.
    bind_operation("gemini_run_task")
    account = register(owned, source="task_job:in_progress", completed=action == "recheck")
    client = Client(goal=CID)
    if action == "recheck":
        result = asyncio.run(owned.recheck_cleanup_result(CID, client=client))
    else:
        token = owned._store.claim(account, CID, force=True)
        owned._store.finish(account, CID, token, verified=False, diagnostic_id=None)
        result = asyncio.run(owned.delete_chat_result(CID, client=client))
    assert result.state is CleanupState.COMPLETED
    assert client.mutations == ["goal"] and client.goal is None
    assert client.goal_reads >= 3 and client.history_reads == 2
    assert owned._store.get(account, CID)["state"] == "completed"


def test_goal_absent_with_present_history_uses_only_conversation_deletion(owned):
    register(owned)
    client = Client(history=True)
    result = asyncio.run(owned.delete_chat_result(CID, client=client))
    assert result.state is CleanupState.COMPLETED
    assert client.mutations == ["conversation"] and client.goal_reads == 2


def test_one_native_dispatch_does_not_claim_a_still_visible_conversation(owned):
    account = register(owned)
    client = Client(goal=CID, history=True, retain_history=True)
    result = asyncio.run(owned.delete_chat_result(CID, client=client))
    assert result.state is CleanupState.FAILED
    assert client.mutations == ["goal"] and client.history
    assert owned._store.get(account, CID)["state"] == "pending"


@pytest.mark.parametrize("malformed", [True, False])
def test_empty_or_wrong_identity_goal_read_cannot_complete_or_dispatch(owned, malformed):
    account = register(owned, completed=True)
    client = Client(goal="c_other_task", malformed=malformed)
    result = asyncio.run(owned.recheck_cleanup_result(CID, client=client))
    assert result.state is CleanupState.FAILED
    assert client.goal_reads == 1 and not client.history_reads and not client.mutations
    assert owned._store.get(account, CID)["state"] == "completed"  # Old evidence was not refreshed.


def test_account_switch_during_fresh_schedule_read_prevents_native_delete(owned, monkeypatch):
    account = register(owned)

    def switch(reads):
        if reads == 2:
            monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")

    client = Client(goal=CID, on_goal_read=switch)
    result = asyncio.run(owned.delete_chat_result(CID, client=client))
    assert result.state is CleanupState.FAILED and not client.mutations
    assert owned._store.get(account, CID)["state"] == "pending"


def test_task_retained_during_owned_recheck_cannot_be_deleted(owned):
    account = register(owned)
    client = Client(goal=CID, on_goal_read=lambda _: owned.schedule_cleanup_result(CID, retain_chat=True))
    result = asyncio.run(owned.recheck_cleanup_result(CID, client=client))
    assert result.state is CleanupState.RETAINED and not client.mutations
    assert owned._store.get(account, CID)["state"] == "retained"


def test_lost_lease_during_schedule_preflight_prevents_native_delete(owned):
    account = register(owned)

    def replace_lease(reads):
        if reads == 2:
            with sqlite3.connect(owned._store.path) as database:
                database.execute("UPDATE cleanup SET lease_token=? WHERE account_key=? AND chat_id=?",
                                 ("replacement", account, CID))

    client = Client(goal=CID, on_goal_read=replace_lease)
    result = asyncio.run(owned.delete_chat_result(CID, client=client))
    assert result.state is CleanupState.PENDING and not client.mutations
    assert owned._store.get(account, CID)["lease_token"] == "replacement"


def test_new_schedule_observed_at_dispatch_defers_native_delete(owned, monkeypatch):
    account = register(owned)
    schedules = AsyncMock(return_value=["synthetic-schedule"])
    monkeypatch.setattr("src.services.web_context_transport.refresh_task_schedule_guard", schedules)
    client = Client(goal=CID)
    result = asyncio.run(owned.delete_chat_result(CID, client=client))
    assert result.state is CleanupState.PENDING and not client.mutations
    schedules.assert_awaited_once_with(client, CID)
    record = owned._store.get(account, CID)
    assert record["state"] == "pending" and record["lease_token"] is None and record["attempts"] == 0


@pytest.mark.parametrize("operation", ["gemini_notebook_chat", "other_account_task"])
def test_non_task_owned_chat_does_not_probe_the_goal_endpoint(owned, monkeypatch, operation):
    if operation == "other_account_task":
        with monkeypatch.context() as other:
            other.setenv("GEMINI_PSID", "synthetic-other-account")
            bind_operation("gemini_run_task")
    else:
        bind_operation(operation)
    register(owned, source="task_job:in_progress")
    client = Client(history=True)
    result = asyncio.run(owned.delete_chat_result(CID, client=client))
    assert result.state is CleanupState.COMPLETED
    assert not client.goal_reads and client.mutations == ["conversation"]
