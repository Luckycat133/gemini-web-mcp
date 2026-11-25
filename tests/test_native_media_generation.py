"""Native submission preserves model, account, exact identity and verified files."""

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

# monkeypatch asyncio.sleep 时用来让出控制权的原始实现（不能用 asyncio.sleep，会递归到自己）。
_ORIGINAL_ASYNCIO_SLEEP = asyncio.sleep

from gemini_webapi.exceptions import APIError
from gemini_webapi.types import Candidate, ModelOutput
import pytest

from src.domain import ArtifactKind, ArtifactState, CleanupObservation, CleanupState
from src.constants import GEMINI_GENERIC_FAILURE_REPLY
from src.infrastructure.native_media_browser import NativeMediaObservation
from src.services import native_media_generation as native
from src.services.artifacts import artifact_from_remote, artifact_id, extract_response_artifacts
from src.services.media_generation import generate_media_response, music_option_arguments, music_options_error
from src.services.media_storage import _save_generated_media
from src.services.model_policy import ModelPolicyError, model_selection_from_response
from tests.test_web_model_selection import discovered_client

CID = "c_0123456789abcdef"
RID = "r_synthetic_native"
RCID = "rc_synthetic_native"
BLOB = "blob:https://gemini.google.com/00000000-0000-0000-0000-000000000000"


def output(*, cid=CID, rid=RID, rcid=RCID):
    return ModelOutput(metadata=[cid, rid], candidates=[Candidate(rcid=rcid, text="")])


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-native-account")
    monkeypatch.delenv("GEMINI_WEB_MEDIA_TRANSPORT", raising=False)
    client = discovered_client()
    client.cookies = {"__Secure-1PSID": "synthetic-native-account"}
    history = SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=output())])
    monkeypatch.setattr(type(client), "read_chat", AsyncMock(return_value=history))
    return client, history


def ready(kind="music", body=b"unverified test bytes"):
    return NativeMediaObservation("ready", CID, True,
        ({"kind": kind, "url": BLOB, "content": body, "byte_size": len(body)},), submitted=True)


def driver(monkeypatch, result, *, during=None):
    calls = []

    async def run(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches):
        calls.append((kind, prompt, model_label, timeout))
        assert account_matches() and cookies["__Secure-1PSID"] == "synthetic-native-account"
        if during:
            during()
        if result.chat_id:
            await on_chat(result.chat_id)
        return result

    monkeypatch.setattr(native, "run_native_media_browser", run)
    return calls


async def generate(client, kind="music", model="neon", *, files=None, **kwargs):
    return await native.generate_native_media_response(client,
        arguments={"prompt": "A synthetic public-domain motif", "model": model, "timeout": 30, "files": files},
        media_type=kind, observe=kwargs.pop("observe", lambda _: None), **kwargs)


@pytest.mark.parametrize("kind", ["music", "video"])
@pytest.mark.parametrize("model,key", [("hydrogen", "56fdd199312815e2"), ("neon", "797f3d0293f288ad"),
                                      ("carbon", "95a4c0abcba24364")])
def test_native_dispatch_uses_exact_registry_label_and_checkpoints_cid_before_actual_turn(setup, monkeypatch, kind, model, key):
    client, _ = setup
    seen = []
    calls = driver(monkeypatch, ready(kind))
    response = asyncio.run(generate(client, kind, model, observe=seen.append))
    assert calls == [(kind, "A synthetic public-domain motif", f"4 {model.title()}", 22.0)]
    assert seen[0].metadata == [CID] and seen[0]._mcp_native_media is True
    assert seen[1].metadata == [CID, RID] and seen[1].rcid == RCID
    assert response.metadata == [CID, RID] and response.rcid == RCID
    assert response.native_model_selector_verified and response._mcp_native_media
    assert model_selection_from_response(response).effective_model == client._model_registry[key].model_name


def test_native_video_forwards_one_validated_reference_to_the_same_single_browser(setup, monkeypatch, tmp_path):
    client, _ = setup
    path = tmp_path / "reference.png"
    path.write_bytes(b"local reference fixture")
    monkeypatch.chdir(tmp_path)
    submitted = []

    async def browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches, reference_image_path):
        assert account_matches()
        submitted.append((kind, model_label, reference_image_path))
        await on_chat(CID)
        return ready("video")

    monkeypatch.setattr(native, "run_native_media_browser", browser)
    result = asyncio.run(generate(client, kind="video", files=[path.name]))
    assert result.native_media_submitted and result.native_model_selector_verified
    assert submitted == [("video", "4 Neon", str(path))]


def test_native_video_reference_cannot_be_omitted_for_a_driver_without_upload_support(setup, monkeypatch, tmp_path):
    client, _ = setup
    path = tmp_path / "reference.png"
    path.write_bytes(b"local reference fixture")
    calls = driver(monkeypatch, ready("video"))
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client, kind="video", files=[str(path)]))
    assert error.value.code == "CAPABILITY_UNAVAILABLE" and not error.value.submitted and not calls

    # A plain video still works through the older browser contract.
    result = asyncio.run(generate(client, kind="video", files=None))
    assert result.native_media_submitted and len(calls) == 1


@pytest.mark.parametrize("kind,count", [("music", 1), ("video", 2)])
def test_native_media_refuses_unsupported_reference_shapes_before_browser_dispatch(setup, monkeypatch, tmp_path, kind, count):
    client, _ = setup
    path = tmp_path / "reference.png"
    path.write_bytes(b"local reference fixture")
    calls = driver(monkeypatch, ready(kind))
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client, kind=kind, files=[str(path)] * count))
    assert error.value.code == "CAPABILITY_UNAVAILABLE" and not error.value.submitted and not calls


