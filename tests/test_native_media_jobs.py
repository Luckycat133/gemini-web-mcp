"""Native submission uncertainty survives worker restarts without another send."""

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.account_identity import current_account_key
from src.infrastructure.operation_store import OperationStore
from src.infrastructure import rpc_contracts
from src.infrastructure.rpc_parsers import parse_completed_empty_media_turn
from src.services import background, media_jobs
from src.services.native_media_generation import NativeMediaGenerationError
from src.constants import is_media_generation_error_reply, is_music_tool_unavailable_reply

_NO_TOOL = ("I can't generate or output an audio file directly right now, as the music generation tool "
            "isn't available in this session.")
_GENERIC_ERROR = "I seem to be encountering an error."
_EMPTY_TURN_FIXTURE = json.loads((Path(__file__).parent / "fixtures/completed_empty_media_turn.json").read_text())
_MUSIC_NOTICE_FIXTURE = json.loads((Path(__file__).parent / "fixtures/music_tool_unavailable_notices.json").read_text())
_PLAYABLE_NO_TOOL = _MUSIC_NOTICE_FIXTURE["observed_first_sentence"]
_TURN_NO_TOOL = _MUSIC_NOTICE_FIXTURE["observed_turn_first_sentence"]
_CHINESE_NO_TOOL = _MUSIC_NOTICE_FIXTURE["observed_chinese_score_fallback_prefix"]
_SCORE_NO_TOOL = _MUSIC_NOTICE_FIXTURE["observed_score_fallback_prefix"] + " synthetic instrumental notation."


@pytest.mark.parametrize("case", _EMPTY_TURN_FIXTURE["cases"], ids=lambda case: case["name"])
def test_completed_empty_media_parser_keeps_all_nonpositive_shapes_unverified(case):
    result = parse_completed_empty_media_turn(case["body"], **_EMPTY_TURN_FIXTURE["arguments"],
                                              reject_code=case.get("reject_code"))
    assert result.status == case["status"]
    if result.status == "success":
        assert result.value["completed"] is True
        assert result.value["text"] == _GENERIC_ERROR
    else:
        assert result.value is None


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-native-media-owner")
    now = [1000.0]
    store = OperationStore(tmp_path / "native.sqlite", clock=lambda: now[0])
    store.create_or_get("op_native", "gemini_generate_music", media_type="music", output_dir=str(tmp_path),
                        filename="fixture", request_model="gemini-3-flash", effective_backend="Gemini Web music mode")
    assert store.claim_worker("op_native", "worker")
    return store, now


@pytest.mark.parametrize("already_observed", [False, True])
def test_native_account_change_keeps_original_cid_without_new_turn_metadata(ledger, monkeypatch, already_observed):
    store, _ = ledger
    cid = "c_0123456789abcdef"
    if already_observed:
        media_jobs._checkpoint(store, "op_native", "worker", SimpleNamespace(
            metadata=[cid], rcid="", _mcp_native_media=True,
        ))
    monkeypatch.setenv("GEMINI_PSID", "another-native-account")
    with pytest.raises(media_jobs._LostMediaLease):
        media_jobs._checkpoint(store, "op_native", "worker", SimpleNamespace(
            metadata=[cid, "r_unverified_after_account_change"], rcid="rc_unverified", _mcp_native_media=True,
        ))
    record = store.get("op_native")
    assert record.upstream_chat_id == cid
    assert record.upstream_response_id is None and record.upstream_candidate_id is None
    assert record.phase == "native_media_submitted" and record.cleanup_protected


