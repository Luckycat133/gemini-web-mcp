"""A notebook list is empty only when the upstream envelope proves it."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.rpc_parsers import RPCEnvelope
from src.services import notebooks


def _read(monkeypatch, envelope, *, http=200):
    monkeypatch.setattr(notebooks, "parse_rpc_envelope", lambda *_: envelope)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=SimpleNamespace(text="fixture", status_code=http)))
    return asyncio.run(notebooks.fetch_notebook_chats(client, "synthetic", 100, 0))


@pytest.mark.parametrize(("body", "status", "expected"), [
    ([None, None, [["c_fixture", "synthetic"]]], "success", 1),
    ([None, None, []], "empty", 0),
    ([], "empty", 0),
])
def test_verified_notebook_pages(monkeypatch, body, status, expected):
    items, page = _read(monkeypatch, RPCEnvelope(True, 1, (body,)))
    assert len(items) == expected
    assert page["diagnostic"]["parser_status"] == status
    assert page["has_more"] is False


@pytest.mark.parametrize(("envelope", "status"), [
    (RPCEnvelope(False, 0, ()), "missing_envelope"),
    (RPCEnvelope(True, 2, ([], [])), "missing_envelope"),
    (RPCEnvelope(True, 1, (), reject_code=7), "rejected"),
    (RPCEnvelope(True, 1, ({"changed": True},)), "changed_shape"),
    (RPCEnvelope(True, 1, ([None, "next", []],)), "incomplete_page"),
    (RPCEnvelope(True, 1, ([None, 42, []],)), "changed_shape"),
    (RPCEnvelope(True, 1, ([None, None, [[None, "title"]]],)), "changed_shape"),
])
def test_unverified_notebook_pages_never_become_empty(monkeypatch, envelope, status):
    with pytest.raises(notebooks.NotebookChatReadError, match=status):
        _read(monkeypatch, envelope)


def test_http_failure_cannot_prove_empty(monkeypatch):
    with pytest.raises(notebooks.NotebookChatReadError, match="http_error"):
        _read(monkeypatch, RPCEnvelope(True, 1, ([None, None, []],)), http=403)


def test_notebook_empty_page_does_not_relax_account_history():
    assert notebooks.parse_contract_body("notebooks.chats", []).status == "empty"
    assert notebooks.parse_contract_body("history.page", []).status == "changed_shape"


def _read_pages(monkeypatch, bodies, *, limit=100, offset=0):
    responses = [SimpleNamespace(text=str(index), status_code=200) for index in range(len(bodies))]
    execute = AsyncMock(side_effect=responses)
    monkeypatch.setattr(notebooks, "execute_contract", execute)
    monkeypatch.setattr(
        notebooks, "parse_rpc_envelope",
        lambda text, _rpc: RPCEnvelope(True, 1, (bodies[int(text)],)),
    )
    client = SimpleNamespace()
    return asyncio.run(notebooks.fetch_notebook_chats(client, "synthetic", limit, offset)), execute


def test_distinct_notebook_pages_are_counted_once(monkeypatch):
    bodies = [
        [None, "next", [["c_one", "first"]]],
        [None, None, [["c_two", "second"]]],
    ]
    (items, page), execute = _read_pages(monkeypatch, bodies)
    assert [item["id"] for item in items] == ["c_one", "c_two"]
    assert page["total_count"] == 2
    assert page["diagnostic"]["page_count"] == 2
    assert page["has_more"] is False
    assert execute.await_args_list[1].kwargs["next_page_token"] == "next"


@pytest.mark.parametrize("tokens", [["same", "same"], ["a", "b", "a"]])
def test_repeated_or_cyclic_page_tokens_fail_closed(monkeypatch, tokens):
    bodies = [[None, token, [[f"c_{index}", "chat"]]] for index, token in enumerate(tokens)]
    with pytest.raises(notebooks.NotebookChatReadError, match="repeated_page_token"):
        _read_pages(monkeypatch, bodies)


@pytest.mark.parametrize("bodies", [
    [[None, "next", [["c_one", "first"]]], [None, None, [["c_one", "duplicate"]]]],
    [[None, None, [["c_one", "first"], ["c_one", "duplicate"]]]],
])
def test_duplicate_notebook_chat_ids_fail_closed(monkeypatch, bodies):
    with pytest.raises(notebooks.NotebookChatReadError, match="duplicate_chat_id"):
        _read_pages(monkeypatch, bodies)


def test_blank_notebook_chat_id_is_not_valid_evidence(monkeypatch):
    with pytest.raises(notebooks.NotebookChatReadError, match="changed_shape"):
        _read(monkeypatch, RPCEnvelope(True, 1, ([None, None, [["   ", "title"]]],)))


@pytest.mark.parametrize("envelope,http,error", [
    (RPCEnvelope(False, 0, ()), 200, "missing_envelope"),
    (RPCEnvelope(True, 1, (), reject_code=7), 200, "rejected"),
    (RPCEnvelope(True, 1, ([None, None, []],)), 403, "http_error"),
    (RPCEnvelope(True, 1, ({"changed": True},)), 200, "changed_shape"),
])
def test_notebook_project_view_never_reports_unverified_empty(monkeypatch, envelope, http, error):
    monkeypatch.setattr(notebooks, "parse_rpc_envelope", lambda *_: envelope)
    monkeypatch.setattr(notebooks, "execute_contract", AsyncMock(return_value=SimpleNamespace(text="fixture", status_code=http)))
    with pytest.raises(notebooks.NotebookChatReadError, match=error):
        asyncio.run(notebooks.fetch_native_notebooks(SimpleNamespace()))
