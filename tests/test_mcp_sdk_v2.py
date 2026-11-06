"""MCP SDK v2 adapter, discovery, structured-output, and golden contracts."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import jsonschema
import pytest
from mcp import Client
from mcp.server import MCPServer as SDKMCPServer
from pydantic import BaseModel, Field

from scripts.snapshot_mcp_v2_contract import build_snapshot
from src.adapters.mcp_sdk import ImageContent, MCPServer, TextContent

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_v2_tool_contract.json"


class _BusinessOutput(BaseModel):
    title: str = Field(description="A user-facing report title.")
    result: list[TextContent] = Field(description="The report evidence.")


def test_catalog_trims_sdk_content_labels_and_preserves_business_schema():
    async def capture() -> list[TextContent | ImageContent]:
        return [TextContent(type="text", text="Frame ready")]

    async def report() -> _BusinessOutput:
        return _BusinessOutput(title="Report", result=[])

    async def tools_for(server):
        for handler in (capture, report):
            server.tool(structured_output=True)(handler)
        return {tool.name: tool for tool in await server.list_tools()}

    sdk = asyncio.run(tools_for(SDKMCPServer("SDK")))
    compact = asyncio.run(tools_for(MCPServer("Project")))
    assert compact["report"].output_schema == sdk["report"].output_schema
    assert compact["capture"].input_schema == sdk["capture"].input_schema
    before = sdk["capture"].output_schema
    after = compact["capture"].output_schema
    assert before is not None and after is not None
    assert "title" in before and "title" not in after
    assert after["required"] == before["required"]
    assert after["properties"]["result"]["items"] == before["properties"]["result"]["items"]
    assert "description" in before["$defs"]["TextContent"]
    assert "description" not in after["$defs"]["TextContent"]
    payload = {"result": [{"type": "text", "text": "Frame ready"}]}
    jsonschema.validate(payload, before)
    jsonschema.validate(payload, after)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"result": [{"type": "image", "data": "missing MIME"}]}, after)


def _reload_server_full():
    """重新加载 src.server 并在 full 工具表面下运行（GEMINI_TOOLS=all）。"""
    os.environ["GEMINI_TOOLS"] = "all"
    if "src.server" in sys.modules:
        del sys.modules["src.server"]
    importlib.import_module("src.server")
    import src.server as srv
    return srv


def _reload_skill_server_full():
    """重新加载 src.skill_server 并在 full 工具表面下运行。"""
    os.environ["GEMINI_SKILL_MODE"] = "full"
    if "src.skill_server" in sys.modules:
        del sys.modules["src.skill_server"]
    importlib.import_module("src.skill_server")
    import src.skill_server as skl
    return skl


def test_runtime_uses_one_project_owned_sdk_adapter():
    srv = _reload_server_full()
    skl = _reload_skill_server_full()
    assert type(srv.mcp) is MCPServer
    assert type(skl.mcp) is MCPServer

    service_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((Path("src") / "services").glob("*.py"))
    )
    assert "from mcp" not in service_sources
    assert "import mcp" not in service_sources
    assert "mcp_sdk" not in service_sources


def test_tool_list_and_schema_golden_matches_v2_baseline():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = build_snapshot()

    assert actual == expected
    for tools in actual["surfaces"].values():
        assert tools
        assert all(tool["output_schema_sha256"] is not None for tool in tools)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_high_level_clients_list_and_validate_primary_structured_output(monkeypatch, mode):
    srv = _reload_server_full()
    payload = {"scope": "core", "groups": {"model": 1}, "tools": []}
    monkeypatch.setattr(srv, "_tool_manifest_payload", lambda _scope: payload)

    async def run():
        async with Client(srv.mcp, mode=mode, cache=None) as client:
            listed = await client.list_tools()
            tool = next(item for item in listed.tools if item.name == "gemini_get_tool_manifest")
            result = await client.call_tool(
                tool.name,
                {"scope": "core", "response_format": "json"},
            )
            return client.protocol_version, client.server_info, tool, result

    protocol_version, server_info, tool, result = asyncio.run(run())

    assert protocol_version == ("2026-07-28" if mode == "auto" else "2025-11-25")
    assert server_info is not None
    assert server_info.version == srv.__version__
    assert result.result_type == "complete"
    assert result.is_error is False
    assert result.structured_content is not None
    assert tool.output_schema is not None
    jsonschema.validate(result.structured_content, tool.output_schema)
    body = json.loads(result.content[0].text)
    assert body["scope"] == "core"
    assert "groups" in body
    assert "tools" in body


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_high_level_clients_list_and_validate_compact_structured_output(monkeypatch, mode):
    srv = _reload_server_full()
    monkeypatch.setattr(srv, "_doctor_payload", lambda **_kwargs: {"overall_status": "ok"})
    monkeypatch.setattr(srv, "_format_doctor_markdown", lambda payload: payload["overall_status"])

    async def run():
        async with Client(srv.mcp, mode=mode, cache=None) as client:
            listed = await client.list_tools()
            tool = next(item for item in listed.tools if item.name == "gemini_doctor")
            result = await client.call_tool(tool.name, {})
            return client.protocol_version, client.server_info, tool, result

    protocol_version, server_info, tool, result = asyncio.run(run())

    assert protocol_version == ("2026-07-28" if mode == "auto" else "2025-11-25")
    assert server_info is not None
    assert server_info.version == srv.__version__
    assert result.result_type == "complete"
    assert result.is_error is False
    assert result.structured_content is not None
    assert tool.output_schema is not None
    jsonschema.validate(result.structured_content, tool.output_schema)
    assert result.content[0].text == "ok"