@pytest.mark.parametrize("submitted", [False, True])
def test_native_failure_releases_only_proven_not_dispatched_work(ledger, monkeypatch, submitted):
    from src import client_wrapper

    store, _ = ledger
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: SimpleNamespace(timeout=30, watchdog_timeout=30))
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    generate = AsyncMock(side_effect=NativeMediaGenerationError("CAPABILITY_UNAVAILABLE", submitted=submitted))
    monkeypatch.setattr(media_jobs, "generate_media_response", generate)
    asyncio.run(media_jobs.execute_media_job(
        {"prompt": "An original short instrumental", "media_type": "music", "output_dir": store.path.parent,
         "filename": "fixture"},
        operation_id="op_native", store=store, worker_token="worker",
    ))
    record = store.get("op_native")
    assert record.account_key == current_account_key()
    assert record.state == ("partial" if submitted else "failed")
    assert record.cleanup_protected is submitted
    assert generate.await_count == 1


def test_cid_only_native_checkpoint_survives_timeout_and_enters_read_only_recovery(ledger, monkeypatch):
    from src import client_wrapper

    store, now = ledger
    cid = "c_0123456789abcdef"
    media_jobs._checkpoint(store, "op_native", "worker", SimpleNamespace(
        metadata=[cid], rcid="", _mcp_native_media=True,
    ))
    media_jobs._unknown(store, "op_native", "worker", code="TIMED_OUT")
    assert store.get("op_native").phase == "native_media_submitted"
    now[0] += 61
    assert [item.operation_id for item in store.recoverable_media()] == ["op_native"]
    dispatch = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(background, "_dispatch", dispatch)
    asyncio.run(background.resume_media_worker(store, store.get("op_native")))
    assert dispatch.await_args.args[2] == "media_resume"

    assert store.claim_worker("op_native", "recovery")
    candidate = SimpleNamespace(rcid="rc_exact", generated_media=[], generated_videos=[])
    response = SimpleNamespace(metadata=[cid, "r_exact"], candidates=[candidate], text="An instrumental.")
    client = SimpleNamespace(read_chat=AsyncMock(return_value=SimpleNamespace(turns=[
        SimpleNamespace(role="model", model_output=response),
    ])), generate_content=AsyncMock())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    save = AsyncMock()
    monkeypatch.setattr(media_jobs, "_save_response", save)
    asyncio.run(media_jobs.recover_media_job(store.get("op_native"), store=store, worker_token="recovery"))
    recovered = store.get("op_native")
    assert (recovered.upstream_chat_id, recovered.upstream_response_id, recovered.upstream_candidate_id) == (
        cid, "r_exact", "rc_exact",
    )
    assert recovered.cleanup_protected and recovered.phase == "generation_observed"
    client.read_chat.assert_awaited_once_with(cid)
    client.generate_content.assert_not_awaited()
    save.assert_awaited_once()


def test_native_cid_recovery_refuses_multiple_turns_and_keeps_provenance(ledger, monkeypatch):
    from src import client_wrapper

    store, _ = ledger
    cid = "c_0123456789abcdef"
    media_jobs._checkpoint(store, "op_native", "worker", SimpleNamespace(metadata=[cid], _mcp_native_media=True))
    candidate = SimpleNamespace(rcid="rc_exact")
    response = SimpleNamespace(metadata=[cid, "r_exact"], candidates=[candidate])
    turn = SimpleNamespace(role="model", model_output=response)
    client = SimpleNamespace(read_chat=AsyncMock(return_value=SimpleNamespace(turns=[turn, turn])))
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    save = AsyncMock()
    monkeypatch.setattr(media_jobs, "_save_response", save)
    asyncio.run(media_jobs.recover_media_job(store.get("op_native"), store=store, worker_token="worker"))
    record = store.get("op_native")
    assert record.phase == "native_media_submitted" and record.state == "partial" and record.cleanup_protected
    assert record.upstream_response_id is None
    save.assert_not_awaited()


