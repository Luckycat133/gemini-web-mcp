"""Current Remy mutations use native messages and independent read-back."""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain import OperationState
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.scheduled import create_remy_schedule_result, delete_remy_schedule_result, update_remy_schedule_result


def schedule_row():
    row = [None] * 29
    row[0], row[1], row[3], row[6], row[7], row[8], row[9] = "remy_fixture", "Original", [[9, 15]], "UTC", "Instructions", "Context", 1
    row[11], row[24], row[26], row[28] = "c_source_fixture", 1, "project_fixture", {"future": "preserve"}
    return row


def response(key, body, *, status=200, reject=None):
    return SimpleNamespace(status_code=status, text=json.dumps([
        ["wrb.fr", get_contract(key).rpc_id, json.dumps(body), None, None, [reject] if reject is not None else None],
    ]))


class NativeSchedules:
    def __init__(self, *, ack="success", read_back="valid"):
        self.row = schedule_row()
        self.before = copy.deepcopy(self.row)
        self.ack, self.read_back = ack, read_back
        self.calls, self.writes = [], []
        self.deleted = False

    async def _batch_execute(self, requests, *, source_path, close_on_error):
        assert source_path == "/scheduled" and close_on_error is False
        request = requests[0]
        keys = ("scheduled.remy_get", "scheduled.remy_registry", "scheduled.remy_create", "scheduled.remy_update", "scheduled.remy_delete")
        key = next(key for key in keys if get_contract(key).rpc_id == request.rpcid)
        payload = json.loads(request.payload)
        self.calls.append((key, payload))
        if key.endswith("_get") or key.endswith("_registry"):
            if self.writes:
                if self.read_back == "http_error":
                    return response(key, [], status=503)
                if self.read_back == "bad_shape":
                    return response(key, {"private_changed_shape": True})
            if key.endswith("_registry"):
                rows = [] if self.deleted else [self.row]
                if self.read_back == "registry_contradiction":
                    rows = [self.before]
                return response(key, [rows])
            assert payload == ["remy_fixture"]
            row = copy.deepcopy(self.row)
            if self.writes and self.read_back == "mismatch":
                row[0] = "other_fixture"
            if self.writes and self.read_back == "stale":
                row = self.before
            empty = (self.deleted and self.read_back != "still_present") or (self.writes and self.read_back == "empty")
            return response(key, [] if empty else [row])
        self.writes.append((key, payload))
        if key.endswith("_delete"):
            assert payload == ["remy_fixture"]
            self.deleted = True
            body = []
        else:
            assert isinstance(payload, list) and len(payload) == 1
            self.row = payload[0]
            self.row[0] = "remy_fixture"
            body = [self.row]
        if self.ack == "timeout":
            raise TimeoutError("Private mutation fixture")
        if self.ack == "http_error":
            return response(key, body, status=503)
        if self.ack == "rejected":
            return response(key, body, reject=7)
        if self.ack == "empty":
            return response(key, [None])
        if self.ack == "changed_shape":
            return response(key, {"changed": True})
        if self.ack == "ambiguous":
            result = response(key, body)
            result.text = json.dumps(json.loads(result.text) * 2)
            return result
        return response(key, body)


def create(client, **kwargs):
    return create_remy_schedule_result(client, title="Created", instructions="Owned fixture instructions",
                                       trigger=kwargs.pop("trigger", {"type": "daily", "hour": 12, "minute": 0}), **kwargs)


@pytest.mark.parametrize("trigger,index,message", [
    ({"type": "daily", "hour": 0, "minute": 0}, 3, [[0, 0]]),
    ({"type": "weekly", "hour": 8, "minute": 15, "days_of_week": [1, 7]}, 4, [[1, 7], [8, 15]]),
    ({"type": "monthly", "hour": 9, "days_of_month": [1, 31]}, 18, [[1, 31], [9, 0]]),
    ({"type": "yearly", "hour": 10, "month": 2, "day": 29}, 19, [[None, 2, 29], [10, 0]]),
    ({"type": "hourly", "minute": 45}, 17, [45]),
    ({"type": "one_off", "unix_seconds": 2000000000}, 20, [["2000000000", 0]]),
])
def test_create_native_trigger_payload_and_read_back(trigger, index, message):
    client = NativeSchedules()
    result = asyncio.run(create(client, trigger=trigger))
    assert result.ok and result.data["verified"] is True
    assert result.meta.verification_status == "verified_created"
    written = client.writes[0][1][0]
    assert written[index] == message
    assert written[6] == "UTC" and written[9] == 1
    assert written[24] == 1
    assert [key for key, _payload in client.calls] == ["scheduled.remy_create", "scheduled.remy_get"]


