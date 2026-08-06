"""Regression contracts for mapping-backed compact history results."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.client_wrapper as client_wrapper
import src.skill_server as skill_server


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