@pytest.mark.parametrize("verified_empty", [True, False])
def test_native_explicit_music_refusal_needs_completed_exact_card_readback(ledger, monkeypatch, verified_empty):
    store, _ = ledger
    cid = "c_0123456789abcdef"
    response = SimpleNamespace(metadata=[cid, "r_exact"], rcid="rc_exact", images=[], videos=[], media=[],
        text=media_jobs._MUSIC_NATIVE_NO_TOOL_PREFIX + ". Here is the score instead.")
    media_jobs._checkpoint(store, "op_native", "worker", response)
    read_cards = AsyncMock(return_value=[], side_effect=None if verified_empty else ValueError("Unverified candidate"))
    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", read_cards)
    finish = AsyncMock()
    monkeypatch.setattr(media_jobs, "_finish_media_rejection", finish)
    asyncio.run(media_jobs._save_response(object(), response, store.get("op_native"), store=store, token="worker"))
    assert read_cards.await_args.kwargs == {"rid": "r_exact", "rcid": "rc_exact", "strict": True}
    if verified_empty:
        assert finish.await_args.kwargs["code"] == "ARTIFACT_NOT_RETURNED"
    else:
        finish.assert_not_awaited()
        assert store.get("op_native").cleanup_protected


def _owned_notice(ledger, *, media_type="music", text=_NO_TOOL):
    store, _ = ledger
    if media_type != "music":
        store = OperationStore(store.path.parent / "video.sqlite", clock=lambda: 1000.0)
        store.create_or_get("op_native", "gemini_generate_video", media_type=media_type, output_dir=str(store.path.parent),
                            filename="fixture", request_model="gemini-4-hydrogen", effective_backend="Gemini Web native browser")
        assert store.claim_worker("op_native", "worker")
    response = SimpleNamespace(metadata=["c_0123456789abcdef", "r_fixture"], rcid="rc_fixture", text=text,
                               media=[], videos=[], images=[], _mcp_native_media=True)
    media_jobs._checkpoint(store, "op_native", "worker", response)
    return store, response


def _notice_rpc(response, *, state=2, cid=None, rcid=None, rich=None, empty=False):
    candidate = [None] * 13
    candidate[0] = rcid or response.rcid
    candidate[1] = [response.text]
    candidate[8] = [state]
    candidate[12] = [None] * 7 + [[]] if rich is None else rich
    body = [[]] if empty else [[[[cid or response.metadata[0], response.metadata[1]], None, None, [[candidate]]]]]
    return SimpleNamespace(status_code=200, text=json.dumps([["wrb.fr", "hNvQHb", json.dumps(body)]]))


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("media_type,text,code", [("music", _NO_TOOL, "ARTIFACT_NOT_RETURNED"),
                                                ("music", _PLAYABLE_NO_TOOL, "ARTIFACT_NOT_RETURNED"),
                                                ("music", _TURN_NO_TOOL, "ARTIFACT_NOT_RETURNED"),
                                                ("music", _SCORE_NO_TOOL, "ARTIFACT_NOT_RETURNED"),
                                                ("video", _GENERIC_ERROR, "UPSTREAM_REJECTED")])
def test_observed_native_notices_require_exact_completed_empty_read_for_submission_and_recovery(
    ledger, monkeypatch, recovery, media_type, text, code,
):
    from src import client_wrapper

    store, response = _owned_notice(ledger, media_type=media_type, text=text)
    candidate = SimpleNamespace(rcid=response.rcid, text=response.text, generated_media=[], generated_videos=[])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_notice_rpc(response)),
        read_chat=AsyncMock(return_value=SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=SimpleNamespace(
            metadata=response.metadata, candidates=[candidate]))])), generate_content=AsyncMock())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    finish = AsyncMock()
    monkeypatch.setattr(media_jobs, "_finish_media_rejection", finish)
    if recovery:
        asyncio.run(media_jobs.recover_media_job(store.get("op_native"), store=store, worker_token="worker"))
    else:
        asyncio.run(media_jobs._save_response(client, response, store.get("op_native"), store=store, token="worker"))
    assert finish.await_count == 1 and finish.await_args.kwargs["code"] == code
    client.generate_content.assert_not_awaited()