@pytest.mark.parametrize("enabled,status", [(False, 2), (True, 1)])
def test_pause_resume_read_modify_write_preserves_unknown_fields(enabled, status):
    client = NativeSchedules()
    client.row[9] = 1 if not enabled else 2
    original = copy.deepcopy(client.row)
    result = asyncio.run(update_remy_schedule_result(client, action_id="remy_fixture", enabled=enabled))
    assert result.ok and result.data["verified"] is True
    written = client.writes[0][1][0]
    original[9] = status
    assert written == original
    assert [key for key, _payload in client.calls] == ["scheduled.remy_get", "scheduled.remy_update", "scheduled.remy_get"]


def test_update_content_and_frequency_preserves_source_project_and_context():
    client = NativeSchedules()
    result = asyncio.run(update_remy_schedule_result(
        client, action_id="remy_fixture", title="Renamed", instructions="Updated instructions",
        timezone_name="Asia/Shanghai", trigger={"type": "weekly", "hour": 18, "days_of_week": [2, 4]},
    ))
    assert result.ok and result.data["verified"] is True
    written = client.writes[0][1][0]
    assert written[3] is None and written[4] == [[2, 4], [18, 0]]
    assert [written[i] for i in (8, 11, 24, 26, 28)] == [client.before[i] for i in (8, 11, 24, 26, 28)]


@pytest.mark.parametrize("field,index,lost", [
    ("title", 1, ""), ("instructions", 7, ""), ("timezone_name", 6, ""),
    ("schedule_status_id", 9, 2), ("trigger", 3, None), ("context", 8, None),
    ("source_chat_id", 11, ""), ("project_id", 26, None),
])
def test_update_cannot_verify_when_readback_loses_an_omitted_business_field(field, index, lost):
    class DropsBusinessField(NativeSchedules):
        async def _batch_execute(self, requests, *, source_path, close_on_error):
            observed = await super()._batch_execute(requests, source_path=source_path, close_on_error=close_on_error)
            if self.writes and requests[0].rpcid == get_contract("scheduled.remy_get").rpc_id:
                body = copy.deepcopy(self.row)
                body[index] = lost
                return response("scheduled.remy_get", [body])
            return observed

    client = DropsBusinessField()
    changes = {"instructions": "Changed instructions"} if field == "title" else {"title": "Renamed"}
    result = asyncio.run(update_remy_schedule_result(client, action_id="remy_fixture", **changes))
    assert result.ok and result.operation_state == "accepted" and result.data["verified"] is False
    assert field in result.data["mismatched_fields"]
    assert len(client.writes) == 1
    assert client.writes[0][1][0][index] == client.before[index]
    assert [key for key, _payload in client.calls] == ["scheduled.remy_get", "scheduled.remy_update", "scheduled.remy_get"]


@pytest.mark.parametrize("action", ["create", "update", "delete"])
@pytest.mark.parametrize("case", ["http_error", "rejected", "empty", "changed_shape", "ambiguous", "timeout"])
def test_bad_ack_cannot_claim_success_or_replay_mutation(action, case):
    client = NativeSchedules(ack=case)
    awaitable = create(client) if action == "create" else (
        update_remy_schedule_result(client, action_id="remy_fixture", enabled=False) if action == "update"
        else delete_remy_schedule_result(client, "remy_fixture")
    )
    result = asyncio.run(awaitable)
    assert result.ok is False
    assert len(client.writes) == 1
    assert client.calls[-1][0] == "scheduled.remy_" + action
    if case == "timeout":
        assert result.meta.operation_state is OperationState.TIMED_OUT and result.retryable is False


@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize("case", ["empty", "mismatch", "stale", "http_error", "bad_shape"])
def test_mutation_echo_is_not_independent_matching_read_back(action, case):
    client = NativeSchedules(read_back=case)
    result = asyncio.run(create(client) if action == "create" else update_remy_schedule_result(
        client, action_id="remy_fixture", title="New title", enabled=False,
    ))
    assert result.ok is True and result.data["accepted"] is True and result.data["verified"] is False
    assert result.meta.operation_state is OperationState.ACCEPTED
    assert len(client.writes) == 1


@pytest.mark.parametrize("case,verified", [("valid", True), ("still_present", False), ("registry_contradiction", False),
                                          ("http_error", False), ("bad_shape", False)])
def test_delete_keeps_registry_and_exact_id_contradictions_unverified(case, verified):
    client = NativeSchedules(read_back=case)
    result = asyncio.run(delete_remy_schedule_result(client, "remy_fixture"))
    assert result.ok and result.data["verified"] is verified
    assert [key for key, _payload in client.calls] == ["scheduled.remy_delete", "scheduled.remy_registry", "scheduled.remy_get"]
    assert len(client.writes) == 1


