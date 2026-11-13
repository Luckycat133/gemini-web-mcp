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
for _fixture_path in sorted((FIXTURE_PATH.parent / "rpc_contracts").glob("*.json")):
    _extra = json.loads(_fixture_path.read_text(encoding="utf-8"))
    assert not set(PARSER_FIXTURES).intersection(_extra), "Duplicate parser fixture names"
    PARSER_FIXTURES.update(_extra)
EXPECTED_CASES = {"success", "empty", "rejected", "changed_shape"}


def test_music_turn_evidence_requires_unique_completed_exact_candidate():
    from src.infrastructure.rpc_parsers import parse_music_chat_turn_evidence

    candidate = [None] * 13
    candidate[0], candidate[8], candidate[12] = "rc_target", [2], [{}]
    turn = [[None, "r_target"], None, None, [[candidate]]]
    body = [[turn]]
    def parse(value):
        return parse_music_chat_turn_evidence(value, rid="r_target", rcid="rc_target")
    assert parse(body).status == "empty"
    card = [None, [None] * 8]
    card[1][7] = [None, "https://example.test/audio"]
    candidate[12][0]["87"] = [card]
    assert parse(body).status == "success"
    card[1][7] = None
    assert parse(body).status == "changed_shape"
    candidate[12][0]["87"] = []
    candidate[8] = [1]
    assert parse(body).status == "changed_shape"
    candidate[8] = [2]
    assert parse([[turn, turn]]).status == "changed_shape"
    assert parse([[]]).status == "changed_shape"


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
        **case.get("arguments", {}),
    )
    assert result.status == case_name


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


def test_research_plan_recovers_only_completed_selected_candidate_with_identity():
    import copy

    fixture = PARSER_FIXTURES["research_chat_plan"]["cases"]["success"]
    body = copy.deepcopy(fixture["body"])
    result = parse_contract_body("research.chat_plan", body, chat_id="c_fixture")
    assert result.status == "success"
    assert result.value["metadata"] == ["c_fixture", "r_fixture", "rc_fixture"]
    assert result.value["plan"]["title"] == "Synthetic plan"
    assert result.value["plan"]["confirm_prompt"] == "Start research"
    assert json.loads(get_contract("research.chat_plan").build_payload(chat_id="c_fixture"))[1] == 100

    assert parse_contract_body("research.chat_plan", body).status == "changed_shape"
    missing_rid = copy.deepcopy(body)
    missing_rid[0][0][0][1] = None
    assert parse_contract_body("research.chat_plan", missing_rid, chat_id="c_fixture").status == "changed_shape"
    missing_rcid = copy.deepcopy(body)
    missing_rcid[0][0][3][0][0][0] = None
    assert parse_contract_body("research.chat_plan", missing_rcid, chat_id="c_fixture").status == "changed_shape"
    incomplete = copy.deepcopy(body)
    incomplete[0][0][3][0][0][8] = [1]
    assert parse_contract_body("research.chat_plan", incomplete, chat_id="c_fixture").status == "empty"

    older = copy.deepcopy(body[0][0])
    newest = copy.deepcopy(older)
    selected = newest[3][0][0]
    selected[12] = []
    newest[3][0].append(copy.deepcopy(older[3][0][0]))
    assert parse_contract_body("research.chat_plan", [[newest, older]], chat_id="c_fixture").status == "empty"


def test_research_rejection_requires_exact_completed_selected_candidate_without_document(monkeypatch):
    import copy

    body = copy.deepcopy(PARSER_FIXTURES["research_chat_plan"]["cases"]["success"]["body"])
    candidate = body[0][0][3][0][0]
    candidate[12] = []
    candidate[1] = ["我是一个语言模型，这超出了我的设计用途。"]
    result = parse_contract_body("research.chat_plan", body, chat_id="c_fixture")
    assert result.status == "rejected" and result.value["metadata"] == ["c_fixture", "r_fixture", "rc_fixture"]
    candidate[8] = [1]
    assert parse_contract_body("research.chat_plan", body, chat_id="c_fixture").status == "empty"
    candidate[8] = [2]
    candidate[1] = ["我是一个语言模型，这超出了我的设计用途。其他文本"]
    assert parse_contract_body("research.chat_plan", body, chat_id="c_fixture").status == "empty"
    candidate[1] = ["我是一个语言模型，这超出了我的设计用途。"]
    from src.infrastructure import rpc_parsers
    monkeypatch.setattr(rpc_parsers._upstream_utils, "extract_deep_research_document", lambda _: {"content": "report"})
    assert parse_contract_body("research.chat_plan", body, chat_id="c_fixture").status == "empty"
