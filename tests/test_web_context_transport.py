"""Observed Notebook and Task contracts, including failure and isolation boundaries."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.rpc_contracts import (
    WebWorkflowTransportChanged, apply_task_generation_transport, get_contract, notebook_attachment,
    notebook_file_reference,
)
from src.infrastructure.rpc_parsers import parse_contract_body
from src.infrastructure.webchannel import parse_protobuf
from src.services.web_context_transport import (
    WebContextUnavailable, delete_task_metadata, fetch_notebooklm_metadata, fetch_task_metadata, update_task_metadata,
)
from src.thinking_client import WebRequestOptions, inject_web_request_options


def _request():
    inner = [None] * 81
    inner[0] = ["synthetic", 0, None, None, None, None, 0]
    inner[3], inner[4] = "!synthetic-scaffold", "synthetic-context"
    inner[49], inner[54], inner[55] = 1, [[[[[1]]]]], [[1]]
    inner[68], inner[79], inner[80] = 1, 7, 2
    return inner


def _response(key, body=None, *, reject=None, status=200):
    value = json.dumps(body) if body is not None else None
    row = ["wrb.fr", get_contract(key).rpc_id, value, None, None, [reject] if reject is not None else None]
    return SimpleNamespace(status_code=status, text=json.dumps([row]))


def test_notebook_file_reference_has_only_observed_identity_and_metadata_fields():
    encoded = notebook_file_reference("notebooks/fixture", "Synthetic")
    fields = {f.number: f.value for f in parse_protobuf(base64.b64decode(encoded))}
    assert set(fields) == {1, 2, 3, 9, 12}
    assert fields[2] == 20 and fields[3] == b"Synthetic" and fields[9] == 14 and fields[12] == b""
    metadata = {f.number: f.value for f in parse_protobuf(fields[1])}
    notebook = {f.number: f.value for f in parse_protobuf(metadata[15])}
    assert notebook == {1: b"notebooks/fixture"}
    assert notebook_attachment("notebooks/fixture", "Synthetic", as_file_reference=True) == [
        [None, 20, 14, ""], "Synthetic", encoded,
    ]


def test_task_mode_preserves_model_thinking_and_unrelated_preferences():
    inner = _request()
    apply_task_generation_transport(inner)
    assert inner[49] == 40 and inner[83] == 1
    assert (inner[68], inner[79], inner[80]) == (1, 7, 2)
    assert inner[54] == [] and inner[55] is None


@pytest.mark.parametrize("mutate", [
    lambda value: value.__setitem__(3, None), lambda value: value.__setitem__(49, 11),
    lambda value: value.__setitem__(55, []), lambda value: value.__setitem__(0, {}),
])
def test_task_transport_refuses_changed_scaffolding(mutate):
    inner = _request()
    mutate(inner)
    with pytest.raises(WebWorkflowTransportChanged):
        apply_task_generation_transport(inner)


@pytest.mark.parametrize("task", [False, True])
def test_chat_and_task_notebooks_use_their_verified_attachment_forms(task):
    inner = _request()
    data = {"f.req": json.dumps([None, json.dumps(inner)])}
    patched = inject_web_request_options(data, WebRequestOptions(
        task_generation=task, notebooks=(("notebooks/fixture", "Synthetic"),)))
    result = json.loads(json.loads(patched["f.req"])[1])
    expected = notebook_attachment("notebooks/fixture", "Synthetic", as_file_reference=not task)
    assert result[0][3] == [expected]
    assert (result[79], result[80]) == (7, 2)
    assert json.loads(json.loads(data["f.req"])[1])[0][3] is None


@pytest.mark.parametrize("value", [None, "invalid", "{}", json.dumps([None, {}])])
def test_notebook_generation_cannot_silently_become_an_unattached_chat(value):
    with pytest.raises(WebWorkflowTransportChanged):
        inject_web_request_options({"f.req": value}, WebRequestOptions(notebooks=(("notebooks/id", "N"),)))


def test_native_notebook_metadata_is_read_without_chat_or_generation():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response(
        "notebooklm.list", [[["notebooks/id", "N", [1793440000], 2]]])))
    result = asyncio.run(fetch_notebooklm_metadata(client))
    assert result[0]["source_count"] == 2
    call = client._batch_execute.await_args
    assert call.args[0][0].payload == "[]"
    assert call.kwargs["source_path"] == "/app"


def test_task_delete_disables_sdk_replay_and_requires_separate_readback():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response("tasks.delete", [])))
    assert asyncio.run(delete_task_metadata(client, "c_fixture")) is None
    assert client._batch_execute.await_count == 1
    assert client._batch_execute.await_args.kwargs["current_retry"] == 0


@pytest.mark.parametrize(("field", "value", "expected_fields"), [
    ("description", "Synthetic title", {2: "Synthetic title", 14: "c_fixture"}),
    ("pinned", True, {14: "c_fixture", 20: 1}),
    ("pinned", False, {14: "c_fixture", 20: 0}),
])
def test_task_update_sends_one_field_mask_and_exact_goal_identity(field, value, expected_fields):
    goal = [None] * 14
    goal[13] = "c_fixture"
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response("tasks.update_metadata", [goal])))

    asyncio.run(update_task_metadata(client, "c_fixture", field, value))

    client._batch_execute.assert_awaited_once()
    call = client._batch_execute.await_args
    rpc = call.args[0][0]
    assert rpc.rpcid == "Dxg8ke" and call.kwargs["current_retry"] == 0
    goal, mask = json.loads(rpc.payload)
    assert {index: entry for index, entry in enumerate(goal, 1) if entry is not None} == expected_fields
    assert mask == [[field]] and call.kwargs["source_path"] == "/app"


@pytest.mark.parametrize(("field", "value"), [("status", 8), ("pinned", 1), ("description", " ")])
def test_task_update_refuses_unknown_or_invalid_mutation_fields(field, value):
    with pytest.raises(ValueError):
        get_contract("tasks.update_metadata").build_payload(chat_id="c_fixture", field=field, value=value)


def test_task_update_acknowledgment_must_match_the_requested_goal():
    goal = [None] * 14
    goal[13] = "c_other"
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response("tasks.update_metadata", [goal])))

    with pytest.raises(WebContextUnavailable):
        asyncio.run(update_task_metadata(client, "c_fixture", "pinned", True))
    client._batch_execute.assert_awaited_once()


def test_task_not_found_code_is_distinct_from_missing_or_rejected_evidence():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response("tasks.get", reject=5)))
    assert asyncio.run(fetch_task_metadata(client, "c_fixture")) is None
    for response in (_response("tasks.get", reject=7), _response("tasks.get", status=403),
                     SimpleNamespace(text="[]"), _response("tasks.get", {"changed": True}),
                     _response('tasks.get', []), _response('tasks.get', [None])):
        client._batch_execute.return_value = response
        with pytest.raises(WebContextUnavailable):
            asyncio.run(fetch_task_metadata(client, "c_fixture"))


def test_notebook_graphql_errors_do_not_become_an_empty_catalog():
    assert parse_contract_body("notebooklm.picker", [{"path": [], "errors": [{"message": "rejected"}],
                                                      "data": {}}]).status == "rejected"


@pytest.mark.parametrize("count", [True, -1, 1.5, float("nan"), float("inf"), "2"])
def test_notebook_picker_rejects_invalid_source_counts(count):
    body = [{"path": [], "data": {"viewer": {"rpcs": {"listNotebooks": {"notebooks": [
        {"id": "notebooks/id", "displayName": "N", "sourceCount": count},
    ]}}}}}]
    assert parse_contract_body("notebooklm.picker", body).status == "changed_shape"


@pytest.mark.parametrize("state,completed", [(0, False), (2, True), (1, False), (None, False), (True, False), (3, False)])
def test_workflow_reply_requires_exact_completed_candidate(state, completed):
    candidate = ["rc_target", ["answer"], None, None, None, None, None, None, [state]]
    turn = [[None, "r_target"], None, None, [[candidate]]]
    parsed = parse_contract_body("web_workflow.turn", [[turn]], chat_id="c_target", rid="r_target", rcid="rc_target")
    assert parsed.ok and parsed.value["completed"] is completed
    assert parsed.value["text"] == ("answer" if completed else None)
    assert parse_contract_body("web_workflow.turn", [[turn]], chat_id="c_target", rid="r_other", rcid="rc_target").status == "empty"
    assert parse_contract_body("web_workflow.turn", [[turn, turn]], chat_id="c_target", rid="r_target", rcid="rc_target").status == "changed_shape"
    assert parse_contract_body("web_workflow.turn", [[turn]], chat_id="c_target", rid="r_target", rcid="rc_other").status == "empty"


def test_task_reply_uses_native_continuation_identifiers_without_replaying_old_turn_metadata():
    inner = _request()
    inner[2] = ["c_0123456789abcdef", "r_previous", "rc_previous"]
    apply_task_generation_transport(inner, conversation_id="c_0123456789abcdef")
    assert inner[2] is None
    assert inner[71] == ["c_0123456789abcdef", None, None, None, None, None, None, ""]
    assert inner[49] == 40 and inner[83] == 1
    assert (inner[79], inner[80]) == (7, 2)


@pytest.mark.parametrize("cid", ["", "different", "c_bad", "c_0123456789abcdef/path"])
def test_task_reply_rejects_unverified_continuation_identity(cid):
    with pytest.raises(WebWorkflowTransportChanged):
        apply_task_generation_transport(_request(), conversation_id=cid)


def test_selected_native_card_choice_uses_only_fk90_field3_and_preserves_model_thinking():
    data = {'f.req': json.dumps([None, json.dumps(_request())])}
    patched = inject_web_request_options(data, WebRequestOptions(
        task_generation=True, task_conversation_id='c_0123456789abcdef', task_action_choice_id='vm',
        thinking_mode_id=7, thinking_level_id=2))
    inner = json.loads(json.loads(patched['f.req'])[1])
    assert inner[89] == [None, None, 'vm']
    assert inner[71][0] == 'c_0123456789abcdef' and inner[2] is None
    assert (inner[49], inner[79], inner[80], inner[83]) == (40, 7, 2, 1)
    assert len(json.loads(json.loads(data['f.req'])[1])) == 81


@pytest.mark.parametrize('choice', ['', True, ' ', 'x' * 513, 'vm\n'])
def test_task_choice_refuses_invalid_identity_and_never_guesses_permission_scope(choice):
    with pytest.raises(WebWorkflowTransportChanged):
        apply_task_generation_transport(_request(), conversation_id='c_0123456789abcdef', action_choice_id=choice)


def test_task_choice_requires_continuation_and_refuses_to_overwrite_unknown_context():
    with pytest.raises(WebWorkflowTransportChanged):
        apply_task_generation_transport(_request(), action_choice_id='vm')
    inner = _request() + [None] * 9
    inner[89] = ['unknown-existing-context']
    with pytest.raises(WebWorkflowTransportChanged):
        apply_task_generation_transport(inner, conversation_id='c_0123456789abcdef', action_choice_id='vm')
    assert inner[89] == ['unknown-existing-context']
    with pytest.raises(WebWorkflowTransportChanged):
        inject_web_request_options({'f.req': '[]'}, WebRequestOptions(task_action_choice_id='vm'))


def test_task_confirmation_returns_observed_button_queries_and_never_a_final_answer():
    card = [None] * 11
    card[2] = "Confirm synthetic fixture deletion?"
    card[6] = [["Confirm", [None, None, None, "yes_exact"]]]
    card[8] = [["Reject", [None, None, None, "no_exact"]]]
    rich = [None] * 35
    rich[34] = [[["http://googleusercontent.com/action_card_content/synthetic"], {"154": card}]]
    candidate = [None] * 13
    candidate[0], candidate[1], candidate[8], candidate[12] = "rc_card", [card[2]], [2], rich
    parsed = parse_contract_body("web_workflow.turn", [[[[None, "r_card"], None, None, [[candidate]]]]],
                                 chat_id="c_target", rid="r_card", rcid="rc_card")
    assert parsed.ok and parsed.value["input_required"] and not parsed.value["completed"]
    assert parsed.value["text"] is None
    assert [(option['label'], option['prompt']) for option in parsed.value['input_options']] == [
        ('Confirm', 'yes_exact'), ('Reject', 'no_exact'),
    ]
    assert all(option['input_option_id'].startswith('option_') for option in parsed.value['input_options'])
