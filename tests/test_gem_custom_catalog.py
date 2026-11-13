import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import RPCEnvelope, parse_contract_body
from src.services import gems


def test_custom_catalog_parser_requires_complete_metadata_unique_ids_and_limits_instructions():
    contract = get_contract("gems.custom_list")
    body = [None, None, [["g1", ["Synthetic", "Description"], ["one"]],
                         ["g2", ["Other", ""], ["two"]]]]
    result = parse_contract_body(contract, body, include_instructions_for="g1")
    assert result.status == "success" and result.value[0]["instructions"] == "one"
    assert "instructions" not in result.value[1]
    assert parse_contract_body(contract, [None, None, []]).status == "empty"
    assert parse_contract_body(contract, [None, None]).status == "changed_shape"
    assert parse_contract_body(contract, [None, None, [body[2][0], body[2][0]]]).status == "changed_shape"
    assert json.loads(contract.build_payload(locale="zh-CN")) == [2, ["zh-CN"], 0]


def test_missing_or_rejected_custom_envelope_cannot_confirm_deletion(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account")
    client = SimpleNamespace(delete_gem=AsyncMock(), language="en")
    monkeypatch.setattr(gems, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=200, text="synthetic")))
    for envelope in (RPCEnvelope(True, 1, ()), RPCEnvelope(True, 1, (), reject_code=403),
                     RPCEnvelope(True, 1, ([None, None, None],))):
        monkeypatch.setattr(gems, "parse_rpc_envelope", lambda *_args: envelope)
        with pytest.raises(gems.GemMutationNotVerified) as error:
            asyncio.run(gems.delete_gem(client, gem_id="fixture"))
        assert error.value.verification_status == "read_back_error"
    assert client.delete_gem.await_count == 3


def test_exact_empty_custom_catalog_confirms_only_target_absence(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account")
    client = SimpleNamespace(delete_gem=AsyncMock(), language="en")
    monkeypatch.setattr(gems, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=200, text="synthetic")))
    monkeypatch.setattr(gems, "parse_rpc_envelope", lambda *_args: RPCEnvelope(True, 1, ([None, None, []],)))
    result = asyncio.run(gems.delete_gem(client, gem_id="fixture"))
    assert result["verification_status"] == "verified_deleted"
    assert client.delete_gem.await_count == 1


def test_gem_does_not_read_back_after_account_switch(monkeypatch):
    account = ["a"]
    monkeypatch.setattr("src.thinking_client.current_account_key", lambda: account[0])
    async def delete(_id):
        account[0] = "b"
    client = SimpleNamespace(delete_gem=AsyncMock(side_effect=delete))
    read = AsyncMock()
    monkeypatch.setattr(gems, "read_custom_gems", read)
    with pytest.raises(PermissionError):
        asyncio.run(gems.delete_gem(client, gem_id="fixture", account_key_provider=lambda: account[0]))
    assert client.delete_gem.await_count == 1 and read.await_count == 0
