"""The project evaluator bridge uses SDK v2 and keeps upstream Skill bytes intact."""

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.run_mcp_builder_evaluation import (
    EvaluationConnection,
    create_connection,
    load_evaluator,
)
from scripts.smoke_mcp_protocol import _safe_environment
from src.adapters.mcp_sdk import TextContent


@pytest.mark.parametrize("transport", ["stdio", "sse", "http", "streamable-http"])
def test_sdk_v2_connection_factory_does_not_import_removed_sdk_v1_symbols(transport):
    connection = create_connection(transport, command="python", url="https://example.invalid/mcp")
    assert isinstance(connection, EvaluationConnection)


def test_content_blocks_reach_the_upstream_agent_loop_as_successful_json(monkeypatch):
    anthropic = ModuleType("anthropic")
    anthropic.Anthropic = object
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    original_connections = sys.modules.get("connections")
    evaluator = load_evaluator()
    assert sys.modules.get("connections") is original_connections

    class Messages:
        def __init__(self):
            self.calls = 0
            self.tool_reply = None

        def create(self, **arguments):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(
                    type="tool_use", name="static_tool", input={}, id="call_test",
                )])
            self.tool_reply = arguments["messages"][-1]["content"][0]["content"]
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(text="<response>done</response>")])

    connection = create_connection("stdio", command="python")
    connection.client = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
        result_type="complete", is_error=False,
        content=[TextContent(type="text", text="successful MCP result", meta={"observed": True})],
        structured_content={"value": 1},
    )))
    messages = Messages()
    response, metrics = asyncio.run(evaluator.agent_loop(
        SimpleNamespace(messages=messages), "test-model", "Use a static tool", [], connection,
    ))
    payload = json.loads(messages.tool_reply)
    assert payload["content"][0]["text"] == "successful MCP result"
    assert payload["content"][0]["_meta"] == {"observed": True}
    assert payload["structured_content"] == {"value": 1}
    assert payload["is_error"] is False
    assert response == "<response>done</response>"
    assert metrics["static_tool"]["count"] == 1


def test_connection_keeps_an_incomplete_tool_state():
    connection = create_connection("stdio", command="python")
    connection.client = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(
        result_type="input_required", is_error=False, content=None, structured_content=None,
    )))
    result = asyncio.run(connection.call_tool("static_tool", {}))
    assert result["result_type"] == "input_required"
    assert result["content"] == []


def test_evaluator_adapter_handshakes_and_calls_the_actual_sdk_v2_server(monkeypatch, tmp_path):
    executable = Path(sys.executable).parent / "gemini-mcp-server"
    if not executable.is_file():
        pytest.skip("Install the project's console entrypoints before the stdio smoke.")
    monkeypatch.chdir(tmp_path)

    async def run():
        async with asyncio.timeout(30):
            async with create_connection("stdio", command=str(executable), env=_safe_environment("model")) as connection:
                tools = await connection.list_tools()
                assert "gemini_get_tool_manifest" in {tool["name"] for tool in tools}
                result = await connection.call_tool("gemini_get_tool_manifest", {"response_format": "json"})
                assert result["result_type"] == "complete"
                assert result["is_error"] is False
                assert json.loads(result["content"][0]["text"])["server"] == "gemini_web_mcp"

    asyncio.run(run())
