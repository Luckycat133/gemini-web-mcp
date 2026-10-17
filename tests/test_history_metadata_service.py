"""All history list/search surfaces require fresh, valid metadata evidence."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter

from src.adapters.mcp_sdk import MCPServer
from src.domain.account_requests import HistoryRequest
from src.infrastructure.rpc_contracts import get_contract
from src.services.account_facade import AccountFacadeService
import src.skill_server as compact
import src.tools.manage as primary


def response(body, *, status=200, reject=None, rpc_id=None):
    part = ["wrb.fr", rpc_id or get_contract("history.page").rpc_id, json.dumps(body),
            None, None, [reject] if reject is not None else None]
    return SimpleNamespace(status_code=status, text=json.dumps([part]))


async def call_history(monkeypatch, surface, action, client, **arguments):
    if surface == "focused":
        request = TypeAdapter(HistoryRequest).validate_python({"action": action, **arguments})
        service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
        return (await service.execute("history", request)).to_dict()
    monkeypatch.setattr(primary, "get_gemini_client", lambda: client)
    monkeypatch.setattr(compact, "get_gemini_client", lambda: client)
    monkeypatch.setattr(primary, "initialize_client", AsyncMock())
    monkeypatch.setattr(compact, "initialize_client", AsyncMock())
    if surface == "compact":
        content = await compact.history(action=action, **arguments)
    else:
        server = MCPServer("history-metadata")
        primary.register_manage_tools(server, layers=["all"])
        tool = "gemini_list_chats" if action == "list" else "gemini_search_chats"
        content = (await server.call_tool(tool, {**arguments, "response_format": "json"})).content
    return content[0].meta["domain_result"]


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
@pytest.mark.parametrize("action", ["list", "search"])
@pytest.mark.parametrize("case", ["http_error", "rejected", "changed_shape", "missing", "ambiguous"])
def test_invalid_history_metadata_never_becomes_an_empty_success(monkeypatch, surface, action, case):
    observed = response([None, None, []])
    if case == "http_error":
        observed.status_code = 503
    elif case == "rejected":
        observed = response([None, None, []], reject=7)
    elif case == "changed_shape":
        observed = response([None, None, [{"private_changed_shape": True}]])
    elif case == "missing":
        observed = response([None, None, []], rpc_id="unrelated_rpc")
    else:
        parts = json.loads(observed.text)
        observed.text = json.dumps(parts + parts)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=observed), list_chats=lambda: [])
    arguments = {"limit": 2, **({"query": "needle"} if action == "search" else {})}
    result = asyncio.run(call_history(monkeypatch, surface, action, client, **arguments))
    assert result["ok"] is False
    assert result["meta"]["operation_state"] == "failed"
    assert result["error"]["code"] in {"UPSTREAM_CHANGED", "UPSTREAM_REJECTED"}
    assert "private_changed_shape" not in json.dumps(result)


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
@pytest.mark.parametrize("action", ["list", "search"])
def test_history_refreshes_external_changes_and_empty_restart_cache(monkeypatch, surface, action):
    remote, rpc_calls, cache_reads = [], [], []

    class Client:
        def __init__(self, cache):
            self.cache = cache

        def list_chats(self):
            cache_reads.append(self.cache)
            return self.cache

        async def read_chat(self, *_args, **_kwargs):
            raise AssertionError("Metadata list/search must not read chat contents")

        async def _batch_execute(self, requests, **kwargs):
            count, token, filters = json.loads(requests[0].payload)
            rpc_calls.append((count, token, filters))
            assert kwargs == {"source_path": "/app", "close_on_error": False}
            assert requests[0].rpcid == get_contract("history.page").rpc_id
            return response([None, None, [] if filters[0] else list(remote)])

    arguments = {"limit": 2, **({"query": "needle"} if action == "search" else {})}

    async def run():
        client = Client([{"id": "c_stale", "title": "needle stale cache"}])
        empty = await call_history(monkeypatch, surface, action, client, **arguments)
        assert empty["ok"] is True
        key = "items" if action == "list" else "matches"
        assert empty["data"][key] == []
        remote.append(["c_external", "needle external chat", False])
        for current in (client, Client(None)):
            result = await call_history(monkeypatch, surface, action, current, **arguments)
            assert result["ok"] is True
            assert [item["id"] for item in result["data"][key]] == ["c_external"]
            assert result["data"]["diagnostic"]["source"] == "history.page"
        assert cache_reads == []
        assert len(rpc_calls) == 6

    asyncio.run(run())


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
def test_history_follows_each_bucket_cursor_and_deduplicates_ids(monkeypatch, surface):
    cursors = []

    class Client:
        async def _batch_execute(self, requests, **_kwargs):
            _count, token, filters = json.loads(requests[0].payload)
            cursors.append((filters[0], token))
            if filters[0]:
                return response([None, None, [["c_pinned", "Pinned", True]]])
            if token is None:
                return response([None, "second", [["c_pinned", "Pinned", True], ["c_one", "One", False]]])
            assert token == "second"
            return response([None, None, [["c_two", "Two", False]]])

        def list_chats(self):
            raise AssertionError("Cursor pagination must use fresh remote metadata")

    result = asyncio.run(call_history(monkeypatch, surface, "list", Client(), limit=3))
    assert result["ok"] is True
    assert {item["id"] for item in result["data"]["items"]} == {"c_pinned", "c_one", "c_two"}
    assert result["data"]["has_more"] is False
    assert (False, "second") in cursors


@pytest.mark.parametrize("surface,expected_turn_limit", [("focused", 100), ("primary", 50), ("compact", 20)])
@pytest.mark.parametrize("match_location", ["hundredth_turn", "eight_thousandth_character"])
def test_search_consumes_focused_bounds_and_preserves_compatibility_bounds(
    monkeypatch, surface, expected_turn_limit, match_location,
):
    needle = "late-search-evidence"
    turns = [SimpleNamespace(role="model", text="Ordinary fixture turn") for _ in range(100)]
    if match_location == "hundredth_turn":
        turns[-1].text = needle
    else:
        turns[0].text = "x" * 7999 + needle
    client = SimpleNamespace(
        _batch_execute=AsyncMock(return_value=response([None, None, [["c_owned", "Owned fixture", False]]])),
        read_chat=AsyncMock(return_value=SimpleNamespace(turns=turns)),
    )
    arguments = {"query": needle, "limit": 20, "scan_turns": True}
    if surface != "compact":
        arguments["turns_per_chat"] = 100
        arguments["max_chars" if surface == "focused" else "max_chars_per_turn"] = 20000

    result = asyncio.run(call_history(monkeypatch, surface, "search", client, **arguments))

    assert result["ok"] is True
    assert result["data"]["scanned_count"] == 1
    client.read_chat.assert_awaited_once_with("c_owned", limit=expected_turn_limit)
    if surface == "focused":
        assert result["data"]["match_count"] == 1
        snippet = result["data"]["matches"][0]["snippets"][0]
        assert snippet["turn_index"] == (100 if match_location == "hundredth_turn" else 1)
        assert needle in snippet["text"]
    else:
        assert result["data"]["match_count"] == 0


def invalid_response(case, contract_key="history.page"):
    rpc_id = get_contract(contract_key).rpc_id
    if case == "http_error":
        return response([None, None, []], status=503, rpc_id=rpc_id)
    if case == "rejected":
        return response([None, None, []], reject=7, rpc_id=rpc_id)
    return response([None, None, [{"private_changed_shape": True}]], rpc_id=rpc_id)


async def call_primary(monkeypatch, client, tool, **arguments):
    monkeypatch.setattr(primary, "get_gemini_client", lambda: client)
    monkeypatch.setattr(primary, "initialize_client", AsyncMock())
    server = MCPServer("history-source-readback")
    primary.register_manage_tools(server, layers=["all"])
    return (await server.call_tool(tool, {"response_format": "json", **arguments})).content


@pytest.mark.parametrize("case", ["http_error", "rejected", "changed_shape"])
def test_deep_history_scan_cannot_treat_unverified_source_as_empty(monkeypatch, case):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=invalid_response(case)))
    content = asyncio.run(call_primary(monkeypatch, client, "gemini_scan_chat_history_sources",
                                      include_notebook_chats=False, include_remy_goals=False))
    result = content[0].meta["domain_result"]
    assert result["ok"] is False
    assert result["meta"]["operation_state"] == "failed"
    assert result["error"]["code"] in {"UPSTREAM_CHANGED", "UPSTREAM_REJECTED"}


@pytest.mark.parametrize("case", ["http_error", "rejected", "changed_shape"])
@pytest.mark.parametrize("source", ["notebooks", "notebook_chats", "remy"])
def test_deep_scan_checks_additional_source_readback(monkeypatch, case, source):
    class Client:
        async def _batch_execute(self, requests, **_kwargs):
            rpc_id = requests[0].rpcid
            if rpc_id == get_contract("notebooks.list").rpc_id:
                if source == "notebooks":
                    return invalid_response(case, "notebooks.list")
                return response([None, None, [["n_owned", ["Owned", "Description"], []]]], rpc_id=rpc_id)
            if rpc_id == get_contract("history.remy_goals").rpc_id:
                return invalid_response(case, "history.remy_goals")
            _size, _token, filters = json.loads(requests[0].payload)
            if len(filters) == 5 and source == "notebook_chats":
                return invalid_response(case, "notebooks.chats")
            return response([None, None, []])

    content = asyncio.run(call_primary(monkeypatch, Client(), "gemini_scan_chat_history_sources",
                                      include_notebook_chats=source != "remy", include_remy_goals=source == "remy"))
    result = content[0].meta["domain_result"]
    assert result["ok"] is False
    assert result["meta"]["operation_state"] == "failed"
    assert "private_changed_shape" not in json.dumps(result)


@pytest.mark.parametrize("case", ["http_error", "rejected", "changed_shape"])
@pytest.mark.parametrize("include_metadata", [True, False])
def test_export_keeps_content_and_distinguishes_unverified_optional_metadata(monkeypatch, case, include_metadata):
    client = SimpleNamespace(
        _batch_execute=AsyncMock(return_value=invalid_response(case)),
        read_chat=AsyncMock(return_value=SimpleNamespace(
            cid="c_owned", turns=[SimpleNamespace(role="user", text="Owned offline fixture")],
        )),
    )
    content = asyncio.run(call_primary(monkeypatch, client, "gemini_export_chat",
                                      chat_id="c_owned", include_metadata=include_metadata))
    payload = json.loads(content[0].text)
    assert payload["turns"] == [{"role": "user", "text": "Owned offline fixture"}]
    if include_metadata:
        assert "metadata_warning" in payload["metadata"]
        assert "private_changed_shape" not in payload["metadata"]["metadata_warning"]
    else:
        assert "metadata" not in payload
        client._batch_execute.assert_not_awaited()


@pytest.mark.parametrize("case", ["http_error", "rejected", "changed_shape"])
def test_compact_export_preserves_content_when_fresh_metadata_cannot_be_verified(monkeypatch, case):
    client = SimpleNamespace(
        _batch_execute=AsyncMock(return_value=invalid_response(case)),
        list_chats=lambda: [],
        read_chat=AsyncMock(return_value=SimpleNamespace(
            cid="c_owned", turns=[SimpleNamespace(role="model", text="Owned offline fixture")],
        )),
    )
    result = asyncio.run(call_history(monkeypatch, "compact", "export", client, chat_id="c_owned"))
    assert result["ok"] is True
    assert result["data"]["turns"] == [{"role": "model", "text": "Owned offline fixture"}]
    assert "metadata_warning" in result["data"]["metadata"]
    client._batch_execute.assert_awaited_once()
