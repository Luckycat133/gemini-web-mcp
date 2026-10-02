"""Regression contracts for mapping-backed compact history results."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.client_wrapper as client_wrapper
import src.skill_server as skill_server


def test_compact_content_search_exposes_failed_reads_without_matches(monkeypatch) -> None:
    client = SimpleNamespace(
        list_chats=lambda: [{"id": "c_unreadable", "title": "Other subject"}],
        read_chat=AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    _patch_client(monkeypatch, client)

    content = _run(skill_server.history(action="search", query="needle", scan_turns=True))

    assert "Content search failed:" in content[0].text
    assert "1 chat reads failed" in content[0].text
    assert "No matches" not in content[0].text
    result = content[0].meta["domain_result"]
    assert result["ok"] is False
    assert result["meta"]["verification_status"] == "content_scan_failed"
    assert result["data"]["read_failures"] == [{"chat_id": "c_unreadable", "error_type": "RuntimeError"}]


def test_compact_content_search_preserves_partial_coverage(monkeypatch) -> None:
    async def read_chat(cid, **kwargs):
        if cid == "c_unreadable":
            raise RuntimeError("unavailable")
        return {"turns": [{"role": "model", "text": "needle in content"}]}

    client = SimpleNamespace(
        list_chats=lambda: [
            {"id": "c_readable", "title": "Other subject"},
            {"id": "c_unreadable", "title": "Another subject"},
        ],
        read_chat=read_chat,
    )
    _patch_client(monkeypatch, client)

    content = _run(skill_server.history(action="search", query="needle", scan_turns=True))

    assert "needle in content" in content[0].text
    assert "1 chat reads failed" in content[0].text
    result = content[0].meta["domain_result"]
    assert result["ok"] is True
    assert result["meta"]["operation_state"] == "partial"
    assert result["data"]["diagnostic"]["content_scan_complete"] is False


def _run(awaitable):
    return asyncio.run(awaitable)


def _patch_client(monkeypatch, client) -> None:
    monkeypatch.setattr(client_wrapper._client_manager, "get_client", lambda: client)
    monkeypatch.setattr(skill_server, "initialize_client", AsyncMock())


def test_compact_history_list_renders_mapping_backed_chats(monkeypatch) -> None:
    client = SimpleNamespace(
        list_chats=lambda: [
            {"title": "Mapped chat", "cid": "c_mapping"},
            {"title": "Second chat", "id": "c_second"},
        ]
    )
    _patch_client(monkeypatch, client)

    content = _run(skill_server.history(action="list"))

    assert "1. Mapped chat (c_mapping)" in content[0].text
    assert "2. Second chat (c_second)" in content[0].text
    assert "Untitled" not in content[0].text


def test_compact_history_read_renders_mapping_backed_turns(monkeypatch) -> None:
    read_chat = AsyncMock(
        return_value={
            "cid": "c_mapping",
            "turns": [
                {"role": "user", "text": "hello"},
                {"role": "model", "text": "world"},
            ],
        }
    )
    client = SimpleNamespace(read_chat=read_chat)
    _patch_client(monkeypatch, client)

    content = _run(skill_server.history(action="read", chat_id="c_mapping"))

    assert content[0].text == "user: hello\n\nmodel: world"
    read_chat.assert_awaited_once_with("c_mapping", limit=10)
