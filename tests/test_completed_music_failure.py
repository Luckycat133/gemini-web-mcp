"""A known NoTool reply plus one exact technical error card proves failure only."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure import rpc_contracts
from src.infrastructure.rpc_parsers import parse_completed_empty_media_turn, parse_completed_music_failure_turn

FIXTURE = json.loads((Path(__file__).parent / "fixtures/completed_music_failure_turn.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_music_failure_fixture_keeps_success_empty_rejection_and_changed_shapes_distinct(case):
    result = parse_completed_music_failure_turn(case["body"], **FIXTURE["arguments"],
                                                reject_code=case.get("reject_code"))
    assert result.status == case["status"]
    if result.status == "success":
        assert result.value["completed"] is True
        assert result.value["text"] == FIXTURE["arguments"]["text"]
    else:
        assert result.value is None


@pytest.mark.parametrize("change", [
    "pending", "bool_flag", "foreign_cid", "wrong_rid", "wrong_candidate", "duplicate_turn", "duplicate_candidate",
    "different_text", "unknown_text", "quoted_text", "url_in_text", "url_in_other_slot", "empty_rich", "extra_rich",
    "opaque_rich", "extra_card_key", "error_code_changed", "bool_error_code", "changed_label", "incomplete_card",
])
def test_music_failure_refuses_unverified_identity_completion_text_and_rich_content(change):
    body = deepcopy(FIXTURE["cases"][0]["body"])
    arguments = dict(FIXTURE["arguments"])
    turn = body[0][0]
    candidate = turn[3][0][0]
    card = candidate[12][0]["73"]
    if change == "pending":
        candidate[8] = [1]
    elif change == "bool_flag":
        candidate[8] = [True]
    elif change == "foreign_cid":
        turn[0][0] = "c_fedcba9876543210"
    elif change == "wrong_rid":
        turn[0][1] = "r_other"
    elif change == "wrong_candidate":
        candidate[0] = "rc_other"
    elif change == "duplicate_turn":
        body[0].append(deepcopy(turn))
    elif change == "duplicate_candidate":
        turn[3][0].append(deepcopy(candidate))
    elif change == "different_text":
        candidate[1][0] += " A different synthetic reply."
    elif change in {"unknown_text", "quoted_text", "url_in_text"}:
        text = ("The track is still generating." if change == "unknown_text" else
                '"' + arguments["text"] + '"' if change == "quoted_text" else
                arguments["text"] + " https://example.invalid/generated.mp3")
        candidate[1][0] = arguments["text"] = text
    elif change == "url_in_other_slot":
        candidate[10] = "blob:https://gemini.google.com/synthetic"
    elif change == "empty_rich":
        candidate[12] = [None, []]
    elif change == "extra_rich":
        candidate[12].append(None)
    elif change == "opaque_rich":
        candidate[12] = [{"opaque_job": "synthetic"}]
    elif change == "extra_card_key":
        candidate[12][0]["87"] = []
    elif change == "error_code_changed":
        card[3] = 4
    elif change == "bool_error_code":
        card[3] = True
    elif change == "changed_label":
        card[1] = "Another card"
    elif change == "incomplete_card":
        card.pop()
    result = parse_completed_music_failure_turn(body, **arguments)
    assert result.status != "success" and result.value is None


def test_music_error_card_does_not_widen_existing_empty_media_proof():
    result = parse_completed_empty_media_turn(FIXTURE["cases"][0]["body"], **FIXTURE["arguments"])
    assert result.status == "changed_shape" and result.value is None


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_music_failure_transport_reuses_registered_read_only_workflow_contract(monkeypatch, case):
    contract = rpc_contracts.get_contract("web_workflow.turn")
    if case.get("reject_code"):
        wire = json.dumps([["wrb.fr", contract.rpc_id, None, None, None, [case["reject_code"]]]])
    else:
        wire = json.dumps([["wrb.fr", contract.rpc_id, json.dumps(case["body"])]])
    read = AsyncMock(return_value=SimpleNamespace(status_code=200, text=wire))
    monkeypatch.setattr(rpc_contracts, "execute_contract", read)
    client = SimpleNamespace(generate_content=AsyncMock())
    arguments = FIXTURE["arguments"]
    result = asyncio.run(rpc_contracts.read_completed_music_failure_turn(client, arguments["chat_id"], arguments["rid"],
                                                                        arguments["rcid"], arguments["text"]))
    assert result.status == case["status"]
    read.assert_awaited_once_with(client, "web_workflow.turn", chat_id=arguments["chat_id"], limit=100)
    assert contract.mode == "read"
    client.generate_content.assert_not_awaited()


@pytest.mark.parametrize("status", [403, 429, 500])
def test_music_failure_transport_never_converts_http_rejection_into_failure_card_proof(monkeypatch, status):
    monkeypatch.setattr(rpc_contracts, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=status)))
    a = FIXTURE["arguments"]
    result = asyncio.run(rpc_contracts.read_completed_music_failure_turn(object(), a["chat_id"], a["rid"], a["rcid"], a["text"]))
    assert result.status == "rejected" and result.reject_code == status and result.value is None
