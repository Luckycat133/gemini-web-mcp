"""Compact history preserves useful identities, read limits and failure evidence."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp import Client

from src.adapters.mcp_sdk import MCPServer
from src.onboarding import domain_result_from_call
from src.services.history import compact_history_payload
from src.tools import manage


def test_compact_history_projection_keeps_identity_pagination_and_search_evidence():
    item = {"id": "c_fixture", "title": "Fixture", "is_pinned": False, "timestamp": 1,
            "time": "1970-01-01", "matched_fields": ["title"], "snippets": [{"text": "evidence"}]}
    original = {"matches": [item], "count": 1, "has_more": True, "next_offset": 20,
                "scanned_count": 20, "diagnostic": {"verbose": "not repeated", "source_rpc": "metadata",
                                                       "has_remote_more": True, "fetched_count": 20}}
    compact = compact_history_payload("search", original)
    assert compact["has_more"] is True and compact["next_offset"] == 20
    assert compact["scanned_count"] == 20
    assert compact["matches"][0] == {key: item[key] for key in
                                      ("id", "title", "is_pinned", "matched_fields", "snippets")}
    assert "diagnostic" not in compact
    assert compact["coverage"] == {"scope": "current_metadata_page", "source_rpc": "metadata",
                                    "has_remote_more": True, "fetched_count": 20}
    assert "timestamp" in original["matches"][0]


def test_compact_scan_and_export_do_not_discard_coverage_or_requested_content():
    payload = {"coverage": {"complete": False}, "errors": ["unavailable"], "text": "requested export"}
    assert compact_history_payload("scan", payload) == payload
    assert compact_history_payload("export", payload) == payload


def test_account_style_history_defaults_to_compact_metadata_without_reading_chat_text(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    client = SimpleNamespace(list_chats=lambda: [SimpleNamespace(cid="c_fixture", title="Fixture",
                                                               is_pinned=False, timestamp=1)],
                             read_chat=AsyncMock(side_effect=AssertionError("unrequested text read")))
    monkeypatch.setattr(manage, "get_gemini_client", lambda: client)
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    server = MCPServer("compact-history")
    manage.register_history_tools(server, {"gemini_history"}, compact=True)

    async def run():
        async with Client(server, mode="auto", cache=None) as session:
            short = await session.call_tool("gemini_history", {})
            detailed = await session.call_tool("gemini_history", {"response_format": "json"})
            return short, detailed

    short, detailed = asyncio.run(run())
    body = json.loads(short.content[0].text)
    assert body["items"] == [{"id": "c_fixture", "title": "Fixture", "is_pinned": False}]
    assert body["has_more"] is False and body["next_offset"] is None
    assert domain_result_from_call(short)["data"] == body
    assert len(short.content[0].text) < len(detailed.content[0].text)
    assert "diagnostic" in json.loads(detailed.content[0].text)
    client.read_chat.assert_not_awaited()


def test_explicit_history_read_uses_the_account_default_text_window(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    reader = AsyncMock(return_value=SimpleNamespace(cid="c_fixture", turns=[
        SimpleNamespace(role="model", text="x" * 2000),
    ]))
    monkeypatch.setattr(manage, "get_gemini_client", lambda: SimpleNamespace(read_chat=reader))
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    server = MCPServer("bounded-history")
    manage.register_history_tools(server, {"gemini_history"}, compact=True)

    async def run():
        async with Client(server, mode="auto", cache=None) as session:
            return await session.call_tool("gemini_history", {"action": "read", "chat_id": "c_fixture"})

    result = asyncio.run(run())
    body = domain_result_from_call(result)["data"]
    assert body["chat_id"] == "c_fixture" and body["count"] == 1
    assert body["turns"][0]["text"].startswith("x" * 800)
    assert "truncated" in body["turns"][0]["text"]
    assert len(body["turns"][0]["text"]) < 850


def test_compact_history_does_not_turn_transport_failure_into_an_empty_success(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    monkeypatch.setattr(manage, "get_gemini_client", lambda: SimpleNamespace(
        list_chats=lambda: (_ for _ in ()).throw(ConnectionError("synthetic transport failure"))))
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    server = MCPServer("failed-history")
    manage.register_history_tools(server, {"gemini_history"}, compact=True)

    async def run():
        async with Client(server, mode="auto", cache=None) as session:
            return await session.call_tool("gemini_history", {})

    result = domain_result_from_call(asyncio.run(run()))
    assert result["ok"] is False and result["error"]["code"] == "NETWORK_ERROR"
