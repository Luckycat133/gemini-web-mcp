"""Public recovery tools preserve explicit handles and typed results."""

import asyncio

import pytest

from src.adapters.mcp_sdk import MCPServer
from src.domain import DomainErrorCode, DomainResult, OperationState
from src.domain.operations import OperationReadData
from src.tools import operations
from src.tools import register_tools


@pytest.mark.parametrize("name", ["gemini_get_operation_status", "gemini_get_operation_result"])
def test_recovery_tools_are_explicit_read_only_and_preserve_reports(monkeypatch, name):
    calls = []

    class Service:
        async def get_status(self, operation_id):
            calls.append(operation_id)
            return DomainResult.success(OperationReadData(
                operation_id, "gemini_research", OperationState.COMPLETED,
                True, True, "report_observed", "Verified research body.",
            ))

        get_result = get_status

    monkeypatch.setattr(operations, "_service", Service)
    mcp = MCPServer("operations-test")
    operations.register_operation_tools(mcp)

    async def run():
        tools = await mcp.list_tools()
        assert [tool.name for tool in tools] == ["gemini_get_operation_status", "gemini_get_operation_result"]
        for tool in tools:
            consumes = tool.name == "gemini_get_operation_result"
            assert tool.annotations.read_only_hint is (not consumes)
            assert tool.annotations.destructive_hint is consumes
            assert tool.annotations.open_world_hint is True
            assert tool.input_schema["required"] == ["operation_id"]
        return await mcp.call_tool(name, {"operation_id": "op_test"})

    result = asyncio.run(run())
    assert calls == ["op_test"]
    assert "Verified research body." in result.content[0].text
    assert result.content[0].meta["domain_result"]["data"]["operation_id"] == "op_test"


def test_recovery_failure_does_not_claim_report_ready(monkeypatch):
    class Service:
        async def get_result(self, operation_id):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Unknown operation handle.")

    monkeypatch.setattr(operations, "_service", Service)
    mcp = MCPServer("operations-failure")
    operations.register_operation_tools(mcp)
    result = asyncio.run(mcp.call_tool("gemini_get_operation_result", {"operation_id": "unknown"}))
    assert result.content[0].meta["domain_result"]["ok"] is False
    assert "ready" not in result.content[0].text


def test_native_app_setup_is_presented_as_attention_with_original_task_url():
    data = OperationReadData("op_setup", "gemini_run_task", OperationState.PARTIAL,
        False, False, "task_setup_required", upstream_status=8, needs_attention=True,
        setup_required=True, setup_kind="app_connection", setup_app_name="Fixture App",
        setup_url="https://gemini.google.com/app/fixture")
    content = operations._present(DomainResult.success(data))
    assert "waiting for attention" in content[0].text and "Fixture App" in content[0].text
    assert "Connect or decline" in content[0].text and "cannot connect the app or complete OAuth" in content[0].text
    assert "https://gemini.google.com/app/fixture" in content[0].text
    assert "requires explicit input" not in content[0].text
    assert "background worker" not in content[0].text
    assert content[0].meta["domain_result"]["data"]["setup_required"] is True


def test_metadata_only_previously_observed_setup_does_not_claim_the_card_is_still_pending():
    data = OperationReadData("op_setup", "gemini_run_task", OperationState.PARTIAL,
        False, False, "task_setup_previously_observed", upstream_status=8, needs_attention=True,
        setup_kind="app_connection", setup_url="https://gemini.google.com/app/fixture")
    content = operations._present(DomainResult.success(data))
    assert "previously observed" in content[0].text and "does not revalidate" in content[0].text
    assert "waiting for attention" not in content[0].text and "Connect or decline" not in content[0].text
    assert "https://gemini.google.com/app/fixture" in content[0].text
    assert content[0].meta["domain_result"]["data"]["setup_required"] is False


def test_media_only_profile_includes_shared_recovery_and_truthful_manifest(monkeypatch):
    from src.services.manifest import tool_manifest_payload

    monkeypatch.setenv("GEMINI_TOOLS", "media")
    mcp = MCPServer("media-profile")
    register_tools(mcp, ["media"])
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {"gemini_generate_media", "gemini_generate_music", "gemini_get_operation_status", "gemini_get_operation_result"}
    metadata = tool_manifest_payload("media")
    assert {item["name"] for item in metadata["tools"] if item["current_enabled"]} == names
