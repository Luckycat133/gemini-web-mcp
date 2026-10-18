"""Inventory adapters require positive, matching offline RPC evidence."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter

from src.adapters.mcp_sdk import MCPServer
from src.domain.account_requests import AccountRequest, NotebooksRequest, ScheduledRequest
from src.infrastructure.rpc_contracts import get_contract
from src.services.account import account_contract_result, account_probe_status, account_read_result
from src.services.account_facade import AccountFacadeService
from src.services import account_facade
import src.skill_server as compact
from src.tools import manage


CASES = json.loads((Path(__file__).parent / "fixtures/rpc_management_cases.json").read_text())
INVENTORIES = [
    ("sharing.public_links", "gemini_list_public_links", "account", {"action": "links"}),
    ("library.locale_capabilities", "gemini_list_library_capabilities", "account", {"action": "library"}),
    ("notebooks.list", "gemini_list_notebooks", "notebooks", {"action": "list"}),
    ("scheduled.registry", "gemini_list_scheduled_actions", "scheduled", {"action": "list"}),
    ("tool_modes.status", "gemini_get_tool_mode_status", "account", {"action": "modes"}),
    ("usage.quota", "gemini_get_usage_limits", "account", {"action": "usage", "scope": "quota"}),
    ("usage.model_state", "gemini_get_usage_limits", "account", {"action": "usage", "scope": "model_state"}),
]
INVALID = ["http", "missing_status", "missing", "wrong_rpc", "ambiguous", "ambiguous_null", "malformed", "changed_shape", "rejected"]


def _response(key, case):
    contract = get_contract(key)
    body = CASES[contract.parser]["cases"]["empty" if case == "empty" else "success"]["body"]
    rpc_id = "unrelated" if case == "wrong_rpc" else contract.rpc_id
    if case == "changed_shape":
        body = {"unsupported": "shape"}
    part = ["wrb.fr", rpc_id, json.dumps(body), None, None, [7] if case == "rejected" else None]
    parts = [] if case == "missing" else [part, part] if case == "ambiguous" else [part]
    if case == "ambiguous_null":
        parts.append(["wrb.fr", rpc_id, None, None, None, None])
    response = SimpleNamespace(status_code=503 if case == "http" else 200,
                               text="invalid JSON" if case == "malformed" else json.dumps(parts))
    if case == "missing_status":
        del response.status_code
    return response


def _request(facade, arguments):
    request_type = {"account": AccountRequest, "notebooks": NotebooksRequest, "scheduled": ScheduledRequest}[facade]
    return TypeAdapter(request_type).validate_python(arguments)


def _domain(content):
    return content[0].meta["domain_result"]


async def _invoke(surface, key, primary_tool, facade, arguments, client):
    if surface == "focused":
        service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
        return (await service.execute(facade, _request(facade, arguments))).to_dict()
    if surface == "compact":
        action = facade if facade != "account" else arguments["action"]
        return _domain(await compact.account(action=action))
    server = MCPServer("strict inventory")
    manage.register_manage_tools(server, layers=["all"])
    args = {"response_format": "json"}
    if primary_tool == "gemini_get_usage_limits":
        args["scope"] = arguments["scope"]
    return _domain((await server.call_tool(primary_tool, args)).content)


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
@pytest.mark.parametrize("key,primary_tool,facade,arguments", INVENTORIES)
@pytest.mark.parametrize("case", INVALID)
def test_inventory_failures_never_become_empty_success(monkeypatch, surface, key, primary_tool, facade, arguments, case):
    response = _response(key, case)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    for module in (manage, compact):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", AsyncMock())
    result = asyncio.run(_invoke(surface, key, primary_tool, facade, arguments, client))
    assert result["ok"] is False
    assert result["error"]["code"] == ("UPSTREAM_REJECTED" if case == "rejected" else "UPSTREAM_CHANGED")
    assert result["meta"]["verification_status"] == "read_back_unavailable"
    assert result["meta"]["diagnostic_id"]
    assert result["meta"]["details"]["read_back_valid"] is False


@pytest.mark.parametrize("key,primary_tool,facade,arguments", INVENTORIES)
def test_valid_empty_inventories_remain_successful(monkeypatch, key, primary_tool, facade, arguments):
    response = _response(key, "empty")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    monkeypatch.setattr(manage, "get_gemini_client", lambda: client)
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    server = MCPServer("valid empty inventory")
    manage.register_manage_tools(server, layers=["all"])
    args = {"response_format": "json"}
    if primary_tool == "gemini_get_usage_limits":
        args["scope"] = arguments["scope"]
    content = asyncio.run(server.call_tool(primary_tool, args)).content
    assert not (content[0].meta or {}).get("domain_result", {}).get("error")
    assert json.loads(content[0].text)
    focused = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
    result = asyncio.run(focused.execute(facade, _request(facade, arguments)))
    assert result.ok


@pytest.mark.parametrize("case", INVALID + ["empty", "success"])
def test_feature_probes_share_strict_evidence_across_surfaces(monkeypatch, case):
    key = "library.locale_capabilities"
    probe = get_contract(key).as_probe()
    response = _response(key, case)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    expected = case in {"empty", "success"}
    for module in (manage, compact, account_facade):
        monkeypatch.setattr(module, "WEB_FEATURE_PROBES", (probe,))
    for module in (manage, compact):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", AsyncMock())
    server = MCPServer("strict probe")
    manage.register_manage_tools(server, layers=["all"])
    content = asyncio.run(server.call_tool("gemini_probe_web_features", {"response_format": "json"})).content
    primary = json.loads(content[0].text)
    assert primary["ok_count"] == int(expected)
    assert primary["results"][0]["ok"] is expected
    compact_text = asyncio.run(compact.account(action="features"))[0].text
    assert (": ok" in compact_text) is expected
    if not expected:
        assert "UPSTREAM_" in compact_text
        assert primary["results"][0]["error_code"].startswith("UPSTREAM_")
    service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
    result = asyncio.run(service.execute("account", _request("account", {"action": "features"})))
    assert result.data["results"][0]["reachable"] is expected


@pytest.mark.parametrize("key", ["notebooks.chats", "scheduled.get"])
@pytest.mark.parametrize("case", INVALID)
def test_related_item_reads_preserve_invalid_observations(monkeypatch, key, case):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response(key, case)))
    for module in (manage, compact):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", AsyncMock())
    if key == "notebooks.chats":
        notebook = {"id": "notebooks/sample", "title": "Sample"}
        diagnostic = {"status_code": 200, "body_present": True, "read_back_valid": True,
                      "parser_status": "success", "reject_code": None, "parser_warnings": []}
        monkeypatch.setattr(manage, "_fetch_native_notebooks", AsyncMock(return_value=([notebook], diagnostic)))
        primary_tool, args = "gemini_list_notebook_chats", {"notebook_id": notebook["id"]}
        facade, focused_args = "notebooks", {"action": "chats", **args}
    else:
        primary_tool, args = "gemini_get_scheduled_action", {"action_id": "sample"}
        facade, focused_args = "scheduled", {"action": "get", **args}
        content = asyncio.run(compact.scheduled(action="get", **args))
        assert _domain(content)["ok"] is False
    server = MCPServer("strict related inventory")
    manage.register_manage_tools(server, layers=["all"])
    content = asyncio.run(server.call_tool(primary_tool, {**args, "response_format": "json"})).content
    assert _domain(content)["ok"] is False
    service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
    result = asyncio.run(service.execute(facade, _request(facade, focused_args)))
    assert result.ok is False


def test_checked_values_and_probe_diagnostics_do_not_include_raw_account_content():
    response = _response("library.locale_capabilities", "success")
    result = account_contract_result(response, "library.locale_capabilities")
    assert result.ok and result.data["value"]
    status = account_probe_status(response, get_contract("library.locale_capabilities").as_probe())
    assert status["ok"] is True
    assert "value" not in status and "text" not in status and "body" not in status


@pytest.mark.parametrize("field,value", [("status_code", 503), ("body_present", False),
                                         ("parser_status", "changed_shape"), ("parser_warnings", ["invalid_entry"]),
                                         ("reject_code", 7), ("read_back_valid", False)])
def test_service_readback_flags_cannot_override_failed_evidence(field, value):
    diagnostic = {"status_code": 200, "body_present": True, "parser_status": "success",
                  "parser_warnings": [], "reject_code": None, "read_back_valid": True}
    diagnostic[field] = value
    assert account_read_result([], diagnostic).ok is False
