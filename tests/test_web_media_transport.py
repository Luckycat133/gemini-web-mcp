"""Current official Web composer modes reach the real SDK request builder."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from gemini_webapi import GeminiClient
import pytest

from src.infrastructure.rpc_contracts import (
    WebWorkflowTransportChanged, apply_image_generation_transport, apply_music_generation_transport,
)
from src.services.model_policy import model_policy_scope
from src.thinking_client import WebRequestOptions, _web_request, inject_web_request_options
from tests.test_web_model_selection import discovered_client


@pytest.mark.parametrize(("kind", "mode"), [("image", 14), ("music", 21)])
def test_media_composer_scope_emits_current_mode_and_exact_registry_selector(monkeypatch, kind, mode):
    client = discovered_client()
    captured = []

    class Captured(BaseException):
        pass

    class Transport:
        def stream(self, *_args, **kwargs):
            envelope = json.loads(kwargs["data"]["f.req"])
            captured.append(json.loads(envelope[1]))
            raise Captured()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    client.client = Transport()
    client._install_thinking_transport()

    async def run():
        with getattr(client, f"{kind}_generation_scope")(), model_policy_scope("media"):
            async for _ in client.generate_content_stream("synthetic media request", model="hydrogen"):
                pass

    with pytest.raises(Captured):
        asyncio.run(run())
    assert len(captured) == 1
    assert captured[0][49] == mode
    assert captured[0][79] == client._model_registry["56fdd199312815e2"].model_number
    assert _web_request.get() is None
    proof = json.loads((Path(__file__).parent / "fixtures/web_media_composer.json").read_text())
    assert proof["feature_modes"][kind] == mode


@pytest.mark.parametrize("bad", [None, [], [None] * 50, [["prompt"]] + [None] * 49])
@pytest.mark.parametrize("apply", [apply_image_generation_transport, apply_music_generation_transport])
def test_changed_media_request_shape_refuses_before_dispatch(apply, bad):
    with pytest.raises(WebWorkflowTransportChanged):
        apply(bad)


def test_music_does_not_overwrite_another_composer_mode():
    query = [None] * 81
    query[0] = [None] * 7
    query[49] = 14
    with pytest.raises(WebWorkflowTransportChanged):
        apply_music_generation_transport(query)
    assert query[49] == 14


@pytest.mark.parametrize("other", ["image_generation", "video_generation", "task_generation"])
def test_music_cannot_dispatch_as_another_generation_workflow(other):
    query = [None] * 81
    query[0] = [None] * 7
    envelope = {"f.req": json.dumps([None, json.dumps(query)])}
    with pytest.raises(WebWorkflowTransportChanged):
        inject_web_request_options(envelope, WebRequestOptions(music_generation=True, **{other: True}))


def test_video_request_mirrors_current_account_companions_and_mp4_format(monkeypatch):
    from types import MappingProxyType
    from src.infrastructure.media_companions import MediaCompanion, MediaCompanionCatalog, MediaCompanionOption

    client = discovered_client()
    client._mcp_media_companions = MediaCompanionCatalog(MappingProxyType({11: (
        MediaCompanion(11, 11, "Duration", 1, True, (
            MediaCompanionOption(13, "10 seconds", True, True),
            MediaCompanionOption(14, "15 seconds", False, True),
        )),
        MediaCompanion(12, 11, "Aspect ratio", 1, True, (
            MediaCompanionOption(16, "Landscape (16:9)", True, True),
            MediaCompanionOption(17, "Portrait (9:16)", False, True),
        )),
        MediaCompanion(21, 11, "Format", 1, True, (
            MediaCompanionOption(67, "Video", False, True),
            MediaCompanionOption(66, "GIF", False, True),
        )),
    )}), frozenset({0, 185, 246}))
    captured = []

    class Captured(BaseException):
        pass

    class Transport:
        def stream(self, *_args, **kwargs):
            captured.append(json.loads(json.loads(kwargs["data"]["f.req"])[1]))
            raise Captured()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    client.client = Transport()
    client._install_thinking_transport()

    async def run():
        with client.video_generation_scope(duration_seconds=15, aspect_ratio="9:16"), model_policy_scope("media"):
            async for _ in client.generate_content_stream("synthetic video", model="hydrogen"):
                pass

    with pytest.raises(Captured):
        asyncio.run(run())
    assert captured[0][49] == 11
    assert set(captured[0][55][0]) == {14, 17, 67}
    assert captured[0][0][9][6][0] == [None, None, 2, 2, 2]
