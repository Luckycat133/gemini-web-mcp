"""Mutation RPCs must not inherit the SDK's automatic submission retries."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from gemini_webapi.exceptions import APIError
from gemini_webapi.utils import decorators

from src.infrastructure.rpc_contracts import execute_contract, get_contract


class _SDKRetryClient:
    _running = True

    def __init__(self):
        self.calls = []
        self.closed = 0

    @decorators.running(retry=2)
    async def _batch_execute(self, requests, *, source_path, close_on_error):
        self.calls.append((requests, source_path, close_on_error))
        if len(self.calls) <= 2:
            raise APIError("Fixture uncertain server failure")
        return SimpleNamespace(status_code=200, text="Fixture read")

    async def close(self):
        self.closed += 1


@pytest.mark.parametrize("key,arguments", [
    ("scheduled.remy_create", {"schedule": ["", "Fixture"]}),
    ("sharing.create", {"chat_id": "c_fixture", "response_id": "rc_fixture", "title": "Fixture"}),
])
def test_real_sdk_decorator_submits_mutation_only_once(monkeypatch, key, arguments):
    monkeypatch.setattr(decorators, "_DELAY_FACTOR", 0)
    client = _SDKRetryClient()
    with pytest.raises(APIError):
        asyncio.run(execute_contract(client, key, **arguments))
    assert len(client.calls) == 1
    requests, source_path, close_on_error = client.calls[0]
    assert requests[0].rpcid == get_contract(key).rpc_id
    assert requests[0].payload == get_contract(key).build_payload(**arguments)
    assert source_path == get_contract(key).source_path and close_on_error is False
    assert client.closed == 1


def test_real_sdk_decorator_keeps_existing_read_retries(monkeypatch):
    monkeypatch.setattr(decorators, "_DELAY_FACTOR", 0)
    client = _SDKRetryClient()
    result = asyncio.run(execute_contract(client, "tasks.get", chat_id="c_fixture"))
    assert result.status_code == 200
    assert len(client.calls) == 3
    assert all(json.loads(call[0][0].payload) == ["c_fixture"] for call in client.calls)
    assert client.closed == 0


def test_mutation_keeps_strict_non_sdk_batch_client_compatible():
    calls = []

    async def strict_batch(requests, *, source_path, close_on_error):
        calls.append((requests, source_path, close_on_error))
        return "Fixture acknowledgement"

    client = SimpleNamespace(_batch_execute=strict_batch)
    result = asyncio.run(execute_contract(client, "scheduled.remy_delete", action_id="schedule_fixture"))
    assert result == "Fixture acknowledgement"
    assert len(calls) == 1
    requests, source_path, close_on_error = calls[0]
    assert json.loads(requests[0].payload) == ["schedule_fixture"]
    assert source_path == "/scheduled" and close_on_error is False
