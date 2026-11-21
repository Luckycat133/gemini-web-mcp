"""Exact-identity metadata transport without conversation-text reads."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.chat_metadata import ChatMetadataUnavailable, mutate_chat_metadata, read_chat_metadata


class Client:
    def __init__(self, *bodies, reject_code=None, status=200):
        self.bodies = iter(bodies)
        self.reject_code = reject_code
        self.status = status
        self.calls = []

    async def _batch_execute(self, rpcs, **kwargs):
        self.calls.append((rpcs[0], kwargs))
        body = next(self.bodies)
        text = json.dumps([["wrb.fr", rpcs[0].rpcid, json.dumps(body), None, None,
                            [self.reject_code] if self.reject_code is not None else None, "generic"]])
        return SimpleNamespace(text=text, status_code=self.status)

    async def read_chat(self, *_args, **_kwargs):
        raise AssertionError("Metadata management must not read private conversation text.")


def test_fresh_chat_metadata_pages_only_until_the_exact_target_is_found():
    client = Client([None, "next", [["c_other", "Other", True]]],
                    [None, None, [["c_target", "Target", True]]])
    result = asyncio.run(read_chat_metadata(client, "c_target"))
    assert result == {"id": "c_target", "title": "Target", "is_pinned": True}
    payloads = [json.loads(rpc.payload) for rpc, _ in client.calls]
    assert payloads == [[100, None, [True, None, True]], [100, "next", [True, None, True]]]


def test_missing_metadata_fields_remain_unknown():
    client = Client([None, None, []], [None, None, [["c_target"]]])
    result = asyncio.run(read_chat_metadata(client, "c_target"))
    assert result == {"id": "c_target", "title": None, "is_pinned": None}
    assert len(client.calls) == 2


def test_pinned_page_limit_does_not_hide_a_target_in_the_recent_bucket():
    client = Client([None, "more_pinned", [["c_pinned", "Other", True]]],
                    [None, None, [["c_target", "Target", False]]])
    result = asyncio.run(read_chat_metadata(client, "c_target", max_pages=1))
    assert result == {"id": "c_target", "title": "Target", "is_pinned": False}
    assert len(client.calls) == 2


def test_a_statusless_response_does_not_pass_the_http_gate():
    class Statusless(Client):
        async def _batch_execute(self, requests, **kwargs):
            response = await super()._batch_execute(requests, **kwargs)
            return SimpleNamespace(text=response.text)
    client = Statusless([None, None, [["c_target", "Title", True]]])
    with pytest.raises(ChatMetadataUnavailable):
        asyncio.run(read_chat_metadata(client, "c_target"))


@pytest.mark.parametrize("raw", [False, 0, True, 1])
def test_explicit_boolean_or_proto_boolean_pin_field_is_observed(raw):
    result = parse_contract_body("history.metadata_page", [None, None, [["c_target", "Title", raw]]])
    assert result.value["items"][0]["is_pinned"] is bool(raw)


@pytest.mark.parametrize("raw", [None, "false", 2, []])
def test_unrecognized_pin_field_is_not_coerced_to_unpinned(raw):
    result = parse_contract_body("history.metadata_page", [None, None, [["c_target", "Title", raw]]])
    assert result.value["items"][0]["is_pinned"] is None


def test_no_target_is_returned_only_after_both_sources_end():
    client = Client([None, None, []], [None, None, []], [], [])
    assert asyncio.run(read_chat_metadata(client, "c_target")) is None
    assert len(client.calls) == 4


def test_task_exact_filter_proves_unpinned_membership_without_pin_field():
    client = Client([None, None, []], [None, None, []], [None, None, [["c_target", "Task title"]]])
    assert asyncio.run(read_chat_metadata(client, "c_target")) == {
        "id": "c_target", "title": "Task title", "is_pinned": False,
    }
    assert json.loads(client.calls[-1][0].payload) == [100, None, [False, None, True, None, None, "c_target"]]


def test_task_exact_filter_proves_pinned_membership_after_empty_unpinned_bucket():
    client = Client([None, None, []], [None, None, []], [], [None, None, [["c_target", "Task title"]]])
    assert asyncio.run(read_chat_metadata(client, "c_target"))["is_pinned"] is True


@pytest.mark.parametrize("entry", [["c_other", "Other"], ["c_target", "Task", True]])
def test_task_exact_filter_rejects_wrong_or_contradictory_row(entry):
    client = Client([None, None, []], [None, None, []], [None, None, [entry]])
    with pytest.raises(ChatMetadataUnavailable):
        asyncio.run(read_chat_metadata(client, "c_target"))


@pytest.mark.parametrize("kind", ["rejected", "changed", "http", "loop", "limit"])
def test_unusable_or_incomplete_metadata_is_not_claimed_absent(kind):
    body = [None, None, []]
    bodies = [body]
    kwargs = {}
    if kind == "rejected":
        kwargs["reject_code"] = 7
    elif kind == "changed":
        bodies = [[None, None, [{"id": "c_target"}]]]
    elif kind == "http":
        kwargs["status"] = 503
    elif kind in {"loop", "limit"}:
        bodies = [[None, "next", []], [None, "next", []]]
    client = Client(*bodies, **kwargs)
    with pytest.raises(ChatMetadataUnavailable):
        asyncio.run(read_chat_metadata(client, "c_target", max_pages=1 if kind == "limit" else 20))


@pytest.mark.parametrize(("action", "changes", "key", "expected"), [
    ("rename", {"title": "Renamed"}, "history.rename", [None, [["title"]], ["c_target", "Renamed"]]),
    ("pin", {"is_pinned": True}, "history.pin", [None, [["pinned"]], ["c_target", None, True]]),
    ("unpin", {"is_pinned": False}, "history.pin", [None, [["pinned"]], ["c_target", None, False]]),
])
def test_chat_mutations_preserve_the_observed_update_field_mask(action, changes, key, expected):
    client = Client([None, ["c_target", "Renamed", True]])
    assert asyncio.run(mutate_chat_metadata(client, action, "c_target", changes)) is None
    rpc, options = client.calls[0]
    assert rpc.rpcid == get_contract(key).rpc_id
    assert json.loads(rpc.payload) == expected
    assert options == {"source_path": "/app", "close_on_error": False, "current_retry": 0}


def test_empty_mutation_ack_is_not_a_verified_mutation_result():
    client = Client([])
    assert asyncio.run(mutate_chat_metadata(client, "rename", "c_target", {"title": "New"})) is None


def test_mutation_rejection_is_not_silently_accepted():
    client = Client([], reject_code=7)
    with pytest.raises(ChatMetadataUnavailable):
        asyncio.run(mutate_chat_metadata(client, "pin", "c_target", {"is_pinned": True}))


def test_unknown_mutation_never_sends_a_request():
    client = Client()
    with pytest.raises(ValueError):
        asyncio.run(mutate_chat_metadata(client, "delete", "c_target", {}))
    assert client.calls == []


def test_actual_sdk_does_not_replay_a_failed_metadata_mutation(monkeypatch):
    from unittest.mock import AsyncMock
    from gemini_webapi import GeminiClient
    from gemini_webapi.exceptions import APIError

    calls = []
    async def post(*args, **kwargs):
        calls.append((args, kwargs))
        raise APIError("Synthetic request outcome unknown")
    client = GeminiClient()
    client._running = True
    client.client = SimpleNamespace(post=post)
    monkeypatch.setattr(GeminiClient, "close", AsyncMock())
    with pytest.raises(APIError):
        asyncio.run(mutate_chat_metadata(client, "rename", "c_fixture", {"title": "New"}))
    assert len(calls) == 1