@pytest.mark.parametrize("text", [_NO_TOOL, _PLAYABLE_NO_TOOL, _TURN_NO_TOOL, _SCORE_NO_TOOL, _CHINESE_NO_TOOL])
@pytest.mark.parametrize("change", ["pending", "empty", "changed_candidate", "changed_response", "foreign_cid", "opaque_card", "uri"])
def test_new_native_music_notice_retains_guard_for_unverified_or_changed_identity(ledger, monkeypatch, change, text):
    store, response = _owned_notice(ledger, text=text)
    kwargs = ({"state": 1} if change == "pending" else {"empty": True} if change == "empty" else
              {"cid": "c_fedcba9876543210"} if change == "foreign_cid" else
              {"rich": [{"opaque_job": "synthetic-handle"}]} if change == "opaque_card" else
              {"rich": ["https://example.invalid/synthetic-audio.mp3"]} if change == "uri" else {"rcid": "rc_other"})
    read = AsyncMock(return_value=_notice_rpc(response, **kwargs))
    if change == "changed_response":
        response.rcid = "rc_other"
    client = SimpleNamespace(_batch_execute=read)
    finish = AsyncMock()
    monkeypatch.setattr(media_jobs, "_finish_media_rejection", finish)
    asyncio.run(media_jobs._save_response(client, response, store.get("op_native"), store=store, token="worker"))
    finish.assert_not_awaited()
    assert store.get("op_native").cleanup_protected and store.get("op_native").state == "partial"
    if change == "changed_response":
        read.assert_not_awaited()


@pytest.mark.parametrize("change", [None, "pending", "empty", "foreign_cid", "changed_candidate", "opaque_card", "url"])
def test_new_provider_error_needs_exact_completed_structurally_empty_candidate(ledger, monkeypatch, change):
    store, response = _owned_notice(ledger, media_type="video", text=_GENERIC_ERROR + " Please try again.")
    kwargs = ({"state": 1} if change == "pending" else {"empty": True} if change == "empty" else
              {"cid": "c_fedcba9876543210"} if change == "foreign_cid" else
              {"rcid": "rc_other"} if change == "changed_candidate" else
              {"rich": [{"opaque_job": "synthetic-handle"}]} if change == "opaque_card" else
              {"rich": [{"60": ["https://lh3.googleusercontent.com/synthetic-video"]}]} if change == "url" else {})
    monkeypatch.setattr(rpc_contracts, "execute_contract", AsyncMock(return_value=_notice_rpc(response, **kwargs)))
    finish = AsyncMock()
    monkeypatch.setattr(media_jobs, "_finish_media_rejection", finish)
    asyncio.run(media_jobs._save_response(object(), response, store.get("op_native"), store=store, token="worker"))
    if change is None:
        assert finish.await_count == 1 and finish.await_args.kwargs["code"] == "UPSTREAM_REJECTED"
    else:
        finish.assert_not_awaited()
        assert store.get("op_native").cleanup_protected and store.get("op_native").state == "partial"


@pytest.mark.parametrize("notice", [_NO_TOOL, _NO_TOOL.replace(", as", " because"), _NO_TOOL.replace("'", "’"),
                                  _PLAYABLE_NO_TOOL, _PLAYABLE_NO_TOOL.replace(" ", "\n"),
                                  _TURN_NO_TOOL, _TURN_NO_TOOL.replace("'", "’"), _TURN_NO_TOOL.replace(" ", "\n"),
                                  _CHINESE_NO_TOOL, _CHINESE_NO_TOOL.replace("。", "。\n"),
                                  _SCORE_NO_TOOL, _SCORE_NO_TOOL.replace("'", "’"), _SCORE_NO_TOOL.replace(" ", "\n")])
def test_music_no_tool_predicate_accepts_only_observed_finite_first_sentences(notice):
    assert is_music_tool_unavailable_reply(notice + " Here is a synthetic score instead.")


