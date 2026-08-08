"""Typed-result parity contracts for primary and compact history reads."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.adapters.mcp_sdk import MCPServer
import src.skill_server as skill_server
import src.tools.manage as manage_tools


def _run(awaitable):
    return asyncio.run(awaitable)


def _domain_data(content):
    return content[0].meta["domain_result"]["data"]


def _domain_result(content):
    return content[0].meta["domain_result"]


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


def _history_page_response(items: list[object], next_page_token: str | None = None) -> SimpleNamespace:
    body = [None, next_page_token, items]
    return SimpleNamespace(text=json.dumps([["wrb.fr", "MaZiqc", json.dumps(body), None, None, None, "generic"]]))


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


def test_history_search_has_identical_typed_data_on_primary_and_compact(monkeypatch) -> None:
    client = SimpleNamespace(
        list_chats=lambda: [
            {"title": "Project Alpha", "cid": "c_alpha", "is_pinned": False, "timestamp": None},
            SimpleNamespace(title="Travel notes", cid="c_travel", is_pinned=True, timestamp=None),
        ]
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="search", query="alpha", limit=10, offset=0))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_search_chats",
            {
                "query": "alpha",
                "limit": 10,
                "offset": 0,
                "scan_turns": False,
                "response_format": "json",
            },
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "query": "alpha",
        "scan_turns": False,
        "scanned_count": 2,
        "match_count": 1,
        "total_count": 2,
        "count": 2,
        "offset": 0,
        "limit": 10,
        "has_more": False,
        "next_offset": None,
        "matches": [
            {
                "id": "c_alpha",
                "title": "Project Alpha",
                "is_pinned": False,
                "timestamp": None,
                "time": "",
                "matched_fields": ["id", "title"],
            }
        ],
        "diagnostic": {"source": "client_cache", "fetched_count": 2, "has_remote_more": False},
        "note": "正文搜索只会在 scan_turns=true 时读取当前页聊天内容。",
    }
    assert compact[0].text == "Project Alpha (c_alpha)"


def test_history_export_has_identical_typed_data_on_primary_and_compact(monkeypatch) -> None:
    client = SimpleNamespace(
        read_chat=AsyncMock(
            return_value=SimpleNamespace(
                cid="c_export",
                turns=[
                    SimpleNamespace(role="user", text="export this"),
                    SimpleNamespace(role="model", text="exported"),
                ],
            )
        ),
        list_chats=lambda: [
            SimpleNamespace(
                title="Export example",
                cid="c_export",
                is_pinned=True,
                timestamp=None,
            )
        ],
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="export", chat_id="c_export", limit=2))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_export_chat",
            {
                "chat_id": "c_export",
                "limit": 2,
                "max_chars_per_turn": 20000,
                "include_metadata": True,
                "response_format": "json",
            },
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "chat_id": "c_export",
        "count": 2,
        "limit": 2,
        "max_chars_per_turn": 20000,
        "turns": [
            {"role": "user", "text": "export this"},
            {"role": "model", "text": "exported"},
        ],
        "metadata": {
            "id": "c_export",
            "title": "Export example",
            "is_pinned": True,
            "timestamp": None,
            "time": "",
        },
    }
    assert compact[0].text.startswith("## Gemini Chat Export: Export example")


def test_history_delete_reports_identical_unverified_acceptance_on_both_surfaces(monkeypatch) -> None:
    client = SimpleNamespace(
        delete_chat=AsyncMock(),
        read_chat=AsyncMock(return_value=None),
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="delete", chat_id="c_delete"))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_delete_chat",
            {"chat_id": "c_delete"},
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "chat_id": "c_delete",
        "delete_requested": True,
        "deleted": None,
        "verification": {
            "status": "not_available",
            "source": None,
        },
    }
    assert compact[0].text == "Delete requested: c_delete (not independently verified)"
    assert primary[0].text == "已请求删除聊天 c_delete；当前客户端无法独立回读验证。"
    assert client.delete_chat.await_count == 2
    client.read_chat.assert_not_awaited()


def test_history_delete_verifies_absence_on_both_surfaces(monkeypatch) -> None:
    client = SimpleNamespace(
        delete_chat=AsyncMock(),
        _batch_execute=AsyncMock(return_value=_history_page_response([])),
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="delete", chat_id="c_delete"))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_delete_chat",
            {"chat_id": "c_delete"},
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "chat_id": "c_delete",
        "delete_requested": True,
        "deleted": True,
        "verification": {
            "status": "verified_absent",
            "source": "history.page",
        },
    }
    assert compact[0].text == "Deleted and verified absent: c_delete"
    assert primary[0].text == "已删除并回读确认聊天不存在: c_delete"


def test_history_delete_fails_closed_when_chat_is_still_present(monkeypatch) -> None:
    client = SimpleNamespace(
        delete_chat=AsyncMock(),
        _batch_execute=AsyncMock(
            return_value=_history_page_response([["c_delete", "Still here", False, None, None, [1700000000, 0]]])
        ),
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="delete", chat_id="c_delete"))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_delete_chat",
            {"chat_id": "c_delete"},
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "chat_id": "c_delete",
        "delete_requested": True,
        "deleted": False,
        "verification": {
            "status": "still_present",
            "source": "history.page",
        },
    }
    assert _domain_result(compact)["ok"] is False
    assert _domain_result(primary)["error"]["code"] == "VERIFICATION_FAILED"
    assert compact[0].text == "Delete not verified: c_delete is still present"
    assert primary[0].text == "删除未验证：聊天 c_delete 仍可读取。"


def test_history_delete_fails_closed_when_read_back_errors(monkeypatch) -> None:
    client = SimpleNamespace(
        delete_chat=AsyncMock(),
        _batch_execute=AsyncMock(side_effect=RuntimeError("private upstream detail")),
    )
    _patch_clients(monkeypatch, client)

    compact = _run(skill_server.history(action="delete", chat_id="c_delete"))
    primary = _run(
        _primary_content(
            _primary_mcp(),
            "gemini_delete_chat",
            {"chat_id": "c_delete"},
        )
    )

    assert _domain_data(compact) == _domain_data(primary) == {
        "chat_id": "c_delete",
        "delete_requested": True,
        "deleted": None,
        "verification": {
            "status": "read_back_error",
            "source": "history.page",
        },
    }
    assert _domain_result(compact)["error"]["code"] == "VERIFICATION_FAILED"
    assert _domain_result(primary)["error"]["code"] == "VERIFICATION_FAILED"
    assert "private upstream detail" not in compact[0].text
    assert "private upstream detail" not in primary[0].text