class DeleteEvidence(NativeSchedules):
    def __init__(self, *, registry="nonempty", by_id="rejected"):
        super().__init__()
        self.registry, self.by_id = registry, by_id

    async def _batch_execute(self, requests, *, source_path, close_on_error):
        observed = await super()._batch_execute(requests, source_path=source_path, close_on_error=close_on_error)
        if requests[0].rpcid == get_contract("scheduled.remy_registry").rpc_id:
            rows = []
            if self.registry != "empty":
                for status in (1, 2, 3):
                    row = schedule_row()
                    row[0], row[9] = f"other_fixture_{status}", status
                    rows.append(row)
            body = [rows, False, 1]
            if self.registry == "continuation":
                body.append("unobserved_continuation_fixture")
            elif self.registry == "duplicate":
                body[0].append(copy.deepcopy(rows[0]))
            elif self.registry == "bad_shape":
                body = {"changed": True}
            observed = response("scheduled.remy_registry", body,
                                status=503 if self.registry == "http_error" else 200,
                                reject=13 if self.registry == "rejected" else None)
            if self.registry == "missing":
                observed.text = "[]"
            elif self.registry == "ambiguous":
                observed.text = json.dumps(json.loads(observed.text) * 2)
        elif requests[0].rpcid == get_contract("scheduled.remy_get").rpc_id:
            if self.by_id == "timeout":
                raise TimeoutError("Fixture read unavailable")
            if self.by_id == "present":
                return response("scheduled.remy_get", [self.before])
            if self.by_id == "rejected":
                observed = response("scheduled.remy_get", None, reject=13)
                parts = json.loads(observed.text)
                parts[0][2] = None
                observed.text = json.dumps(parts)
            elif self.by_id == "bad_shape":
                return response("scheduled.remy_get", {"changed": True})
        return observed


@pytest.mark.parametrize("registry", ["empty", "nonempty"])
@pytest.mark.parametrize("by_id", ["rejected", "timeout", "bad_shape"])
def test_complete_registry_absence_verifies_delete_when_exact_read_is_unavailable(registry, by_id):
    client = DeleteEvidence(registry=registry, by_id=by_id)
    result = asyncio.run(delete_remy_schedule_result(client, "remy_fixture"))
    assert result.operation_state == "completed" and result.data["verified"] is True
    assert result.meta.verification_status == "verified_deleted_registry_absence"
    assert result.data["verification_evidence"] == "complete_registry_absence"
    assert result.data["by_id_observation"] == "unavailable"
    assert result.meta.details["registry"]["complete"] is True
    if by_id == "rejected":
        assert result.meta.details["read_back"]["reject_code"] == 13
        assert result.meta.details["read_back"]["body_present"] is False
        assert result.meta.details["read_back"]["parser_status"] == "rejected"
    assert len(client.writes) == 1
    assert [key for key, _payload in client.calls] == ["scheduled.remy_delete", "scheduled.remy_registry", "scheduled.remy_get"]


@pytest.mark.parametrize("registry", ["continuation", "duplicate", "bad_shape", "http_error", "rejected", "missing", "ambiguous"])
def test_incomplete_or_invalid_registry_cannot_prove_deleted_from_a_rejected_exact_read(registry):
    client = DeleteEvidence(registry=registry)
    result = asyncio.run(delete_remy_schedule_result(client, "remy_fixture"))
    assert result.operation_state == "accepted" and result.data["verified"] is False
    assert result.meta.details["registry"]["complete"] is False
    assert len(client.writes) == 1


def test_complete_registry_absence_cannot_override_a_successful_exact_read_of_the_schedule():
    client = DeleteEvidence(by_id="present")
    result = asyncio.run(delete_remy_schedule_result(client, "remy_fixture"))
    assert result.operation_state == "accepted" and result.data["verified"] is False
    assert result.data["by_id_observation"] == "present"
    assert result.meta.details["registry"]["complete"] is True
    assert len(client.writes) == 1


def test_native_delete_contract_names_the_actual_registry_absence_proof():
    assert get_contract("scheduled.remy_delete").verification_strategy == "independent_complete_remy_registry_absence"


@pytest.mark.parametrize("changes", [{}, {"enabled": "false"}, {"title": " "}, {"timezone_name": "invalid/fixture"},
                                     {"trigger": {"type": "daily", "hour": 24}},
                                     {"trigger": {"type": "weekly", "hour": 9, "days_of_week": []}},
                                     {"trigger": {"type": "hourly", "minute": 60}},
                                     {"trigger": {"type": "yearly", "hour": 9, "month": 2, "day": 30}},
                                     {"trigger": {"type": "daily", "hour": 9, "ignored": True}}])
def test_invalid_updates_fail_before_any_private_read_or_mutation(changes):
    client = NativeSchedules()
    result = asyncio.run(update_remy_schedule_result(client, action_id="remy_fixture", **changes))
    assert result.error_code == "INVALID_ARGUMENT"
    assert client.calls == []


@pytest.mark.parametrize("key", ["scheduled.remy_create", "scheduled.remy_update"])
@pytest.mark.parametrize("case", ["success", "empty", "rejected", "changed_shape"])
def test_native_write_contract_has_four_response_shape_cases(key, case):
    fixtures = json.loads((Path(__file__).parent / "fixtures/rpc_management_cases.json").read_text())
    fixture = fixtures["remy_schedule_get"]["cases"][case]
    assert parse_contract_body(key, fixture["body"], reject_code=fixture.get("reject_code")).status == case
