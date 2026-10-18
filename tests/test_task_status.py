"""Native Task read-back requires a matching, valid goal without generation."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import OperationState
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.tasks import get_task_status_result


def task_goal(status=2, chat_id="c_task_fixture"):
    goal = [None] * 14
    goal[0], goal[1], goal[2], goal[13] = "goal_fixture", "Private fixture description", status, chat_id
    return [goal]


def task_response(body, *, status=200, reject=None, rpc_id=None):
    part = ["wrb.fr", rpc_id or get_contract("tasks.get").rpc_id, json.dumps(body),
            None, None, [reject] if reject is not None else None]
    return SimpleNamespace(status_code=status, text=json.dumps([part]))


@pytest.mark.parametrize("status,state,terminal", [
    (0, OperationState.PARTIAL, False), (1, OperationState.QUEUED, False),
    (2, OperationState.RUNNING, False), (3, OperationState.COMPLETED, True),
    (4, OperationState.FAILED, True), (5, OperationState.PARTIAL, False),
    (6, OperationState.RUNNING, False), (7, OperationState.QUEUED, False),
    (8, OperationState.CANCELLED, True),
])
def test_task_status_uses_only_observed_identity_and_enum(status, state, terminal):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=task_response(task_goal(status))))

    result = asyncio.run(get_task_status_result(client, "c_task_fixture"))

    assert result.ok is (status not in {4, 8})
    assert result.meta.operation_state is state
    assert result.data["status_id"] == status
    assert result.data["terminal"] is terminal
    assert result.data["chat_id"] == "c_task_fixture"
    assert result.data["goal_id"] == "goal_fixture"
    assert "Private fixture description" not in json.dumps(result.to_dict())
    request = client._batch_execute.await_args.args[0][0]
    assert request.rpcid == get_contract("tasks.get").rpc_id
    assert json.loads(request.payload) == ["c_task_fixture"]
    assert client._batch_execute.await_args.kwargs == {"source_path": "/app", "close_on_error": False}


@pytest.mark.parametrize("body", [
    [], [None], {}, [["short"]], task_goal(True), task_goal("3"), task_goal(3.0),
    task_goal(-1), task_goal(9), task_goal(None), task_goal(3, ""), task_goal(3, "c_other_fixture"),
])
def test_empty_mismatched_or_malformed_goals_cannot_complete(body):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=task_response(body)))

    result = asyncio.run(get_task_status_result(client, "c_task_fixture"))

    assert result.ok is False
    assert result.meta.operation_state is not OperationState.COMPLETED
    assert result.data["terminal"] is False
    assert result.data["status"] == "unknown"
    assert result.data["chat_id"] == "c_task_fixture"
    client._batch_execute.assert_awaited_once()


@pytest.mark.parametrize("case", ["http_error", "rejected", "missing", "ambiguous"])
def test_task_status_requires_one_successful_matching_envelope(case):
    observed = task_response(task_goal(3))
    if case == "http_error":
        observed.status_code = 503
    elif case == "rejected":
        observed = task_response(task_goal(3), reject=7)
    elif case == "missing":
        observed = task_response(task_goal(3), rpc_id="unrelated_rpc")
    else:
        parts = json.loads(observed.text)
        observed.text = json.dumps(parts + parts)
    result = asyncio.run(get_task_status_result(
        SimpleNamespace(_batch_execute=AsyncMock(return_value=observed)), "c_task_fixture",
    ))
    assert result.ok is False
    assert result.data["terminal"] is False
    assert result.meta.verification_status == "read_back_unavailable"


def test_repeated_task_status_reads_never_submit_or_delete():
    client = SimpleNamespace(
        _batch_execute=AsyncMock(side_effect=[task_response(task_goal(2)), task_response(task_goal(3))]),
        generate_content=AsyncMock(), delete_chat=AsyncMock(),
    )

    async def run():
        first = await get_task_status_result(client, "c_task_fixture")
        second = await get_task_status_result(client, "c_task_fixture")
        assert first.meta.operation_state is OperationState.RUNNING
        assert second.meta.operation_state is OperationState.COMPLETED

    asyncio.run(run())
    assert client._batch_execute.await_count == 2
    client.generate_content.assert_not_awaited()
    client.delete_chat.assert_not_awaited()


@pytest.mark.parametrize("chat_id", [None, "", " ", "not_a_chat"])
def test_task_status_rejects_missing_source_before_rpc(chat_id):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    assert asyncio.run(get_task_status_result(client, chat_id)).error_code == "INVALID_ARGUMENT"
    client._batch_execute.assert_not_awaited()


def test_task_status_read_timeout_is_typed_and_not_retried():
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=TimeoutError("Private fixture failure")))
    result = asyncio.run(get_task_status_result(client, "c_task_fixture"))
    assert result.ok is False
    assert result.meta.operation_state is OperationState.TIMED_OUT
    assert "Private fixture failure" not in json.dumps(result.to_dict())
    client._batch_execute.assert_awaited_once()


def test_task_goal_parser_omits_schedules_and_descriptions():
    body = task_goal(3) + [[["Private schedule fixture"]]]
    result = parse_contract_body("tasks.get", body, expected_id="c_task_fixture")
    assert result.status == "success"
    assert set(result.value) == {"goal_id", "chat_id", "status", "status_id", "terminal"}
