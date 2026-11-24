"""The account surface exposes explicit account management only."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import Client

from src.domain.chat_management import ChatMetadata
from src.infrastructure.rpc_contracts import get_contract
from src.onboarding import domain_result_from_call
from src.surfaces.account import build_server, mcp
from src.tools import chat_management, web_tasks


EXPECTED = {
    "gemini_history", "gemini_manage_chat", "gemini_delete_chat", "gemini_notebooks",
    "gemini_manage_notebook", "gemini_tasks",
}
EXPECTED_FULL = {
    "gemini_history", "gemini_delete_chat", "gemini_notebooks", "gemini_move_chat_to_notebook",
    "gemini_account_inventory", "gemini_list_scheduled_actions", "gemini_get_scheduled_action",
    "gemini_create_scheduled_action", "gemini_delete_scheduled_action", "gemini_manage_gems", "gemini_cleanup",
    "gemini_manage_chat", "gemini_tasks", "gemini_task_browser", "gemini_preferences",
    "gemini_skills", "gemini_manage_notebook",
}


def _tools(server):
    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}
    return asyncio.run(run())


def test_account_catalog_and_safety_annotations():
    tools = _tools(mcp)
    assert set(tools) == EXPECTED
    assert all(tool.annotations is not None for tool in tools.values())
    for name in ("gemini_history", "gemini_notebooks", "gemini_tasks"):
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.destructive_hint is False
    for name in ("gemini_delete_chat", "gemini_manage_notebook"):
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is True
    task_arguments = tools["gemini_tasks"].input_schema["properties"]
    assert set(task_arguments) == {"action", "chat_id", "limit", "page_token", "max_chars", "response_id", "candidate_id"}
    assert task_arguments["action"]["enum"] == ["list", "get", "progress"]
    assert tools["gemini_notebooks"].input_schema["properties"]["source"]["default"] == "notebooklm"
    control = tools["gemini_manage_chat"].annotations
    assert control.read_only_hint is False and control.destructive_hint is False
    assert control.idempotent_hint is False and control.open_world_hint is True
    chat_arguments = tools["gemini_manage_chat"].input_schema["properties"]
    assert chat_arguments["action"]["enum"] == ["rename", "pin", "unpin", "move_to_notebook"]
    assert chat_arguments["notebook_id"]["default"] is None
    history_schema = tools["gemini_history"].input_schema["properties"]
    assert history_schema["response_format"]["default"] == "compact"
    assert history_schema["max_chars_per_turn"]["default"] == 800


def test_opt_in_full_account_preserves_previous_catalog_and_annotations():
    tools = _tools(build_server(full=True))
    assert set(tools) == EXPECTED_FULL
    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["gemini_delete_scheduled_action"].annotations.destructive_hint is True
    assert tools["gemini_cleanup"].annotations.destructive_hint is True
    assert tools["gemini_tasks"].annotations.destructive_hint is True
    assert tools['gemini_task_browser'].annotations.destructive_hint is True
    assert tools['gemini_preferences'].annotations.read_only_hint is False
    assert tools['gemini_preferences'].annotations.idempotent_hint is False
    assert tools['gemini_preferences'].annotations.open_world_hint is True
    for name in ('gemini_skills', 'gemini_manage_notebook'):
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is True
        assert tools[name].annotations.idempotent_hint is False
        assert tools[name].annotations.open_world_hint is True
    task_arguments = tools['gemini_tasks'].input_schema['properties']
    assert {'rename', 'pin', 'unpin', 'progress', 'create_schedule', 'update_schedule', 'pause_schedule',
            'resume_schedule', 'delete_schedule'} <= set(task_arguments['action']['enum'])
    assert task_arguments['title']['default'] is None
    assert tools["gemini_notebooks"].input_schema["properties"]["source"]["default"] == "notebooklm"
    control = tools["gemini_manage_chat"].annotations
    assert control.read_only_hint is False and control.destructive_hint is False
    assert control.idempotent_hint is True and control.open_world_hint is True
    chat_arguments = tools["gemini_manage_chat"].input_schema["properties"]
    assert chat_arguments["action"]["enum"] == ["rename", "pin", "unpin"]
    assert "notebook_id" not in chat_arguments


@pytest.mark.parametrize("full,expected", [(False, EXPECTED), (True, EXPECTED_FULL)])
def test_account_catalog_over_real_stdio(tmp_path, full, expected):
    import os
    import sys
    from pathlib import Path
    from mcp import StdioServerParameters, stdio_client

    environment = os.environ.copy()
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment.update(GEMINI_BROWSER_AUTO_LOAD="false", GEMINI_AUTO_REFRESH="false",
                       GEMINI_OPERATION_DB_PATH=str(tmp_path / "operations.sqlite"),
                       GEMINI_CLEANUP_DB_PATH=str(tmp_path / "cleanup.sqlite"))
    params = StdioServerParameters(command=sys.executable,
                                   args=["-c", f"from src.surfaces.account import build_server; build_server(full={full!r}).run()"],
                                   env=environment, cwd=Path(__file__).resolve().parents[1])
    async def run():
        async with Client(stdio_client(params), mode="auto", cache=None) as client:
            return {tool.name for tool in (await client.list_tools()).tools}
    assert asyncio.run(run()) == expected


def _call(name, arguments):
    async def run():
        async with Client(mcp, mode="auto", cache=None) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(run())


def test_lean_task_schema_rejects_mutations_before_initialization(monkeypatch):
    initialize = AsyncMock(side_effect=AssertionError("Task writes must not reach the lean surface"))
    monkeypatch.setattr(web_tasks, "initialize_client", initialize)
    result = _call("gemini_tasks", {"action": "delete", "chat_id": "c_fixture"})
    assert result.is_error
    initialize.assert_not_awaited()


@pytest.mark.parametrize("mode", ["verified", "read_back_missing", "target_missing", "account_changed", "uncertain_delivery", "http_failure"])
def test_lean_chat_move_requires_same_account_and_independent_exact_membership(monkeypatch, mode):
    notebook_id = "notebooks/fixture"
    chat_id = "c_fixture"
    calls = []

    async def execute(requests, **kwargs):
        request = requests[0]
        calls.append(request.rpcid)
        status_code = 200
        if request.rpcid == get_contract("notebooks.list").rpc_id:
            if mode == "account_changed":
                monkeypatch.setenv("GEMINI_PSID", "synthetic-changed-account")
            current_id = "notebooks/unrelated" if mode == "target_missing" else notebook_id
            body = [None, None, [[current_id, ["Private fixture title"]]]]
        elif request.rpcid == get_contract("notebooks.move_chat").rpc_id:
            mutation = json.loads(request.payload)[2]
            assert mutation[0] == chat_id and mutation[7] == notebook_id
            assert kwargs["current_retry"] == 0
            if mode == "uncertain_delivery":
                raise TimeoutError("Private fixture error")
            body = [None, [chat_id, "Private fixture chat"]]
            if mode == "http_failure":
                status_code = 503
        else:
            assert request.rpcid == get_contract("notebooks.chats").rpc_id
            assert kwargs["source_path"] == "/notebook/fixture"
            observed = "c_other" if mode == "read_back_missing" else chat_id
            body = [None, None, [[observed, "Private fixture chat"]]]
        text = json.dumps([["wrb.fr", request.rpcid, json.dumps(body), None, None, None, "generic"]])
        return SimpleNamespace(text=text, status_code=status_code)

    monkeypatch.setenv("GEMINI_PSID", "synthetic-original-account")
    monkeypatch.setattr(chat_management, "initialize_client", AsyncMock())
    monkeypatch.setattr(chat_management, "get_gemini_client", lambda: SimpleNamespace(_batch_execute=execute))
    response = _call("gemini_manage_chat", {"action": "move_to_notebook", "chat_id": chat_id, "notebook_id": notebook_id})
    result = domain_result_from_call(response)
    assert result["ok"] is (mode == "verified")
    assert "Private fixture" not in str(result)
    assert calls.count(get_contract("notebooks.list").rpc_id) == 1
    assert calls.count(get_contract("notebooks.move_chat").rpc_id) == int(mode not in {"target_missing", "account_changed"})
    assert calls.count(get_contract("notebooks.chats").rpc_id) == int(mode in {"verified", "read_back_missing", "http_failure"})
    if mode == "account_changed":
        assert result["error"]["code"] == "AUTH_REQUIRED" and result["data"] is None
    else:
        assert result["data"]["verified"] is (mode == "verified")
        assert result["data"]["mutation_sent"] is {"target_missing": False, "uncertain_delivery": None}.get(mode, True)
    if mode == "read_back_missing":
        assert result["meta"]["verification_status"] == "read_back_not_observed"
    if mode == "http_failure":
        assert result["meta"]["verification_status"] == "move_unverified"


def test_move_rejects_a_stale_title_before_dispatch(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-original-account")
    monkeypatch.setattr(chat_management, "initialize_client", AsyncMock())
    dispatch = AsyncMock()
    monkeypatch.setattr(chat_management, "get_gemini_client", lambda: SimpleNamespace(_batch_execute=dispatch))
    monkeypatch.setattr(chat_management, "_read_metadata", AsyncMock(return_value=ChatMetadata("c_fixture", "Current", False)))
    response = _call("gemini_manage_chat", {"action": "move_to_notebook", "chat_id": "c_fixture",
                                          "notebook_id": "notebooks/fixture", "expected_title": "Previous"})
    result = domain_result_from_call(response)
    assert not result["ok"] and result["meta"]["verification_status"] == "expected_title_conflict"
    dispatch.assert_not_awaited()
