"""Streaming media recovery keeps identity and cleanup boundaries explicit."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from gemini_webapi.types import Candidate, ModelOutput
from gemini_webapi.types.image import GeneratedImage
from gemini_webapi.types.video import GeneratedMedia, GeneratedVideo

from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.artifacts import response_image_items
from src.services.media_generation import (
    MediaGenerationProgress, VideoModeUnavailable, compose_media_prompt, generate_media_response, media_timeout_scope,
)
from src.client_wrapper import ScheduledCleanupChatId
from src.domain import CleanupObservation, CleanupState
from src.adapters.mcp_sdk import MCPServer
import src.skill_server as skill_server
import src.tools.media as media_tools


def _output(cid, rid, rcid, *, text="", image=None, media=None, video=None):
    return ModelOutput(metadata=[cid, rid], candidates=[Candidate(
        rcid=rcid, text=text, generated_images=[image] if image else [], generated_media=[media] if media else [],
        generated_videos=[video] if video else [],
    )])


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_stream_interruption_keeps_first_observed_cid_durable(tmp_path, failure):
    db = tmp_path / "cleanup.sqlite"
    manager = RemoteChatCleanupManager(store_path=db, account_key_provider=lambda: "test-account")

    class Client:
        async def generate_content_stream(self, **_kwargs):
            yield _output("c_live", "r_one", "rc_one", text="working")
            raise failure()

    async def run():
        await generate_media_response(
            Client(), arguments={}, schedule_cleanup=manager.schedule_cleanup_from_response,
            retain_chat=False, delete_after_seconds=None, source="test_music",
        )

    with pytest.raises(failure):
        asyncio.run(run())
    restarted = RemoteChatCleanupManager(store_path=db, account_key_provider=lambda: "test-account")
    pending = restarted.list_pending_cleanup()
    assert "c_live" in pending
    assert pending["c_live"].source == "test_music:in_progress"


def test_stream_merges_only_matching_turn_and_selected_candidate():
    image = GeneratedImage(url="https://example.test/generated.png")
    music = GeneratedMedia(mp3_url="https://example.test/song.mp3", url="")
    old_image = GeneratedImage(url="https://example.test/old.png")
    other_song = GeneratedMedia(mp3_url="https://example.test/other.mp3", url="")
    chunks = [
        _output("c_live", "r_old", "rc_one", image=old_image),
        _output("c_live", "r_new", "rc_other", media=other_song),
        _output("c_live", "r_new", "rc_current", image=image, media=music),
        _output("c_live", "r_new", "rc_current", text="complete"),
    ]

    class Client:
        async def generate_content_stream(self, **_kwargs):
            for item in chunks:
                yield item

    scheduled = []
    def schedule(response, **kwargs):
        scheduled.append((response, kwargs))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_live")

    result = asyncio.run(generate_media_response(
        Client(), arguments={}, schedule_cleanup=schedule,
        retain_chat=False, delete_after_seconds=None, source="test",
    ))
    assert result.text == "complete"
    assert [item.mp3_url for item in result.media] == [music.mp3_url]
    assert [item.url for item in response_image_items(result, generated_only=True)] == [image.url]
    assert len(scheduled) == 1
    assert scheduled[0][1]["delete_after_seconds"] == 1800


def test_sdk_video_in_early_frame_is_not_lost_or_mixed_with_other_candidates():
    current = GeneratedVideo(url="https://example.test/current.mp4")
    old = GeneratedVideo(url="https://example.test/old.mp4")
    other = GeneratedVideo(url="https://example.test/other.mp4")
    chunks = [
        _output("c_live", "r_old", "rc_one", video=old),
        _output("c_live", "r_new", "rc_other", video=other),
        _output("c_live", "r_new", "rc_current", video=current),
        _output("c_live", "r_new", "rc_current", text="complete"),
    ]

    class Client:
        async def generate_content_stream(self, **_kwargs):
            for item in chunks:
                yield item

    result = asyncio.run(generate_media_response(
        Client(), arguments={}, schedule_cleanup=lambda *_args, **_kwargs: None,
        retain_chat=False, delete_after_seconds=None, source="test",
    ))
    assert [item.url for item in result.videos] == [current.url]
    assert [item.url for item in result.candidates[result.chosen].generated_videos] == [current.url]
    assert result.text == "complete"


def test_fallback_client_keeps_original_retention_and_response():
    response = SimpleNamespace(metadata=["c_one", "r_one"], text="done", images=[], media=[])

    class Client:
        async def generate_content(self, **_kwargs):
            return response

    scheduled = []
    result = asyncio.run(generate_media_response(
        Client(), arguments={}, schedule_cleanup=lambda _response, **kwargs: scheduled.append(kwargs),
        retain_chat=True, delete_after_seconds=None, source="test",
    ))
    assert result.text == "done"
    assert scheduled == [{"retain_chat": True, "delete_after_seconds": None, "source": "test:in_progress"}]


def test_video_scope_covers_stream_iteration_and_restores_state():
    class Client:
        active = False

        @contextmanager
        def video_generation_scope(self):
            self.active = True
            try:
                yield
            finally:
                self.active = False

        async def generate_content_stream(self, **_kwargs):
            assert self.active
            yield _output("c_video", "r_one", "rc_one", text="done")
            assert self.active

    client = Client()
    result = asyncio.run(generate_media_response(
        client, arguments={}, schedule_cleanup=lambda *_args, **_kwargs: None, retain_chat=False,
        delete_after_seconds=None, source="test", media_type="video",
    ))
    assert result.text == "done"
    assert client.active is False


def test_video_without_dedicated_mode_does_not_fall_back_to_chat():
    class Client:
        async def generate_content(self, **_kwargs):
            raise AssertionError("must not call generic chat")

    with pytest.raises(VideoModeUnavailable):
        asyncio.run(generate_media_response(
            Client(), arguments={}, schedule_cleanup=lambda *_args, **_kwargs: None, retain_chat=False,
            delete_after_seconds=None, source="test", media_type="video",
        ))


@pytest.mark.parametrize("kind", ["image", "music", "video"])
def test_media_prompt_composition_is_idempotent(kind):
    prompt = compose_media_prompt(kind, "a blue ball")
    assert compose_media_prompt(kind, prompt) == prompt


@pytest.mark.parametrize("cancel_first", [False, True])
def test_overlapping_main_and_compact_timeout_leases_do_not_restore_early(monkeypatch, cancel_first):
    class Client:
        timeout = 30.0
        watchdog_timeout = 30.0

        def __init__(self):
            self.entered = [asyncio.Event(), asyncio.Event()]
            self.release = [asyncio.Event(), asyncio.Event()]
            self.calls = 0

        async def generate_content_stream(self, **_kwargs):
            index = self.calls
            self.calls += 1
            self.entered[index].set()
            await self.release[index].wait()
            yield SimpleNamespace(metadata=[], text="done", images=[], videos=[], media=[])

    client = Client()

    async def noop(*_args, **_kwargs):
        return None

    for module in (media_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", noop)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", noop)
        monkeypatch.setattr(module, "finalize_remote_chat_cleanup_from_response", noop)
    server = MCPServer("timeout-lease-overlap")
    media_tools.register_media_tools(server)

    async def run():
        first = asyncio.create_task(server.call_tool("gemini_generate_music", {"background": False, "prompt": "a"}))
        await client.entered[0].wait()
        second = asyncio.create_task(skill_server.create(background=False, prompt="b", type="music"))
        await client.entered[1].wait()
        assert (client.timeout, client.watchdog_timeout) == (600.0, 600.0)
        if cancel_first:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            client.release[0].set()
            await first
        # A non-media observer and the remaining request still see a valid lease.
        assert (client.timeout, client.watchdog_timeout) == (600.0, 600.0)
        client.release[1].set()
        await second
        assert (client.timeout, client.watchdog_timeout) == (30.0, 30.0)

    asyncio.run(run())


def test_media_timeout_lease_never_shortens_existing_nonmedia_watchdog():
    client = SimpleNamespace(timeout=400.0, watchdog_timeout=400.0)
    with media_timeout_scope(client, 180):
        assert (client.timeout, client.watchdog_timeout) == (400.0, 400.0)
    assert (client.timeout, client.watchdog_timeout) == (400.0, 400.0)


def test_media_timeout_lease_preserves_explicit_external_change():
    client = SimpleNamespace(timeout=30.0, watchdog_timeout=30.0)
    with media_timeout_scope(client, 600):
        client.timeout = 45.0
        client.watchdog_timeout = 45.0
    assert (client.timeout, client.watchdog_timeout) == (45.0, 45.0)


def test_media_timeout_leases_recompute_remaining_request_on_out_of_order_exit():
    client = SimpleNamespace(timeout=30.0, watchdog_timeout=30.0)
    long_request = media_timeout_scope(client, 600)
    short_request = media_timeout_scope(client, 180)
    long_request.__enter__()
    short_request.__enter__()
    assert (client.timeout, client.watchdog_timeout) == (600.0, 600.0)
    long_request.__exit__(None, None, None)
    assert (client.timeout, client.watchdog_timeout) == (180.0, 180.0)
    short_request.__exit__(None, None, None)
    assert (client.timeout, client.watchdog_timeout) == (30.0, 30.0)


def test_failed_cleanup_registration_retries_on_next_frame():
    progress = MediaGenerationProgress()
    calls = []
    response = _output("c_live", "r_one", "rc_one", text="working")

    class Client:
        async def generate_content_stream(self, **_kwargs):
            yield response
            yield response

    def schedule(_response, **_kwargs):
        calls.append(1)
        state = CleanupState.FAILED if len(calls) == 1 else CleanupState.PENDING
        return ScheduledCleanupChatId("c_live", CleanupObservation(state=state, upstream_chat_id="c_live"))

    asyncio.run(generate_media_response(
        Client(), arguments={}, schedule_cleanup=schedule, retain_chat=False,
        delete_after_seconds=None, source="test", progress=progress,
    ))
    assert len(calls) == 2
    assert progress.last_chat_id == "c_live"
    assert not progress.registration_failures


def test_cleanup_storage_exception_does_not_mask_stream_cancellation():
    progress = MediaGenerationProgress()

    class Client:
        async def generate_content_stream(self, **_kwargs):
            yield _output("c_live", "r_one", "rc_one")
            raise asyncio.CancelledError()

    def schedule(*_args, **_kwargs):
        raise OSError("storage unavailable")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(generate_media_response(
            Client(), arguments={}, schedule_cleanup=schedule, retain_chat=False,
            delete_after_seconds=None, source="test", progress=progress,
        ))
    assert progress.last_chat_id == "c_live"
    assert progress.registration_failures["c_live"].state is CleanupState.FAILED


@pytest.mark.parametrize("surface", ["primary", "compact"])
def test_media_timeout_reports_cid_and_failed_cleanup_without_masking_timeout(monkeypatch, surface):
    class Client:
        timeout = 30.0
        watchdog_timeout = 30.0

        async def generate_content_stream(self, **_kwargs):
            yield _output("c_live", "r_one", "rc_one", text="working")
            raise asyncio.TimeoutError()

    client = Client()
    module = media_tools if surface == "primary" else skill_server
    monkeypatch.setattr(module, "get_gemini_client", lambda: client)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module, "initialize_client", noop)
    monkeypatch.setattr(module, "cleanup_due_remote_chats", noop)
    monkeypatch.setattr(module, "schedule_remote_chat_cleanup_from_response", lambda *_a, **_kw:
        ScheduledCleanupChatId("c_live", CleanupObservation(state=CleanupState.FAILED, upstream_chat_id="c_live")))

    async def run():
        if surface == "compact":
            return await skill_server.create(background=False, prompt="music", type="music")
        server = MCPServer("media-timeout-cleanup-failure")
        media_tools.register_media_tools(server)
        return (await server.call_tool("gemini_generate_music", {"background": False, "prompt": "music"})).content

    result = asyncio.run(run())[0].meta["domain_result"]
    assert result["error"]["code"] == "TIMED_OUT"
    assert result["data"]["source_chat_id"] == "c_live"
    assert result["meta"]["details"]["cleanup_registration_failures"][0]["state"] == "failed"
    assert result["warnings"][0]["code"] == "cleanup_registration_unverified"


@pytest.mark.parametrize("surface", ["primary", "compact"])
def test_both_media_surfaces_record_cid_before_cancel(monkeypatch, tmp_path, surface):
    db = tmp_path / f"{surface}.sqlite"
    manager = RemoteChatCleanupManager(store_path=db, account_key_provider=lambda: "test-account")

    class Client:
        timeout = 30.0
        watchdog_timeout = 120.0

        async def generate_content_stream(self, **_kwargs):
            yield _output("c_running", "r_one", "rc_one", text="working")
            raise asyncio.CancelledError()

    client = Client()
    module = media_tools if surface == "primary" else skill_server
    monkeypatch.setattr(module, "get_gemini_client", lambda: client)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module, "initialize_client", noop)
    monkeypatch.setattr(module, "cleanup_due_remote_chats", noop)
    monkeypatch.setattr(module, "schedule_remote_chat_cleanup_from_response", manager.schedule_cleanup_from_response)

    async def run():
        if surface == "compact":
            return await skill_server.create(background=False, prompt="music", type="music")
        server = MCPServer("media-stream-cancel")
        media_tools.register_media_tools(server)
        return await server.call_tool("gemini_generate_music", {"background": False, "prompt": "music"})

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    restarted = RemoteChatCleanupManager(store_path=db, account_key_provider=lambda: "test-account")
    assert "c_running" in restarted.list_pending_cleanup()


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("retain", [False, True])
def test_sdk_metadata_before_first_media_output_is_checkpointed_once(monkeypatch, failure, retain):
    """Exercise installed SDK framing with synthetic bytes; no network is used."""
    import json
    from gemini_webapi import GeminiClient
    from src.thinking_client import ThinkingLevelGeminiClient

    inner = json.dumps([None, ["c_early_media", "r_early_media"]], separators=(",", ":"))
    body = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(body) + 1) + "\n" + body).encode()

    class Response:
        status_code = 200
        http_version = 2
        async def aiter_content(self):
            yield wire
            raise failure()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False

    class Transport:
        requests = 0
        def stream(self, *args, **kwargs):
            self.requests += 1
            return Response()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(GeminiClient, "_sync_activity", noop)
    monkeypatch.setattr(GeminiClient, "close", noop)
    client = ThinkingLevelGeminiClient()
    from gemini_webapi.types import AvailableModel
    client._model_registry = {"flash": AvailableModel(model_id="flash", model_name="gemini-flash", display_name="Flash", description="", capacity=1)}
    client.client = transport = Transport()
    client._running = True
    events = []
    def checkpoint(response):
        events.append(("checkpoint", response.cid, response.rid))
    def schedule(response, **kwargs):
        events.append(("schedule", response.cid, kwargs))
        return CleanupObservation(state=CleanupState.RETAINED if retain else CleanupState.PENDING,
                                  upstream_chat_id=response.cid)
    progress = MediaGenerationProgress()
    with pytest.raises(BaseException):
        asyncio.run(generate_media_response(client, arguments={"prompt": "synthetic"}, schedule_cleanup=schedule,
                                            retain_chat=retain, delete_after_seconds=None, source="test",
                                            media_type="image", progress=progress, checkpoint=checkpoint))
    assert transport.requests == 1
    assert events[0] == ("checkpoint", "c_early_media", "r_early_media")
    assert events[1][0:2] == ("schedule", "c_early_media")
    assert events[1][2]["retain_chat"] is retain
    assert progress.last_chat_id == "c_early_media"
