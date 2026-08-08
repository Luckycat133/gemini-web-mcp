"""Typed-result parity contracts for primary and compact history reads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.adapters.mcp_sdk import MCPServer
import src.skill_server as skill_server
import src.tools.manage as manage_tools


def _run(awaitable):
    return asyncio.run(awaitable)


def _domain_data(content):
    return content[0].meta["domain_result"]["data"]


def _patch_clients(monkeypatch, client) -> None:
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: client)
    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: client)
    monkeypatch.setattr(skill_server, "initialize_client", AsyncMock())
    monkeypatch.setattr(manage_tools, "initialize_client", AsyncMock())


def _primary_mcp() -> MCPServer:
    mcp = MCPServer("history-parity")
    manage_tools.register_manage_tools(mcp, layers=["all"])
    return mcp


async def _primary_content(mcp: MCPServer, name: str, arguments: dict):
    return (await mcp.call_tool(name, arguments)).content


def test_history_list_has_identical_typed_data_on_primary_and_compact(monkeypatch) -> None:
    client = SimpleNamespace(
        list_chats=lambda: [
            {"title": "Mapped chat", "cid": "c_mapping", "is_pinned": True, "timestamp": None},
            SimpleNamespace(title="Object chat", cid="c_object", is_pinned=False, timestamp=None),
        ]
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="list", limit=1, offset=0))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_list_chats",
            {"limit": 1, "offset": 0, "response_format": "json"},
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "total_count": 2,
        "count": 1,
        "offset": 0,
        "limit": 1,
        "has_more": True,
        "next_offset": 1,
        "items": [
            {
                "id": "c_mapping",
                "title": "Mapped chat",
                "is_pinned": True,
                "timestamp": None,
                "time": "",
            }
        ],
        "diagnostic": {"source": "client_cache", "fetched_count": 2, "has_remote_more": False},
    }
    assert "1. Mapped chat (c_mapping)" in compact[0].text


def test_history_read_has_identical_typed_data_on_primary_and_compact(monkeypatch) -> None:
    client = SimpleNamespace(
        read_chat=AsyncMock(
            return_value={
                "cid": "c_mapping",
                "turns": [
                    {"role": "user", "text": "hello"},
                    {"role": "model", "text": "world"},
                ],
            }
        )
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="read", chat_id="c_mapping", limit=10))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_read_chat",
            {
                "chat_id": "c_mapping",
                "limit": 10,
                "max_chars_per_turn": 4000,
                "response_format": "json",
            },
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "chat_id": "c_mapping",
        "count": 2,
        "limit": 10,
        "turns": [
            {"role": "user", "text": "hello"},
            {"role": "model", "text": "world"},
        ],
    }
    assert compact[0].text == "user: hello\n\nmodel: world"


def test_primary_history_read_markdown_renders_from_typed_turns(monkeypatch) -> None:
    client = SimpleNamespace(
        read_chat=AsyncMock(
            return_value=SimpleNamespace(
                cid="c_markdown",
                turns=[SimpleNamespace(role="user", text="hello markdown")],
            )
        )
    )
    _patch_clients(monkeypatch, client)

    content = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_read_chat",
            {"chat_id": "c_markdown", "response_format": "markdown"},
        )
    )

    assert content[0].text == (
        "## 💬 聊天记录: c_markdown\n"
        "返回 1 条 turn\n\n"
        "### 1. user\n"
        "hello markdown"
    )
    assert _domain_data(content)["count"] == 1
