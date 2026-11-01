"""Exercise the account facade through the real MCP SDK and stdio transport."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters, stdio_client


_SERVER = r'''
from types import SimpleNamespace
from gemini_webapi.constants import AccountStatus
from src.adapters.mcp_sdk import MCPServer
import src.tools.manage as manage

async def initialize():
    pass

async def notebooks(_client, _locale):
    return ([{"id": "nb_test", "title": "Example", "emoji": "", "source_count": 0}],
            {"source_rpc": "test", "observed": "fixture"})

async def notebook_chats(_client, _notebook_id, _limit, _offset):
    return ([{"id": "c_test", "title": "Example chat"}],
            {"count": 1, "offset": 0, "has_more": False, "next_offset": None,
             "diagnostic": {"fetched_count": 1}})

async def scheduled(_client, _max_chars):
    return ([{"id": "task_test", "title": "Example task", "enabled": True}],
            {"source_rpc": "test", "observed": "fixture"})

manage.get_gemini_client = lambda: SimpleNamespace(_batch_execute=object(), account_status=AccountStatus.UNAUTHENTICATED)
manage.initialize_client = initialize
manage._fetch_native_notebooks = notebooks
manage._fetch_notebook_chats = notebook_chats
manage._fetch_scheduled_registry = scheduled
mcp = MCPServer("account-inventory-fixture")
manage.register_account_tools(mcp, {"gemini_account_inventory"})
mcp.run()
'''


def test_account_inventory_dependencies_work_through_stdio_without_exposing_other_tools():
    env = os.environ.copy()
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        env.pop(name, None)
    env["GEMINI_PSID"] = "synthetic-test-account"
    env["GEMINI_BROWSER_AUTO_LOAD"] = "false"
    env["GEMINI_AUTO_REFRESH"] = "false"
    params = StdioServerParameters(
        command=sys.executable, args=["-c", _SERVER], env=env, cwd=Path(__file__).resolve().parents[1],
    )

    async def run():
        async with Client(stdio_client(params), mode="auto", cache=None) as client:
            listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} == {"gemini_account_inventory"}
            outputs = []
            for args in (
                {"surface": "status", "response_format": "json"},
                {"surface": "notebooks", "response_format": "json"},
                {"surface": "notebook_chats", "notebook_id": "nb_test", "response_format": "json"},
                {"surface": "scheduled", "response_format": "json"},
            ):
                result = await client.call_tool("gemini_account_inventory", args)
                assert not result.is_error and result.result_type == "complete"
                outputs.append(json.loads(result.content[0].text))
            return outputs

    status, notebooks, chats, scheduled = asyncio.run(run())
    assert status["summary"]["account_status"] == "UNAUTHENTICATED"
    assert status["summary"]["status_evidence"] == "sdk_cached_unverified"
    assert notebooks["items"][0]["id"] == "nb_test"
    assert chats["items"][0]["id"] == "c_test"
    assert scheduled["results"][0]["items"][0]["id"] == "task_test"
