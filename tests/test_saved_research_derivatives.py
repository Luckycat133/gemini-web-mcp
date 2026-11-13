"""Saved reports are local derivative inputs after their owned chat is deleted."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.adapters.mcp_sdk import MCPServer
from src.infrastructure.operation_store import OperationStore
from src.services.research_result_storage import save_research_result
import src.tools.research as research


@pytest.fixture
def saved_report(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-derivative-owner")
    store = OperationStore()
    store.create("op_report", "gemini_research")
    store.update("op_report", state="running", phase="start_returned", upstream_chat_id="c_original")
    # Put the marker beyond the bounded operation preview: the derivative must
    # read the full artifact, not recycle that preview.
    text = "# Stored Research\n\n## Evidence\n" + "evidence " * 600 + "\n\n## Tail verification\nFULL_REPORT_TAIL_MARKER"
    artifact = save_research_result(store, store.get("op_report"), SimpleNamespace(
        ready=True, content=text, markdown=text,
        sources=[SimpleNamespace(url="https://example.com/source", title="Synthetic source")]))
    store.release_cleanup("op_report")
    store.update_cleanup("op_report", "completed")
    return store, artifact


def server_without_remote(monkeypatch):
    def no_remote():
        raise AssertionError("Saved derivatives must not initialize or access Gemini")
    initialize = AsyncMock(side_effect=no_remote)
    monkeypatch.setattr(research, "initialize_client", initialize)
    monkeypatch.setattr(research, "get_gemini_client", no_remote)
    monkeypatch.setattr(research, "finalize_remote_chat_cleanup", AsyncMock(side_effect=no_remote))
    server = MCPServer("saved-research")
    research.register_research_tools(server)
    return server, initialize


def test_operation_actions_and_webpage_use_full_local_report_after_cleanup_and_restart(saved_report, monkeypatch, tmp_path):
    store, _ = saved_report
    # No connection-local service instance survives this reopen.
    assert OperationStore(store.path).get("op_report").cleanup_state == "completed"
    server, initialize = server_without_remote(monkeypatch)
    async def run():
        actions = await server.call_tool("gemini_list_research_report_actions", {
            "operation_id": "op_report", "response_format": "json"})
        created = await server.call_tool("gemini_create_from_research_report", {
            "operation_id": "op_report", "output_dir": str(tmp_path), "response_format": "json"})
        return actions, created
    actions, created = asyncio.run(run())
    catalog = json.loads(actions.content[0].text)
    assert catalog["operation_id"] == "op_report" and catalog["chat_id"] == "c_original"
    assert catalog["native_web_menu_observed"] is False
    assert actions.content[0].meta["domain_result"]["ok"] is True
    derived = json.loads(created.content[0].text)
    assert "FULL_REPORT_TAIL_MARKER" in Path(derived["path"]).read_text()
    assert "https://example.com/source" in Path(derived["path"]).read_text()
    assert derived["report_source"] == "verified_local_artifact"
    assert created.content[0].meta["domain_result"]["data"]["state"] == "local"
    initialize.assert_not_awaited()


@pytest.mark.parametrize("tool", ["gemini_list_research_report_actions", "gemini_create_from_research_report"])
@pytest.mark.parametrize("arguments", [{}, {"chat_id": "c_one", "operation_id": "op_report"}, {"operation_id": " "}])
def test_report_input_xor_is_rejected_before_auth(monkeypatch, tool, arguments):
    server, initialize = server_without_remote(monkeypatch)
    result = asyncio.run(server.call_tool(tool, arguments))
    payload = result.content[0].meta["domain_result"]
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "exactly one" in result.content[0].text
    initialize.assert_not_awaited()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "different_account"])
@pytest.mark.parametrize("tool", ["gemini_list_research_report_actions", "gemini_create_from_research_report"])
def test_invalid_or_cross_account_cache_never_falls_back_to_gemini(saved_report, monkeypatch, tmp_path, damage, tool):
    _, artifact = saved_report
    if damage == "missing":
        Path(artifact.local_path).unlink()
    elif damage == "corrupt":
        Path(artifact.local_path).write_text("Changed report")
    else:
        monkeypatch.setenv("GEMINI_PSID", "different-derivative-owner")
    server, initialize = server_without_remote(monkeypatch)
    arguments = {"operation_id": "op_report"}
    if tool == "gemini_create_from_research_report":
        arguments["output_dir"] = str(tmp_path / "new-output")
    result = asyncio.run(server.call_tool(tool, arguments))
    payload = result.content[0].meta["domain_result"]
    if damage == "different_account":
        assert payload["error"]["code"] == "CAPABILITY_UNAVAILABLE" and payload["data"] is None
    else:
        assert payload["meta"]["operation_state"] == "partial"
        assert payload["data"]["operation_id"] == "op_report"
        assert not payload["data"]["report_available"]
    assert "FULL_REPORT_TAIL_MARKER" not in str(payload)
    assert not (tmp_path / "new-output").exists()
    initialize.assert_not_awaited()


def test_unsaved_report_handle_stays_partial_without_remote_or_cleanup(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-derivative-owner")
    store = OperationStore()
    store.create("op_pending", "gemini_research")
    store.update("op_pending", state="running", phase="start_returned", upstream_chat_id="c_original")
    server, initialize = server_without_remote(monkeypatch)
    result = asyncio.run(server.call_tool("gemini_list_research_report_actions", {"operation_id": "op_pending"}))
    payload = result.content[0].meta["domain_result"]
    assert payload["data"]["state"] == "partial" and payload["data"]["operation_id"] == "op_pending"
    assert store.get("op_pending").cleanup_protected
    initialize.assert_not_awaited()
