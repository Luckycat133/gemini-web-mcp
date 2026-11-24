"""Synchronous native tools wait on an owned, resumable operation."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock
import wave

import pytest
from mcp import Client

from src import skill_server
from src.adapters import MCPServer
from src.domain import Artifact, ArtifactKind, ArtifactState
from src.infrastructure.operation_store import OperationStore
from src.onboarding import domain_result_from_call
from src.services import background
from src.tools import media
from src.tools.operations import register_operation_tools

_CID = "c_0123456789abcdef"
_RID = "r_review_inline"
_RCID = "rc_review_inline"


class _NativeClient:
    def _resolve_policy_model(self, value):
        raise AssertionError("The inline adapter should not select a model itself.")


def _save_audio(store, operation_id, token, request):
    directory = Path(request["output_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{request['filename']}.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0" * 1600)
    artifact = Artifact(id="artifact_inline", kind=ArtifactKind.AUDIO, state=ArtifactState.LOCAL,
                        local_path=str(path), mime_type="audio/wav")
    assert store.save_media_artifacts(operation_id, (artifact,), worker_token=token, artifact_state="local")
    assert store.release_media_cleanup(operation_id, worker_token=token)
    store.update_cleanup(operation_id, "retained" if request["retain_chat"] else "completed")
    store.retire_worker(operation_id, token)


@pytest.fixture(params=["primary", "compact"])
def surface(request, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-native-inline")
    monkeypatch.setenv("GEMINI_WEB_MEDIA_TRANSPORT", "native")
    if request.param == "primary":
        adapter = media
        server = MCPServer("protected native media")
        media.register_media_tools(server)
        register_operation_tools(server)
        create = "gemini_generate_music", {"timeout_seconds": 1}
        read = "gemini_get_operation_result", lambda handle: {"operation_id": handle}
    else:
        adapter = skill_server
        server = skill_server.mcp
        monkeypatch.setattr(skill_server, "media_timeout", lambda *_: 1)
        create = "create", {"type": "music"}
        read = "session", lambda handle: {"action": "operation_result", "session_id": handle}
    monkeypatch.setattr(adapter, "get_gemini_client", _NativeClient)
    for method in ("initialize_client", "cleanup_due_remote_chats", "generate_media_response",
                   "finalize_remote_chat_cleanup_from_response"):
        monkeypatch.setattr(adapter, method, AsyncMock(side_effect=AssertionError(f"Unprotected inline call to {method}")))
    monkeypatch.setattr(adapter, "schedule_remote_chat_cleanup_from_response",
                        Mock(side_effect=AssertionError("Unprotected inline cleanup registration")))
    return server, create, read


@pytest.mark.parametrize("outcome", ["ready", "unknown", "save_failed"])
def test_native_inline_uses_one_protected_generation_and_recovers_the_same_handle(surface, outcome, monkeypatch, tmp_path):
    server, (tool, options), (read_tool, read_args) = surface
    monkeypatch.chdir(tmp_path)
    launches = []

    def launch(payload, store):
        operation_id, token = payload["operation_id"], payload["worker_token"]
        record = store.get(operation_id)
        assert record.cleanup_protected
        if payload["kind"] == "media_resume":
            assert record.error_code == "ARTIFACT_SAVE_FAILED"
            store.retire_worker(operation_id, token)
            return 234
        assert payload["kind"] == "media"
        launches.append(payload)
        assert store.checkpoint_media(operation_id, worker_token=token, chat_id=_CID, response_id=_RID, candidate_id=_RCID,
                                      native_submission=True, selected_model="gemini-neon")
        if outcome == "ready":
            _save_audio(store, operation_id, token, payload["request"])
        if outcome == "save_failed":
            assert store.finish_worker(operation_id, worker_token=token, state="partial", phase="media_artifact_not_observed",
                                       verification_status="not_observed", error_code="ARTIFACT_SAVE_FAILED")
            store.retire_worker(operation_id, token)
        return 123

    monkeypatch.setattr(background, "_launch_detached_worker", launch)

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            first = await client.call_tool(tool, {"prompt": "Synthetic piano", "model": "neon", "background": False,
                                                 "retain_chat": outcome == "ready", "music_duration": "short",
                                                 "music_vocals": "instrumental",
                                                 **({"filename": "piano_fixture"} if outcome == "ready" else {}), **options})
            result = domain_result_from_call(first)
            assert result["ok"]
            handle = result["data"]["operation_id"]
            assert handle in first.content[0].text
            operation, = launches
            assert operation["operation_id"] == handle
            request = operation["request"]
            assert request["music_duration"] == "short" and request["music_vocals"] == "instrumental"
            assert request["web_media_transport"] == "native"
            assert request["filename"] == ("piano_fixture" if outcome == "ready" else handle)
            assert Path(request["output_dir"]) == tmp_path / "generated_media"
            store = OperationStore()

            if outcome == "ready":
                assert result["data"]["state"] == "local"
                assert result["data"]["artifacts"][0]["mime_type"] in {"audio/wav", "audio/x-wav"}
            else:
                assert result["data"]["state"] == "partial"
                assert result["data"]["continuation_possible"]
                assert not result["data"]["artifacts"]
                assert store.get(handle).cleanup_protected
                if outcome == "save_failed":
                    assert result["data"]["error_code"] == "ARTIFACT_SAVE_FAILED"
                    return
                assert result["meta"]["verification_status"] == "wait_elapsed"
                _save_audio(store, handle, operation["worker_token"], request)

            recovered = domain_result_from_call(await client.call_tool(read_tool, read_args(handle)))
            assert recovered["data"]["operation_id"] == handle
            path = Path(recovered["data"]["artifacts"][0]["local_path"])
            with wave.open(str(path), "rb") as stream:
                assert stream.getnframes() == 800

    asyncio.run(run())
    assert len(launches) == 1


def test_explicit_sdk_transport_keeps_the_existing_synchronous_route(surface, monkeypatch):
    server, (tool, options), _ = surface
    adapter = media if tool == "gemini_generate_music" else skill_server
    monkeypatch.setenv("GEMINI_WEB_MEDIA_TRANSPORT", "sdk")
    monkeypatch.setattr(adapter, "initialize_client", AsyncMock())
    monkeypatch.setattr(adapter, "cleanup_due_remote_chats", AsyncMock())
    generate = AsyncMock(side_effect=TimeoutError)
    submit = AsyncMock()
    monkeypatch.setattr(adapter, "generate_media_response", generate)
    monkeypatch.setattr(background, "submit_media", submit)

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            return await client.call_tool(tool, {"prompt": "Synthetic piano", "background": False, **options})

    result = domain_result_from_call(asyncio.run(run()))
    assert result["error"]["code"] == "TIMED_OUT"
    generate.assert_awaited_once()
    submit.assert_not_awaited()
