"""Exact native Canvas replacement, scope guards and four-copy verification."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.canvas_contracts import (
    CanvasDocumentShapeError, UnsupportedCanvasDocument, plain_document_matches, replace_plain_document,
)
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.canvas_edits import replace_canvas_text_result


ARTIFACT_ID = "artifact_fixture_document"


def document(text="Old document"):
    structured = [[[None, [None, 1, "Old heading"]], [None, [None, 0, text]]]]
    raw = [ARTIFACT_ID, None, "Fixture document", None, text, None, "version_fixture",
           [[1700000000, 0], None, 1, False, False], structured, "fixture.md", 1, None, None,
           [text, None, deepcopy(structured)]]
    return raw


def response(key, body, *, status=200, reject=None):
    part = ["wrb.fr", get_contract(key).rpc_id, json.dumps(body), None, None,
            [reject] if reject is not None else None]
    return SimpleNamespace(status_code=status, text=json.dumps([part]))


def calls(client):
    return [(call.args[0][0].rpcid, json.loads(call.args[0][0].payload))
            for call in client._batch_execute.await_args_list]


def run(awaitable):
    return asyncio.run(awaitable)


def test_plain_replacement_changes_only_four_slots_and_deepcopies_unknown_fields():
    raw = document()
    raw[3] = {"future_outer": [1, 2]}
    raw.extend([None] * (40 - len(raw)))
    raw[39] = ["unknown_outer", {"nested": [3]}]
    raw[13].extend([None, "markdown", {"future_document": [4]}])
    snapshot = deepcopy(raw)
    replaced = replace_plain_document(raw, "New text\n第二行")
    assert raw == snapshot and replaced is not raw
    expected = deepcopy(raw)
    expected[4] = expected[13][0] = "New text\n第二行"
    expected[8] = expected[13][2] = [[[None, [None, 0, "New text\n第二行"]]]]
    assert replaced == expected
    assert plain_document_matches(replaced, "New text\n第二行")
    replaced[39][1]["nested"].append(5)
    assert raw[39][1]["nested"] == [3]
    replaced[8][0][0][1][2] = "Only one copy"
    assert replaced[13][2][0][0][1][2] == "New text\n第二行"


def test_plain_replacement_explicitly_discards_allowed_heading_and_inline_formatting():
    raw = document()
    raw[8][0][0][1].append([[0, 3, [[2]]]])
    raw[13][2] = deepcopy(raw[8])
    replaced = replace_plain_document(raw, "Replacement")
    assert replaced[8] == [[[None, [None, 0, "Replacement"]]]]
    assert plain_document_matches(replaced, "Replacement")


@pytest.mark.parametrize("slot", ["outer_attachments", "outer_citations", "doc_attachments", "doc_citations",
                                  "attachment_node", "inline_citation", "other_variant", "code_kind"])
def test_replacement_refuses_attachments_citations_and_other_artifact_kinds(slot):
    raw = document()
    if slot == "outer_attachments":
        raw[5] = [["attachment_fixture"]]
    elif slot == "outer_citations":
        raw[11] = [["citation_fixture"]]
    elif slot == "doc_attachments":
        raw[13][1] = [["attachment_fixture"]]
    elif slot == "doc_citations":
        raw[13].append([["citation_fixture"]])
    elif slot == "attachment_node":
        raw[8][0].append([None, None, ["image_fixture"]])
    elif slot == "inline_citation":
        raw[8][0][0][1].append([[0, 3, [None, None, None, [[None, None, "source_fixture"]]]]])
    elif slot == "other_variant":
        raw.extend([None] * (18 - len(raw)))
        raw[17] = ["print('code')"]
    else:
        raw[10] = 2
    snapshot = deepcopy(raw)
    with pytest.raises(UnsupportedCanvasDocument):
        replace_plain_document(raw, "Replacement")
    assert raw == snapshot


@pytest.mark.parametrize("damage", ["outer", "variant", "text", "structured", "node_text", "annotation"])
def test_malformed_document_fails_closed_without_modifying_input(damage):
    raw = document()
    if damage == "outer":
        raw = {"unexpected": True}
    elif damage == "variant":
        raw[13] = False
    elif damage == "text":
        raw[4] = {"unexpected": True}
    elif damage == "structured":
        raw[8] = [False]
    elif damage == "node_text":
        raw[8][0][0][1][2] = 42
    else:
        raw[8][0][0][1].append([False])
    snapshot = deepcopy(raw)
    with pytest.raises(CanvasDocumentShapeError):
        replace_plain_document(raw, "Replacement")
    assert raw == snapshot


def test_update_registry_uses_the_actual_update_descriptor_not_version_navigation():
    contract = get_contract("canvas.update")
    assert contract.rpc_id == "taAw7b" and contract.mode == "mutation"
    assert contract.parser == "native_void_ack"
    raw = replace_plain_document(document(), "Replacement")
    assert json.loads(contract.build_payload(artifact_id=ARTIFACT_ID, updated_ez=raw)) == [ARTIFACT_ID, raw]
    for case, status in (([], "success"), (None, "empty"), ({"changed": True}, "changed_shape")):
        assert parse_contract_body(contract, case).status == status
    assert parse_contract_body(contract, [], reject_code=7).status == "rejected"
    with pytest.raises(ValueError):
        contract.build_payload(artifact_id="different", updated_ez=raw)


def test_replace_reads_the_same_artifact_independently_and_verifies_all_four_slots():
    before = document()
    after = replace_plain_document(before, "Replacement")
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("canvas.get", before), response("canvas.update", []), response("canvas.get", after),
    ]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text="Replacement"))
    assert result.ok and result.operation_state == "completed" and result.data["updated"] is True
    assert result.meta.verification_status == "verified_updated"
    assert result.data["artifact_id"] == ARTIFACT_ID and result.data["formatting_replaced"] is True
    assert calls(client) == [("vCj0tc", [ARTIFACT_ID]), ("taAw7b", [ARTIFACT_ID, after]), ("vCj0tc", [ARTIFACT_ID])]
    assert "updated_ez" not in result.to_dict()["data"]


def test_readback_accepts_observed_heading_and_paragraph_normalization_in_both_copies():
    text = "# Fixture heading\n\nReplacement body."
    after = replace_plain_document(document(), text)
    normalized = [[[None, [None, 1, "Fixture heading"]], [None, [None, 0, "Replacement body."]]]]
    after[8] = deepcopy(normalized)
    after[13][2] = deepcopy(normalized)
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("canvas.get", document()), response("canvas.update", []), response("canvas.get", after),
    ]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text=text))
    assert result.operation_state == "completed" and result.meta.verification_status == "verified_updated"
    assert result.data["updated"] is True
    assert len(calls(client)) == 3


def test_readback_normalizes_structured_block_edges_without_losing_source_whitespace():
    text = "  Replacement  body with preserved spaces.  \n"
    after = replace_plain_document(document(), text)
    normalized = [[[None, [None, 0, "Replacement  body with preserved spaces."]]]]
    after[8] = deepcopy(normalized)
    after[13][2] = deepcopy(normalized)
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("canvas.get", document()), response("canvas.update", []), response("canvas.get", after),
    ]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text=text))
    assert result.meta.verification_status == "verified_updated" and result.data["updated"] is True
    assert after[4] == after[13][0] == text
    assert result.data["item"]["text"] == text
    assert len(calls(client)) == 3


@pytest.mark.parametrize("slot", ["outer_text", "doc_text", "outer_structured", "doc_structured"])
def test_normalization_does_not_hide_lost_source_or_interior_structured_whitespace(slot):
    text = "  Replacement  body.  \n"
    after = replace_plain_document(document(), text)
    normalized = [[[None, [None, 0, "Replacement  body."]]]]
    after[8] = deepcopy(normalized)
    after[13][2] = deepcopy(normalized)
    if slot == "outer_text":
        after[4] = text.strip()
    elif slot == "doc_text":
        after[13][0] = text.strip()
    elif slot == "outer_structured":
        after[8][0][0][1][2] = "Replacement body."
    else:
        after[13][2][0][0][1][2] = "Replacement body."
    assert not plain_document_matches(after, text)


@pytest.mark.parametrize("boundary", ["\t", "\r", "\u3000", "\u00a0"])
def test_structured_whitespace_normalization_is_not_extended_beyond_observed_spaces_and_lf(boundary):
    text = boundary + "Replacement" + boundary
    after = replace_plain_document(document(), text)
    after[8] = after[13][2] = [[[None, [None, 0, "Replacement"]]]]
    assert not plain_document_matches(after, text)


@pytest.mark.parametrize("slot", [8, 13])
@pytest.mark.parametrize("damage", ["stale", "wrong_heading", "missing_paragraph", "annotation", "unknown_level"])
def test_normalization_never_hides_a_stale_or_differently_formatted_structured_copy(slot, damage):
    text = "# Fixture heading\n\nReplacement body."
    after = replace_plain_document(document(), text)
    normalized = [[[None, [None, 1, "Fixture heading"]], [None, [None, 0, "Replacement body."]]]]
    after[8] = deepcopy(normalized)
    after[13][2] = deepcopy(normalized)
    structured = after[8] if slot == 8 else after[13][2]
    if damage == "stale":
        structured[0][1][1][2] = "Stale body."
    elif damage == "wrong_heading":
        structured[0][0][1][1] = 0
    elif damage == "missing_paragraph":
        structured[0].pop()
    elif damage == "annotation":
        structured[0][1][1].append([[0, 3, [[2]]]])
    else:
        structured[0][0][1][1] = 2
    assert not plain_document_matches(after, text)


@pytest.mark.parametrize("slot", ["outer_text", "doc_text", "outer_structured", "doc_structured"])
def test_one_stale_copy_prevents_completion_even_if_public_text_parser_looks_updated(slot):
    after = replace_plain_document(document(), "Replacement")
    if slot == "outer_text":
        after[4] = "Stale"
    elif slot == "doc_text":
        after[13][0] = "Stale"
    elif slot == "outer_structured":
        after[8][0][0][1][2] = "Stale"
    else:
        after[13][2][0][0][1][2] = "Stale"
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("canvas.get", document()), response("canvas.update", []), response("canvas.get", after),
    ]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text="Replacement"))
    assert result.ok and result.operation_state == "accepted" and result.data["updated"] is False
    assert result.meta.verification_status == "read_back_mismatch"
    assert result.data["artifact_id"] == ARTIFACT_ID and result.warnings[0].code == "MUTATION_UNVERIFIED"
    assert len(calls(client)) == 3


@pytest.mark.parametrize("phase", ["read_before", "update", "read_after"])
def test_timeout_retains_identity_is_not_retryable_and_does_not_replay(phase):
    replies = {"read_before": [TimeoutError("fixture timeout")],
               "update": [response("canvas.get", document()), TimeoutError("fixture timeout")],
               "read_after": [response("canvas.get", document()), response("canvas.update", []), TimeoutError("fixture timeout")]}
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=replies[phase]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text="Replacement"))
    assert not result.ok and result.error_code == "TIMED_OUT" and result.retryable is False
    assert result.data["artifact_id"] == ARTIFACT_ID
    assert result.data["submitted"] is (phase != "read_before")
    assert result.meta.details["stage"] == phase
    assert len([call for call in calls(client) if call[0] == "taAw7b"]) == (0 if phase == "read_before" else 1)


@pytest.mark.parametrize("damage", ["http", "missing", "ambiguous", "rejected", "shape"])
def test_invalid_update_ack_never_claims_completion(damage):
    ack = response("canvas.update", [])
    if damage == "http":
        ack.status_code = 503
    elif damage == "missing":
        ack.text = "[]"
    elif damage == "ambiguous":
        parts = json.loads(ack.text)
        ack.text = json.dumps(parts + parts)
    elif damage == "rejected":
        ack = response("canvas.update", [], reject=7)
    else:
        ack = response("canvas.update", ["changed"])
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[response("canvas.get", document()), ack]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text="Replacement"))
    assert not result.ok and result.data["artifact_id"] == ARTIFACT_ID
    assert result.data["submitted"] is True and result.retryable is False
    assert len(calls(client)) == 2


@pytest.mark.parametrize("kind", ["http", "missing", "wrong_id", "attachments"])
def test_preflight_failure_never_submits_an_update(kind):
    raw = document()
    read = response("canvas.get", raw)
    if kind == "http":
        read.status_code = 503
    elif kind == "missing":
        read.text = "[]"
    elif kind == "wrong_id":
        raw[0] = "another_artifact"
        read = response("canvas.get", raw)
    else:
        raw[5] = [["attachment"]]
        read = response("canvas.get", raw)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=read))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text="Replacement"))
    assert not result.ok and result.data["submitted"] is False
    assert len(calls(client)) == 1


def test_unavailable_readback_is_accepted_without_exposing_raw_document():
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("canvas.get", document()), response("canvas.update", []), response("canvas.get", [], status=503),
    ]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text="Replacement"))
    assert result.operation_state == "accepted" and result.meta.verification_status == "read_back_unavailable"
    assert result.data["item"] is None and result.data["artifact_id"] == ARTIFACT_ID


@pytest.mark.parametrize("artifact_id,text", [(" ", "text"), (" padded ", "text"), (ARTIFACT_ID, None), (ARTIFACT_ID, False)])
def test_malformed_arguments_fail_before_any_request(artifact_id, text):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    result = run(replace_canvas_text_result(client, artifact_id, text=text))
    assert result.error_code == "INVALID_ARGUMENT"
    client._batch_execute.assert_not_awaited()


def test_explicit_empty_replacement_clears_the_document_in_all_copies():
    after = replace_plain_document(document(), "")
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("canvas.get", document()), response("canvas.update", []), response("canvas.get", after),
    ]))
    result = run(replace_canvas_text_result(client, ARTIFACT_ID, text=""))
    assert result.meta.verification_status == "verified_updated" and result.data["updated"] is True
