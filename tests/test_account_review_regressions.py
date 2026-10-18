"""Account regressions use complete offline RPC evidence, never a real account."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.adapters.mcp_sdk import MCPServer
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.doctor import doctor_payload, format_doctor_markdown
from src.services.history import delete_chat_result, list_chats_result, search_chats_result
from src.services.scheduled import delete_action
from src.tools import manage


def _response(key, body, *, status_code=200, reject_code=None):
    rpc_id = get_contract(key).rpc_id
    reject = [reject_code] if reject_code is not None else None
    return SimpleNamespace(
        status_code=status_code,
        text=json.dumps([["wrb.fr", rpc_id, json.dumps(body), None, None, reject, "generic"]]),
    )


class _ScheduledClient:
    def __init__(self, get_response, registry_body=None, delete_response=None):
        self.responses = {
            get_contract("scheduled.delete").rpc_id: delete_response or _response("scheduled.delete", []),
            get_contract("scheduled.registry").rpc_id: _response("scheduled.registry", registry_body or [[_task("other")]]),
            get_contract("scheduled.get").rpc_id: get_response,
        }

    async def _batch_execute(self, requests, **_kwargs):
        return self.responses[requests[0].rpcid]


def _task(identifier, deleted=False):
    return [identifier, [["Instructions", "Daily", "Task"]], [None, None, None, [] if deleted else None]]


@pytest.mark.parametrize("row", [{"new_shape": "keep"}, [], [None, "Title"], ["", "Title"]])
def test_changed_history_row_cannot_verify_deletion(row):
    client = SimpleNamespace(delete_chat=AsyncMock(), _batch_execute=AsyncMock(return_value=_response("history.page", [None, None, [row]])))
    result = asyncio.run(delete_chat_result(client, "keep"))
    assert result.ok is False
    assert result.meta.verification_status == "read_back_error"
    assert result.data["deleted"] is None


@pytest.mark.parametrize("token", [{"cursor": "next"}, 12, False])
def test_invalid_history_continuation_cannot_verify_deletion(token):
    client = SimpleNamespace(delete_chat=AsyncMock(), _batch_execute=AsyncMock(return_value=_response("history.page", [None, token, []])))
    result = asyncio.run(delete_chat_result(client, "keep"))
    assert result.ok is False and result.data["deleted"] is None


def test_repeated_history_continuation_cannot_verify_deletion():
    client = SimpleNamespace(delete_chat=AsyncMock(), _batch_execute=AsyncMock(return_value=_response("history.page", [None, "same", []])))
    result = asyncio.run(delete_chat_result(client, "keep"))
    assert result.ok is False
    assert client._batch_execute.await_count == 2


@pytest.mark.parametrize("get_response", [
    _response("scheduled.get", {"new_shape": "target"}),
    _response("scheduled.get", [_task("wrong")]),
    _response("scheduled.get", [_task("target", deleted=True), _task("other")]),
    _response("scheduled.get", [["target", {"changed": "details"}, [None, None, None, []]]]),
    _response("scheduled.get", [["target", [], {"changed": "metadata"}]]),
    _response("scheduled.get", [["target", [], [None, None, None, "changed_state_marker"]]]),
    _response("scheduled.get", [], status_code=503),
    _response("scheduled.get", [], reject_code=7),
    _response("history.page", []),
    SimpleNamespace(text="not json", status_code=200),
])
def test_invalid_scheduled_get_never_confirms_delete(get_response):
    result = asyncio.run(delete_action(_ScheduledClient(get_response), action_id="target"))
    assert result["ok"] is True  # The mutation request was accepted.
    assert result["verification_status"] == "read_back_unverified"
    assert result["deleted_by_id_after_delete"] is None
    assert result["readable_by_id_after_delete"] is None
    assert result["get_task_error"]


def test_matching_deleted_scheduled_state_confirms_deletion():
    result = asyncio.run(delete_action(_ScheduledClient(_response("scheduled.get", [_task("target", deleted=True)])), action_id="target"))
    assert result["deleted_by_id_after_delete"] is True
    assert result["verification_status"] == "deleted_state_by_id"


def test_valid_empty_scheduled_get_and_nonempty_registry_confirm_absence():
    result = asyncio.run(delete_action(_ScheduledClient(_response("scheduled.get", [])), action_id="target"))
    assert result["verification_status"] == "not_visible_not_readable_by_id"


@pytest.mark.parametrize("delete_response", [
    _response("scheduled.delete", [], status_code=503),
    _response("scheduled.delete", [], reject_code=7),
    _response("scheduled.delete", {"changed": "acknowledgement"}),
    _response("history.page", []),
    SimpleNamespace(text="not json", status_code=200),
])
def test_invalid_scheduled_ack_cannot_start_verified_delete(delete_response):
    client = _ScheduledClient(_response("scheduled.get", [_task("target", deleted=True)]), delete_response=delete_response)
    result = asyncio.run(delete_action(client, action_id="target"))
    assert result["ok"] is False and result["deleted_by_id_after_delete"] is None


@pytest.mark.parametrize("key,body", [
    ("history.page", [None, None, [{"changed": "row"}]]),
    ("scheduled.registry", [[{"changed": "row"}]]),
    ("scheduled.registry", [[[]]]),
])
def test_record_shape_changes_are_parser_failures(key, body):
    assert parse_contract_body(key, body).status == "changed_shape"


def test_exhausted_bounded_history_page_does_not_return_same_continuation():
    chats = [{"id": f"c_{index}", "title": "Chat"} for index in range(5000)]
    result = list_chats_result(chats, 10, 5000, diagnostic={"has_remote_more": True})
    assert result.operation_state == "partial"
    assert result.data["has_more"] is False
    assert result.data["next_offset"] is None
    assert result.data["diagnostic"]["coverage_complete"] is False


def test_remote_history_continuation_never_exceeds_adapter_offset_bound():
    chats = [{"id": f"c_{index}", "title": "Chat"} for index in range(5010)]
    result = list_chats_result(chats, 10, 5000, diagnostic={"has_remote_more": True, "max_offset": 5000})
    assert result.operation_state == "partial"
    assert result.data["count"] == 10
    assert result.data["next_offset"] is None and result.data["has_more"] is False


def test_ambiguous_history_envelopes_do_not_verify_absence():
    rpc_id = get_contract("history.page").rpc_id
    bodies = ([None, None, []], [None, None, [["keep", "Chat", False]]])
    response = SimpleNamespace(status_code=200, text=json.dumps([
        ["wrb.fr", rpc_id, json.dumps(body), None, None, None, "generic"] for body in bodies
    ]))
    client = SimpleNamespace(delete_chat=AsyncMock(), _batch_execute=AsyncMock(return_value=response))
    result = asyncio.run(delete_chat_result(client, "keep"))
    assert result.ok is False and result.data["deleted"] is None


def test_primary_history_source_bound_reports_incomplete_json(monkeypatch):
    class Client:
        async def _batch_execute(self, requests, **_kwargs):
            count, token, filters = json.loads(requests[0].payload)
            start = int(token or 0)
            rows = [] if filters[0] else [[f"c_{index}", "Chat", False] for index in range(start, min(start + count, 5001))]
            continuation = str(start + len(rows)) if rows and start + len(rows) < 5001 else None
            return _response("history.page", [None, continuation, rows])

    monkeypatch.setattr(manage, "get_gemini_client", Client)
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    server = MCPServer("account regression")
    manage.register_manage_tools(server, layers=["all"])
    content = asyncio.run(server.call_tool("gemini_list_chats", {"offset": 5000, "limit": 10, "response_format": "json"})).content
    data = json.loads(content[0].text)
    assert data["count"] == 0 and data["next_offset"] is None and data["has_more"] is False
    assert data["diagnostic"]["has_remote_more"] is True


@pytest.mark.parametrize("successful_reads", [0, 1])
def test_content_search_preserves_failed_reads_without_title_matches(successful_reads):
    responses = [SimpleNamespace(turns=[SimpleNamespace(role="user", text="ordinary text")])] * successful_reads
    responses.extend(ConnectionError("private response details") for _index in range(2 - successful_reads))
    client = SimpleNamespace(read_chat=AsyncMock(side_effect=responses))
    chats = [{"id": "one", "title": "Other"}, {"id": "two", "title": "Other"}]
    result = asyncio.run(search_chats_result(client, chats, "needle", 10, 0, scan_turns=True))
    assert result.ok is bool(successful_reads)
    assert result.operation_state == ("partial" if successful_reads else "failed")
    assert result.data["match_count"] == 0
    assert len(result.data["read_failures"]) == 2 - successful_reads
    assert result.data["diagnostic"]["content_scan_complete"] is False
    assert "private response details" not in json.dumps(result.to_dict())


def test_failed_profile_is_not_recommended_and_validation_error_is_sanitized():
    report = doctor_payload(
        validate_browser=True,
        cookie_status_provider=lambda: {"available": True, "has_cookie": True},
        profile_provider=lambda _browser, **_kwargs: [{
            "profile": "Default", "has_psid": True, "chrome_selected_profile": True,
            "account_available": False, "validation_error": "private authentication cookie text",
        }],
    )
    check = next(item for item in report["checks"] if item["name"] == "browser_profile_alignment")
    assert check["status"] == "warn"
    assert "recommended_profile" not in check["details"]
    assert report["browser_profiles"][0]["validation_error"] == "ACCOUNT_VALIDATION_FAILED"
    assert "private authentication" not in json.dumps(report)
    assert "ACCOUNT_VALIDATION_FAILED" in format_doctor_markdown(report)


def test_unvalidated_cookie_profile_does_not_claim_usable_account():
    report = doctor_payload(
        cookie_status_provider=lambda: {"available": True, "has_cookie": True},
        profile_provider=lambda _browser, **_kwargs: [{"profile": "Default", "has_psid": True}],
    )
    check = next(item for item in report["checks"] if item["name"] == "browser_profile_alignment")
    assert check["status"] == "warn" and "unvalidated" in check["message"]