@pytest.mark.parametrize("text", _MUSIC_NOTICE_FIXTURE["non_refusals"])
def test_playable_audio_notice_does_not_match_pending_quote_history_or_changed_suffix(text):
    assert not is_music_tool_unavailable_reply(text)


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("notice", [_PLAYABLE_NO_TOOL, _TURN_NO_TOOL, _SCORE_NO_TOOL, _CHINESE_NO_TOOL])
def test_known_audio_notice_finishes_original_handle_and_cleans_only_after_exact_proof(
    ledger, monkeypatch, recovery, notice,
):
    from src import client_wrapper

    store, response = _owned_notice(ledger, text=notice + _MUSIC_NOTICE_FIXTURE["synthetic_followup"])
    candidate = SimpleNamespace(rcid=response.rcid, text=response.text, generated_media=[], generated_videos=[])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_notice_rpc(response)),
        read_chat=AsyncMock(return_value=SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=SimpleNamespace(
            metadata=response.metadata, candidates=[candidate]))])), generate_content=AsyncMock())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    finalize = AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(value="completed")))
    monkeypatch.setattr(client_wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    generate = AsyncMock()
    monkeypatch.setattr(media_jobs, "generate_media_response", generate)
    if recovery:
        asyncio.run(media_jobs.recover_media_job(store.get("op_native"), store=store, worker_token="worker"))
    else:
        asyncio.run(media_jobs._save_response(client, response, store.get("op_native"), store=store, token="worker"))
    record = store.get("op_native")
    assert record.state == "failed" and record.error_code == "ARTIFACT_NOT_RETURNED"
    assert record.phase == "media_rejected" and record.cleanup_state == "completed" and not record.cleanup_protected
    assert (record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id) == (
        response.metadata[0], response.metadata[1], response.rcid,
    )
    assert client._batch_execute.await_count == 2  # Strict music cards, then independent completed-empty proof.
    finalize.assert_awaited_once()
    assert finalize.await_args.kwargs["source"] == "gemini_generate_music:rejected"
    generate.assert_not_awaited()
    client.generate_content.assert_not_awaited()


@pytest.mark.parametrize("text", ["Generating your track. The audio will appear when ready.",
    '"' + _NO_TOOL + '"', "> " + _NO_TOOL, "If the tool says " + _NO_TOOL,
    "Please reply with: " + _NO_TOOL, "The next instruction is: " + _GENERIC_ERROR,
    "I seem to be encountering an error while the tool is still running.",
    "I can't generate or output an audio file directly right now, but I'm still checking the tool."])
def test_notice_predicates_reject_pending_quoted_and_worded_instructions(text):
    assert not is_music_tool_unavailable_reply(text)
    assert not is_media_generation_error_reply(text)


@pytest.mark.parametrize("text,expected", [(_NO_TOOL, "ARTIFACT_NOT_RETURNED"),
    (_GENERIC_ERROR, "UPSTREAM_REJECTED"), ('"' + _NO_TOOL + '"', None),
    ("If the tool says " + _NO_TOOL, None), ("Generating your track. Please wait.", None)])
def test_native_dom_notice_patterns_follow_the_same_finite_prefixes(text, expected):
    from tests.test_native_media_browser import _DOM_FIXTURE

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the bundled browser-script contract")
    old = "I don't have a built-in audio generation tool available right now to render a playable sound file directly"
    program = _DOM_FIXTURE.replace(json.dumps(old), json.dumps(text.rstrip(".")))
    script = Path(__file__).parents[1] / "src/data/native_media_browser.js"
    completed = subprocess.run([node, "-e", program, str(script)], check=True, capture_output=True, text=True, timeout=10)
    signals = json.loads(completed.stdout)["music"]
    assert [signal["code"] for signal in signals] == ([expected] if expected else [])



_NOTATION_ONLY = "An original instrumental score.\n\n```abc\nX:1\nT:Synthetic\nM:4/4\nL:1/8\nK:C\nCDEF GABc | cBAG FEDC |\n```\n"


@pytest.mark.parametrize("change", [None, "pending", "foreign_cid", "changed_candidate", "opaque_card", "url", "media", "account"])
@pytest.mark.parametrize("reply_kind", ["notation", "technical_card"])
def test_completed_music_alternatives_end_only_with_independent_exact_failed_or_empty_turn(
    ledger, monkeypatch, change, reply_kind,
):
    from src import client_wrapper
    from src.domain import CleanupObservation, CleanupState

    text = _NOTATION_ONLY if reply_kind == "notation" else _SCORE_NO_TOOL
    store, response = _owned_notice(ledger, text=text)
    rich = None if reply_kind == "notation" else [{"8": [], "73": [None, "Generating your track", None, 3]}]
    kwargs = {"rich": rich}
    if change == "pending":
        kwargs["state"] = 1
    elif change == "foreign_cid":
        kwargs["cid"] = "c_fedcba9876543210"
    elif change == "changed_candidate":
        kwargs["rcid"] = "rc_other"
    elif change == "opaque_card":
        kwargs["rich"] = [{"opaque_job": "synthetic"}]
    elif change == "url":
        response.text += " https://example.invalid/track.mp3"
    elif change == "media":
        response.media = [object()]
    wire = _notice_rpc(response, **kwargs)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=wire), generate_content=AsyncMock())
    cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=response.metadata[0]))
    monkeypatch.setattr(client_wrapper, "finalize_remote_chat_cleanup_from_response", cleanup)
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=())))
    if change == "account":
        monkeypatch.setenv("GEMINI_PSID", "another-owner")
    asyncio.run(media_jobs._save_response(client, response, store.get("op_native"), store=store, token="worker"))
    record = store.get("op_native")
    if change is None:
        assert record.state == "failed" and record.phase == "media_rejected" and not record.cleanup_protected
        assert record.error_code == ("ARTIFACT_NOT_RETURNED" if reply_kind == "notation" else "UPSTREAM_REJECTED")
        assert record.verification_status == ("completed_music_notation_without_artifact_observed" if reply_kind == "notation"
                                               else "upstream_media_error_card_observed")
        assert record.cleanup_state == "completed"
        cleanup.assert_awaited_once()
    else:
        assert record.state != "failed" and record.cleanup_protected
        cleanup.assert_not_awaited()
    client.generate_content.assert_not_awaited()


