"""Regression tests for Phase 4 Gemini 4 live parity fixes."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import constants
from src.cli import _extract_domain_payload
from src.domain import OperationState
from src.domain.operations import OperationReadData
from src.infrastructure.media_companions import MediaCompanion, MediaCompanionCatalog, MediaCompanionOption
from src.infrastructure.rpc_contracts import (
    WebWorkflowTransportChanged,
    apply_image_companion_options,
    apply_music_companion_options,
)
from src.infrastructure.rpc_parsers import parse_research_chat_plan
from src.services import gems as gems_service
from src.services import research as research_service
from src.services.operations import _operation_read_result
from src.thinking_client import ThinkingLevelGeminiClient, WebRequestOptions, inject_web_request_options


def _run(coro):
    return asyncio.run(coro)


def test_create_gem_coerces_none_description_to_empty_string(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    captured: list[dict[str, object]] = []

    class FakeClient:
        async def create_gem(self, *, name: str, prompt: str, description: object):
            captured.append({"name": name, "prompt": prompt, "description": description})
            return SimpleNamespace(id="gem_1", name=name, description="", prompt=prompt)

    monkeypatch.setattr(
        gems_service,
        "read_custom_gems",
        AsyncMock(return_value=[{"id": "gem_1", "name": "Test", "description": "", "instructions": "Be concise"}]),
    )

    result = _run(
        gems_service.create_gem(
            FakeClient(),
            name="Test",
            description=None,
            instructions="Be concise",
        )
    )
    assert result["verification_status"] == "verified"
    assert captured == [{"name": "Test", "prompt": "Be concise", "description": ""}]


def test_cli_extract_domain_payload_marks_cross_prefixed_legacy_text_failed():
    raw_res = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="❌ Gem 操作结果未验证")],
        is_error=False,
        meta={},
        structured_content=None,
    )
    ok, data, texts, error = _extract_domain_payload(raw_res)
    assert ok is False
    assert data is None
    assert texts == ["❌ Gem 操作结果未验证"]
    assert error["code"] == "TOOL_ERROR"


def test_canvas_and_guided_learning_modes_encode_web_fields():
    guided = constants.resolve_learning_mode_config("guided_learning")
    canvas = constants.resolve_learning_mode_config("canvas")
    assert guided == {"id": 11, "x9b_field": "LYd", "x9b_value": 2, "prompt_prefix": ""}
    assert canvas == {"id": 8, "x9b_field": "QLd", "x9b_value": 2, "prompt_prefix": ""}

    body = [None] * 81
    body[0] = ["Write a clock", 0, None, None, None, None, 0]
    req = {"f.req": json.dumps([None, json.dumps(body)])}
    patched = inject_web_request_options(
        req,
        WebRequestOptions(
            learning_mode_id=8,
            learning_x9b_field="QLd",
            learning_x9b_value=2,
        ),
    )
    inner = json.loads(json.loads(patched["f.req"])[1])
    assert inner[54] == [[[None, [2]]]]
    assert inner[55] == [[8]]


def test_thinking_client_parse_candidate_merges_canvas_immersive_document(monkeypatch):
    client = ThinkingLevelGeminiClient()

    monkeypatch.setattr(
        ThinkingLevelGeminiClient.__bases__[0],
        "_parse_candidate",
        lambda self, candidate_data, cid, rid, rcid: ("Intro line", "", [], [], [], [], []),
    )
    candidate_data: list[object] = [None] * 31
    doc: list[object] = [None] * 18
    doc[2] = "Clock Widget"
    doc[3] = None
    doc[4] = "<svg>clock</svg>"
    candidate_data[30] = [doc]

    from src import thinking_client as tc

    token = tc._web_request.set(WebRequestOptions(learning_mode_id=8, learning_x9b_field="QLd", learning_x9b_value=2))
    try:
        parsed = client._parse_candidate(candidate_data, "c_1", "r_1", "rc_1")
    finally:
        tc._web_request.reset(token)

    assert parsed[0] == "Intro line\n\n## Clock Widget\n\n<svg>clock</svg>"


def test_apply_image_companion_options_writes_mode14_payload_and_catalog_selects_aspect():
    body = [None] * 81
    body[0] = ["lighthouse", 0, None, None, None, None, 0]
    body[49] = 14
    original = deepcopy(body)
    apply_image_companion_options(body, (65, 63))
    assert body[55] == [[65, 63]]
    assert body[0][9][6][1] == [None, "16:9", 2]

    with pytest.raises(WebWorkflowTransportChanged):
        apply_image_companion_options(original, (999,))

    catalog = MediaCompanionCatalog(
        by_mode={
            14: (
                MediaCompanion(
                    id=19, mode=14, title="长宽比", selection_mode=1, available=True,
                    options=(
                        MediaCompanionOption(id=61, title="1:1", subtitle=None, is_default=False, available=True),
                        MediaCompanionOption(id=63, title="16:9", subtitle=None, is_default=False, available=True),
                    ),
                ),
                MediaCompanion(
                    id=20, mode=14, title="格式", selection_mode=1, available=True,
                    options=(
                        MediaCompanionOption(id=65, title="图片", subtitle=None, is_default=False, available=True),
                    ),
                ),
            )
        },
        capabilities=frozenset(),
    )
    client = ThinkingLevelGeminiClient()
    client._mcp_media_companions = catalog
    assert client._media_companion_options(WebRequestOptions(image_generation=True, image_aspect_ratio="16:9")) == (65, 63)


def test_music_refusal_new_phrase_and_gemini4_genre_options():
    msg = "Since direct audio generation isn't available right now, I've composed a warm acoustic piece."
    assert constants.is_music_tool_unavailable_reply(msg) is True

    body = [None] * 81
    body[0] = ["acoustic melody", 0, None, None, None, None, 0]
    body[49] = 21
    apply_music_companion_options(body, (42, 69, 53))
    assert body[55] == [[42, 69, 53]]
    assert body[0][9][6][2] == [4, 3]


def test_research_refusal_fast_fails_and_resolves_gemini4_families():
    assert constants.is_research_refusal_reply("我没法提供这方面的帮助，因为我只是一个语言模型。") is True

    fake_body = [
        [
            [
                ["c_1234", "r_1"],
                None,
                None,
                [
                    [
                        [
                            "rc_1",
                            ["我没法提供这方面的帮助，因为我只是一个语言模型。"],
                            None, None, None, None, None, None, [2],
                        ]
                    ]
                ],
            ]
        ]
    ]
    parsed = parse_research_chat_plan(fake_body, chat_id="c_1234")
    assert parsed.status == "rejected"

    class FakeFailClient:
        async def create_deep_research_plan(self, query, chat=None, model=None):
            raise RuntimeError("Gemini did not return a deep research plan. Preview: '我没法提供这方面的帮助，因为我只是一个语言模型。'")

    with pytest.raises(research_service._ResearchPlanRejectedError):
        _run(research_service.create_deep_research_plan(FakeFailClient(), "Compare Python", SimpleNamespace(cid="c_1"), "flash"))

    client = ThinkingLevelGeminiClient()
    from gemini_webapi.types import AvailableModel

    h = AvailableModel(model_id="h", model_name="gemini-4-hydrogen", display_name="4 Hydrogen", description="", capacity=1)
    n = AvailableModel(model_id="n", model_name="gemini-neon", display_name="4 Neon", description="", capacity=1)
    client._model_registry = {"h": h, "n": n}
    assert research_service.resolve_deep_research_transport_model("hydrogen", client)[0] is h
    assert research_service.resolve_deep_research_transport_model("neon", client)[0] is n


def test_operation_read_result_formats_specific_media_failure_messages():
    quota = OperationReadData(
        operation_id="op_" + "a" * 32,
        operation="gemini_generate_video",
        state=OperationState.FAILED,
        continuation_possible=False,
        report_available=False,
        verification_status="upstream_limit_message_observed",
        error_code="UPSTREAM_LIMIT_EXCEEDED",
    )
    refusal = OperationReadData(
        operation_id="op_" + "b" * 32,
        operation="gemini_generate_music",
        state=OperationState.FAILED,
        continuation_possible=False,
        report_available=False,
        verification_status="model_media_refusal_observed",
        error_code="ARTIFACT_NOT_RETURNED",
    )
    err_card = OperationReadData(
        operation_id="op_" + "c" * 32,
        operation="gemini_generate_video",
        state=OperationState.FAILED,
        continuation_possible=False,
        report_available=False,
        verification_status="upstream_media_error_card_observed",
        error_code="UPSTREAM_REJECTED",
    )
    assert "limited this generation request" in _operation_read_result(quota).error.message
    assert "without a media file" in _operation_read_result(refusal).error.message
    assert "error card" in _operation_read_result(err_card).error.message


def test_cli_extract_domain_payload_marks_fastmcp_wrapped_cross_failed():
    raw_res = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="❌ 当前 Gemini 4 账号已迁移")],
        is_error=False,
        meta={},
        structured_content={"result": [{"type": "text", "text": "❌ 当前 Gemini 4 账号已迁移"}]},
    )
    ok, data, texts, error = _extract_domain_payload(raw_res)
    assert ok is False
    assert data is None
    assert texts == ["❌ 当前 Gemini 4 账号已迁移"]
    assert error["code"] == "TOOL_ERROR"


def test_video_options_error_rejects_15s_with_reference_image():
    from src.services.media_generation import video_options_error

    err = video_options_error(
        media_type="video",
        model="flash",
        thinking_level="standard",
        duration_seconds=15,
        delete_after_seconds=None,
        aspect_ratio="16:9",
        image_path="/tmp/example.jpg",
    )
    assert err == "Reference-image video generation only supports duration_seconds=10."
    assert video_options_error(
        media_type="video",
        model="flash",
        thinking_level="standard",
        duration_seconds=10,
        delete_after_seconds=None,
        aspect_ratio="16:9",
        image_path="/tmp/example.jpg",
    ) is None


def test_music_and_research_refusal_matchers_cover_gemini4_variants():
    assert constants.is_music_tool_unavailable_reply(
        "No audio generation tool is currently active in this session, so I cannot render a playable audio track directly, "
        "though here is a complete composition sketch and tablature."
    ) is True
    assert constants.is_research_refusal_reply(
        "我没法提供帮助。我只是一个语言模型，无法处理和理解这个问题。"
    ) is True
    assert constants.is_research_refusal_reply(
        "I cannot assist with that as I am only a language model."
    ) is True


def test_operation_store_reject_research_turn_requires_exact_identity(tmp_path):
    from src.infrastructure.operation_store import OperationStore

    store = OperationStore(tmp_path / "ops.db")
    import os
    old = os.environ.get("GEMINI_PSID")
    os.environ["GEMINI_PSID"] = "psid-synthetic"
    try:
        from src.infrastructure.account_identity import current_account_key

        acct = current_account_key()
        assert acct is not None
        op_id = "op_" + "1" * 32
        store.create_or_get(op_id, "gemini_research")
        store.attach_late_chat(op_id, "c_1234567890abcdef")
        store.update(op_id, state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_1234567890abcdef")
        assert store.reject_research_turn(
            op_id,
            account_key=acct,
            chat_id="c_1234567890abcdef",
            response_id="r_1234",
            candidate_id="rc_1234",
        ) is False
        assert store.bind_research_turn(
            op_id,
            account_key=acct,
            chat_id="c_1234567890abcdef",
            response_id="r_1234",
            candidate_id="rc_1234",
        ) is True
        assert store.reject_research_turn(
            op_id,
            account_key=acct,
            chat_id="c_1234567890abcdef",
            response_id="r_1234",
            candidate_id="rc_1234",
        ) is True
        saved = store.get(op_id)
        assert saved is not None
        assert saved.state == "failed"
        assert saved.phase == "plan_rejected"
        assert saved.error_code == "UPSTREAM_REJECTED"
        assert saved.cleanup_protected is False
    finally:
        if old is None:
            os.environ.pop("GEMINI_PSID", None)
        else:
            os.environ["GEMINI_PSID"] = old


def test_skill_bard_error_info_1130_maps_to_upstream_rejected(monkeypatch):
    from src.domain import DomainErrorCode
    from src.services import managed_skill_transport as transport
    from src.services.managed_skills import ManagedSkillDependencies, ManagedSkillService

    monkeypatch.setattr(transport, "current_account_key", lambda: "acct-1")
    wire = (
        ")]}\'\n\n225\n"
        '[["wrb.fr","SMuXte",null,null,null,[3,null,[["type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",'
        '[1130,null,"无法上传附件"]]]],"generic"]]\n'
    )

    async def fake_execute(_client, _key, **_kwargs):
        return SimpleNamespace(status_code=200, text=wire)

    monkeypatch.setattr(transport, "execute_contract", fake_execute)

    async def upload(_client, _content, _filename):
        return "private-upload-handle"

    service = ManagedSkillService(
        ManagedSkillDependencies(
            lambda: object(),
            AsyncMock(),
            AsyncMock(),
            lambda: "acct-1",
            upload,
            transport.mutate_skill,
        )
    )
    res = _run(
        service.mutate(
            "create",
            title="synth-skill",
            description="Temp desc",
            instructions="Always begin with X",
        )
    )
    assert res.ok is False
    assert res.error is not None
    assert res.error.code == DomainErrorCode.UPSTREAM_REJECTED
    assert "1130" in res.error.message
    assert res.meta.verification_status == "mutation_rejected"


def test_native_media_browser_js_strips_show_thinking_prefix_before_error_match():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for native_media_browser.js contract")
    script = Path(__file__).parents[1] / "src/data/native_media_browser.js"
    program = """