@pytest.mark.parametrize("value", ["", "   ", "missing.png", "not-an-image.txt"])
def test_native_video_refuses_empty_or_invalid_reference_before_browser_dispatch(setup, monkeypatch, tmp_path, value):
    client, _ = setup
    (tmp_path / "not-an-image.txt").write_bytes(b"not an image")
    monkeypatch.chdir(tmp_path)
    calls = driver(monkeypatch, ready("video"))
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client, kind="video", files=[value]))
    assert error.value.code == "INVALID_ARGUMENT" and not error.value.submitted and not calls


@pytest.mark.parametrize("model,key,labels", [
    ("hydrogen", "56fdd199312815e2", ("日常帮助", "Everyday help")),
    ("neon", "797f3d0293f288ad", ("更深入的分析", "Deeper analysis")),
    ("carbon", "95a4c0abcba24364", ("代码和复杂工作", "Code and complex work")),
])
def test_native_technical_selector_is_stable_when_registry_description_locale_changes(setup, monkeypatch, model, key, labels):
    client, _ = setup
    original = client._model_registry[key]
    calls = driver(monkeypatch, ready())
    for label in labels:
        client._model_registry[key] = original.model_copy(update={"display_name": label})
        response = asyncio.run(generate(client, model=model))
        assert calls[-1][2] == f"4 {model.title()}"
        selection = model_selection_from_response(response)
        assert selection.effective_model == original.model_name and selection.fallback_reason is None
    assert len(calls) == 2


def test_native_legacy_descriptor_preserves_its_discovered_display_label(setup, monkeypatch):
    client, _ = setup
    legacy = client._model_registry["56fdd199312815e2"].model_copy(update={
        "model_id": "synthetic-legacy-native-id", "model_name": "gemini-3.8-flash", "display_name": "Legacy Flash",
        "aliases": [],
    })
    client._model_registry[legacy.model_id] = legacy
    calls = driver(monkeypatch, ready())
    response = asyncio.run(generate(client, model=legacy))
    assert calls[0][2] == "Legacy Flash"
    assert model_selection_from_response(response).effective_model == legacy.model_name


@pytest.mark.parametrize("duration", ["short", "standard"])
@pytest.mark.parametrize("vocals", ["instrumental", "vocals", "auto"])
def test_explicit_music_controls_reach_native_browser_once_without_changing_model(setup, monkeypatch, duration, vocals):
    client, _ = setup
    seen = []

    async def browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches,
                      music_duration=None, music_vocals=None):
        seen.append((kind, model_label, music_duration, music_vocals))
        assert account_matches()
        await on_chat(CID)
        return ready()

    monkeypatch.setattr(native, "run_native_media_browser", browser)
    response = asyncio.run(generate_media_response(client, arguments={"prompt": "motif", "model": "neon"},
        schedule_cleanup=lambda *a, **k: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=CID),
        retain_chat=False, delete_after_seconds=None, source="fixture", media_type="music",
        music_duration=duration, music_vocals=vocals))
    assert seen == [("music", "4 Neon", duration, vocals)]
    assert model_selection_from_response(response).effective_model == "gemini-neon"


@pytest.mark.parametrize("options", [{"music_duration": "short"}, {"music_vocals": "instrumental"}])
def test_music_options_refuse_before_dispatch_when_driver_cannot_verify_controls(setup, monkeypatch, options):
    client, _ = setup
    calls = driver(monkeypatch, ready())  # Deliberately no music-control parameters.
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client, **options))
    assert error.value.code == "CAPABILITY_UNAVAILABLE" and not error.value.submitted and not calls


def test_explicit_sdk_music_controls_fail_before_any_native_or_sdk_submission(setup, monkeypatch):
    client, _ = setup
    calls = driver(monkeypatch, ready())
    stream = AsyncMock()
    monkeypatch.setattr(type(client), "generate_content_stream", stream)
    monkeypatch.setenv("GEMINI_WEB_MEDIA_TRANSPORT", "sdk")
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate_media_response(client, arguments={"prompt": "motif", "model": "neon"},
            schedule_cleanup=lambda *a, **k: None, retain_chat=False, delete_after_seconds=None,
            source="fixture", media_type="music", music_duration="short", music_vocals="instrumental"))
    assert error.value.code == "CAPABILITY_UNAVAILABLE" and not error.value.submitted and not calls
    stream.assert_not_called()


@pytest.mark.parametrize("media_type,options", [
    ("video", {"music_duration": "short"}), ("image", {"music_vocals": "auto"}),
    ("music", {"music_duration": "full"}), ("music", {"music_vocals": "yes"}),
    ("music", {"music_duration": []}), ("music", {"music_vocals": False}),
])
def test_shared_music_options_validator_rejects_wrong_workflow_or_unknown_values(media_type, options):
    assert music_options_error(media_type=media_type, **options)


def test_omitted_music_controls_remain_omitted_for_legacy_clients():
    assert music_option_arguments(None, None) == {}
    assert music_option_arguments("short", None) == {"music_duration": "short"}
    assert music_option_arguments(None, "instrumental") == {"music_vocals": "instrumental"}
    assert music_options_error(media_type="video") is None


