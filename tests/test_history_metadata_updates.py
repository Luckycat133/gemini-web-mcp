"""History writes preserve field masks and require fresh independent evidence."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import TypeAdapter, ValidationError
import pytest

from src.adapters.mcp_sdk import MCPServer
from src.domain.account_requests import HistoryRequest
from src.infrastructure.rpc_contracts import get_contract
from src.services.account_facade import AccountFacadeService
from src.services.history_mutations import update_chat_metadata_result
from src.tools import register_tools
import src.skill_server as compact
import src.tools.manage as primary


def response(rpc, body, *, status=200, reject=None):
    return SimpleNamespace(status_code=status, text=json.dumps([
        ["wrb.fr", rpc, json.dumps(body), None, None, [reject] if reject is not None else None, "generic"],
    ]))


class Client:
    def __init__(self, *, ack_status=200, reject=None, ack_body=None, apply=True, read_error=False):
        self.items = {"c_target": ["c_target", "Original", False], "c_other": ["c_other", "Unrelated", True]}
        self.calls = []
        self.ack_status, self.reject, self.ack_body = ack_status, reject, ack_body
        self.apply, self.read_error = apply, read_error

    async def _batch_execute(self, requests, **kwargs):
        rpc = requests[0]
        payload = json.loads(rpc.payload)
        self.calls.append((rpc.rpcid, payload))
        if rpc.rpcid == "MUAZcd":
            fields, row = payload[1][0], payload[2]
            echoed = list(self.items[row[0]])
            for field in fields:
                index = {"title": 1, "pinned": 2}[field]
                echoed[index] = row[index]
                if self.apply:
                    self.items[row[0]][index] = row[index]
            body = self.ack_body if self.ack_body is not None else [None, echoed]
            return response(rpc.rpcid, body, status=self.ack_status, reject=self.reject)
        assert rpc.rpcid == "MaZiqc"
        if self.read_error:
            raise TimeoutError("private response text must not be returned")
        pinned = payload[2][0]
        rows = [row for row in self.items.values() if row[2] is pinned]
        return response(rpc.rpcid, [None, None, rows])


@pytest.mark.parametrize("changes,mask,row", [
    ({"title": "Renamed"}, ["title"], ["c_target", "Renamed"]),
    ({"pinned": True}, ["pinned"], ["c_target", None, True]),
    ({"pinned": False}, ["pinned"], ["c_target", None, False]),
    ({"title": "Renamed", "pinned": False}, ["title", "pinned"], ["c_target", "Renamed", False]),
])
def test_update_field_mask_preserves_omitted_metadata(changes, mask, row):
    client = Client()
    result = asyncio.run(update_chat_metadata_result(client, "c_target", **changes))
    assert result.ok and result.meta.verification_status == "verified_updated"
    assert result.data["verified"] is True
    assert client.calls[0] == ("MUAZcd", [None, [mask], row])
    assert [call[1][2][0] for call in client.calls[1:]] == [True, False]
    assert client.items["c_other"] == ["c_other", "Unrelated", True]
    if "title" not in changes:
        assert client.items["c_target"][1] == "Original"
    if "pinned" not in changes:
        assert client.items["c_target"][2] is False


@pytest.mark.parametrize("changes", [{}, {"title": ""}, {"title": "  "}, {"pinned": 1}, {"pinned": "false"}])
def test_invalid_updates_do_not_reach_transport(changes):
    client = Client()
    result = asyncio.run(update_chat_metadata_result(client, "c_target", **changes))
    assert result.error_code == "INVALID_ARGUMENT"
    assert not client.calls


def test_an_echo_is_not_independent_update_proof():
    client = Client(apply=False)
    result = asyncio.run(update_chat_metadata_result(client, "c_target", title="Renamed", pinned=True))
    assert result.ok and result.meta.operation_state.value == "accepted"
    assert result.meta.verification_status == "read_back_mismatch"
    assert result.data["verified"] is False
    assert set(result.data["mismatched_fields"]) == {"title", "is_pinned"}


@pytest.mark.parametrize("options", [
    {"ack_status": 503}, {"reject": 7}, {"ack_body": {"changed": True}},
    {"ack_body": [None, ["c_wrong", "Renamed", True]]},
    {"ack_body": [None, ["c_target", "Renamed", "true"]]},
    {"ack_body": []}, {"ack_body": [None, None]},
])
def test_invalid_mutation_acknowledgement_cannot_complete(options):
    client = Client(**options)
    result = asyncio.run(update_chat_metadata_result(client, "c_target", title="Renamed"))
    assert not result.ok
    assert result.meta.operation_state.value == "failed"
    assert len(client.calls) == 1


def test_read_back_exception_preserves_acceptance_without_exposing_private_error():
    client = Client(read_error=True)
    result = asyncio.run(update_chat_metadata_result(client, "c_target", title="Renamed"))
    assert result.ok and result.meta.operation_state.value == "accepted"
    assert result.meta.verification_status == "read_back_unavailable"
    assert result.data["accepted"] is True and result.data["verified"] is False
    assert "private response" not in str(result.to_dict())


def test_missing_target_in_bounded_read_back_is_not_reported_deleted_or_updated():
    client = Client()
    original_batch = client._batch_execute

    async def batch(requests, **kwargs):
        if requests[0].rpcid == "MaZiqc":
            return response("MaZiqc", [None, None, []])
        return await original_batch(requests, **kwargs)

    client._batch_execute = batch
    result = asyncio.run(update_chat_metadata_result(client, "c_target", title="Renamed"))
    assert result.meta.verification_status == "target_not_observed"
    assert result.meta.operation_state.value == "accepted"


@pytest.mark.parametrize("bad_row", [["c_target", "Original", "false"], ["c_target", {"changed": True}, True]])
def test_malformed_read_back_fields_cannot_verify_an_update(bad_row):
    client = Client()
    original_batch = client._batch_execute

    async def batch(requests, **kwargs):
        if requests[0].rpcid == "MaZiqc":
            return response("MaZiqc", [None, None, [bad_row]])
        return await original_batch(requests, **kwargs)

    client._batch_execute = batch
    result = asyncio.run(update_chat_metadata_result(client, "c_target", pinned=True))
    assert result.data["verified"] is False
    assert result.meta.operation_state.value == "accepted"


def test_conflicting_metadata_between_buckets_cannot_verify_an_update():
    client = Client()
    original_batch = client._batch_execute

    async def batch(requests, **kwargs):
        if requests[0].rpcid == "MaZiqc":
            pinned = json.loads(requests[0].payload)[2][0]
            return response("MaZiqc", [None, None, [["c_target", "Original", pinned]]])
        return await original_batch(requests, **kwargs)

    client._batch_execute = batch
    result = asyncio.run(update_chat_metadata_result(client, "c_target", pinned=True))
    assert result.data["verified"] is False
    assert result.meta.operation_state.value == "accepted"
    assert result.meta.details["read_back"]["conflicting_record_count"] == 1


def test_mutation_is_a_registered_preview_contract_with_read_back():
    contract = get_contract("history.update")
    assert contract.mode == "mutation"
    assert contract.verification_strategy == "independent_history_metadata_read_back"
    with pytest.raises(ValueError):
        contract.build_payload(chat_id="c_target")


@pytest.mark.parametrize("changes", [{"title": "Renamed"}, {"pinned": False}, {"title": "Renamed", "pinned": True}])
def test_three_surfaces_share_verified_update_state(monkeypatch, changes):
    async def run():
        values = []
        for surface in ("primary", "compact", "focused"):
            client = Client()
            if surface == "focused":
                service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
                result = await service.execute("history", TypeAdapter(HistoryRequest).validate_python(
                    {"action": "update", "chat_id": "c_target", **changes},
                ))
                values.append(result.to_dict())
                assert result.meta.details["effect"] == "mutation"
                continue
            module = primary if surface == "primary" else compact
            monkeypatch.setattr(module, "get_gemini_client", lambda: client)
            monkeypatch.setattr(module, "initialize_client", AsyncMock())
            if surface == "primary":
                server = MCPServer("history-update")
                primary.register_manage_tools(server, layers=["history-update"])
                tool = next(tool for tool in await server.list_tools() if tool.name == "gemini_update_chat")
                assert tool.annotations.read_only_hint is False
                assert tool.annotations.destructive_hint is False
                content = (await server.call_tool(tool.name, {"chat_id": "c_target", **changes})).content
            else:
                content = await compact.history(action="update", chat_id="c_target", **changes)
            values.append(content[0].meta["domain_result"])
        assert all(value["ok"] and value["meta"]["verification_status"] == "verified_updated" for value in values)
        assert values[0]["data"] == values[1]["data"] == values[2]["data"]

    asyncio.run(run())


def test_history_organize_enables_updates_without_deletion():
    server = MCPServer("history-organize")
    register_tools(server, ["history-organize"])
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "gemini_update_chat" in tools
    assert "gemini_delete_chat" not in tools
    assert "gemini_cleanup_test_artifacts" not in tools


def test_focused_update_requires_a_change_and_strict_boolean():
    adapter = TypeAdapter(HistoryRequest)
    assert adapter.validate_python({"action": "update", "chat_id": "c_target", "pinned": False}).pinned is False
    for change in ({}, {"title": "  "}, {"pinned": "false"}):
        with pytest.raises(ValidationError):
            adapter.validate_python({"action": "update", "chat_id": "c_target", **change})