const fs = require('fs');
const fn = eval('(' + fs.readFileSync(process.argv[1], 'utf8') + ')');
const mk = (text) => ({
    innerText: text,
    matches: (sel) => sel.includes('model-response'),
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    getClientRects: () => [{width: 100, height: 40, left: 0, top: 0}],
    getBoundingClientRect: () => ({width: 100, height: 40, left: 0, top: 0}),
    hasAttribute: () => false,
    disabled: false,
    offsetParent: {},
});
globalThis.location = {origin: 'https://gemini.google.com', pathname: '/app/0123456789abcdef'};
globalThis.getComputedStyle = () => ({display: 'block', visibility: 'visible', opacity: '1'});
globalThis.document = {
    querySelectorAll: (sel) => sel.includes('model-response')
        ? [mk("显示思路 I'm having a hard time fulfilling your request. Can I help you with something else instead?"),
           mk("Show thinking I seem to be encountering an error.")]
        : [],
};
globalThis.window = {};
globalThis.window.top = globalThis.window;
fn({action: 'status', kind: 'video'}).then(r => console.log(JSON.stringify(r)));
"""
    completed = subprocess.run([node, "-e", program, str(script)], check=True, capture_output=True, text=True, timeout=5)
    signals = json.loads(completed.stdout)["signals"]
    assert [s["code"] for s in signals] == ["UPSTREAM_REJECTED", "UPSTREAM_REJECTED"]


def test_builtin_music_tool_unavailable_and_research_show_thinking_prefix():
    import shutil
    import subprocess
    from pathlib import Path

    assert constants.is_music_tool_unavailable_reply(
        "I don't have a built-in music generation tool available right now to output an audio file directly, so here is a warm, gentle fingerpicking arrangement."
    ) is True
    assert constants.is_music_tool_unavailable_reply(
        "I cannot generate audio files directly right now, as the music generation tool is unavailable. However, here is an original fingerstyle arrangement."
    ) is True
    assert constants.is_generation_failure_reply(
        "I'm sorry, I'm experiencing unusually high traffic right now generating videos and am at full capacity. Please try again later."
    ) is False
    assert constants.is_research_refusal_reply("我是一个文本 AI，在这方面爱莫能助。") is True

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for native_research_browser.js contract")
    script = Path(__file__).parents[1] / "src/data/native_research_browser.js"
    program = """