def test_native_generation_reads_original_ready_media_when_browser_player_stays_unloaded(setup, monkeypatch):
    client, history = setup
    media = SimpleNamespace(mp3_url="https://lh3.googleusercontent.com/owned-native-audio", url="")
    history.turns[0].model_output = SimpleNamespace(metadata=[CID, RID], candidates=[SimpleNamespace(rcid=RCID)],
                                                  text="An original track", videos=[], media=[media])
    calls, closed, seen = [], [], []

    async def waiting_browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches):
        calls.append(kind)
        await on_chat(CID)
        try:
            # An unplayed native player need not load its bytes.
            # 用假 sleep 代替真实 60 秒等待：生产代码靠 asyncio.wait_for(..., timeout=timeout)
            # 触发超时路径，被取消的等待会立刻结束，不用真等 60 秒拖慢整套 CI。
            await _ORIGINAL_ASYNCIO_SLEEP(60)
        finally:
            closed.append(True)

    monkeypatch.setattr(native, "run_native_media_browser", waiting_browser)
    send = AsyncMock(side_effect=AssertionError("Read-back must never generate again"))
    monkeypatch.setattr(type(client), "generate_content", send)
    response = asyncio.run(generate(client, observe=seen.append))
    assert calls == ["music"] and closed == [True]
    assert response.media == [media] and response.metadata == [CID, RID] and response.rcid == RCID
    assert seen[0].metadata == [CID] and response.native_model_selector_verified
    send.assert_not_awaited()


@pytest.mark.parametrize("account_changes", [False, True])
def test_native_rate_limit_stops_canonical_reads_and_preserves_passive_browser_observation(
    setup, monkeypatch, account_changes,
):
    client, _ = setup
    started, release_read, release_browser, closed = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen = []

    async def throttled(*_args, **_kwargs):
        started.set()
        await release_read.wait()
        raise APIError("Batch execution failed with status code 429")

    async def browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches):
        try:
            await on_chat(CID)
            await release_browser.wait()
            return ready()
        finally:
            closed.set()

    real_wait = asyncio.wait

    async def quick_wait(tasks, *, timeout):
        return await real_wait(tasks, timeout=0.001)

    read = AsyncMock(side_effect=throttled)
    monkeypatch.setattr(type(client), "read_chat", read)
    monkeypatch.setattr(native, "run_native_media_browser", browser)
    monkeypatch.setattr(native.asyncio, "wait", quick_wait)
    send = AsyncMock()
    monkeypatch.setattr(type(client), "generate_content", send)

    async def exercise():
        operation = asyncio.create_task(generate(client, observe=seen.append))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            await asyncio.sleep(0.01)
            read.assert_awaited_once()
            release_read.set()
            await asyncio.sleep(0.01)
            assert not operation.done() and not closed.is_set()
            read.assert_awaited_once()
            if account_changes:
                monkeypatch.setenv("GEMINI_PSID", "different-synthetic-account")
            release_browser.set()
            with pytest.raises(native.NativeMediaGenerationError) as error:
                await asyncio.wait_for(operation, timeout=1)
            assert error.value.code == ("READBACK_UNVERIFIED" if account_changes else "RATE_LIMITED")
            assert error.value.submitted and error.value.chat_id == CID
        finally:
            if not operation.done():
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation

    asyncio.run(exercise())
    assert [response.metadata for response in seen] == [[CID]]
    assert closed.is_set()
    read.assert_awaited_once()
    send.assert_not_awaited()


def test_final_native_readback_reports_rate_limit_without_a_second_probe(setup, monkeypatch):
    client, history = setup
    read = AsyncMock(side_effect=[history, APIError("Batch execution failed with status code 429")])
    monkeypatch.setattr(type(client), "read_chat", read)
    calls = driver(monkeypatch, ready("video"))
    seen = []

    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client, "video", observe=seen.append))

    assert error.value.code == "RATE_LIMITED" and error.value.submitted and error.value.chat_id == CID
    assert [response.metadata for response in seen] == [[CID], [CID, RID]]
    assert len(calls) == 1 and read.await_count == 2


def _failure_rpc(text, *, state=2, rich=None, cid=CID, rid=RID, rcid=RCID):
    candidate = [None] * 13
    candidate[0], candidate[1], candidate[8] = rcid, [text], [state]
    candidate[12] = [None, []] if rich is None else rich
    body = [[[[cid, rid], None, None, [[candidate]]]]]
    return SimpleNamespace(status_code=200, text=json.dumps([["wrb.fr", "hNvQHb", json.dumps(body)]]))


@pytest.mark.parametrize("change", [None, "pending", "uri", "opaque", "candidate", "response", "chat", "empty"])
def test_native_failure_requires_independent_exact_completed_empty_proof(monkeypatch, change):
    text = GEMINI_GENERIC_FAILURE_REPLY
    options = ({"state": 1} if change == "pending" else {"rich": ["https://example.invalid/clip.mp4"]} if change == "uri"
               else {"rich": [{"opaque": "synthetic-job"}]} if change == "opaque" else
               {"rcid": "rc_other"} if change == "candidate" else {"rid": "r_other"} if change == "response" else
               {"cid": "c_fedcba9876543210"} if change == "chat" else {})
    rpc = _failure_rpc(text, **options)
    if change == "empty":
        rpc.text = json.dumps([["wrb.fr", "hNvQHb", json.dumps([[]])]])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=rpc))
    response = SimpleNamespace(metadata=[CID, RID], rcid=RCID, text=text, images=[], videos=[], media=[])
    assert asyncio.run(native._verified_native_failure(client, response, CID, "video", timeout=1)) is (change is None)
    client._batch_execute.assert_awaited_once()


