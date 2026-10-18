"""Current Remy schedule reads stay separate from legacy scheduled mutations."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter

from src.domain.account_requests import ScheduledRequest
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.scheduled import (
    delete_action,
    fetch_remy_schedule_by_id,
    fetch_remy_schedule_registry,
    fetch_scheduled_registry,
)


def schedule(*, identifier="schedule_fixture", status=1):
    row = [None] * 25
    row[0], row[1], row[2] = identifier, "Fixture schedule", True
    row[3], row[6], row[7], row[9] = [[9, 30]], "Asia/Shanghai", "Fixture instructions", status
    row[11], row[23], row[24] = "c_schedule_fixture", "fixture", 1
    return row


def response(key, body, *, status=200, reject=None, rpc_id=None):
    part = ["wrb.fr", rpc_id or get_contract(key).rpc_id, json.dumps(body),
            None, None, [reject] if reject is not None else None]
    return SimpleNamespace(status_code=status, text=json.dumps([part]))


def test_matching_legacy_empty_is_not_the_current_registry():
    replies = {
        "XPSWpd": response("scheduled.registry", []),
        "Ey90Ac": response("scheduled.remy_registry", [[schedule()], False, 1]),
    }
    calls = []

    async def batch(requests, **kwargs):
        request = requests[0]
        calls.append((request.rpcid, json.loads(request.payload), kwargs))
        return replies[request.rpcid]

    async def run():
        client = SimpleNamespace(_batch_execute=batch)
        old, old_diagnostic = await fetch_scheduled_registry(client, 500)
        current, current_diagnostic = await fetch_remy_schedule_registry(client, 500)
        assert old == [] and old_diagnostic["backend"] == "legacy"
        assert old_diagnostic["read_back_valid"] is True
        assert current[0]["id"] == "schedule_fixture" and current[0]["backend"] == "remy"
        assert current_diagnostic["read_back_valid"] is True
        assert current_diagnostic["backend"] == "remy"

    asyncio.run(run())
    assert [call[:2] for call in calls] == [("XPSWpd", []), ("Ey90Ac", [])]
    assert calls[1][2] == {"source_path": "/scheduled", "close_on_error": False}


@pytest.mark.parametrize("state,name,enabled", [
    (0, "unspecified", None), (1, "active", True), (2, "paused", False), (3, "completed", False),
])
def test_current_status_enum_never_becomes_legacy_running_or_deleted(state, name, enabled):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response(
        "scheduled.remy_get", [schedule(status=state)],
    )))
    item, diagnostic = asyncio.run(fetch_remy_schedule_by_id(client, "schedule_fixture", 500))
    assert item["task_state"] == name and item["enabled"] is enabled
    assert item["schedule_status_id"] == state and item["task_state_id"] is None
    assert diagnostic["matched_task"] is True and diagnostic["read_back_valid"] is True
    request = client._batch_execute.await_args.args[0][0]
    assert request.rpcid == "Mfr55e" and json.loads(request.payload) == ["schedule_fixture"]
    assert client._batch_execute.await_args.kwargs == {"source_path": "/scheduled", "close_on_error": False}


@pytest.mark.parametrize("key,reader,body", [
    ("scheduled.remy_registry", fetch_remy_schedule_registry, [[schedule()], False, 1]),
    ("scheduled.remy_get", fetch_remy_schedule_by_id, [schedule()]),
])
@pytest.mark.parametrize("case", ["http_error", "rejected", "missing", "ambiguous", "malformed"])
def test_invalid_current_response_never_becomes_verified_empty(key, reader, body, case):
    observed = response(key, body)
    if case == "http_error":
        observed.status_code = 503
    elif case == "rejected":
        observed = response(key, body, reject=7)
    elif case == "missing":
        observed = response(key, body, rpc_id="unrelated")
    elif case == "ambiguous":
        parts = json.loads(observed.text)
        observed.text = json.dumps(parts + parts)
    else:
        observed = response(key, {"changed": "shape"})
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=observed))
    arguments = (client, "schedule_fixture", 500) if key.endswith("get") else (client, 500)
    item, diagnostic = asyncio.run(reader(*arguments))
    assert not item and diagnostic["read_back_valid"] is False


def test_current_get_rejects_another_schedule_id():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response(
        "scheduled.remy_get", [schedule(identifier="other_schedule_fixture")],
    )))
    item, diagnostic = asyncio.run(fetch_remy_schedule_by_id(client, "schedule_fixture", 500))
    assert item is None and diagnostic["matched_task"] is False
    assert diagnostic["read_back_valid"] is False
    assert diagnostic["parser_warnings"] == ["returned_id_mismatch"]


@pytest.mark.parametrize("body", [[], [[]], [None, False, 1]])
def test_current_positive_empty_registry_requires_matching_envelope(body):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("scheduled.remy_registry", body)))
    items, diagnostic = asyncio.run(fetch_remy_schedule_registry(client, 500))
    assert items == [] and diagnostic["read_back_valid"] is True
    assert diagnostic["parser_status"] == "empty"


@pytest.mark.parametrize("status", [True, "1", 1.0, -1, 4])
def test_current_invalid_status_is_a_changed_shape(status):
    parsed = parse_contract_body("scheduled.remy_registry", [[schedule(status=status)], False, 1])
    assert parsed.status == "changed_shape"


@pytest.mark.parametrize("trigger_index,message,kind,details", [
    (3, [[9, 30]], "daily", {"hour": 9, "minute": 30}),
    (4, [[1, 3, 5], [8, 15]], "weekly", {"days_of_week": [1, 3, 5], "hour": 8, "minute": 15}),
    (17, [2], "hourly", {"interval": 2}),
    (18, [[1, 15], [7]], "monthly", {"days_of_month": [1, 15], "hour": 7, "minute": 0}),
    (19, [[0, 6, 10], [10]], "yearly", {"month": 6, "day": 10, "hour": 10, "minute": 0}),
    (20, [["1234567890"]], "one_off", {"unix_seconds": 1234567890}),
    (5, ["Fixture condition"], "event", {"condition": "Fixture condition"}),
    (15, ["Fixture query", "Fixture condition"], "gmail", {"condition": "Fixture condition"}),
])
def test_current_schedule_reads_observed_trigger_fields(trigger_index, message, kind, details):
    row = schedule()
    row[3], row[trigger_index] = None, message
    parsed = parse_contract_body("scheduled.remy_get", [row], expected_id="schedule_fixture")
    assert parsed.status == "success"
    assert parsed.value["trigger"] == {"type": kind, **details}


@pytest.mark.parametrize("change", [
    {3: [[24]]}, {3: "changed"}, {3: [[9, True]]}, {3: [[9]], 4: [[], [8]]},
    {3: None, 4: [["Monday"], [8]]}, {3: None, 20: ["changed"]},
])
def test_malformed_current_triggers_are_not_valid_readback(change):
    row = schedule()
    for index, value in change.items():
        row[index] = value
    assert parse_contract_body("scheduled.remy_get", [row]).status == "changed_shape"


def test_current_reads_bound_user_text_without_returning_raw_proto():
    row = schedule()
    row[1], row[7] = "Title fixture " * 50, "Instructions fixture " * 50
    item = parse_contract_body("scheduled.remy_get", [row], max_chars=40).value
    assert len(item["title"]) <= 41 and len(item["instructions"]) <= 41
    assert "raw" not in item and "raw_body" not in item


@pytest.mark.parametrize("action", ["list", "get"])
@pytest.mark.parametrize("backend", ["legacy", "remy"])
def test_current_read_backend_is_explicit_and_legacy_is_default(action, backend):
    args = {"action": action, **({"action_id": "schedule_fixture"} if action == "get" else {})}
    adapter = TypeAdapter(ScheduledRequest)
    assert adapter.validate_python(args).backend == "legacy"
    assert adapter.validate_python({**args, "backend": backend}).backend == backend


def test_current_delete_keeps_explicit_native_backend():
    request = TypeAdapter(ScheduledRequest).validate_python({
        "action": "delete", "action_id": "schedule_fixture", "backend": "remy",
    })
    assert request.backend == "remy" and request.action_id == "schedule_fixture"


def test_legacy_delete_never_consults_current_registry_for_absence():
    calls = []
    replies = {
        "Q4Gw3c": response("scheduled.delete", []),
        "XPSWpd": response("scheduled.registry", [[
            ["other_legacy_fixture", [["Fixture instructions", "Daily", "Fixture title"]], [[]]],
        ]]),
        "kwDCne": response("scheduled.get", []),
    }

    async def batch(requests, **_kwargs):
        calls.append(requests[0].rpcid)
        return replies[requests[0].rpcid]

    result = asyncio.run(delete_action(SimpleNamespace(_batch_execute=batch), action_id="legacy_fixture"))
    assert result["verification_status"] == "not_visible_not_readable_by_id"
    assert calls == ["Q4Gw3c", "XPSWpd", "kwDCne"]
    assert result["registry_diagnostic"]["backend"] == "legacy"
    assert result["get_task_diagnostic"]["backend"] == "legacy"
