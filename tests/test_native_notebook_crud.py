"""Current public Notebook CRUD/source contracts and independent verification."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services import notebooks


FIXTURES = json.loads((Path(__file__).parent / "fixtures/rpc_management_cases.json").read_text())


def record(*, title="Fixture Notebook", instructions="Fixture instructions", sources=True):
    value = deepcopy(FIXTURES["notebook_get"]["cases"]["success"]["body"][0])
    value[1][0] = title
    value[2][0] = instructions
    if not sources:
        value[1][10] = [[], []]
    return value


def source_record(*, text=None, url=None, upload_reference=None, mime_type="text/plain", state=2):
    value = record()
    source = value[1][10][1][0][1]
    source[3:6] = [None, None, None]
    source[6], source[8] = state, mime_type
    if text is not None:
        source[5] = [text, 2 if mime_type == "text/markdown" else 1]
    if url is not None:
        source[3] = [url]
    if upload_reference is not None:
        source[4] = [upload_reference, mime_type]
    return value


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def _batch_execute(self, requests, *, source_path, close_on_error):
        request = requests[0]
        self.calls.append((request.rpcid, json.loads(request.payload), source_path))
        key, body, *options = self.responses.pop(0)
        assert request.rpcid == get_contract(key).rpc_id
        status, reject = options if options else (200, None)
        text = json.dumps([["wrb.fr", request.rpcid, json.dumps(body), None, None, [reject] if reject else None]])
        return SimpleNamespace(status_code=status, text=text)


def run(awaitable):
    return asyncio.run(awaitable)


def test_get_exposes_typed_sources_without_internal_write_metadata():
    client = Client(("notebooks.get", [record()]))
    result = run(notebooks.get_notebook(client, "notebooks/fixture"))
    assert result.ok and result.data["item"]["instructions"] == "Fixture instructions"
    assert result.data["item"]["sources"][0]["name"] == "sources/fixture"
    assert result.data["item"]["sources"][0]["text"] == "Fixture source text"
    assert "metadata" not in result.data
    assert client.calls[0][1] == ["notebooks/fixture", ["zh-CN"], False]


@pytest.mark.parametrize("damage", ["gem", "wrong_id", "invalid_source", "duplicate_source", "source_context", "associations"])
def test_notebook_detail_changed_shape_cannot_become_mutation_input(damage):
    item = record()
    if damage == "gem":
        item[1][12][0] = 1
    elif damage == "wrong_id":
        item[0] = "other"
    elif damage == "invalid_source":
        item[1][10][1][0][1][6] = "ready"
    elif damage == "duplicate_source":
        item[1][10][1].append(item[1][10][1][0])
    elif damage == "source_context":
        item[1][10] = "changed"
    else:
        item[1][10][0] = [False]
    parsed = parse_contract_body("notebooks.get", [item], expected_id="notebooks/fixture")
    assert parsed.status == "changed_shape"


def test_create_uses_notebook_namespace_and_independent_get():
    client = Client(("notebooks.create", ["notebooks/fixture"]), ("notebooks.get", [record(instructions="", sources=False)]),
                    ("notebooks.update", [True]), ("notebooks.get", [record(sources=False)]))
    result = run(notebooks.create_notebook(client, title="Fixture Notebook", instructions="Fixture instructions"))
    assert result.ok and result.operation_state == "completed" and result.data["verified"] is True
    assert [call[0] for call in client.calls] == ["oMH3Zd", "HcT8bb", "kHv0Vd", "HcT8bb"]
    metadata = client.calls[0][1][0]
    assert metadata[:3] == ["Fixture Notebook", "", ""]
    assert metadata[16] == [2, None, None, None, 1, []]


@pytest.mark.parametrize("body,status,reject", [([], 503, None), ([], 200, 7), ([record(title="Stale")], 200, None)])
def test_create_ack_does_not_claim_completion_when_readback_is_invalid_or_stale(body, status, reject):
    client = Client(("notebooks.create", ["notebooks/fixture"]), ("notebooks.get", body, status, reject))
    if body and status == 200 and reject is None:
        client.responses.extend([("notebooks.update", [True]), ("notebooks.get", body)])
    result = run(notebooks.create_notebook(client, title="Fixture Notebook", instructions="Fixture instructions"))
    assert result.ok and result.operation_state == "accepted"
    assert result.data["id"] == "notebooks/fixture" and result.data["verified"] is False
    assert result.warnings[0].code == "MUTATION_UNVERIFIED"
    assert len(client.calls) == (4 if body and status == 200 and reject is None else 2)


def test_update_maps_response_metadata_to_request_fields_and_preserves_source_ids():
    original = record()
    original[1][7] = True
    original[1][8] = False
    original[1][14] = ["🌱", [123], None, "sources/fixture", [456], None, True, 2, False]
    changed = deepcopy(original)
    changed[1][0] = "Updated"
    client = Client(("notebooks.get", [original]), ("notebooks.update", [True]), ("notebooks.get", [changed]))
    result = run(notebooks.update_notebook(client, "notebooks/fixture", title="Updated"))
    assert result.operation_state == "completed"
    metadata = client.calls[1][1][1]
    assert metadata[:3] == ["Updated", "Fixture description", "Fixture instructions"]
    assert metadata[8:10] == [False, True]
    assert metadata[14] == [[[None, None, None, None, None, "sources/fixture"]]]
    assert metadata[16] == original[1][12]
    assert metadata[18] == ["🌱", None, None, None, None, None, True, 2, False]


def test_update_losing_a_source_is_unverified():
    client = Client(("notebooks.get", [record()]), ("notebooks.update", [True]),
                    ("notebooks.get", [record(title="Updated", sources=False)]))
    result = run(notebooks.update_notebook(client, "notebooks/fixture", title="Updated"))
    assert result.operation_state == "accepted" and result.data["verified"] is False


def test_delete_uses_valid_complete_registry_absence():
    client = Client(("notebooks.get", [record()]), ("notebooks.delete", []),
                    ("notebooks.list", [None, None, []]))
    result = run(notebooks.delete_notebook(client, "notebooks/fixture"))
    assert result.operation_state == "completed"
    assert client.calls[1][0:2] == ("UXcSJb", ["notebooks/fixture"])


def test_delete_with_chats_does_not_claim_chat_deletion_from_notebook_absence():
    client = Client(("notebooks.get", [record()]), ("notebooks.delete_with_chats", []),
                    ("notebooks.list", [None, None, []]))
    result = run(notebooks.delete_notebook(client, "notebooks/fixture", delete_chats=True))
    assert result.operation_state == "accepted"
    assert result.meta.verification_status == "notebook_absent_chats_unverified"
    assert client.calls[1][0] == "Nwkn9"


@pytest.mark.parametrize("kind", ["text", "markdown", "url", "upload"])
def test_add_source_uses_observed_oneof_and_separate_get(kind):
    values = {"text": {"text": "Fixture source text"},
              "markdown": {"text": "Fixture source text", "mime_type": "text/markdown"},
              "url": {"url": "https://example.com/source"},
              "upload": {"upload_reference": "/contrib_service/file", "mime_type": "application/pdf"}}[kind]
    observed = source_record(**values)
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [observed]),
                    ("notebooks.get", [observed]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source", **values))
    assert result.operation_state == "completed"
    assert result.data["source_name"] == "sources/fixture"
    assert client.calls[1][1][2] == [1, 3]
    source = client.calls[1][1][1]
    assert source[1] == "Fixture source"
    index, expected = {"text": (5, ["Fixture source text", 1]),
                       "markdown": (5, ["Fixture source text", 2]),
                       "url": (3, ["https://example.com/source"]),
                       "upload": (4, ["/contrib_service/file", "application/pdf"])}[kind]
    assert source[index] == expected
    assert sum(source[i] is not None for i in (3, 4, 5)) == 1


@pytest.mark.parametrize("kind", ["text", "url", "upload"])
@pytest.mark.parametrize("damage", ["value", "kind", "missing", "ambiguous"])
def test_source_readback_must_match_requested_kind_and_content_without_replaying(kind, damage):
    values = {"text": {"text": "Requested text"}, "url": {"url": "https://example.com/requested"},
              "upload": {"upload_reference": "/contrib_service/requested", "mime_type": "application/pdf"}}[kind]
    observed = source_record(**values)
    source = observed[1][10][1][0][1]
    index = {"text": 5, "url": 3, "upload": 4}[kind]
    if damage == "value":
        source[index][0] = "Different content or reference"
    elif damage == "kind":
        source[3:6] = [None, None, None]
        source[3 if kind == "text" else 5] = ["https://example.com/other"] if kind == "text" else ["Other text", 1]
    elif damage == "missing":
        source[index] = None
    else:
        source[3 if kind == "text" else 5] = ["https://example.com/other"] if kind == "text" else ["Other text", 1]
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [source_record(**values)]),
                    ("notebooks.get", [observed]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source", **values))
    assert result.ok and result.operation_state == "accepted" and result.data["verified"] is False
    assert result.data["source_name"] == "sources/fixture"
    assert len(client.calls) == 3 and sum(call[0] == get_contract("notebooks.add_source").rpc_id for call in client.calls) == 1


def test_markdown_source_requires_matching_readback_format():
    observed = source_record(text="# Requested heading", mime_type="text/markdown")
    observed[1][10][1][0][1][5][1] = 1
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [record()]),
                    ("notebooks.get", [observed]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source",
                                              text="# Requested heading", mime_type="text/markdown"))
    assert result.operation_state == "accepted" and result.data["verified"] is False


def test_url_source_with_empty_text_placeholder_has_only_one_active_kind():
    observed = source_record(url="https://example.com/requested")
    observed[1][10][1][0][1][5] = ["", 0]
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [observed]),
                    ("notebooks.get", [observed]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source",
                                              url="https://example.com/requested"))
    assert result.operation_state == "completed" and result.data["item"]["source_kind"] == "url"


@pytest.mark.parametrize("damage", ["ambiguous", "text_format_type", "text_format_unknown"])
def test_source_parser_rejects_ambiguous_content_and_invalid_text_format(damage):
    observed = source_record(text="Fixture source text")
    source = observed[1][10][1][0][1]
    if damage == "ambiguous":
        source[3] = ["https://example.com/other"]
    else:
        source[5][1] = True if damage == "text_format_type" else 7
    assert parse_contract_body("notebooks.get", [observed], expected_id="notebooks/fixture").status == "changed_shape"


def test_upload_source_mime_mismatch_remains_unverified():
    observed = source_record(upload_reference="/contrib_service/requested", mime_type="text/plain")
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [observed]),
                    ("notebooks.get", [observed]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source",
                                              upload_reference="/contrib_service/requested", mime_type="application/pdf"))
    assert result.operation_state == "accepted" and result.meta.verification_status == "source_content_mismatch"


@pytest.mark.parametrize("state", [1, 4])
def test_processing_source_without_populated_content_remains_accepted(state):
    observed = source_record(state=state)
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [observed]),
                    ("notebooks.get", [observed]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source", text="fixture"))
    assert result.operation_state == "accepted" and result.meta.verification_status == "source_processing"
    assert result.data["source_name"] == "sources/fixture" and result.data["verified"] is False


def test_ready_source_with_only_indexed_resource_references_cannot_prove_original_content():
    observed = source_record()
    source = observed[1][10][1][0][1]
    source.extend([None] * (15 - len(source)))
    source[14] = ["https://example.com/fixture-indexed-resource", "fixture-reference"]
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [observed]),
                    ("notebooks.get", [observed]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source", text="Requested text"))
    assert result.operation_state == "accepted" and result.meta.verification_status == "source_content_unobserved"
    assert result.data["source_name"] == "sources/fixture" and result.data["verified"] is False
    assert len(client.calls) == 3


def test_source_ack_without_created_name_fails_before_independent_read():
    item = record()
    item[1][14] = []
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [item]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source", text="text"))
    assert not result.ok and result.error_code == "UPSTREAM_CHANGED"
    assert len(client.calls) == 2


def test_remove_source_requires_membership_and_valid_absence():
    client = Client(("notebooks.get", [record()]), ("notebooks.remove_source", []),
                    ("notebooks.get", [record(sources=False)]))
    result = run(notebooks.remove_notebook_source(client, "notebooks/fixture", "sources/fixture"))
    assert result.operation_state == "completed"
    assert client.calls[1][1] == ["sources/fixture", [1, 3]]
    wrong = Client(("notebooks.get", [record()]))
    result = run(notebooks.remove_notebook_source(wrong, "notebooks/fixture", "sources/not-owned"))
    assert not result.ok and len(wrong.calls) == 1


@pytest.mark.parametrize("operation", ["create", "update", "source", "delete"])
def test_malformed_arguments_fail_before_network(operation):
    client = Client()
    if operation == "create":
        result = run(notebooks.create_notebook(client, title="", subtype=4))
    elif operation == "update":
        result = run(notebooks.update_notebook(client, "n", title=False))
    elif operation == "source":
        result = run(notebooks.add_notebook_source(client, "n", display_name="name", text="a", url="https://example.com"))
    else:
        result = run(notebooks.delete_notebook(client, "n", delete_chats="false"))
    assert not result.ok and result.error_code == "INVALID_ARGUMENT" and not client.calls


def test_notebook_description_cannot_create_or_mutate_a_resource_silently():
    client = Client()
    create = run(notebooks.create_notebook(client, title="Notebook", description="Unsupported description"))
    update = run(notebooks.update_notebook(client, "notebooks/fixture", description="Unsupported description"))
    assert create.error_code == update.error_code == "CAPABILITY_UNAVAILABLE"
    assert not client.calls


@pytest.mark.parametrize("body,status", [([True], "success"), ([False], "rejected"), ([], "empty"),
                                        ([1], "changed_shape"), ([False, True], "changed_shape"), (True, "changed_shape")])
def test_current_notebook_update_boolean_ack_is_strict(body, status):
    assert parse_contract_body("notebooks.update", body).status == status


def test_rejected_initial_instruction_update_retains_created_id_without_recreating():
    client = Client(("notebooks.create", ["notebooks/fixture"]), ("notebooks.get", [record(instructions="", sources=False)]),
                    ("notebooks.update", [False]))
    result = run(notebooks.create_notebook(client, title="Fixture Notebook", instructions="Fixture instructions"))
    assert result.operation_state == "accepted" and result.data["id"] == "notebooks/fixture"
    assert result.meta.verification_status == "initial_update_unverified"
    assert len([call for call in client.calls if call[0] == "oMH3Zd"]) == 1


def test_source_file_upload_uses_sdk_session_after_target_validation(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"synthetic PDF fixture")
    uploads = []

    async def upload(file, *, client, push_id):
        uploads.append((file, client, push_id))
        return "/contrib_service/source-fixture"

    import gemini_webapi.utils
    monkeypatch.setattr(gemini_webapi.utils, "upload_file", upload)
    observed = source_record(upload_reference="/contrib_service/source-fixture", mime_type="application/pdf")
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [observed]),
                    ("notebooks.get", [observed]))
    client.client, client.push_id = object(), "ordinary-sdk-push-id"
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source", file_path=str(source)))
    assert result.operation_state == "completed"
    assert uploads == [(source, client.client, client.push_id)]
    assert client.calls[1][1][1][4] == ["/contrib_service/source-fixture", "application/pdf"]


def test_source_file_is_not_uploaded_for_an_unverified_target(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("fixture")
    uploads = []

    async def upload(*args, **kwargs):
        uploads.append(True)

    import gemini_webapi.utils
    monkeypatch.setattr(gemini_webapi.utils, "upload_file", upload)
    client = Client(("notebooks.get", [], 503, None))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="source", file_path=str(source)))
    assert not result.ok and not uploads


@pytest.mark.parametrize("state,status", [(1, "source_processing"), (4, "source_processing"), (3, "source_not_ready")])
def test_added_source_retention_does_not_claim_indexing_complete(state, status):
    processing = source_record(text="fixture", state=state)
    client = Client(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [processing]),
                    ("notebooks.get", [processing]))
    result = run(notebooks.add_notebook_source(client, "notebooks/fixture", display_name="Fixture source", text="fixture"))
    assert result.operation_state == "accepted" and result.data["source_name"] == "sources/fixture"
    assert result.meta.verification_status == status and result.data["verified"] is False