@pytest.mark.parametrize("text,kind", [
    ("Generating your video. Please wait.", "video"),
    ('"' + GEMINI_GENERIC_FAILURE_REPLY + '"', "video"),
    ("Earlier, " + GEMINI_GENERIC_FAILURE_REPLY, "video"),
    ("I cannot directly generate or output playable audio files here, as the music generation tool is currently unavailable.", "video"),
])
def test_unrecognized_or_wrong_media_notice_does_not_start_terminal_read(text, kind):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    response = SimpleNamespace(metadata=[CID, RID], rcid=RCID, text=text, images=[], videos=[], media=[])
    assert not asyncio.run(native._verified_native_failure(client, response, CID, kind, timeout=1))
    client._batch_execute.assert_not_awaited()


@pytest.mark.parametrize("text,kind", [
    (GEMINI_GENERIC_FAILURE_REPLY, "video"),
    ("I seem to be encountering an error.", "video"),
    ("I cannot directly generate or output playable audio files here, as the music generation tool is currently unavailable.", "music"),
])
def test_verified_native_failure_ends_only_local_browser_observer_without_another_send(setup, monkeypatch, text, kind):
    client, history = setup
    history.turns[0].model_output = SimpleNamespace(metadata=[CID, RID], candidates=[SimpleNamespace(rcid=RCID)],
                                                  text=text, videos=[], media=[])
    rpc = AsyncMock(return_value=_failure_rpc(text))
    monkeypatch.setattr(type(client), "_batch_execute", rpc)
    calls, closed = [], []

    async def waiting_browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches):
        calls.append(kind)
        await on_chat(CID)
        try:
            await asyncio.Future()
        finally:
            closed.append(True)

    real_wait = asyncio.wait

    async def quick_wait(tasks, *, timeout):
        return await real_wait(tasks, timeout=0.001)

    monkeypatch.setattr(native.asyncio, "wait", quick_wait)
    monkeypatch.setattr(native, "run_native_media_browser", waiting_browser)
    send = AsyncMock(side_effect=AssertionError("A rejection must never be resubmitted"))
    monkeypatch.setattr(type(client), "generate_content", send)
    response = asyncio.run(asyncio.wait_for(generate(client, kind), timeout=1))
    assert calls == [kind] and closed == [True]
    assert response.metadata == [CID, RID] and response.rcid == RCID and response.text == text
    assert response._mcp_native_media and response.native_media_submitted and response.native_model_selector_verified
    assert not response.media and not response.videos
    rpc.assert_awaited_once()
    send.assert_not_awaited()


@pytest.mark.parametrize("change", ["pending", "uri", "opaque", "identity"])
def test_unverified_native_failure_keeps_observing_original_submission(setup, monkeypatch, change):
    client, history = setup
    text = GEMINI_GENERIC_FAILURE_REPLY
    history.turns[0].model_output = SimpleNamespace(metadata=[CID, RID], candidates=[SimpleNamespace(rcid=RCID)],
                                                  text=text, videos=[], media=[])
    read, closed = asyncio.Event(), []
    options = ({"state": 1} if change == "pending" else {"rich": ["https://example.invalid/clip.mp4"]} if change == "uri"
               else {"rich": [{"opaque": "synthetic-job"}]} if change == "opaque" else {"rid": "r_other"})

    async def proof(*args, **kwargs):
        read.set()
        return _failure_rpc(text, **options)

    async def browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches):
        await on_chat(CID)
        try:
            await asyncio.Future()
        finally:
            closed.append(True)

    real_wait = asyncio.wait

    async def quick_wait(tasks, *, timeout):
        return await real_wait(tasks, timeout=0.001)

    monkeypatch.setattr(native.asyncio, "wait", quick_wait)
    monkeypatch.setattr(type(client), "_batch_execute", proof)
    monkeypatch.setattr(native, "run_native_media_browser", browser)
    send = AsyncMock()
    monkeypatch.setattr(type(client), "generate_content", send)

    async def exercise():
        operation = asyncio.create_task(generate(client))
        try:
            await asyncio.wait_for(read.wait(), timeout=1)
            await asyncio.sleep(0.01)
            assert not operation.done()
        finally:
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation

    asyncio.run(exercise())
    assert closed == [True]
    send.assert_not_awaited()


def test_native_refusal_proof_429_stops_further_reads_and_keeps_the_exact_turn(setup, monkeypatch):
    client, history = setup
    history.turns[0].model_output = SimpleNamespace(metadata=[CID, RID], candidates=[SimpleNamespace(rcid=RCID)],
                                                  text=GEMINI_GENERIC_FAILURE_REPLY, videos=[], media=[])
    proof_requested, release_browser = asyncio.Event(), asyncio.Event()
    seen = []

    async def proof(*args, **kwargs):
        proof_requested.set()
        raise APIError("Batch execution failed with status code 429")

    async def browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches):
        await on_chat(CID)
        await release_browser.wait()
        return NativeMediaObservation("rejected", CID, True, error_code="UPSTREAM_REJECTED", submitted=True)

    real_wait = asyncio.wait

    async def quick_wait(tasks, *, timeout):
        return await real_wait(tasks, timeout=0.001)

    rpc = AsyncMock(side_effect=proof)
    monkeypatch.setattr(type(client), "_batch_execute", rpc)
    monkeypatch.setattr(native.asyncio, "wait", quick_wait)
    monkeypatch.setattr(native, "run_native_media_browser", browser)

    async def exercise():
        operation = asyncio.create_task(generate(client, "video", observe=seen.append))
        await asyncio.wait_for(proof_requested.wait(), timeout=1)
        await asyncio.sleep(0.01)
        assert not operation.done()
        assert client.read_chat.await_count == 2
        rpc.assert_awaited_once()
        release_browser.set()
        with pytest.raises(native.NativeMediaGenerationError) as error:
            await asyncio.wait_for(operation, timeout=1)
        assert error.value.code == "RATE_LIMITED" and error.value.submitted and error.value.chat_id == CID

    asyncio.run(exercise())
    assert seen[0].metadata == [CID]
    assert all(response.metadata == [CID, RID] and response.rcid == RCID for response in seen[1:])
    assert client.read_chat.await_count == 2
    rpc.assert_awaited_once()


