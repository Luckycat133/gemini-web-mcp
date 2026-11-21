"""The preference adapter preserves scoped actions and structured evidence."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp import Client
import pytest

from src.adapters.mcp_sdk import MCPServer
from src.domain import DomainResult
from src.domain.preferences import PermissionRecord
from src.services.preferences import PreferencesDependencies, PreferencesService
from src.onboarding import domain_result_from_call
from src.tools import preferences


@pytest.mark.parametrize("action,arguments,method,expected", [
    ("get_browser", {}, "get_browser", {}),
    ("list_permissions", {"limit": 5, "offset": 2}, "list_permissions", {"limit": 5, "offset": 2}),
    ("set_browser", {"browser": "remote_only", "permission": "always_ask", "expected_revision": "a" * 64},
     "set_browser", {"browser": "remote_only", "permission": "always_ask", "expected_revision": "a" * 64}),
    ("set_permission", {"permission_id": "b" * 64, "permission": "always_ask"},
     "set_permission", {"permission_id": "b" * 64, "permission": "always_ask", "expected_revision": None}),
])
def test_adapter_routes_only_selected_action(monkeypatch, action, arguments, method, expected):
    handlers = {name: AsyncMock(return_value=DomainResult.success({"observed": True},
                                                                verification_status="verified"))
                for name in ("get_browser", "list_permissions", "set_browser", "set_permission")}
    monkeypatch.setattr(preferences, "_service", lambda: SimpleNamespace(**handlers))
    server = MCPServer("preference-adapter-test")
    preferences.register_preference_tools(server)

    async def call():
        async with Client(server, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_preferences", {"action": action, **arguments})

    result = domain_result_from_call(asyncio.run(call()))
    assert result["ok"] and result["data"] == {"observed": True}
    handlers[method].assert_awaited_once_with(**expected)
    assert sum(handler.await_count for handler in handlers.values()) == 1


def test_read_cannot_silently_ignore_write_arguments(monkeypatch):
    monkeypatch.setattr(preferences, "_service", lambda: pytest.fail("Invalid request constructed a service"))
    server = MCPServer("preference-invalid-test")
    preferences.register_preference_tools(server)

    async def call():
        async with Client(server, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_preferences", {"action": "get_browser", "permission": "always_allow"})

    result = domain_result_from_call(asyncio.run(call()))
    assert not result["ok"] and result["error"]["code"] == "INVALID_ARGUMENT"


def test_preference_schema_and_effect_annotations():
    server = MCPServer("preference-schema-test")
    preferences.register_preference_tools(server)

    async def inspect():
        async with Client(server, mode="auto", cache=None) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(inspect())
    assert len(tools) == 1 and tools[0].name == "gemini_preferences"
    tool = tools[0]
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    assert tool.annotations.open_world_hint is True
    assert set(tool.input_schema["properties"]["action"]["enum"]) == {
        "get_browser", "set_browser", "list_permissions", "set_permission"}
    assert not {"cookies", "grant_id", "native_permission_id", "auto_approve"} & tool.input_schema["properties"].keys()


def test_public_read_exposes_native_preference_as_observed_without_effective_permission_claim(monkeypatch):
    native = [['web_browser_agent', 'actuate']] + [None] * 8 + [[['remy_choice_id', 'vm']]]
    record = PermissionRecord(native, 'synthetic-private-grant', 2, None, 1, 'Browser', None)
    service = PreferencesService(PreferencesDependencies(
        client_provider=lambda: object(), client_initializer=AsyncMock(),
        read_permissions=AsyncMock(return_value=(record,)), read_catalog=AsyncMock(return_value=()),
        write_permission=AsyncMock(side_effect=AssertionError('must not write')),
        account_key_provider=lambda: 'synthetic-private-account'))
    monkeypatch.setattr(preferences, '_service', lambda: service)
    server = MCPServer('preference-readback-scope')
    preferences.register_preference_tools(server)

    async def call():
        async with Client(server, mode='auto', cache=None) as client:
            return await client.call_tool('gemini_preferences', {'action': 'get_browser'})

    result = domain_result_from_call(asyncio.run(call()))
    assert result['ok'] and result['data']['permission_preference'] == 'always_allow'
    assert result['data']['effective_permission'] == 'unknown'
    assert result['data']['behavior_verified'] is False
    assert result['data']['verification_scope'] == 'stored_preference'
    assert 'synthetic-private' not in str(result)
