"""Typed native account actions keep their shared behavior across MCP adapters."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import Client
from pydantic import TypeAdapter, ValidationError

import src.services.account_facade as facade
import src.skill_server as compact
import src.surfaces.account as focused
import src.tools.manage as primary
from src.adapters.mcp_sdk import MCPServer
from src.domain.account_requests import AccountRequest, NotebooksRequest, ScheduledRequest
from src.domain import DomainResult, OperationState
from src.onboarding import domain_result_from_call
from src.services.account_facade import AccountFacadeService
from tests.test_native_scheduled_mutations import NativeSchedules


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_native_schedule_create_survives_typed_mcp_adapters(monkeypatch, surface, mode):
    remote = NativeSchedules()
    service = AccountFacadeService(client_provider=lambda: remote, initializer=AsyncMock())
    module = {"primary": primary, "compact": compact, "focused": focused}[surface]
    monkeypatch.setattr(module, "get_account_facade_service", lambda: service)
    if surface == "primary":
        server = MCPServer("native-primary-fixture")
        primary.register_manage_tools(server)
    else:
        server = module.mcp

    async def run():
        async with Client(server, cache=None, mode=mode) as connection:
            return domain_result_from_call(await connection.call_tool(
                "scheduled" if surface == "compact" else "gemini_scheduled",
                {"request": {"action": "create", "title": "Created", "instructions": "Instructions",
                             "trigger": {"type": "weekly", "days_of_week": [1, 3], "hour": 9, "minute": 15},
                             "enabled": False}},
            ))

    result = asyncio.run(run())
    assert result["ok"] and result["meta"]["verification_status"] == "verified_created"
    assert result["data"]["backend"] == "remy" and result["data"]["item"]["enabled"] is False
    assert remote.writes[0][0] == "scheduled.remy_create"


def test_native_resource_mutation_exception_preserves_only_ids_and_prevents_retry(monkeypatch):
    mutation = AsyncMock(side_effect=TimeoutError("private prompt or credentials must not escape"))
    monkeypatch.setattr(facade, "create_public_link_result", mutation)
    service = AccountFacadeService(client_provider=lambda: SimpleNamespace(), initializer=AsyncMock())
    request = TypeAdapter(AccountRequest).validate_python({
        "action": "sharing", "request": {"action": "create", "chat_id": "c_fixture", "response_id": "rc_fixture", "title": "Private title"},
    })
    result = asyncio.run(service.execute("account", request)).to_dict()
    assert result["ok"] is False and result["error"]["retryable"] is False
    assert result["data"] == {"chat_id": "c_fixture"}
    assert result["meta"]["details"]["effect"] == "mutation"
    assert "private prompt" not in str(result) and "Private title" not in str(result)
    assert mutation.await_count == 1


@pytest.mark.parametrize("payload", [
    {"action": "skills", "request": {"action": "update", "skill_id": "skill_fixture"}},
    {"action": "library_assets", "request": {"action": "delete"}},
    {"action": "canvas", "request": {"action": "edit", "chat_id": "c_fixture", "prompt": "guess"}},
])
def test_new_resource_requests_require_action_specific_targets(payload):
    with pytest.raises(ValidationError):
        TypeAdapter(AccountRequest).validate_python(payload)


def test_notebook_source_requires_exactly_one_content_kind():
    with pytest.raises(ValidationError, match="exactly one"):
        TypeAdapter(NotebooksRequest).validate_python({
            "action": "add_source", "notebook_id": "notebook_fixture", "display_name": "Source",
            "file_path": "/tmp/fixture.txt", "text": "ambiguous",
        })


def test_native_schedule_backend_and_boolean_are_explicit():
    with pytest.raises(ValidationError):
        TypeAdapter(ScheduledRequest).validate_python({
            "action": "create", "backend": "legacy", "title": "Fixture", "instructions": "Fixture",
            "trigger": {"type": "daily", "hour": 9},
        })
    with pytest.raises(ValidationError):
        TypeAdapter(ScheduledRequest).validate_python({
            "action": "update", "action_id": "schedule_fixture", "enabled": "false",
        })


def test_native_resource_accepted_result_does_not_become_completed(monkeypatch):
    monkeypatch.setattr(facade, "get_library_asset_result", AsyncMock(return_value=DomainResult.success(
        {"item": None, "complete": False}, operation_state=OperationState.PARTIAL,
        verification_status="target_not_observed",
    )))
    service = AccountFacadeService(client_provider=lambda: SimpleNamespace(), initializer=AsyncMock())
    request = TypeAdapter(AccountRequest).validate_python({
        "action": "library_assets", "request": {"action": "get", "asset_id": "fixture"},
    })
    result = asyncio.run(service.execute("account", request))
    assert result.ok and result.meta.operation_state is OperationState.PARTIAL
