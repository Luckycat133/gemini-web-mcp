"""Legacy foreground research retains early SDK identity through rollback."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import APIError
from gemini_webapi.types import AvailableModel

from src.adapters.mcp_sdk import MCPServer
from src.domain import CleanupObservation, CleanupState
from src.infrastructure.account_identity import current_account_key
from src.infrastructure.operation_store import OperationStore
from src.thinking_client import ThinkingLevelGeminiClient
from src.tools import research


@pytest.mark.parametrize("switch_account", [False, True])
def test_foreground_research_preserves_early_sdk_identity_on_rollback(monkeypatch, tmp_path, switch_account):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-a")
    account_a = current_account_key()
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    handle = "op_" + "a" * 32
    monkeypatch.setattr(research, "new_operation_id", lambda: handle)
    inner = json.dumps([None, ["c_synthetic", "r_synthetic"]], separators=(",", ":"))
    body = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(body) + 1) + "\n" + body).encode()

    class Response:
        status_code = 200
        http_version = 2
        async def aiter_content(self):
            if switch_account:
                monkeypatch.setenv("GEMINI_PSID", "synthetic-account-b")
            yield wire
            raise APIError("synthetic stream failure")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return False

    class Transport:
        requests = 0
        def stream(self, *_args, **_kwargs):
            self.requests += 1
            return Response()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(GeminiClient, "close", AsyncMock())
    monkeypatch.setattr(GeminiClient, "fetch_latest_chat_response", AsyncMock(return_value=None))
    client = ThinkingLevelGeminiClient()
    model = AvailableModel(model_id="pro", model_name="gemini-pro", display_name="Pro", description="", capacity=1)
    client._model_registry = {"pro": model}
    client.client = transport = Transport()
    client._running = True
    monkeypatch.setattr(research, "get_gemini_client", lambda: client)
    monkeypatch.setattr(research, "initialize_client", AsyncMock())
    monkeypatch.setattr(research, "cleanup_due_remote_chats", AsyncMock())
    scheduled = []
    def schedule(cid, **kwargs):
        scheduled.append((cid, current_account_key()))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)
    monkeypatch.setattr(research, "schedule_remote_chat_cleanup", schedule)
    finalize = AsyncMock()
    monkeypatch.setattr(research, "finalize_remote_chat_cleanup", finalize)
    server = MCPServer("foreground-research")
    research.register_research_tools(server)
    result = asyncio.run(server.call_tool("gemini_deep_research", {
        "query": "synthetic", "model": "pro", "wait_for_completion": True,
    }))
    record = OperationStore().get(handle)
    assert record.account_key == account_a
    assert record.upstream_chat_id == "c_synthetic" and record.upstream_response_id == "r_synthetic"
    assert record.cleanup_protected
    assert transport.requests == 1
    assert all(owner == account_a for _, owner in scheduled)
    assert bool(scheduled) is not switch_account
    finalize.assert_not_awaited()
    assert handle in str(result)


def test_foreground_report_after_account_switch_stays_protected(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-a")
    account_a = current_account_key()
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    handle = "op_" + "b" * 32
    monkeypatch.setattr(research, "new_operation_id", lambda: handle)
    chat = SimpleNamespace(cid="c_synthetic", metadata=["c_synthetic", "r_synthetic", "rc_synthetic"])

    class Client:
        def start_chat(self, **_kwargs):
            return chat

        async def create_deep_research_plan(self, *_args, **_kwargs):
            chat.cid = "c_synthetic"
            return SimpleNamespace(cid=chat.cid, research_id="research_synthetic")

        async def start_deep_research(self, *_args, **_kwargs):
            return SimpleNamespace(state="running", metadata=chat.metadata)

        async def wait_for_deep_research(self, *_args, **_kwargs):
            monkeypatch.setenv("GEMINI_PSID", "synthetic-account-b")
            return SimpleNamespace(done=True, final_output=SimpleNamespace(
                deep_research_document=SimpleNamespace(ready=True, content="synthetic report")))

    monkeypatch.setattr(research, "get_gemini_client", lambda: Client())
    monkeypatch.setattr(research, "initialize_client", AsyncMock())
    monkeypatch.setattr(research, "cleanup_due_remote_chats", AsyncMock())
    scheduled = []

    def schedule(cid, **_kwargs):
        scheduled.append(current_account_key())
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)

    monkeypatch.setattr(research, "schedule_remote_chat_cleanup", schedule)
    finalize = AsyncMock()
    monkeypatch.setattr(research, "finalize_remote_chat_cleanup", finalize)
    server = MCPServer("foreground-research")
    research.register_research_tools(server)
    result = asyncio.run(server.call_tool("gemini_deep_research", {
        "query": "synthetic", "wait_for_completion": True,
    }))
    record = OperationStore().get(handle)
    assert record.cleanup_protected and record.account_key == account_a
    assert record.state != "completed"
    assert scheduled and all(owner == account_a for owner in scheduled)
    finalize.assert_not_awaited()
    assert handle in str(result)
