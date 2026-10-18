"""The current public-link listing uses shared conversations, never legacy probes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.account_requests import AccountPage
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services.account_facade import AccountFacadeService


def _row(token="public-token", title="Shared title", full_conversation=True):
    return [None, None, [full_conversation, title], token, [1700000000, 0]]


def test_current_public_link_contract_matches_observed_page_request():
    contract = get_contract("sharing.public_links")
    assert contract.rpc_id == "OXxBcc"
    assert contract.source_path == "/sharing"
    assert json.loads(contract.build_payload()) == []
    assert contract.mode == "read"


def test_current_shared_conversation_metadata_has_real_url_without_invented_state():
    parsed = parse_contract_body("sharing.public_links", [[_row(), _row("second", None, False)]])
    assert parsed.status == "success"
    first, second = parsed.value
    assert first["id"] == "public-token"
    assert first["title"] == "Shared title"
    assert first["url"] == "https://gemini.google.com/share/public-token"
    assert second["title"] == ""
    assert first["disabled"] is None and second["disabled"] is None


def test_shared_token_is_encoded_as_one_url_path_segment():
    parsed = parse_contract_body("sharing.public_links", [[_row("token/?next=https://example.com")]])
    assert parsed.value[0]["url"] == "https://gemini.google.com/share/token%2F%3Fnext%3Dhttps%3A%2F%2Fexample.com"


@pytest.mark.parametrize("body", [[], [[]]])
def test_empty_shared_conversation_message_is_positive_empty_evidence(body):
    result = parse_contract_body("sharing.public_links", body)
    assert result.status == "empty" and result.value == []


@pytest.mark.parametrize("body", [
    {"changed": []},
    [[None]],
    [[_row(token=None)]],
    [[_row(token="")]],
    [[_row(title=False)]],
    [[_row(full_conversation="false")]],
    [[_row(), {"changed": "entry"}]],
    [[["legacy-id", "legacy-title", False, None, "legacy-token"]]],
])
def test_changed_shared_conversation_rows_cannot_become_an_empty_or_successful_listing(body):
    assert parse_contract_body("sharing.public_links", body).status == "changed_shape"


def test_current_public_links_through_focused_account_facade():
    rpc_id = get_contract("sharing.public_links").rpc_id
    response = SimpleNamespace(status_code=200, text=json.dumps([
        ["wrb.fr", rpc_id, json.dumps([[_row()]]), None, None, None, "generic"],
    ]))
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
    result = asyncio.run(service.execute("account", AccountPage(action="links")))
    assert result.ok and result.data["count"] == 1
    assert result.data["items"][0]["url"] == "https://gemini.google.com/share/public-token"
    request = client._batch_execute.await_args.args[0][0]
    assert request.rpcid == "OXxBcc" and json.loads(request.payload) == []
    assert client._batch_execute.await_args.kwargs["source_path"] == "/sharing"
