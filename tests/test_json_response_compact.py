"""Compact JSON preserves complete payloads through the real SDK handlers."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.adapters.mcp_sdk import MCPServer, TextContent
from src.tools import manage


def _pretty_response(payload):
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def test_compact_json_preserves_nested_values_and_text_whitespace():
    payload = {
        "items": [{"id": "c_一", "title": "中文 🙂", "text": " first\n  second\tlast "}],
        "empty": [], "unknown": None, "verified": False, "count": 0,
        "pagination": {"offset": 0, "has_more": True, "next_offset": 1},
    }
    text = manage._json_response(payload)[0].text
    assert json.loads(text) == payload
    assert len(text) < len(_pretty_response(payload)[0].text)


@pytest.mark.parametrize("surface", ["notebooks", "history"])
def test_real_sdk_json_is_lossless_without_additional_fetches(monkeypatch, surface):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=AssertionError("Unexpected network access")))
    monkeypatch.setattr(manage, "get_gemini_client", lambda: client)
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    server = MCPServer("compact-json-test")
    if surface == "notebooks":
        rows = [{"id": f"notebook_{i}", "title": f"项目 🙂 {i}", "source_count": i,
                 "emoji": "", "project_type": 2, "project_subtype": 1} for i in range(4)]
        fetch = AsyncMock(return_value=(rows, {"source_rpc": "fixture", "observed": True}))
        monkeypatch.setattr(manage, "_fetch_native_notebooks", fetch)
        manage.register_notebook_tools(server, {"gemini_notebooks"})
        name = "gemini_notebooks"
        arguments = {"source": "gemini", "response_format": "json", "limit": 2, "offset": 1}
    else:
        rows = [{"id": f"c_{i}", "title": f"讨论 🙂 {i}", "is_pinned": False, "time": ""}
                for i in range(4)]
        fetch = AsyncMock(return_value=(rows, {"source": "fixture", "fetched_count": 4,
                                               "has_remote_more": True}))
        monkeypatch.setattr(manage, "_fetch_recent_conversation_metadata", fetch)
        manage.register_history_tools(server, {"gemini_history"})
        name = "gemini_history"
        arguments = {"action": "list", "response_format": "json", "limit": 2, "offset": 1}

    compact_response = manage._json_response
    outcomes = []
    for formatter in (_pretty_response, compact_response):
        monkeypatch.setattr(manage, "_json_response", formatter)
        fetch.reset_mock()
        result = asyncio.run(server.call_tool(name, arguments))
        assert not result.is_error
        fetch.assert_awaited_once()
        client._batch_execute.assert_not_awaited()
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["offset"] == 1 and payload["count"] == 2
        assert payload["has_more"] is True and payload["next_offset"] == 3
        assert len(payload["items"]) == 2
        wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert json.loads(wire["structuredContent"]["result"][0]["text"]) == payload
        if surface == "history":
            assert result.content[0].meta["domain_result"]["data"] == payload
        outcomes.append((payload, text))
    assert outcomes[0][0] == outcomes[1][0]
    assert len(outcomes[1][1]) < len(outcomes[0][1])