@pytest.mark.parametrize("proof_kind", ["empty", "failure_card"])
def test_typed_failure_proof_throttling_retains_original_media_guard(ledger, monkeypatch, proof_kind):
    from src.infrastructure.rpc_parsers import RPCParseResult
    from src.services.music_recovery import MusicErrorCardObserved

    store, response = _owned_notice(ledger, text=_NOTATION_ONLY if proof_kind == "empty" else _SCORE_NO_TOOL)
    read_cards = AsyncMock(return_value=[])
    if proof_kind == "failure_card":
        read_cards.side_effect = MusicErrorCardObserved("Synthetic exact error card")
    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", read_cards)
    proof = AsyncMock(return_value=RPCParseResult("rejected", reject_code=429))
    monkeypatch.setattr(media_jobs, "read_completed_empty_media_turn" if proof_kind == "empty"
                        else "read_completed_music_failure_turn", proof)
    finish = AsyncMock()
    monkeypatch.setattr(media_jobs, "_finish_media_rejection", finish)
    asyncio.run(media_jobs._save_response(object(), response, store.get("op_native"), store=store, token="worker"))
    record = store.get("op_native")
    assert record.state == "partial" and record.error_code == "RATE_LIMITED" and record.cleanup_protected
    assert record.phase == "media_readback_rate_limited"
    assert (record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id) == (
        response.metadata[0], response.metadata[1], response.rcid,
    )
    proof.assert_awaited_once()
    finish.assert_not_awaited()
