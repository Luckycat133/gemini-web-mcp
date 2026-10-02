"""Fixture-backed contracts for centralized management RPC adapters."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.infrastructure.rpc_contracts import (
    RPC_CONTRACTS,
    WEB_FEATURE_PROBE_CONTRACTS,
    WEB_FEATURE_PROBE_KEYS,
    WEB_FEATURE_PROBES,
    get_contract,
)
from src.infrastructure.rpc_parsers import PARSER_FUNCTIONS, parse_contract_body, parse_rpc_envelope


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rpc_management_cases.json"
PARSER_FIXTURES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
EXPECTED_CASES = {"success", "empty", "rejected", "changed_shape"}


def test_every_registered_parser_has_four_shape_fixtures():
    assert set(PARSER_FIXTURES) == set(PARSER_FUNCTIONS)
    for fixture in PARSER_FIXTURES.values():
        assert set(fixture["cases"]) == EXPECTED_CASES


@pytest.mark.parametrize(
    ("parser_name", "case_name"),
    [
        (parser_name, case_name)
        for parser_name in sorted(PARSER_FIXTURES)
        for case_name in ("success", "empty", "rejected", "changed_shape")
    ],
)
def test_registered_parser_fixture_status(parser_name: str, case_name: str):
    fixture = PARSER_FIXTURES[parser_name]
    case = fixture["cases"][case_name]
    result = parse_contract_body(
        fixture["contract_key"],
        case["body"],
        reject_code=case.get("reject_code"),
    )
    assert result.status == case_name


@pytest.mark.parametrize("url", [None, "", "  ", 123, False, [], {}])
def test_observed_music_card_with_invalid_url_is_changed_shape(url):
    body = json.loads(json.dumps(PARSER_FIXTURES["music_chat"]["cases"]["success"]["body"]))
    body[0][0][3][0][0][12][0]["87"][0][1][7][1] = url
    assert parse_contract_body("media.music_chat", body).status == "changed_shape"


def test_music_empty_chat_and_non_media_turn_remain_verified_empty():
    assert parse_contract_body("media.music_chat", [[]]).status == "empty"
    assert parse_contract_body("media.music_chat", [[[None, None, None, [[["rc_1"]]]]]]).status == "empty"


@pytest.mark.parametrize(
    ("contract_key", "body"),
    [
        (fixture["contract_key"], body)
        for fixture in PARSER_FIXTURES.values()
        for body in fixture.get("malformed_items", [])
    ],
)
def test_valid_outer_envelope_does_not_hide_malformed_items(contract_key, body):
    assert parse_contract_body(contract_key, body).status == "changed_shape"


def test_registry_owns_probe_payloads_and_evidence():
    assert len(WEB_FEATURE_PROBES) == 21
    assert tuple(contract.key for contract in WEB_FEATURE_PROBE_CONTRACTS) == WEB_FEATURE_PROBE_KEYS
    assert all(contract.mode == "read" for contract in WEB_FEATURE_PROBE_CONTRACTS)
    assert len({contract.key for contract in RPC_CONTRACTS.values()}) == len(RPC_CONTRACTS)
    for probe in WEB_FEATURE_PROBES:
        assert probe["rpcid"]
        assert probe["source_path"].startswith("/")
        assert probe["observed"]
        json.loads(probe["payload"])


def test_parameterized_payload_builders_preserve_observed_shapes():
    assert json.loads(get_contract("media.music_chat").build_payload(chat_id="c_1")) == [
        "c_1", 10, None, 1, [1], [4], None, 1,
    ]
    assert json.loads(
        get_contract("history.page").build_payload(
            filter_payload=[False, None, True],
            page_size=25,
            next_page_token="next",
        )
    ) == [25, "next", [False, None, True]]
    assert json.loads(
        get_contract("notebooks.move_chat").build_payload(
            chat_id="chat-1",
            notebook_id="notebook-1",
            project_type=7,
        )
    ) == [
        None,
        [["bot_id", "bot_project_metadata"]],
        ["chat-1", None, None, None, None, None, None, "notebook-1", None, None, None, None, None, [7]],
    ]
    assert json.loads(get_contract("scheduled.get").build_payload(action_id="task-1")) == ["task-1"]
    assert json.loads(get_contract("scheduled.delete").build_payload(action_id="task-1")) == [None, ["task-1"]]


def test_music_chat_parser_keeps_media_identity_without_raw_response():
    body = PARSER_FIXTURES["music_chat"]["cases"]["success"]["body"]
    parsed = parse_contract_body("media.music_chat", body)
    assert parsed.status == "success"
    assert parsed.value == [
        {
            "title": "song.mp3",
            "url": "https://cdn.example/song.mp3",
            "rid": "r_1",
            "rcid": "rc_1",
        }
    ]


def test_rpc_envelope_preserves_rejection_evidence_without_raw_handler_logic():
    rpc_id = get_contract("scheduled.registry").rpc_id
    response = json.dumps([["wrb.fr", rpc_id, json.dumps([[]]), None, None, [7], "generic"]])
    parsed = parse_rpc_envelope(response, rpc_id)
    assert parsed.parsed is True
    assert parsed.reject_code == 7
    assert parsed.bodies == ([[]],)


def test_compact_server_import_does_not_load_management_monolith():
    script = (
        "import sys; import src.skill_server; "
        "assert 'src.tools.manage' not in sys.modules, sorted(k for k in sys.modules if k.startswith('src.tools'))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_rpc_ids_do_not_reappear_in_handlers_or_services():
    source_root = Path(__file__).parents[1] / "src"
    registry_path = source_root / "infrastructure" / "rpc_contracts.py"
    rpc_ids = {contract.rpc_id for contract in RPC_CONTRACTS.values()}
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        if path == registry_path:
            continue
        source = path.read_text(encoding="utf-8")
        for rpc_id in rpc_ids:
            if f'"{rpc_id}"' in source or f"'{rpc_id}'" in source:
                offenders.append(f"{path.relative_to(source_root)}:{rpc_id}")
    assert offenders == []
