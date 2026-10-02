"""Real upstream save behavior through offline HTTP/decoder seams."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import gemini_webapi.types.video as upstream_video

import src.tools.media as media_tools
from tests.media_fixtures import write_audio, write_video
from tests.test_media_tools import _FakeMediaClient, _make_mcp, _patch_media_env, _call_tool


def _upstream_media():
    return upstream_video.GeneratedMedia(title="song", url="https://cdn.test/song.mp4", mp3_url="https://cdn.test/song.mp3")


@pytest.mark.parametrize("filename", ["song", "song.mp3", "song.mp4"])
def test_real_generated_media_saves_audio_and_video_to_distinct_paths(monkeypatch, tmp_path, filename):
    source_audio = write_audio(tmp_path / "source.wav").read_bytes()
    source_video = write_video(tmp_path / "source.avi").read_bytes()
    sessions = []

    class Session:
        def __init__(self, **_kwargs):
            self.closed = False
            sessions.append(self)

        async def get(self, url, **_kwargs):
            return SimpleNamespace(status_code=200, content=source_audio if url.endswith("mp3") else source_video,
                                   headers={"content-type": "audio/wav" if url.endswith("mp3") else "video/x-msvideo"})

        async def close(self):
            self.closed = True

    monkeypatch.setattr(upstream_video, "AsyncSession", Session)
    # Existing files must survive both independently named downloads.
    (tmp_path / "song.mp3").write_bytes(b"existing audio")
    (tmp_path / "song.mp4").write_bytes(b"existing video")
    client = _FakeMediaClient(media=[_upstream_media()])
    _patch_media_env(monkeypatch, client)

    async def run():
        return await _call_tool(_make_mcp(), "gemini_generate_music", prompt="song", output_dir=str(tmp_path), filename=filename)

    result = asyncio.run(run())[0].meta["domain_result"]
    artifacts = {artifact["kind"]: artifact for artifact in result["data"]["artifacts"]}
    audio, video = Path(artifacts["audio"]["local_path"]), Path(artifacts["video"]["local_path"])
    assert audio == tmp_path / "song_2.mp3"
    assert video == tmp_path / "song_2.mp4"
    assert audio.read_bytes() == source_audio
    assert video.read_bytes() == source_video
    assert (tmp_path / "song.mp3").read_bytes() == b"existing audio"
    assert (tmp_path / "song.mp4").read_bytes() == b"existing video"
    assert artifacts["audio"]["verification"]["status"] == "verified"
    assert all(session.closed for session in sessions)


@pytest.mark.parametrize("pending_type", ["audio", "video"])
def test_deadline_bounds_real_upstream_206_polling_and_preserves_remote_uris(monkeypatch, tmp_path, pending_type):
    sessions = []
    source_audio = write_audio(tmp_path / "source.wav").read_bytes()

    class PendingSession:
        def __init__(self, **_kwargs):
            self.closed = False
            self.requests = 0
            sessions.append(self)

        async def get(self, url, **_kwargs):
            self.requests += 1
            if pending_type == "video" and url.endswith("mp3"):
                return SimpleNamespace(status_code=200, content=source_audio, headers={"content-type": "audio/wav"})
            return SimpleNamespace(status_code=206)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(upstream_video, "AsyncSession", PendingSession)
    monkeypatch.setattr(media_tools, "_media_timeout", lambda *_args: 0.04)
    scheduled = []
    client = _FakeMediaClient(media=[_upstream_media()])
    _patch_media_env(monkeypatch, client, captured_schedule=scheduled)

    async def run():
        loop = asyncio.get_running_loop()
        start = loop.time()
        content = await _call_tool(_make_mcp(), "gemini_generate_music", prompt="song", output_dir=str(tmp_path))
        return content, loop.time() - start

    content, elapsed = asyncio.run(run())
    domain = content[0].meta["domain_result"]
    assert elapsed < 0.3
    assert domain["error"]["code"] == "TIMED_OUT"
    assert domain["meta"]["operation_state"] == "timed_out"
    assert domain["data"]["source_chat_id"] == "c_media1"
    assert {artifact["uri"] for artifact in domain["data"]["artifacts"]} == {"https://cdn.test/song.mp3", "https://cdn.test/song.mp4"}
    assert scheduled[0]["preserve_for_recovery"] is True
    assert all(session.closed and session.requests == 1 for session in sessions)
    if pending_type == "video":
        audio = next(artifact for artifact in domain["data"]["artifacts"] if artifact["kind"] == "audio")
        assert audio["state"] == "local"
        assert audio["verification"]["status"] == "verified"
        assert Path(audio["local_path"]).read_bytes() == source_audio


def test_generation_and_save_share_one_deadline(monkeypatch):
    cancelled = []

    class SlowImage:
        url = "https://cdn.test/result.png"
        title = alt = "result"

        async def save(self, **_kwargs):
            try:
                await asyncio.sleep(0.04)
            finally:
                cancelled.append(True)

    class SlowGeneration(_FakeMediaClient):
        async def generate_content(self, **kwargs):
            await asyncio.sleep(0.025)
            return await super().generate_content(**kwargs)

    monkeypatch.setattr(media_tools, "_media_timeout", lambda *_args: 0.05)
    scheduled = []
    client = SlowGeneration(images=[SlowImage()])
    _patch_media_env(monkeypatch, client, captured_schedule=scheduled)

    async def run():
        return await _call_tool(_make_mcp(), "gemini_generate_media", prompt="cat", media_type="image")

    domain = asyncio.run(run())[0].meta["domain_result"]
    assert domain["error"]["code"] == "TIMED_OUT"
    assert domain["data"]["artifacts"][0]["uri"] == SlowImage.url
    assert scheduled[0]["preserve_for_recovery"] is True
    assert cancelled == [True]


def test_music_chat_recovery_uses_the_same_deadline(monkeypatch):
    cancelled = []

    async def slow_recovery(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(media_tools, "_media_timeout", lambda *_args: 0.04)
    scheduled = []
    client = _FakeMediaClient(media=[])
    _patch_media_env(monkeypatch, client, captured_schedule=scheduled, fetch_music=slow_recovery)

    async def run():
        return await _call_tool(_make_mcp(), "gemini_generate_music", prompt="song")

    domain = asyncio.run(run())[0].meta["domain_result"]
    assert domain["error"]["code"] == "TIMED_OUT"
    assert domain["data"]["source_chat_id"] == "c_media1"
    assert scheduled[0]["preserve_for_recovery"] is True
    assert cancelled == [True]


def test_recovered_music_uri_survives_a_download_deadline(monkeypatch):
    class RecoveredMedia:
        title = "recovered song"
        mp3_url = "https://cdn.test/recovered.mp3"
        url = ""

        async def save(self, **_kwargs):
            await asyncio.Event().wait()

    async def recovery(*_args):
        return [RecoveredMedia()]

    monkeypatch.setattr(media_tools, "_media_timeout", lambda *_args: 0.04)
    client = _FakeMediaClient(media=[])
    _patch_media_env(monkeypatch, client, fetch_music=recovery)

    async def run():
        return await _call_tool(_make_mcp(), "gemini_generate_music", prompt="song")

    domain = asyncio.run(run())[0].meta["domain_result"]
    assert domain["error"]["code"] == "TIMED_OUT"
    artifact = domain["data"]["artifacts"][0]
    assert artifact["uri"] == RecoveredMedia.mp3_url
    assert artifact["source_chat_id"] == "c_media1"