def test_native_failure_account_drift_during_proof_keeps_local_observer_running(setup, monkeypatch):
    client, history = setup
    text = GEMINI_GENERIC_FAILURE_REPLY
    history.turns[0].model_output = SimpleNamespace(metadata=[CID, RID], candidates=[SimpleNamespace(rcid=RCID)],
                                                  text=text, videos=[], media=[])
    proof_read, continued = asyncio.Event(), []

    async def proof(*args, **kwargs):
        monkeypatch.setenv("GEMINI_PSID", "changed-synthetic-account")
        proof_read.set()
        return _failure_rpc(text)

    monkeypatch.setattr(type(client), "_batch_execute", proof)

    async def browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches):
        await on_chat(CID)
        await proof_read.wait()
        await asyncio.sleep(0.01)
        continued.append(True)
        return NativeMediaObservation("unverified", CID, True, error_code="READBACK_UNVERIFIED", submitted=True)

    real_wait = asyncio.wait

    async def quick_wait(tasks, *, timeout):
        return await real_wait(tasks, timeout=0.001)

    monkeypatch.setattr(native.asyncio, "wait", quick_wait)
    monkeypatch.setattr(native, "run_native_media_browser", browser)
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(asyncio.wait_for(generate(client), timeout=1))
    assert error.value.submitted and continued == [True]


@pytest.mark.parametrize("model", ["argon", "neon-typo", "auto"])
def test_absent_or_unsupported_model_never_opens_native_composer(setup, monkeypatch, model):
    client, _ = setup
    calls = driver(monkeypatch, ready())
    with pytest.raises(ModelPolicyError):
        asyncio.run(generate(client, model=model))
    assert not calls


def test_unknown_membership_refuses_before_browser_dispatch(setup, monkeypatch):
    client, _ = setup
    client._usage_info = {}
    calls = driver(monkeypatch, ready())
    with pytest.raises(ModelPolicyError):
        asyncio.run(generate(client))
    assert not calls


def test_mismatched_cookie_account_refuses_before_browser_dispatch(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setenv("GEMINI_PSID", "another-account")
    calls = driver(monkeypatch, ready())
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client))
    assert error.value.code == "AUTH_REQUIRED" and not error.value.submitted and not calls


@pytest.mark.parametrize("changed_read", [1, 2])
def test_account_change_during_native_readback_never_checkpoints_returned_turn(setup, monkeypatch, changed_read):
    client, history = setup
    reads = 0
    observed = []

    async def read_chat(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == changed_read:
            monkeypatch.setenv("GEMINI_PSID", "another-account")
        return history

    monkeypatch.setattr(type(client), "read_chat", read_chat)
    driver(monkeypatch, ready())

    def observe(response):
        import os

        observed.append((list(response.metadata), os.environ["GEMINI_PSID"]))

    with pytest.raises(native.NativeMediaGenerationError):
        asyncio.run(generate(client, observe=observe))
    assert observed[0][0] == [CID]
    assert all(account == "synthetic-native-account" for _, account in observed)
    assert len(observed) == changed_read


@pytest.mark.parametrize("history_change", ["wrong_cid", "multiple_turns", "multiple_candidates", "wrong_metadata_candidate"])
def test_readback_refuses_ambiguous_or_other_turn_without_manufacturing_identity(setup, monkeypatch, history_change):
    client, history = setup
    if history_change == "wrong_cid":
        history.turns[0].model_output = output(cid="c_fedcba9876543210")
    elif history_change == "multiple_turns":
        history.turns.append(SimpleNamespace(role="model", model_output=output(rid="r_other")))
    elif history_change == "multiple_candidates":
        history.turns[0].model_output.candidates.append(Candidate(rcid="rc_other", text=""))
    else:
        history.turns[0].model_output.metadata.append("rc_other")
    calls = driver(monkeypatch, ready())
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client))
    assert error.value.submitted and error.value.chat_id == CID and len(calls) == 1


def test_unknown_postsubmit_result_never_retries_or_uses_sdk(setup, monkeypatch):
    client, _ = setup
    calls = driver(monkeypatch, NativeMediaObservation(error_code="TIMED_OUT", submitted=True))
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client))
    assert error.value.submitted and error.value.code == "TIMED_OUT" and len(calls) == 1


def test_history_timeout_preserves_cid_checkpoint_and_submission_uncertainty(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(type(client), "read_chat", AsyncMock(side_effect=TimeoutError))
    calls = driver(monkeypatch, ready())
    seen = []
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client, observe=seen.append))
    assert seen[0].metadata == [CID] and error.value.submitted and error.value.chat_id == CID
    assert error.value.code == "READBACK_UNVERIFIED" and len(calls) == 1


