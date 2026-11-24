"""Generic failure prose cannot override unknown or potentially usable media."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi.exceptions import APIError

from src.constants import GEMINI_GENERIC_FAILURE_REPLY, is_generation_failure_reply
from src.domain import CleanupObservation, CleanupState
from src.infrastructure.operation_store import OperationStore
from src.infrastructure.rpc_contracts import get_contract
from src.services import media_jobs

CID, RID, RCID = "c_0123456789abcdef", "r_synthetic", "rc_synthetic"
CHINESE_FAILURE = "由于程序代码的局限，我无法提供这方面的帮助。"
NOTICES = [GEMINI_GENERIC_FAILURE_REPLY, "Sorry, something went wrong. Please try your request again.",
           "I encountered an error doing what you asked. Could you try again?", "I seem to be encountering an error.",
           CHINESE_FAILURE]


def _wire(text, *, change=None):
    candidate = [None] * 13
    candidate[0], candidate[1], candidate[8], candidate[12] = RCID, [text], [2], [None, []]
    turn = [[CID, RID], None, None, [[candidate]]]
    if change == "opaque":
        candidate[12] = [{"opaque_job": "synthetic-pending"}]
    elif change == "uri":
        candidate[12] = [{"unparsed_media": ["https://example.invalid/video.mp4"]}]
    elif change == "pending":
        candidate[8] = [1]
    elif change == "foreign_cid":
        turn[0][0] = "c_fedcba9876543210"
    elif change == "wrong_rid":
        turn[0][1] = "r_other"
    elif change == "wrong_candidate":
        candidate[0] = "rc_other"
    elif change == "different_text":
        candidate[1][0] = "A different synthetic reply."
    body = [[turn, turn] if change == "duplicate" else [turn]]
    return json.dumps([["wrb.fr", get_contract("web_workflow.turn").rpc_id, json.dumps(body)]])


def _prepare(tmp_path, monkeypatch, text, *, media_type="video", change=None, has_media=False):
    from src import client_wrapper

    monkeypatch.setenv("GEMINI_PSID", "synthetic-generic-failure-owner")
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(tmp_path / "cleanup.sqlite"))
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create_or_get("op_synthetic", "gemini_create_media", media_type=media_type, output_dir=str(tmp_path),
                        filename="synthetic", request_model="gemini-4-hydrogen", effective_backend="Gemini Web native browser")
    assert store.claim_worker("op_synthetic", "worker")
    response = SimpleNamespace(metadata=[CID, RID], rcid=RCID, text=text, images=[], videos=[], media=[], _mcp_native_media=True)
    if has_media:
        setattr(response, {"image": "images", "video": "videos", "music": "media"}[media_type], [object()])
    media_jobs._checkpoint(store, "op_synthetic", "worker", response)
    if change == "response_identity":
        response.rcid = "rc_other"
    read = AsyncMock(return_value=SimpleNamespace(status_code=429 if change == "typed_429" else 200,
                                                 text=_wire(text, change=change)))
    if change == "exception_429":
        read.side_effect = APIError("Batch execution failed with status code 429")
    if change == "account":
        async def changed_account(*args, **kwargs):
            monkeypatch.setenv("GEMINI_PSID", "another-synthetic-owner")
            return SimpleNamespace(status_code=200, text=_wire(text))
        read.side_effect = changed_account
    elif change == "lease_lost":
        async def changed_lease(*args, **kwargs):
            store.retire_worker("op_synthetic", "worker")
            assert store.claim_worker("op_synthetic", "replacement")
            return SimpleNamespace(status_code=200, text=_wire(text))
        read.side_effect = changed_lease
    client = SimpleNamespace(_batch_execute=read, generate_content=AsyncMock())
    cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=CID))
    monkeypatch.setattr(client_wrapper, "finalize_remote_chat_cleanup_from_response", cleanup)
    save = AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=()))
    monkeypatch.setattr(media_jobs, "_save_generated_media", save)
    return store, response, client, cleanup, save


@pytest.mark.parametrize("change", ["opaque", "uri", "pending", "foreign_cid", "wrong_rid", "wrong_candidate",
                                  "different_text", "duplicate", "typed_429", "exception_429", "account",
                                  "response_identity", "lease_lost"])
@pytest.mark.parametrize("text", NOTICES[:3] + [CHINESE_FAILURE])
def test_legacy_generic_failure_keeps_original_guard_without_strict_empty_proof(tmp_path, monkeypatch, change, text):
    store, response, client, cleanup, save = _prepare(tmp_path, monkeypatch, text, change=change)
    asyncio.run(media_jobs._save_response(client, response, store.get("op_synthetic"), store=store, token="worker"))
    saved = store.get("op_synthetic")
    assert saved.state != "failed" and saved.cleanup_protected
    assert (saved.upstream_chat_id, saved.upstream_response_id, saved.upstream_candidate_id) == (CID, RID, RCID)
    if change in {"typed_429", "exception_429"}:
        assert saved.error_code == "RATE_LIMITED"
    if change == "response_identity":
        client._batch_execute.assert_not_awaited()
    elif change == "lease_lost":
        assert saved.worker_token == "replacement"
    cleanup.assert_not_awaited()
    save.assert_not_awaited()
    client.generate_content.assert_not_awaited()


@pytest.mark.parametrize("text", NOTICES)
@pytest.mark.parametrize("media_type", ["image", "video", "music"])
def test_known_generic_failure_requires_exact_completed_empty_turn_before_cleanup(tmp_path, monkeypatch, text, media_type):
    store, response, client, cleanup, _ = _prepare(tmp_path, monkeypatch, text, media_type=media_type)
    asyncio.run(media_jobs._save_response(client, response, store.get("op_synthetic"), store=store, token="worker"))
    saved = store.get("op_synthetic")
    assert saved.state == "failed" and saved.error_code == "UPSTREAM_REJECTED" and not saved.cleanup_protected
    assert saved.cleanup_state == "completed"
    cleanup.assert_awaited_once()
    client._batch_execute.assert_awaited_once()
    client.generate_content.assert_not_awaited()


@pytest.mark.parametrize("media_type", ["image", "video", "music"])
@pytest.mark.parametrize("text", [GEMINI_GENERIC_FAILURE_REPLY, CHINESE_FAILURE])
def test_known_media_takes_precedence_over_generic_failure_prose(tmp_path, monkeypatch, media_type, text):
    store, response, client, cleanup, save = _prepare(tmp_path, monkeypatch, text,
                                                    media_type=media_type, has_media=True)
    asyncio.run(media_jobs._save_response(client, response, store.get("op_synthetic"), store=store, token="worker"))
    cleanup.assert_not_awaited()
    client._batch_execute.assert_not_awaited()
    save.assert_awaited_once()
    assert save.await_args.args[0] is response


@pytest.mark.parametrize("text", [CHINESE_FAILURE, " \n" + CHINESE_FAILURE + "\n "])
def test_observed_chinese_failure_requires_the_complete_reply(text):
    assert is_generation_failure_reply(text)


@pytest.mark.parametrize("text", [
    '"' + CHINESE_FAILURE + '"', "“" + CHINESE_FAILURE + "”", "> " + CHINESE_FAILURE,
    "模型可能回答：" + CHINESE_FAILURE, "请回复：" + CHINESE_FAILURE, "此前的错误是：" + CHINESE_FAILURE,
    "```text\n" + CHINESE_FAILURE + "\n```", CHINESE_FAILURE + "这只是一个示例。",
    CHINESE_FAILURE + " https://example.invalid/result.mp4", "正在生成视频，请稍等。",
    "由于程序代码的局限，但我仍在尝试生成视频。",
])
def test_discussion_quotation_instructions_and_partial_chinese_phrases_are_not_generation_failures(text):
    assert not is_generation_failure_reply(text)
