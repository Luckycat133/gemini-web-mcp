"""An observed native capacity reply needs the original completed exact-turn evidence."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import CleanupObservation, CleanupState
from src.infrastructure.account_identity import current_account_key
from src.services import media_jobs


class RejectionStore:
    def __init__(self):
        self.events = []

    def finish_worker(self, operation_id, **arguments):
        self.events.append(("finish", arguments))
        return True

    def release_failed_media_cleanup(self, operation_id, **arguments):
        self.events.append(("release", operation_id))
        return True

    def is_worker_owner(self, operation_id, token):
        return True

    def update_cleanup(self, operation_id, state):
        self.events.append(("cleanup", state))


@pytest.fixture
def prepared(monkeypatch):
    from src import client_wrapper

    monkeypatch.setenv("GEMINI_PSID", "synthetic-video-capacity-account")
    record = SimpleNamespace(account_key=current_account_key(), operation_id="op_capacity", operation="video",
                             media_type="video", output_dir=None, filename="fixture", requested_model=None,
                             request_model="gemini-4-hydrogen", effective_backend="Gemini Web video mode",
                             retain_chat=False, delete_after_seconds=None)
    text = media_jobs._VIDEO_CAPACITY_REJECTION
    response = SimpleNamespace(metadata=["c_capacity", "r_capacity"], rcid="rc_capacity", text=text,
                               images=[], videos=[], media=[])
    turn = {"chat_id": "c_capacity", "rid": "r_capacity", "rcid": "rc_capacity",
            "completed": True, "text": text}
    readback = AsyncMock(return_value=turn)
    save = AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=()))
    cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED,
                                                        upstream_chat_id="c_capacity"))
    monkeypatch.setattr(media_jobs, "fetch_workflow_turn", readback)
    monkeypatch.setattr(media_jobs, "_save_generated_media", save)
    monkeypatch.setattr(client_wrapper, "finalize_remote_chat_cleanup_from_response", cleanup)
    return record, response, turn, readback, save, cleanup


@pytest.mark.parametrize("whitespace", [False, True])
def test_exact_completed_native_capacity_reply_is_rate_limited_and_cleaned_up(prepared, whitespace):
    record, response, _, readback, save, cleanup = prepared
    if whitespace:
        response.text = "\n" + response.text.replace("right now", "right\n now") + "\n"
    store = RejectionStore()
    client = object()
    asyncio.run(media_jobs._save_response(client, response, record, store=store, token="worker"))
    readback.assert_awaited_once_with(client, "c_capacity", "r_capacity", "rc_capacity")
    save.assert_not_awaited()
    cleanup.assert_awaited_once()
    assert [event[0] for event in store.events] == ["finish", "release", "cleanup", "finish"]
    assert store.events[0][1]["state"] == "partial"
    assert store.events[-1][1]["state"] == "failed"
    assert store.events[-1][1]["error_code"] == "UPSTREAM_LIMIT_EXCEEDED"
    assert store.events[-1][1]["verification_status"] == "upstream_limit_message_observed"


@pytest.mark.parametrize("change", ["missing", "incomplete", "chat", "response", "candidate", "different_text", "read_error"])
def test_unverified_capacity_reply_keeps_the_outcome_unconfirmed(prepared, change):
    record, response, turn, readback, save, cleanup = prepared
    if change == "missing":
        readback.return_value = None
    elif change == "incomplete":
        turn["completed"] = False
    elif change in {"chat", "response", "candidate"}:
        turn[{"chat": "chat_id", "response": "rid", "candidate": "rcid"}[change]] = "different"
    elif change == "different_text":
        turn["text"] = "A different completed reply."
    elif change == "read_error":
        readback.side_effect = RuntimeError("synthetic read failure")
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    save.assert_awaited_once()
    cleanup.assert_not_awaited()
    assert len(store.events) == 1
    assert store.events[0][1]["state"] == "partial"
    assert store.events[0][1]["phase"] == "media_artifact_not_observed"
    assert store.events[0][1]["error_code"] is None


@pytest.mark.parametrize("change", ["other_kind", "substring", "inline_media", "missing_identity"])
def test_capacity_text_alone_does_not_override_media_or_other_workflows(prepared, change):
    record, response, _, readback, _, cleanup = prepared
    if change == "other_kind":
        record.media_type = "image"
    elif change == "substring":
        response.text += " This is a discussion of an example error."
    elif change == "inline_media":
        response.videos = [object()]
    else:
        response.rcid = ""
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    readback.assert_not_awaited()
    cleanup.assert_not_awaited()
    assert all(event[0] != "release" for event in store.events)
    assert all(event[1].get("error_code") != "UPSTREAM_LIMIT_EXCEEDED"
               for event in store.events if event[0] == "finish")


def test_capacity_readback_does_not_release_cleanup_after_account_switch(prepared, monkeypatch):
    record, response, turn, readback, save, cleanup = prepared

    async def switched(*arguments):
        monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
        return turn

    readback.side_effect = switched
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    assert store.events == []
    save.assert_not_awaited()
    cleanup.assert_not_awaited()