def test_observed_rejection_returns_real_turn_for_strict_existing_rejection_readback(setup, monkeypatch):
    client, history = setup
    history.turns[0].model_output.candidates[0].text = "I can create more videos as soon as your limit resets. Check your usage in Settings."
    calls = driver(monkeypatch, NativeMediaObservation("rejected", CID, True,
        error_code="UPSTREAM_LIMIT_EXCEEDED", submitted=True))
    response = asyncio.run(generate(client, "video"))
    assert response.metadata == [CID, RID] and response.rcid == RCID and not response.videos
    assert response.native_error_code == "UPSTREAM_LIMIT_EXCEEDED" and len(calls) == 1


@pytest.mark.parametrize("options", [{"duration_seconds": 15}, {"aspect_ratio": "9:16"}])
def test_driver_without_verified_option_support_refuses_before_submission(setup, monkeypatch, options):
    client, _ = setup
    calls = driver(monkeypatch, ready("video"))
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(generate(client, "video", **options))
    assert not error.value.submitted and not calls


def test_native_default_route_reuses_cleanup_checkpoint_and_explicit_sdk_override(setup, monkeypatch):
    client, _ = setup
    calls = driver(monkeypatch, ready())
    checkpoints = []
    cleanup = Mock(return_value=CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=CID))
    result = asyncio.run(generate_media_response(client, arguments={"prompt": "motif", "model": "neon"},
        schedule_cleanup=cleanup, retain_chat=False, delete_after_seconds=None, source="fixture",
        media_type="music", checkpoint=checkpoints.append))
    assert result.rcid == RCID and len(calls) == 1 and cleanup.call_count == 1
    assert checkpoints[0].metadata == [CID] and cleanup.call_args.kwargs["delete_after_seconds"] == 1800
    assert native.native_media_enabled(client, "music")
    monkeypatch.setenv("GEMINI_WEB_MEDIA_TRANSPORT", "sdk")
    assert not native.native_media_enabled(client, "music")
    assert not native.native_media_enabled(client, "image")
    assert not native.native_media_enabled(AsyncMock(), "video")
    sdk_calls = []

    async def sdk_stream(self, **kwargs):
        sdk_calls.append(kwargs)
        yield output()

    monkeypatch.setattr(type(client), "generate_content_stream", sdk_stream)
    sdk_result = asyncio.run(generate_media_response(client,
        arguments={"prompt": "explicit compatible request", "model": "neon"},
        schedule_cleanup=cleanup, retain_chat=False, delete_after_seconds=None, source="fixture", media_type="music"))
    assert sdk_result.rcid == RCID and len(calls) == 1
    assert len(sdk_calls) == 1 and sdk_calls[0]["prompt"] == "explicit compatible request"


@pytest.mark.parametrize("kind", ["music", "video"])
def test_native_blob_uses_existing_storage_signature_and_track_verification(setup, monkeypatch, tmp_path, kind):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are needed for independent playable-media verification")
    client, _ = setup
    sample = tmp_path / ("sample.mp3" if kind == "music" else "sample.mp4")
    source = ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.2"] if kind == "music" else [
        "-f", "lavfi", "-i", "color=c=black:s=32x32:d=0.2", "-pix_fmt", "yuv420p"]
    subprocess.run(["ffmpeg", "-v", "error", *source, str(sample)], check=True, timeout=15)
    driver(monkeypatch, ready(kind, sample.read_bytes()))
    response = asyncio.run(generate(client, kind))
    saved = asyncio.run(_save_generated_media(response, media_type=kind, output_dir=str(tmp_path / "saved"),
        filename="native", prompt="", requested_model="neon", request_model="neon", effective_backend="web",
        observed_backend=None, source_chat_id=CID))
    assert not saved.failures and len(saved.artifacts) == 1
    artifact = saved.artifacts[0]
    assert artifact.state == ArtifactState.LOCAL and Path(artifact.local_path).read_bytes() == sample.read_bytes()
    assert artifact.uri is None
    assert artifact.id == artifact_id(artifact.kind, local_path=artifact.local_path)
    assert not extract_response_artifacts(response, media_type=kind)
    assert {"media_content_signature", "media_track_probe"} <= set(artifact.verification.methods)


@pytest.mark.parametrize("kind", ["music", "video"])
def test_fake_blob_cannot_be_reported_as_usable_media(setup, monkeypatch, tmp_path, kind):
    client, _ = setup
    driver(monkeypatch, ready(kind))
    response = asyncio.run(generate(client, kind))
    saved = asyncio.run(_save_generated_media(response, media_type=kind, output_dir=str(tmp_path),
        filename="native", prompt="", requested_model="neon", request_model="neon", effective_backend="web",
        observed_backend=None, source_chat_id=CID))
    assert not saved.artifacts and saved.failures and not list(tmp_path.iterdir())
    assert not extract_response_artifacts(response, media_type=kind)


@pytest.mark.parametrize("attribute,media_type", [("image_url", "image"), ("video_url", "video"),
                                                 ("audio_url", "music")])
def test_ephemeral_browser_objects_are_never_exposed_as_direct_remote_artifacts(attribute, media_type):
    assert not extract_response_artifacts(SimpleNamespace(**{attribute: BLOB}), media_type=media_type)
    remote = "https://lh3.googleusercontent.com/fixture-media"
    artifacts = extract_response_artifacts(SimpleNamespace(**{attribute: remote}), media_type=media_type)
    assert len(artifacts) == 1 and artifacts[0].state == ArtifactState.REMOTE and artifacts[0].uri == remote


