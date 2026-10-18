"""Current schedule IDs stay on their explicit read-only backend across adapters."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.skill_server as compact
from src.adapters.mcp_sdk import MCPServer
from src.domain.account_requests import ScheduledGet, ScheduledList
from src.infrastructure.rpc_contracts import get_contract
from src.services.account_facade import AccountFacadeService
from src.tools import manage


def _row():
    return ["native-schedule", "Daily briefing", True, [[9, 15]], None, None,
            "Asia/Shanghai", "Summarize project updates", "", 1]


class CurrentScheduleClient:
    def __init__(self):
        self.calls = []

    async def _batch_execute(self, requests, **_kwargs):
        request = requests[0]
        self.calls.append(request)
        if request.rpcid == get_contract("scheduled.remy_registry").rpc_id:
            assert json.loads(request.payload) == []
            body = [[_row()], False, 1]
        elif request.rpcid == get_contract("scheduled.remy_get").rpc_id:
            assert json.loads(request.payload) == ["native-schedule"]
            body = [_row()]
        else:
            raise AssertionError("A native schedule must not use a legacy RPC")
        return SimpleNamespace(status_code=200, text=json.dumps([
            ["wrb.fr", request.rpcid, json.dumps(body), None, None, None, "generic"],
        ]))


async def _call(surface, action, client):
    if surface == "focused":
        request = ScheduledList(action="list", backend="remy") if action == "list" else ScheduledGet(
            action="get", action_id="native-schedule", backend="remy",
        )
        service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
        result = await service.execute("scheduled", request)
        assert result.ok
        return result.data
    arguments = {"action": action, "backend": "remy"}
    if action == "get":
        arguments["action_id"] = "native-schedule"
    if surface == "compact":
        content = await compact.scheduled(**arguments)
        domain = content[0].meta["domain_result"]
        assert domain["ok"]
        return domain["data"]
    arguments.pop("action")
    arguments["response_format"] = "json"
    server = MCPServer("native schedule surfaces")
    manage.register_manage_tools(server, layers=["all"])
    name = "gemini_list_scheduled_actions" if action == "list" else "gemini_get_scheduled_action"
    result = await server.call_tool(name, arguments)
    payload = json.loads(result.content[0].text)
    return payload["results"][0] if action == "list" else payload


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
@pytest.mark.parametrize("action", ["list", "get"])
def test_native_schedule_reads_use_the_selected_backend(monkeypatch, surface, action):
    client = CurrentScheduleClient()
    for module in (compact, manage):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", AsyncMock())
    result = asyncio.run(_call(surface, action, client))
    assert result["backend"] == "remy"
    item = result["items"][0] if action == "list" else result["item"]
    assert item["id"] == "native-schedule" and item["backend"] == "remy"
    assert item["schedule_status_id"] == 1 and item["task_state_id"] is None
    assert result["diagnostic"]["read_back_valid"] is True
    assert len(client.calls) == 1


@pytest.mark.parametrize("surface,action", [("primary", "delete"), ("compact", "delete"), ("compact", "create")])
def test_native_mutations_are_rejected_before_client_or_legacy_rpc(monkeypatch, surface, action):
    provider = AsyncMock(side_effect=AssertionError("Native mutation must not prepare a client"))
    monkeypatch.setattr(compact, "get_gemini_client", provider)
    monkeypatch.setattr(manage, "get_gemini_client", provider)

    async def run():
        if surface == "compact":
            return await compact.scheduled(action=action, action_id="native-schedule", backend="remy")
        server = MCPServer("native mutation boundary")
        manage.register_manage_tools(server, layers=["all"])
        return (await server.call_tool("gemini_delete_scheduled_action", {
            "action_id": "native-schedule", "backend": "remy",
        })).content

    content = asyncio.run(run())
    result = content[0].meta["domain_result"]
    assert result["ok"] is False and result["error"]["code"] == "INVALID_ARGUMENT"
    assert provider.call_count == 0
