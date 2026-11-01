"""Synthetic native app-connection cards, without live identities or OAuth URLs."""

from copy import deepcopy
import json

import pytest

from src.infrastructure.rpc_parsers import parse_workflow_turn
from src.infrastructure.task_setup import parse_app_setup_card


def _candidate():
    candidate = [None] * 34
    candidate[0], candidate[1], candidate[8] = "rc_setup", ["Connect the fixture app first."], [2]
    candidate[33] = [[
        "fixture_app_tool", [1, None, "fixture_app_tool"],
        [None, "Fixture App", "Synthetic app description", "https://example.invalid/icon.png"],
    ]]
    return candidate


def _parse(candidate):
    return parse_workflow_turn([[[[None, "r_setup"], None, None, [[candidate]]]]],
                               chat_id="c_setup", rid="r_setup", rcid="rc_setup")


def test_pending_native_connection_card_is_not_a_final_answer_or_text_approval():
    candidate = _candidate()
    candidate[33][0].extend([None, [True, ["PRIVATE_OAUTH_PROVIDER", ["PRIVATE_SCOPE"]]]])
    original = deepcopy(candidate)
    result = _parse(candidate)
    assert result.ok and result.value["setup_required"] is True
    assert result.value["setup_kind"] == "app_connection"
    assert result.value["setup_app_name"] == "Fixture App"
    assert not result.value["completed"] and result.value["text"] is None
    assert not result.value["input_required"] and result.value["input_options"] == []
    assert "input_card" not in result.value
    serialized = json.dumps(result.value)
    assert "PRIVATE_OAUTH_PROVIDER" not in serialized and "PRIVATE_SCOPE" not in serialized
    assert "fixture_app_tool" not in serialized and "example.invalid" not in serialized
    assert candidate == original


@pytest.mark.parametrize("state", [0, 2, 3, 10, 999, True, None, "1"])
def test_declined_granted_or_unknown_consent_is_never_misclassified_as_pending_setup(state):
    candidate = _candidate()
    candidate[33][0][1][0] = state
    result = _parse(candidate)
    assert result.ok and result.value["setup_required"] is False
    assert not result.value["input_required"] and not result.value["input_options"]
    assert not result.value["completed"]


@pytest.mark.parametrize("changed", ["rows", "multiple", "row", "identity", "missing_tool",
                                    "consent_shape", "consent_context", "metadata", "name", "icon",
                                    "control_name", "incomplete", "bool_completion"])
def test_changed_native_setup_shapes_never_expose_a_setup_or_executable_reply(changed):
    candidate = _candidate()
    row = candidate[33][0]
    if changed == "rows":
        candidate[33] = {"unknown": "native shape"}
    elif changed == "multiple":
        candidate[33].append(deepcopy(row))
    elif changed == "row":
        candidate[33][0] = None
    elif changed == "identity":
        row[0] = "different_tool"
    elif changed == "missing_tool":
        row[1][2] = ""
    elif changed == "consent_shape":
        row[1].append("unknown")
    elif changed == "consent_context":
        row[1][1] = ["unknown"]
    elif changed == "metadata":
        row[2] = None
    elif changed == "name":
        row[2][1] = ""
    elif changed == "icon":
        row[2][3] = 1
    elif changed == "control_name":
        row[2][1] = "Fixture\nInjected label"
    elif changed == "incomplete":
        candidate[8] = [1]
    else:
        candidate[8] = [True]
    result = _parse(candidate)
    assert result.ok and not result.value["setup_required"]
    assert not result.value["input_options"] and not result.value["input_required"]
    assert not result.value["completed"]


@pytest.mark.parametrize("rows", [None, []])
def test_absent_setup_content_preserves_the_normal_answer(rows):
    candidate = _candidate()
    candidate[33] = rows
    result = _parse(candidate)
    assert result.ok and result.value["completed"]
    assert not result.value["setup_required"] and not result.value["setup_card_present"]
    assert result.value["text"] == "Connect the fixture app first."


def test_setup_parser_does_not_use_message_words_as_a_connection_contract():
    candidate = _candidate()
    candidate[33] = None
    candidate[1] = ["You must connect an app; continue or no thanks?"]
    assert parse_app_setup_card(candidate) == {"setup_card_present": False, "setup_required": False}


def test_native_setup_takes_precedence_over_a_mixed_ordinary_confirmation_card():
    candidate = _candidate()
    card = [None] * 9
    card[7] = [["Continue", [None, None, None, "connect the app"]]]
    candidate[12] = [None] * 35
    candidate[12][34] = [[None, {"154": card}]]
    result = _parse(candidate)
    assert result.ok and result.value["setup_required"]
    assert not result.value["input_required"] and not result.value["input_options"]
    assert "input_card" not in result.value
