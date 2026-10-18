"""Observed public frontend contracts, with no live account calls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.canvas import get_canvas_artifact_result, list_canvas_chat_artifacts_result
from src.services.library import delete_library_asset_result, get_library_asset_result, list_library_assets_result
from src.services.sharing import create_public_link_result, delete_public_link_result


def response(key, body, *, status=200, reject=None, rpc_id=None):
    part = ["wrb.fr", rpc_id or get_contract(key).rpc_id, json.dumps(body),
            None, None, [reject] if reject is not None else None]
    return SimpleNamespace(status_code=status, text=json.dumps([part]))


def asset(*, kind=6, turn="r_library_fixture", native_id=None):
    return [["c_library_fixture", turn], [1700000000, 0], kind, "Library fixture",
            ["Fixture text", "https://example.test/image.png"], "rc_fixture", None, None, None, None, native_id]


def asset_id(row=None):
    return parse_contract_body("library.page", [[row or asset()]]).value["items"][0]["id"]


def link(token="link_fixture", *, full=True, title="Sharing fixture"):
    return [None, None, [full, title], token]


def calls(client):
    return [(call.args[0][0].rpcid, json.loads(call.args[0][0].payload))
            for call in client._batch_execute.await_args_list]


@pytest.mark.parametrize("section,filters", [
    ("all", [True, True, True, True, True, False, True, True, True, True]),
    ("documents", [False, False, False, True, True, False, False, True, False, False]),
    ("media", [True, True, True, False, False, False, True, False, True, True]),
    ("purchases", [False, False, False, False, False, True, False, False, False, False]),
])
def test_library_filters_and_continuation_match_frontend(section, filters):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("library.page", [[asset()], "next"])))
    result = asyncio.run(list_library_assets_result(client, section=section, page_size=20, next_page_token="previous"))
    assert result.ok and result.data["complete"] is False
    assert result.data["next_page_token"] == "next"
    assert result.data["items"][0]["id"] == asset_id()
    assert calls(client) == [("jGArJ", [filters, 20, "previous"])]
    assert client._batch_execute.await_args.kwargs == {"source_path": "/library", "close_on_error": False}


@pytest.mark.parametrize("key,body", [
    ("library.page", [[asset()]]), ("canvas.get", ["artifact_fixture", None, "Title", None, "text"]),
    ("sharing.create", [None, None, "link_fixture"]),
])
@pytest.mark.parametrize("failure", ["http", "missing", "ambiguous", "changed", "rejected"])
def test_inventory_or_artifact_failures_never_claim_observed_data(key, body, failure):
    reply = response(key, body)
    if failure == "http":
        reply.status_code = 503
    elif failure == "missing":
        reply = response(key, body, rpc_id="unrelated")
    elif failure == "ambiguous":
        parts = json.loads(reply.text)
        reply.text = json.dumps(parts + parts)
    elif failure == "changed":
        reply = response(key, {"unexpected": True})
    else:
        reply = response(key, body, reject=7)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=reply))
    if key == "library.page":
        result = asyncio.run(list_library_assets_result(client))
    elif key == "canvas.get":
        result = asyncio.run(get_canvas_artifact_result(client, "artifact_fixture"))
    else:
        result = asyncio.run(create_public_link_result(client, "c_fixture", "rc_fixture", title="Title"))
    assert not result.ok
    assert result.error_code == ("UPSTREAM_REJECTED" if failure == "rejected" else "UPSTREAM_CHANGED")


def test_library_get_resolves_later_page_and_preserves_native_identity():
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("library.page", [[], "next"]), response("library.page", [[asset(native_id="bmF0aXZl")]]),
    ]))
    result = asyncio.run(get_library_asset_result(client, "bmF0aXZl"))
    assert result.ok and result.data["found"] and result.data["complete"]
    assert result.data["item"]["native_id"] == "bmF0aXZl"
    assert calls(client)[1][1][-1] == "next"


def test_bounded_library_miss_is_not_absence_or_a_delete_authorization():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("library.page", [[asset()], "more"])))
    read = asyncio.run(get_library_asset_result(client, "missing", max_pages=1))
    assert read.ok and read.data["complete"] is False
    assert read.meta.verification_status == "target_not_observed"
    deleted = asyncio.run(delete_library_asset_result(client, "missing", max_pages=1))
    assert not deleted.ok and deleted.error_code == "VERIFICATION_FAILED"
    assert {rpc for rpc, _ in calls(client)} == {"jGArJ"}


def test_library_delete_uses_one_observed_target_and_fresh_complete_absence():
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("library.page", [[asset(kind=1, native_id="bmF0aXZl"), asset(turn="r_unrelated")]]),
        response("library.delete", [None, 1, []]),
        response("library.page", [[asset(turn="r_unrelated")]]),
    ]))
    result = asyncio.run(delete_library_asset_result(client, "bmF0aXZl"))
    assert result.ok and result.data["deleted"]
    assert result.meta.verification_status == "verified_deleted"
    assert calls(client)[1] == ("PGX16d", [[[["c_library_fixture", "r_library_fixture"], 1, None, None, "bmF0aXZl"]]])


def test_library_delete_rejects_an_ambiguous_legacy_group_before_mutation():
    first, second = asset(kind=1), asset(kind=1)
    second[3] = "Another generated image"
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("library.page", [[first, second]])))
    result = asyncio.run(delete_library_asset_result(client, asset_id(first)))
    assert not result.ok and result.error_code == "INVALID_ARGUMENT"
    assert len(calls(client)) == 1


def test_observed_legacy_documents_from_one_draft_keep_distinct_snapshot_handles():
    first, second = asset(kind=5), asset(kind=5)
    first[3], first[4] = "Document one", ["Fixture content one"]
    second[3], second[4] = "Document two", ["Fixture content two"]
    parsed = parse_contract_body("library.page", [[first, second], "continuation_fixture"])
    assert parsed.status == "success" and len(parsed.value["items"]) == 2
    items = parsed.value["items"]
    assert items[0]["id"] != items[1]["id"]
    assert all(item["id_kind"] == "content_snapshot" and item["native_id"] is None for item in items)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("library.page", [[first, second]])))
    result = asyncio.run(delete_library_asset_result(client, items[0]["id"]))
    assert not result.ok and result.error_code == "CAPABILITY_UNAVAILABLE"
    assert len(calls(client)) == 1


def test_library_snapshot_handle_is_deterministic_and_excludes_timestamp():
    first, second = asset(), asset()
    second[1] = [1800000000, 500]
    assert asset_id(first) == asset_id(second)
    second[3] = "Another document"
    assert asset_id(first) != asset_id(second)


def test_renamed_snapshot_in_the_same_upstream_scope_cannot_verify_deletion():
    old, renamed = asset(kind=1), asset(kind=1)
    renamed[3] = "Changed concurrently"
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("library.page", [[old]]), response("library.delete", []), response("library.page", [[renamed]]),
    ]))
    result = asyncio.run(delete_library_asset_result(client, asset_id(old)))
    assert result.ok and not result.data["deleted"]
    assert result.meta.verification_status == "read_back_mismatch"


def test_a_section_miss_cannot_claim_global_absence_or_delete_another_backend():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("library.page", [[]])))
    result = asyncio.run(delete_library_asset_result(client, "purchase_fixture"))
    assert not result.ok and result.error_code == "OPERATION_NOT_FOUND"
    assert result.meta.verification_status == "target_not_observed"
    assert result.data["section"] == "all" and len(calls(client)) == 1


@pytest.mark.parametrize("after,expected", [
    (response("library.page", [[asset(kind=1)]]), "read_back_mismatch"),
    (response("library.page", [], status=503), "read_back_unavailable"),
    (response("library.page", [[], "more"]), "read_back_unavailable"),
])
def test_library_ack_without_complete_absence_stays_accepted(after, expected):
    row = asset(kind=1)
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("library.page", [[row]]), response("library.delete", []), after,
    ]))
    result = asyncio.run(delete_library_asset_result(client, asset_id(row), max_pages=1))
    assert result.ok and result.meta.operation_state.value == "accepted"
    assert result.meta.verification_status == expected and not result.data["deleted"]


@pytest.mark.parametrize("kind", [4, 5, 6, 9, 10, 12, 13])
@pytest.mark.parametrize("native_id", [None, "bmF0aXZl"])
def test_current_library_caller_rejects_document_types_before_mutation(kind, native_id):
    row = asset(kind=kind, native_id=native_id)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("library.page", [[row]])))
    result = asyncio.run(delete_library_asset_result(client, asset_id(row)))
    assert not result.ok and result.error_code == "CAPABILITY_UNAVAILABLE"
    assert result.meta.verification_status == "unsupported_artifact_type"
    assert result.data["type_id"] == kind and result.data["deleted"] is False
    assert [key for key, _payload in calls(client)] == [get_contract("library.page").rpc_id]


def test_unknown_library_type_is_rejected_by_listing_before_mutation():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("library.page", [[asset(kind=99)]])))
    result = asyncio.run(delete_library_asset_result(client, "snapshot_fixture"))
    assert not result.ok and result.error_code == "UPSTREAM_CHANGED"
    assert [key for key, _payload in calls(client)] == [get_contract("library.page").rpc_id]


@pytest.mark.parametrize("kind,selector", [(0, 0), (1, 1), (2, 2), (3, 3), (7, 5), (8, 4), (11, 6)])
def test_current_library_supported_types_keep_one_exact_source_target(kind, selector):
    row = asset(kind=kind)
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("library.page", [[row]]), response("library.delete", []), response("library.page", [[]]),
    ]))
    result = asyncio.run(delete_library_asset_result(client, asset_id(row)))
    assert result.ok and result.data["deleted"] is True
    assert result.meta.verification_status == "verified_deleted"
    assert calls(client)[1] == (get_contract("library.delete").rpc_id, [[[["c_library_fixture", "r_library_fixture"], selector]]])


def test_create_public_link_uses_public_destination_and_reads_exact_token():
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("sharing.create", [None, None, "link_fixture"]),
        response("sharing.public_links", [[link(), link("other")]]),
    ]))
    result = asyncio.run(create_public_link_result(client, "c_fixture", "rc_fixture", title="Sharing fixture"))
    assert result.ok and result.data["verified"]
    assert result.meta.verification_status == "verified_created"
    assert calls(client) == [
        ("fuVx7", [None, "c_fixture", None, "rc_fixture", [True, "Sharing fixture"], ["zh-CN"], False, None, None, 1]),
        ("OXxBcc", []),
    ]


def test_single_response_share_preserves_the_native_omitted_false_default():
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("sharing.create", [None, None, "link_fixture"]),
        response("sharing.public_links", [[link(full=None)]]),
    ]))
    result = asyncio.run(create_public_link_result(client, "c_fixture", "rc_fixture", title="Sharing fixture", full_conversation=False))
    assert result.ok and result.data["verified"]
    assert calls(client)[0][1][4] == [False, "Sharing fixture"]


def test_create_without_draft_resolves_only_the_latest_verified_source():
    output = SimpleNamespace(metadata=["c_fixture", "r_fixture"], rcid="rc_observed")
    history = SimpleNamespace(cid="c_fixture", turns=[SimpleNamespace(role="model", model_output=output)])
    client = SimpleNamespace(read_chat=AsyncMock(return_value=history), _batch_execute=AsyncMock(side_effect=[
        response("sharing.create", [None, None, "link_fixture"]), response("sharing.public_links", [[link()]]),
    ]))
    result = asyncio.run(create_public_link_result(client, "c_fixture", title="Sharing fixture"))
    assert result.ok and result.data["verified"]
    client.read_chat.assert_awaited_once_with("c_fixture", limit=1)
    assert calls(client)[0][1][3] == "rc_observed"


@pytest.mark.parametrize("history", [
    None, SimpleNamespace(cid="other", turns=[]), SimpleNamespace(cid="c_fixture", turns=[]),
    SimpleNamespace(cid="c_fixture", turns=[SimpleNamespace(role="model", model_output=SimpleNamespace(metadata=["other"], rcid="rc_other"))]),
])
def test_unobserved_draft_never_submits_a_share_request(history):
    client = SimpleNamespace(read_chat=AsyncMock(return_value=history), _batch_execute=AsyncMock())
    result = asyncio.run(create_public_link_result(client, "c_fixture", title="Sharing fixture"))
    assert not result.ok and result.error_code == "UPSTREAM_CHANGED"
    client._batch_execute.assert_not_awaited()


@pytest.mark.parametrize("after,status", [
    (response("sharing.public_links", [[link("other")]]), "target_not_observed"),
    (response("sharing.public_links", [[link(full=False)]]), "read_back_mismatch"),
    (response("sharing.public_links", [[link(title="Other title")]]), "read_back_mismatch"),
    (response("sharing.public_links", [], status=503), "read_back_unavailable"),
])
def test_create_public_link_echo_is_not_independent_verification(after, status):
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[response("sharing.create", [None, None, "link_fixture"]), after]))
    result = asyncio.run(create_public_link_result(client, "c_fixture", "rc_fixture", title="Sharing fixture"))
    assert result.ok and not result.data["verified"]
    assert result.meta.operation_state.value == "accepted" and result.meta.verification_status == status


def test_delete_public_link_never_populates_delete_all_switch():
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("sharing.public_links", [[link(), link("unrelated")]]),
        response("sharing.delete", []), response("sharing.public_links", [[link("unrelated")]]),
    ]))
    result = asyncio.run(delete_public_link_result(client, "link_fixture"))
    assert result.ok and result.data["deleted"]
    assert result.meta.verification_status == "verified_deleted"
    assert calls(client)[1] == ("SgORbf", [None, "link_fixture"])


def test_delete_public_link_fresh_mismatch_and_read_error_stay_accepted():
    for after in (response("sharing.public_links", [[link()]]), TimeoutError()):
        client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
            response("sharing.public_links", [[link()]]), response("sharing.delete", []), after,
        ]))
        result = asyncio.run(delete_public_link_result(client, "link_fixture"))
        assert result.ok and not result.data["deleted"] and result.meta.operation_state.value == "accepted"


def test_absent_public_token_does_not_issue_a_mutation():
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("sharing.public_links", [])))
    result = asyncio.run(delete_public_link_result(client, "link_fixture"))
    assert result.ok and result.data["already_absent"]
    assert calls(client) == [("OXxBcc", [])]


@pytest.mark.parametrize("index,kind,language_index", [(13, "document", 4), (17, "code", 2)])
def test_canvas_version_reads_native_text_and_language(index, kind, language_index):
    body = [None] * (index + 1)
    body[0], body[2], body[4] = "artifact_fixture", "Title", "legacy text"
    content = [None] * (language_index + 1)
    content[0], content[language_index] = "Native content", "python"
    body[index] = content
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("canvas.get", body)))
    result = asyncio.run(get_canvas_artifact_result(client, "artifact_fixture"))
    assert result.ok and result.data["text"] == "Native content"
    assert result.data["kind"] == kind and result.data["language"] == "python"
    assert calls(client) == [("vCj0tc", ["artifact_fixture"])]


def test_empty_and_wrong_canvas_artifact_never_claim_content():
    for body, code in (([], "ARTIFACT_NOT_RETURNED"), (["other", None, "Title", None, "text"], "UPSTREAM_CHANGED")):
        client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("canvas.get", body)))
        result = asyncio.run(get_canvas_artifact_result(client, "artifact_fixture"))
        assert not result.ok and result.error_code == code


def test_canvas_chat_reads_observed_version_ids_with_source_and_draft_identity():
    candidate = ["rc_fixture"] + [None] * 30
    candidate[30] = [["artifact_fixture", None, "Title", None, "Content"]]
    turn = [["c_fixture", "r_fixture"], None, None, [[candidate]]]
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("canvas.chat_artifacts", [[turn]])))
    result = asyncio.run(list_canvas_chat_artifacts_result(client, "c_fixture", limit=5))
    assert result.ok and result.data["items"] == [{
        "artifact_id": "artifact_fixture", "title": "Title", "text": "Content", "kind": None,
        "type_id": None, "language": None, "latest_version_id": None,
        "chat_id": "c_fixture", "turn_id": "r_fixture", "response_id": "rc_fixture",
    }]
    assert calls(client) == [("hNvQHb", ["c_fixture", 5, None, 1, [1], [4], None, 1])]


def test_canvas_chat_reads_the_same_known_artifact_field_in_sparse_jspb():
    candidate = ["rc_fixture", {"31": [["artifact_fixture", None, "Title", None, "Content"]]}]
    turn = [["c_fixture", "r_fixture"], None, None, [[candidate]]]
    result = parse_contract_body("canvas.chat_artifacts", [[turn]], expected_chat_id="c_fixture")
    assert result.status == "success" and result.value["items"][0]["artifact_id"] == "artifact_fixture"


def test_canvas_versions_contract_reads_all_returned_versions_without_navigation_mutations():
    payload = get_contract("canvas.versions").build_payload(artifact_id="artifact_fixture")
    assert json.loads(payload) == ["artifact_fixture"]
    versions = [["version_one", None, "Title 1", None, "Text 1"],
                ["version_two", None, "Title 2", None, "Text 2"]]
    result = parse_contract_body("canvas.versions", [versions])
    assert result.status == "success"
    assert [item["artifact_id"] for item in result.value] == ["version_one", "version_two"]


def test_canvas_history_page_retains_continuation_when_latest_turn_has_no_artifact():
    turn = [["c_fixture", "r_latest"], None, None, [[["rc_latest"]]]]
    page = parse_contract_body("canvas.chat_artifacts", [[turn], "older_page_fixture"], expected_chat_id="c_fixture")
    assert page.status == "empty"
    assert page.value == {"items": [], "next_page_token": "older_page_fixture"}
    payload = get_contract("canvas.chat_artifacts").build_payload(chat_id="c_fixture", limit=10, next_page_token="older_page_fixture")
    assert json.loads(payload) == ["c_fixture", 10, "older_page_fixture", 1, [1], [4], None, 1]


@pytest.mark.parametrize("body", [[], [[]], [None]])
def test_canvas_versions_positive_empty_is_a_known_empty_proto(body):
    assert parse_contract_body("canvas.versions", body).status == "empty"


@pytest.mark.parametrize("body", [None, {}, [1], [[None]], [[[]]], [
    [["same", None, "Title", None, "Text"], ["same", None, "Title", None, "Text"]],
]])
def test_canvas_versions_malformed_or_duplicate_entries_cannot_be_usable_versions(body):
    assert parse_contract_body("canvas.versions", body).status == "changed_shape"


def test_canvas_chat_cannot_return_a_different_source_or_claim_empty_from_malformed_turn():
    for body in ([[[["other", "r_fixture"]]]], [[[None]]]):
        client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response("canvas.chat_artifacts", body)))
        result = asyncio.run(list_canvas_chat_artifacts_result(client, "c_fixture"))
        assert not result.ok and result.error_code == "UPSTREAM_CHANGED"


@pytest.mark.parametrize("body", [
    [[asset(), asset()]], [[[["c", "r"], None, True, "Title"]]], [[[["c", "r"], None, 6, 17]]],
    [[[[None, None], None, 6, "Title"]]], [[asset()], {"changed": True}],
])
def test_changed_library_item_cannot_hide_behind_a_valid_page(body):
    assert parse_contract_body("library.page", body).status == "changed_shape"
