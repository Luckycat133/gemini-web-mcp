"""Public MCP mutation contract over deterministic real metadata transport adapters."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import Client

from src.adapters.mcp_sdk import MCPServer
from src.infrastructure.rpc_contracts import get_contract
from src.onboarding import domain_result_from_call
from src.tools import chat_management as tools


class MetadataClient:
    def __init__(self):
        self.title = "Fixture"
        self.pinned = False
        self.mutations = []
        self.unknown_post_pin = False
        self.response_lost = False

    async def _batch_execute(self, requests, **_kwargs):
        rpc = requests[0]
        body = json.loads(rpc.payload)
        if rpc.rpcid == get_contract("history.rename").rpc_id:
            self.mutations.append(body)
            row = body[2]
            if body[1] == [["title"]]:
                self.title = row[1]
            else:
                self.pinned = row[2]
            if self.response_lost:
                raise TimeoutError("Synthetic mutation response loss")
            result = [None, ["c_fixture", self.title, self.pinned]]
        else:
            included = body[2][0] == self.pinned
            pinned = None if self.mutations and self.unknown_post_pin else self.pinned
            result = [None, None, [["c_fixture", self.title, pinned]] if included else []]
        text = json.dumps([["wrb.fr", rpc.rpcid, json.dumps(result), None, None, None, "generic"]])
        return SimpleNamespace(status_code=200, text=text)


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-chat-control-account")
    client = MetadataClient()
    monkeypatch.setattr(tools, "get_gemini_client", lambda: client)
    monkeypatch.setattr(tools, "initialize_client", AsyncMock())
    server = MCPServer("chat-control")
    tools.register_chat_management_tools(server)
    return server, client


def call(server, **arguments):
    async def run():
        async with Client(server, mode="auto", cache=None) as session:
            return domain_result_from_call(await session.call_tool("gemini_manage_chat", arguments))
    return asyncio.run(run())


@pytest.mark.parametrize("action", ["rename", "pin", "unpin"])
def test_public_chat_mutations_are_verified_and_repeated_state_is_a_noop(runtime, action):
    server, client = runtime
    if action == "unpin":
        client.pinned = True
    args = {"action": action, "chat_id": "c_fixture"}
    if action == "rename":
        args["title"] = "Renamed"
    first = call(server, **args)
    second = call(server, **args)
    assert first["ok"] is True and first["data"]["verified"] is True
    assert first["data"]["changed"] is True and first["data"]["mutation_sent"] is True
    assert second["ok"] is True and second["data"]["changed"] is False
    assert second["data"]["mutation_sent"] is False and len(client.mutations) == 1


def test_public_guard_conflict_does_not_dispatch_a_mutation(runtime):
    server, client = runtime
    result = call(server, action="rename", chat_id="c_fixture", title="New", expected_title="Stale")
    assert result["ok"] is False and result["meta"]["verification_status"] == "expected_title_conflict"
    assert client.mutations == []


def test_unknown_readback_never_claims_that_a_sent_mutation_changed_the_chat(runtime):
    server, client = runtime
    client.unknown_post_pin = True
    result = call(server, action="pin", chat_id="c_fixture")
    assert result["ok"] is False and result["data"]["verified"] is False
    assert result["data"]["changed"] is None and result["data"]["mutation_sent"] is True
    assert len(client.mutations) == 1


def test_lost_mutation_reply_is_verified_without_replay(runtime):
    server, client = runtime
    client.response_lost = True
    result = call(server, action="rename", chat_id="c_fixture", title="Renamed")
    assert result["ok"] is True and result["data"]["changed"] is True
    assert len(client.mutations) == 1


def test_invalid_target_is_rejected_before_initialization(runtime, monkeypatch):
    server, client = runtime
    initializer = AsyncMock(side_effect=AssertionError("unnecessary initialization"))
    monkeypatch.setattr(tools, "initialize_client", initializer)
    result = call(server, action="pin", chat_id="not-an-exact-chat-id")
    assert result["ok"] is False and result["error"]["code"] == "INVALID_ARGUMENT"
    initializer.assert_not_awaited()
    assert client.mutations == []


def test_public_control_can_bind_automatic_authentication_before_the_first_mutation(runtime, monkeypatch):
    server, client = runtime
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    async def initialize():
        monkeypatch.setenv("GEMINI_PSID", "synthetic-discovered-browser-account")
    monkeypatch.setattr(tools, "initialize_client", initialize)
    result = call(server, action="pin", chat_id="c_fixture")
    assert result["ok"] is True and len(client.mutations) == 1