const fs = require('fs');
const fn = eval('(' + fs.readFileSync(process.argv[1], 'utf8') + ')');
const mk = (text) => ({
    innerText: text,
    closest: () => null,
    getClientRects: () => [{width: 100, height: 40, left: 0, top: 0}],
    getBoundingClientRect: () => ({width: 100, height: 40, left: 0, top: 0}),
    matches: () => false,
    querySelectorAll: () => [],
    getAttribute: () => null,
});
globalThis.location = {origin: 'https://gemini.google.com', pathname: '/app/0123456789abcdef'};
globalThis.getComputedStyle = () => ({display: 'block', visibility: 'visible', opacity: '1'});
globalThis.document = {
    querySelectorAll: (sel) => sel.includes('model-response') ? [mk("显示思路 我是一个文本 AI，在这方面爱莫能助。")] : [],
    querySelector: () => null,
};
globalThis.window = {};
globalThis.window.top = globalThis.window;
fn({action: 'status', model: '', prompt: 'test', thinking_level: 'low'}).then(r => console.log(JSON.stringify(r)));
"""
    completed = subprocess.run([node, "-e", program, str(script)], check=True, capture_output=True, text=True, timeout=5)
    parsed = json.loads(completed.stdout)
    assert parsed["refusal"] == "我是一个文本 AI，在这方面爱莫能助。"