def test_direct_remote_constructor_marks_browser_object_unavailable():
    artifact = artifact_from_remote(ArtifactKind.AUDIO, BLOB)
    assert artifact.state == ArtifactState.FAILED and artifact.uri is None
    assert artifact.verification.methods == ("browser_object_uri_unavailable",)


@pytest.mark.parametrize("reply_kind", ["notation", "technical_card"])
@pytest.mark.parametrize("complete", [False, True])
def test_completed_music_score_or_known_failure_card_can_end_only_local_observation(reply_kind, complete):
    text = ("An original score.\n```abc\nX:1\nM:4/4\nL:1/8\nK:C\nCDEF GABc |\n```\n" if reply_kind == "notation" else
            "I can't generate an audio track directly right now, but here is an original, synthetic score.")
    rich = None if reply_kind == "notation" else [{"8": [], "73": [None, "Generating your track", None, 3]}]
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_failure_rpc(text, state=2 if complete else 1, rich=rich)),
                             generate_content=AsyncMock())
    response = SimpleNamespace(metadata=[CID, RID], rcid=RCID, text=text, images=[], videos=[], media=[])
    assert asyncio.run(native._verified_native_failure(client, response, CID, "music", timeout=1)) is complete
    client.generate_content.assert_not_awaited()


@pytest.mark.parametrize("limited_read", ["empty", "failure_card"])
def test_typed_http_429_stops_failure_proof_fallback_and_preserves_rate_limit(monkeypatch, limited_read):
    from src.infrastructure.rpc_parsers import RPCParseResult

    text = "I can't generate an audio track directly right now, but here is an original, synthetic score."
    empty = AsyncMock(return_value=RPCParseResult("rejected", reject_code=429) if limited_read == "empty"
                      else RPCParseResult("changed_shape"))
    failed = AsyncMock(return_value=RPCParseResult("rejected", reject_code=429))
    monkeypatch.setattr(native, "read_completed_empty_media_turn", empty)
    monkeypatch.setattr(native, "read_completed_music_failure_turn", failed)
    response = SimpleNamespace(metadata=[CID, RID], rcid=RCID, text=text, images=[], videos=[], media=[])
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(native._verified_native_failure(object(), response, CID, "music", timeout=1))
    assert error.value.code == "RATE_LIMITED" and error.value.submitted and error.value.chat_id == CID
    assert failed.await_count == (0 if limited_read == "empty" else 1)
    empty.assert_awaited_once()


