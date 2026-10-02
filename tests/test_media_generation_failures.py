"""Real SDK failed streams retain only this request's observed chat identity."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from gemini_webapi import GeminiClient
import gemini_webapi.client as upstream_client
from gemini_webapi.client import ChatSession
from gemini_webapi.exceptions import APIError
import gemini_webapi.utils.parsing as upstream_parsing

import src.skill_server as compact
import src.tools.media as primary
from src.services.media_generation import (
    MediaGenerationAttempt,
    MediaGenerationError,
    media_generation_kwargs,
)
from src.thinking_client import MediaRequestObservation, _OwnedMediaChatSession, _web_request
from tests.media_fixtures import write_image
from tests.test_media_generation_workflows import patch_compact, run_surface
from tests.test_media_tools import _FakeMediaClient, _patch_media_env
from tests.test_native_media_transport import SealedSession, sdk_client_at_http_boundary
from tests.test_thinking_client import _parse_inner


def response_frame(body):
    envelope = [["wrb.fr", "offline-stream", json.dumps(body)]]
    payload = json.dumps(envelope)
    # The actual SDK's framing parser counts both surrounding newlines.
    frame = f"{len(payload) + 2}\n{payload}\n"
    if hasattr(upstream_parsing, "parse_response_by_frame"):
        parts, remainder = upstream_parsing.parse_response_by_frame(frame)
        assert not remainder
    else:
        parts = upstream_parsing.StreamingFrameParser().feed(frame)
    assert parts == envelope
    return (")]}'\n" + frame).encode()


def metadata_frame(cid):
    return response_frame([None, [cid, "r_offline_fixture"]])


class FailedStreamResponse:
    status_code = 200

    def __init__(self, cid=None, *, pause=False, deadline=None):
        self.cid = cid
        self.pause = pause
        self.deadline = deadline

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_content(self):
        if self.cid:
            yield metadata_frame(self.cid)
        if self.deadline is not None:
            # The SDK has consumed the preceding frame before it asks for the
            # next chunk. This makes the total-deadline case deterministic.
            self.deadline.reschedule(asyncio.get_running_loop().time())
        if self.pause:
            await asyncio.Event().wait()


def failing_sdk_client(monkeypatch, calls, *, cid=None, pause=False, deadline=None):
    client = sdk_client_at_http_boundary(monkeypatch, [])

    async def read_chat(chat_id, *_args, **_kwargs):
        # SDK2.1.1 may recover an incomplete stream even without final context.
        # Seal that real read boundary to this allocated CID, never history.
        assert chat_id == cid
        raise APIError("The original request may have been silently aborted by Google.")

    def stream(method, url, **kwargs):
        calls.append(kwargs)
        return FailedStreamResponse(cid, pause=pause, deadline=deadline)

    client.client = SealedSession(stream)
    monkeypatch.setattr(client, "read_chat", read_chat)
    client._install_thinking_transport()
    return client


def test_request_owned_session_is_blank_and_does_not_mutate_sdk_shared_defaults(monkeypatch):
    sentinel = ["c_existing", "r_existing", "rc_existing", None, None, None, None, None, None, "old"]
    monkeypatch.setattr(upstream_client, "DEFAULT_METADATA", sentinel)
    client = sdk_client_at_http_boundary(monkeypatch, [])
    observation = MediaRequestObservation()
    chat = _OwnedMediaChatSession(client, observation)
    assert isinstance(chat, ChatSession)
    assert chat.metadata[:3] == ["", "", ""]
    backup = chat.metadata
    chat.metadata = ["c_new", "r_new", "rc_new"]
    assert backup[:3] == ["", "", ""]
    assert sentinel == ["c_existing", "r_existing", "rc_existing", None, None, None, None, None, None, "old"]
    chat.metadata = backup
    chat.cid = ""
    assert chat.cid == ""
    assert observation.chat_id == "c_new"


def test_chat_observer_persists_each_new_identity_before_sdk_rollback(monkeypatch):
    calls = []
    client = sdk_client_at_http_boundary(monkeypatch, [])
    observation = MediaRequestObservation(on_chat_observed=calls.append)
    chat = _OwnedMediaChatSession(client, observation)
    backup = chat.metadata
    chat.metadata = ["c_observed", "r_observed", "rc_observed"]
    assert calls == ["c_observed"]
    chat.metadata = ["c_observed", "r_observed", "rc_observed"]
    chat.metadata = backup
    assert calls == ["c_observed"]
    assert observation.chat_id == "c_observed"


@pytest.mark.parametrize("known_cid", [None, "c_offline_allocated"])
def test_real_sdk_api_abort_preserves_allocated_cid_across_metadata_rollback(monkeypatch, known_cid):
    monkeypatch.setattr(upstream_client, "DEFAULT_METADATA", [
        "c_existing", "r_existing", "rc_existing", None, None, None, None, None, None, "",
    ])
    calls = []
    client = failing_sdk_client(monkeypatch, calls, cid=known_cid)
    attempt = MediaGenerationAttempt()

    async def run():
        with pytest.raises(MediaGenerationError) as error:
            await attempt.generate(client, **media_generation_kwargs(
                "fixture", "music", model="gemini-3-flash", thinking_level="standard",
            ))
        assert error.value.code.value == "UPSTREAM_REJECTED"
        assert error.value.failure_kind == "request_interrupted"
        assert _web_request.get() is None
        with pytest.raises(ValueError, match="single generation"):
            await attempt.generate(client, prompt="cannot duplicate", media_mode="music")

    asyncio.run(run())
    assert len(calls) == 1
    assert _parse_inner(calls[0]["data"])[2][:3] == ["", "", ""]
    assert _parse_inner(calls[0]["data"])[49] == 21
    assert not {"media_mode", "media_observation", "current_retry"} & calls[0].keys()
    if known_cid:
        assert attempt.recovery_response.metadata == [known_cid]
    else:
        assert attempt.recovery_response is None


@pytest.mark.parametrize("surface", ["primary", "compact"])
@pytest.mark.parametrize("known_cid", [None, "c_offline_allocated"])
def test_music_api_abort_result_and_retention_match_both_real_surfaces(monkeypatch, tmp_path, surface, known_cid):
    monkeypatch.chdir(tmp_path)
    calls, cleanup = [], []
    client = failing_sdk_client(monkeypatch, calls, cid=known_cid)
    _patch_media_env(monkeypatch, client, captured_schedule=cleanup)
    patch_compact(monkeypatch, client, cleanup)
    result = run_surface(surface, "offline music fixture", media_type="music")[0].meta["domain_result"]

    assert result["ok"] is False
    assert result["error"]["code"] == "UPSTREAM_REJECTED"
    assert result["error"]["retryable"] is False
    assert "possible upstream interruption" in result["error"]["message"]
    assert "quota" not in result["error"]["message"].lower()
    assert result["meta"]["operation_state"] == "failed"
    assert result["meta"]["details"]["artifact_state"] == "failed"
    assert result["meta"]["details"]["upstream_failure_kind"] == "request_interrupted"
    assert result["meta"]["details"]["request_chat_observed"] is bool(known_cid)
    assert result["data"].get("source_chat_id") == known_cid
    assert len(calls) == 1
    if known_cid:
        assert len(cleanup) == 1
        assert cleanup[0]["owns_chat"] is True
        assert cleanup[0]["preserve_for_recovery"] is True
        assert result["meta"]["details"]["cleanup"]["state"] == "retained"
    else:
        assert cleanup == []
        assert "cleanup" not in result["meta"]["details"]


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_media_api_error_never_exposes_arbitrary_upstream_exception_text(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    private_value = "private-response-body-and-session-marker"
    client = _FakeMediaClient(raise_exc=APIError(private_value))
    cleanup = []
    _patch_media_env(monkeypatch, client, captured_schedule=cleanup)
    patch_compact(monkeypatch, client, cleanup)
    image_path = write_image(tmp_path / "source.png") if surface == "edit" else None
    content = run_surface(surface, "fixture", image_path=image_path)
    result = content[0].meta["domain_result"]

    assert result["error"]["code"] == "UPSTREAM_REJECTED"
    assert result["meta"]["details"]["upstream_failure_kind"] == "upstream_api_failed"
    assert private_value not in json.dumps(result)
    assert private_value not in "\n".join(item.text for item in content)
    assert cleanup == []


def test_sdk_parse_api_error_is_changed_contract_without_raw_exception(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = _FakeMediaClient(raise_exc=APIError(
        "Failed to parse response body from Google (ValueError). private fixture body",
    ))
    _patch_media_env(monkeypatch, client)
    content = run_surface("primary", "fixture")
    result = content[0].meta["domain_result"]
    assert result["error"]["code"] == "UPSTREAM_CHANGED"
    assert result["meta"]["details"]["upstream_failure_kind"] == "response_parse_failed"
    assert "private fixture body" not in json.dumps(result)


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_api_failure_before_generation_is_classified_safely_without_cleanup(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    client = _FakeMediaClient()
    cleanup = []
    _patch_media_env(monkeypatch, client, captured_schedule=cleanup)
    patch_compact(monkeypatch, client, cleanup)

    async def fail_initialization():
        raise APIError("private initialization response marker")

    module = primary if surface == "primary" else compact
    monkeypatch.setattr(module, "initialize_client", fail_initialization)
    image_path = write_image(tmp_path / "source.png") if surface == "edit" else None
    content = run_surface(surface, "fixture", image_path=image_path)
    result = content[0].meta["domain_result"]
    assert result["error"]["code"] == "UPSTREAM_REJECTED"
    assert "private initialization" not in json.dumps(result)
    assert "private initialization" not in "\n".join(item.text for item in content)
    assert client.captured_generate_kwargs is None
    assert cleanup == []


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_real_total_timeout_retains_metadata_observed_before_cancellation(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    calls, cleanup = [], []
    timeout_scope = asyncio.Timeout(None)
    client = failing_sdk_client(
        monkeypatch, calls, cid="c_offline_deadline", pause=True, deadline=timeout_scope,
    )
    _patch_media_env(monkeypatch, client, captured_schedule=cleanup)
    patch_compact(monkeypatch, client, cleanup)
    # Both surface adapters use the same total budget. Schedule its real
    # expiration only after the SDK has parsed the allocated CID frame.
    import src.services.creation as creation
    monkeypatch.setattr(creation.asyncio, "timeout", lambda _seconds: timeout_scope)
    image_path = write_image(tmp_path / "source.png") if surface == "edit" else None
    if image_path:
        async def uploaded_fixture(*_args, **_kwargs):
            return "https://offline.test/uploaded"
        monkeypatch.setattr(upstream_client, "upload_file", uploaded_fixture)

    result = run_surface(surface, "fixture", image_path=image_path)[0].meta["domain_result"]
    assert result["error"]["code"] == "TIMED_OUT"
    assert result["data"]["source_chat_id"] == "c_offline_deadline"
    assert result["meta"]["details"]["cleanup"]["state"] == "retained"
    assert cleanup[0]["preserve_for_recovery"] is True
    assert len(calls) == 1


def test_existing_chat_cannot_enter_an_owned_media_attempt_or_reach_http(monkeypatch):
    calls = []
    client = failing_sdk_client(monkeypatch, calls)
    existing = _OwnedMediaChatSession(client, MediaRequestObservation())
    existing.metadata = ["c_existing", "r_existing"]
    observation = MediaRequestObservation()

    async def run():
        with pytest.raises(ValueError, match="existing chat"):
            await client.generate_content("fixture", media_mode="image", chat=existing, media_observation=observation)
        with pytest.raises(ValueError, match="existing chat"):
            await MediaGenerationAttempt().generate(client, prompt="fixture", chat=existing)

    asyncio.run(run())
    assert calls == []
    assert observation.chat_id is None
    assert existing.cid == "c_existing"
    assert _web_request.get() is None


def test_concurrent_owned_generations_and_success_metadata_stay_independent(monkeypatch):
    client = sdk_client_at_http_boundary(monkeypatch, [])

    async def run():
        started = {name: asyncio.Event() for name in ("one", "two")}

        async def generate(_client, prompt, *, chat, **kwargs):
            assert "media_observation" not in kwargs
            assert chat.metadata[:3] == ["", "", ""]
            chat.metadata = [f"c_{prompt}", f"r_{prompt}"]
            started[prompt].set()
            await started["two" if prompt == "one" else "one"].wait()
            assert chat.cid == f"c_{prompt}"
            return SimpleNamespace(metadata=chat.metadata, text="fixture")

        monkeypatch.setattr(GeminiClient, "generate_content", generate)
        attempts = [MediaGenerationAttempt(), MediaGenerationAttempt()]
        results = await asyncio.gather(*(
            attempt.generate(client, prompt=name, media_mode="image")
            for attempt, name in zip(attempts, ("one", "two"))
        ))
        assert [result.metadata[0] for result in results] == ["c_one", "c_two"]
        assert [attempt.recovery_response.metadata[0] for attempt in attempts] == ["c_one", "c_two"]
        assert _web_request.get() is None

    asyncio.run(run())


def test_real_sdk_success_preserves_returned_metadata_and_candidate(monkeypatch):
    calls = []
    client = sdk_client_at_http_boundary(monkeypatch, [])
    attempt = MediaGenerationAttempt()

    class CompletedStreamResponse(FailedStreamResponse):
        async def aiter_content(self):
            candidate = [None] * 9
            candidate[0], candidate[1], candidate[8] = "rc_complete", ["fixture complete"], [2]
            body = [None] * 26
            body[1], body[4], body[25] = ["c_complete", "r_complete"], [candidate], "saved-context"
            yield response_frame(body)

    def stream(_method, _url, **kwargs):
        calls.append(kwargs)
        return CompletedStreamResponse()

    client.client = SealedSession(stream)
    client._install_thinking_transport()
    result = asyncio.run(attempt.generate(
        client, prompt="fixture", model="gemini-3-flash", media_mode="image",
    ))
    assert result.metadata[:3] == ["c_complete", "r_complete", "rc_complete"]
    assert result.metadata[9] == "saved-context"
    assert result.text == "fixture complete"
    assert attempt.recovery_response.metadata == ["c_complete"]
    assert len(calls) == 1


def test_real_sdk_stream_observation_is_consumed_and_survives_exception(monkeypatch):
    calls = []
    client = failing_sdk_client(monkeypatch, calls, cid="c_stream_failure")
    observation = MediaRequestObservation()

    async def run():
        with pytest.raises(APIError):
            async for _ in client.generate_content_stream(
                "fixture", model="gemini-3-flash", media_mode="music", media_observation=observation,
            ):
                pass
        assert _web_request.get() is None

    asyncio.run(run())
    assert observation.chat_id == "c_stream_failure"
    assert len(calls) == 1
    assert "media_observation" not in calls[0]
