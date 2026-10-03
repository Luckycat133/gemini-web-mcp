"""Matching valid RPC read-backs are required for account mutations."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.notebooks import fetch_native_notebooks, fetch_notebook_chats, move_chat_to_notebook
from src.services.gems import GemMutationNotVerified, delete_gem, list_gems
from src.services.scheduled import create_daily_action
from tests._account_rpc_fakes import scheduled_read


def _response(key, body, *, status=200, reject=None, rpc_id=None):
    part = ["wrb.fr", rpc_id or get_contract(key).rpc_id, json.dumps(body), None, None, [reject] if reject else None]
    return SimpleNamespace(status_code=status, text=json.dumps([part]))


def _notebooks():
    return [None, None, [["n_work", ["Work", "Description"], []]]]


@pytest.mark.parametrize("case", ["http_error", "rejected", "missing", "malformed", "ambiguous"])
def test_invalid_notebook_registry_is_not_an_empty_observation(case):
    response = _response("notebooks.list", _notebooks())
    if case == "http_error":
        response.status_code = 503
    elif case == "rejected":
        response = _response("notebooks.list", _notebooks(), reject=7)
    elif case == "missing":
        response = _response("notebooks.list", _notebooks(), rpc_id="other_rpc")
    elif case == "malformed":
        response = _response("notebooks.list", [None, None, [{"new_shape": True}]])
    else:
        parts = json.loads(response.text)
        response.text = json.dumps(parts + parts)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    items, diagnostic = asyncio.run(fetch_native_notebooks(client))
    assert items == [] and diagnostic["read_back_valid"] is False


@pytest.mark.parametrize("mutation", ["changed_shape", "rejected", "wrong_id", "http_error"])
def test_rejected_or_unconfirmed_move_cannot_succeed_from_existing_membership(mutation):
    body = [None, ["c_owned", "Chat"]]
    move = _response("notebooks.move_chat", body)
    if mutation == "changed_shape":
        move = _response("notebooks.move_chat", [None, "unexpected"])
    elif mutation == "rejected":
        move = _response("notebooks.move_chat", body, reject=7)
    elif mutation == "wrong_id":
        move = _response("notebooks.move_chat", [None, ["c_other", "Chat"]])
    else:
        move.status_code = 503
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        _response("notebooks.list", _notebooks()), move,
        _response("notebooks.chats", [None, None, [["c_owned", "Already in target"]]]),
    ]))
    result = asyncio.run(move_chat_to_notebook(client, chat_id="c_owned", notebook_id="n_work"))
    assert result["ok"] is False and result["accepted"] is False
    assert result["verified_in_target_notebook"] is False
    assert client._batch_execute.await_count == 2


@pytest.mark.parametrize("status", [200, 503])
def test_move_requires_successful_positive_target_readback(status):
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        _response("notebooks.list", _notebooks()),
        _response("notebooks.move_chat", [None, ["c_owned", "Chat"]]),
        _response("notebooks.chats", [None, None, [["c_owned", "Chat"]]], status=status),
    ]))
    result = asyncio.run(move_chat_to_notebook(client, chat_id="c_owned", notebook_id="n_work"))
    assert result["ok"] is (status == 200)
    assert result["verified_in_target_notebook"] is (status == 200)
    if status == 503:
        assert result["verification_status"] == "read_back_unverified"


def test_notebook_pagination_stops_repeated_token_and_never_returns_zero_progress_offset():
    response = _response("notebooks.chats", [None, "repeated", [["c_one", "Chat"]]])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    items, pagination = asyncio.run(fetch_notebook_chats(client, "n_work", limit=2, offset=2))
    assert items == [] and pagination["next_offset"] is None
    assert pagination["diagnostic"]["complete"] is False
    assert pagination["diagnostic"]["incomplete_reason"] == "pagination_not_progressing"
    assert client._batch_execute.await_count == 2


@pytest.mark.parametrize("key,body", [
    ("notebooks.list", [None, None, [{}]]),
    ("notebooks.list", [None, None, [[]]]),
    ("notebooks.list", [None, None, [["n_id", "changed metadata"]]]),
    ("notebooks.list", [None, None, [], [{}]]),
    ("notebooks.move_chat", [None, []]),
])
def test_notebook_changed_rows_are_explicitly_changed_shape(key, body):
    assert parse_contract_body(key, body).status == "changed_shape"


@pytest.mark.parametrize("reply", [None, "unexpected", [{"new_shape": True}]])
def test_malformed_gem_collection_does_not_prove_deletion(reply):
    client = SimpleNamespace(delete_gem=AsyncMock(), fetch_gems=AsyncMock(return_value=reply))
    with pytest.raises(GemMutationNotVerified) as exc:
        asyncio.run(delete_gem(client, gem_id="g_owned"))
    assert exc.value.verification_status == "read_back_error"


@pytest.mark.parametrize("case", ["missing_custom", "http_error", "rejected", "changed_shape", "wrong_identifier", "empty"])
def test_real_sdk_partial_custom_registry_never_proves_gem_absence(case):
    from gemini_webapi import GeminiClient
    from gemini_webapi.constants import AccountStatus

    class Client(GeminiClient):
        async def _batch_execute(self, requests, **_kwargs):
            # The old SDK fetch_gems still returns this system-only Jar, which
            # previously made a custom Gem deletion appear positively verified.
            if len(requests) == 2:
                return SimpleNamespace(status_code=200, text=json.dumps([[
                    "wrb.fr", get_contract("gems.custom_registry").rpc_id,
                    json.dumps([None, None, [["g_system", ["System", "Default"], None]]]),
                    None, None, None, "system",
                ]]))
            part = ["wrb.fr", get_contract("gems.custom_registry").rpc_id,
                    json.dumps([None, None, []]), None, None, None, "custom"]
            if case == "missing_custom":
                return SimpleNamespace(status_code=200, text="[]")
            if case == "rejected":
                part[5] = [7]
            elif case == "changed_shape":
                part[2] = json.dumps([None, None, [{}]])
            elif case == "wrong_identifier":
                part[-1] = "system"
            return SimpleNamespace(status_code=503 if case == "http_error" else 200, text=json.dumps([part]))

        async def delete_gem(self, _id):
            return None

    client = Client(secure_1psid="fake-offline-cookie")
    client.account_status = AccountStatus.AVAILABLE
    assert asyncio.run(GeminiClient.fetch_gems(client)).get("g_owned") is None
    if case == "empty":
        assert asyncio.run(delete_gem(client, gem_id="g_owned"))["verification_status"] == "verified_deleted"
    else:
        with pytest.raises(GemMutationNotVerified) as exc:
            asyncio.run(delete_gem(client, gem_id="g_owned"))
        assert exc.value.verification_status == "read_back_error"


@pytest.mark.parametrize("case, expected_state, expected_count", [
    ("complete", "completed", 2), ("empty", "completed", 0),
    ("missing_custom", "partial", 1), ("missing_system", "partial", 1),
    ("rejected_custom", "partial", 1), ("changed_custom", "partial", 1),
    ("ambiguous_custom", "partial", 1), ("wrong_rpc_custom", "partial", 1),
    ("missing_both", "failed", 0), ("http_error", "failed", 0),
])
def test_gem_listing_requires_each_observed_registry(case, expected_state, expected_count):
    from gemini_webapi import GeminiClient

    class Client(GeminiClient):
        async def _batch_execute(self, requests, **kwargs):
            assert [(request.identifier, json.loads(request.payload)) for request in requests] == [
                ("system", [3, [self.language], 0]), ("custom", [2, [self.language], 0]),
            ]
            assert kwargs == {"source_path": "/app", "close_on_error": False}
            parts = []
            for identifier in ("system", "custom"):
                if case in {"missing_both", f"missing_{identifier}"}:
                    continue
                rows = [] if case == "empty" else [[f"g_{identifier}", [identifier.title(), "Description"], ["Instructions"]]]
                if case == "changed_custom" and identifier == "custom":
                    rows = [{"new_shape": True}]
                part = ["wrb.fr", get_contract(f"gems.{identifier}_registry").rpc_id,
                        json.dumps([None, None, rows]), None, None, None, identifier]
                if case == "rejected_custom" and identifier == "custom":
                    part[5] = [7]
                if case == "wrong_rpc_custom" and identifier == "custom":
                    part[1] = "unrelated"
                parts.append(part)
                if case == "ambiguous_custom" and identifier == "custom":
                    parts.append(part)
            return SimpleNamespace(status_code=503 if case == "http_error" else 200, text=json.dumps(parts))

    result = asyncio.run(list_gems(Client(secure_1psid="fake-offline-cookie")))
    assert result.meta.operation_state.value == expected_state
    assert result.ok is (expected_state != "failed")
    assert len(result.data["items"]) == expected_count
    assert result.data["diagnostic"]["complete"] is (expected_state == "completed")
    if expected_state == "partial":
        assert result.warnings[0].code == "GEM_REGISTRY_INCOMPLETE"
    if case == "complete":
        assert {item["predefined"] for item in result.data["items"]} == {True, False}


@pytest.mark.parametrize("case", ["rejected", "http_error", "ambiguous", "changed_shape", "valid"])
def test_scheduled_creation_acceptance_requires_valid_matching_response(case):
    body = ["task_owned", [None, [["Instructions", "Daily", "Title"]]]]
    response = _response("scheduled.create_daily", body)
    if case == "rejected":
        response = _response("scheduled.create_daily", body, reject=7)
    elif case == "http_error":
        response.status_code = 503
    elif case == "ambiguous":
        response.text = json.dumps(json.loads(response.text) * 2)
    elif case == "changed_shape":
        response = _response("scheduled.create_daily", ["task_owned"])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    fetch = AsyncMock(return_value=scheduled_read([{"id": "task_owned"}]))
    by_id = AsyncMock(return_value=scheduled_read({"id": "task_owned"}))
    result = asyncio.run(create_daily_action(client, title="Title", instructions="Instructions", hour=9,
                                            timezone_name="Asia/Shanghai", locale="en", fetch_registry=fetch,
                                            fetch_by_id=by_id))
    assert result["accepted"] is result["ok"] is (case == "valid")
    assert result["verified"] is (case == "valid")
    assert fetch.await_count == (1 if case == "valid" else 0)


@pytest.mark.parametrize("invalid", ["http_error", "different_id", "deleted_state"])
def test_scheduled_created_id_is_not_verified_from_invalid_or_deleted_task(invalid):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response(
        "scheduled.create_daily", ["task_owned", [None, [["Instructions", "Daily", "Title"]]]],
    )))
    task = {"id": "task_other" if invalid == "different_id" else "task_owned",
            "task_state_id": 6 if invalid == "deleted_state" else 1}
    read = scheduled_read(task)
    if invalid == "http_error":
        read[1]["status_code"] = 503
        read[1]["read_back_valid"] = False
    result = asyncio.run(create_daily_action(client, title="Title", instructions="Instructions", hour=9,
                                            timezone_name="Asia/Shanghai", locale="en",
                                            fetch_registry=AsyncMock(return_value=scheduled_read([])),
                                            fetch_by_id=AsyncMock(return_value=read)))
    assert result["accepted"] is True and result["verified"] is False


@pytest.mark.parametrize("case, label", [("http_error", "Creation not confirmed"),
                                         ("rejected", "Creation not confirmed"),
                                         ("unverified", "Accepted create request"),
                                         ("verified", "Created")])
def test_compact_scheduled_creation_text_preserves_ack_and_verification_states(monkeypatch, case, label):
    from src import skill_server

    response = _response("scheduled.create_daily", ["task_owned", [None, [["Instructions", "Daily", "Title"]]]],
                         status=503 if case == "http_error" else 200, reject=7 if case == "rejected" else None)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    entries = [{"id": "task_owned"}] if case == "verified" else []
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry", AsyncMock(return_value=scheduled_read(entries)))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id", AsyncMock(return_value=scheduled_read(None)))
    result = asyncio.run(skill_server._scheduled_create(client, "Title", "Instructions", 9, "Asia/Shanghai"))
    assert result[0].text.startswith(label + ":")