def _presubmit_retry_driver(monkeypatch, results, *, elapsed=40.0, first_callback=False, drift=None):
    clock, calls = [100.0], []
    monkeypatch.setattr(native, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def browser(kind, prompt, model_label, cookies, *, timeout, on_chat, account_matches,
                      duration_seconds=None, aspect_ratio=None, thinking_level="standard",
                      music_duration=None, music_vocals=None):
        index = len(calls)
        assert index < len(results), "A pre-submit timeout must not start a third composer"
        calls.append({"kind": kind, "prompt": prompt, "model": model_label, "cookies": dict(cookies),
                      "timeout": timeout, "on_chat": on_chat, "duration_seconds": duration_seconds,
                      "aspect_ratio": aspect_ratio, "thinking_level": thinking_level,
                      "music_duration": music_duration, "music_vocals": music_vocals})
        clock[0] += elapsed
        if index == 0 and first_callback:
            await on_chat(CID)
        if index == 0 and drift is not None:
            drift()
        result = results[index]
        if result.chat_id:
            await on_chat(result.chat_id)
        return result

    monkeypatch.setattr(native, "run_native_media_browser", browser)
    return calls, clock


async def _generate_with_retry_budget(client, *, kind="video", timeout=240, observe=lambda _: None, **options):
    return await native.generate_native_media_response(client,
        arguments={"prompt": "  A synthetic blue paper cube or piano motif.\n", "model": "hydrogen",
                   "thinking_level": "low", "timeout": timeout}, media_type=kind, observe=observe, **options)


@pytest.mark.parametrize("kind", ["video", "music"])
def test_proven_unsubmitted_timeout_reopens_same_native_composer_once_with_remaining_budget_and_real_artifact(
    setup, monkeypatch, tmp_path, kind,
):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are needed for independent playable-media verification")
    client, _ = setup
    sample = tmp_path / ("sample.mp4" if kind == "video" else "sample.mp3")
    source = (["-f", "lavfi", "-i", "color=c=blue:s=32x32:d=0.2", "-pix_fmt", "yuv420p"] if kind == "video" else
              ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.2"])
    subprocess.run(["ffmpeg", "-v", "error", *source, str(sample)], check=True, timeout=15)
    initial = NativeMediaObservation(error_code="TIMED_OUT", submitted=False, stage="open_tools")
    calls, _ = _presubmit_retry_driver(monkeypatch, [initial, ready(kind, sample.read_bytes())])
    seen = []
    options = ({"duration_seconds": 15, "aspect_ratio": "9:16"} if kind == "video" else
               {"music_duration": "short", "music_vocals": "instrumental"})
    sdk_send = AsyncMock()
    monkeypatch.setattr(type(client), "generate_content", sdk_send)
    response = asyncio.run(_generate_with_retry_budget(client, kind=kind, observe=seen.append, **options))
    assert len(calls) == 2 and [call["timeout"] for call in calls] == [208.0, 168.0]
    assert {k: v for k, v in calls[0].items() if k != "timeout"} == {
        k: v for k, v in calls[1].items() if k != "timeout"
    }
    assert calls[1]["model"] == "4 Hydrogen" and calls[1]["thinking_level"] == "low"
    assert seen[0].metadata == [CID] and seen[1].metadata == [CID, RID]
    assert response.metadata == [CID, RID] and response.rcid == RCID
    saved = asyncio.run(_save_generated_media(response, media_type=kind, output_dir=str(tmp_path / "saved"),
        filename="native-retry", prompt="", requested_model="hydrogen", request_model="gemini-4-hydrogen",
        effective_backend="web", observed_backend=None, source_chat_id=CID))
    assert not saved.failures and len(saved.artifacts) == 1
    artifact = saved.artifacts[0]
    assert artifact.state == ArtifactState.LOCAL and Path(artifact.local_path).read_bytes() == sample.read_bytes()
    assert {"media_content_signature", "media_track_probe"} <= set(artifact.verification.methods)
    sdk_send.assert_not_awaited()


def test_two_presubmit_timeouts_never_open_a_third_composer(setup, monkeypatch):
    client, _ = setup
    timeout = NativeMediaObservation(error_code="TIMED_OUT", submitted=False, stage="model_selection")
    calls, _ = _presubmit_retry_driver(monkeypatch, [timeout, timeout])
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(_generate_with_retry_budget(client))
    assert error.value.code == "TIMED_OUT" and not error.value.submitted and error.value.chat_id is None
    assert len(calls) == 2


@pytest.mark.parametrize("error_code", [
    "UPSTREAM_REJECTED", "UPSTREAM_LIMIT_EXCEEDED", "RATE_LIMITED", "CAPABILITY_UNAVAILABLE", "AUTH_REQUIRED", "UPSTREAM_CHANGED",
])
def test_presubmit_non_timeout_errors_never_reopen_composer(setup, monkeypatch, error_code):
    client, _ = setup
    calls, _ = _presubmit_retry_driver(monkeypatch, [NativeMediaObservation(error_code=error_code, submitted=False)])
    with pytest.raises(native.NativeMediaGenerationError) as error:
        asyncio.run(_generate_with_retry_budget(client))
    assert error.value.code == error_code and len(calls) == 1


@pytest.mark.parametrize("change", ["submitted", "unknown_submission", "cid", "send_stage", "observe_stage", "rejected", "media"])
def test_timeout_with_any_submission_or_response_uncertainty_never_retries(setup, monkeypatch, change):
    client, _ = setup
    kwargs = ({"submitted": True} if change == "submitted" else {"submitted": None} if change == "unknown_submission" else
              {"chat_id": CID} if change == "cid" else {"stage": "send"} if change == "send_stage" else
              {"stage": "observe"} if change == "observe_stage" else {"state": "rejected"} if change == "rejected" else
              {"media": ({"kind": "video", "url": BLOB},)})
    result = NativeMediaObservation(error_code="TIMED_OUT", **kwargs)
    calls, _ = _presubmit_retry_driver(monkeypatch, [result])
    with pytest.raises(native.NativeMediaGenerationError):
        asyncio.run(_generate_with_retry_budget(client))
    assert len(calls) == 1


def test_first_attempt_callback_prevents_retry_even_when_driver_loses_result_identity(setup, monkeypatch):
    client, _ = setup
    result = NativeMediaObservation(error_code="TIMED_OUT", submitted=False, stage="open_tools")
    calls, _ = _presubmit_retry_driver(monkeypatch, [result], first_callback=True)
    seen = []
    with pytest.raises(native.NativeMediaGenerationError):
        asyncio.run(_generate_with_retry_budget(client, observe=seen.append))
    assert len(calls) == 1 and seen[0].metadata == [CID]
    assert seen[1].metadata == [CID, RID] and seen[1].rcid == RCID


@pytest.mark.parametrize("elapsed", [160.0, 250.0])
def test_presubmit_retry_does_not_reset_deadline_or_start_without_a_ui_action_budget(setup, monkeypatch, elapsed):
    client, _ = setup
    calls, _ = _presubmit_retry_driver(monkeypatch, [NativeMediaObservation(error_code="TIMED_OUT", submitted=False)],
                                       elapsed=elapsed)
    with pytest.raises(native.NativeMediaGenerationError):
        asyncio.run(_generate_with_retry_budget(client))
    assert len(calls) == 1


def test_presubmit_account_drift_never_reopens_composer(setup, monkeypatch):
    client, _ = setup
    calls, _ = _presubmit_retry_driver(monkeypatch, [NativeMediaObservation(error_code="TIMED_OUT", submitted=False)],
        drift=lambda: monkeypatch.setenv("GEMINI_PSID", "another-synthetic-account"))
    with pytest.raises(native.NativeMediaGenerationError):
        asyncio.run(_generate_with_retry_budget(client))
    assert len(calls) == 1


def test_unstructured_browser_timeout_cannot_prove_presubmit_and_is_not_retried(setup, monkeypatch):
    client, _ = setup
    failed = AsyncMock(side_effect=TimeoutError())

    async def browser(*args, thinking_level="standard", **kwargs):
        return await failed(*args, thinking_level=thinking_level, **kwargs)

    monkeypatch.setattr(native, "run_native_media_browser", browser)
    with pytest.raises(TimeoutError):
        asyncio.run(_generate_with_retry_budget(client))
    failed.assert_awaited_once()
