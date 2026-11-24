"""Confirmed Web music choices reach both native request representations."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock

from gemini_webapi import GeminiClient
import pytest

from src.infrastructure.media_companions import MediaCompanion, MediaCompanionCatalog, MediaCompanionOption
from src.infrastructure.rpc_contracts import WebWorkflowTransportChanged, apply_music_companion_options
from src.services.model_policy import model_policy_scope
from src.thinking_client import WebRequestOptions, inject_web_request_options
from tests.test_web_model_selection import discovered_client


FIXTURE = json.loads((Path(__file__).parent / "fixtures/media_companion_transport.json").read_text())


def music_request():
    body = [None] * 81
    body[0] = ["synthetic music", 0, None, None, None, None, 0]
    body[49] = 21
    return body


@pytest.mark.parametrize("case", ["success", "empty", "rejected", "changed_shape"])
def test_current_composer_fixture_preserves_atomic_music_contract(case):
    fixture = FIXTURE["cases"][case]
    body = music_request()
    if "context" in fixture:
        body[0].extend([None, None, fixture["context"]])
    original = deepcopy(body)
    if case != "success":
        with pytest.raises(WebWorkflowTransportChanged):
            apply_music_companion_options(body, tuple(fixture["option_ids"]))
        assert body == original
        return
    apply_music_companion_options(body, tuple(fixture["option_ids"]))
    assert body[55] == [fixture["option_ids"]]
    assert body[0][9][6][2] == fixture["parameters"]
    assert body[3:5] == original[3:5]
    assert body[83:] == original[83:]


@pytest.mark.parametrize("option,parameter", [(23, 1), (24, 2), (25, 3), (42, 4), (43, 5)])
def test_all_observed_music_duration_choices_have_exact_native_enum(option, parameter):
    body = music_request()
    apply_music_companion_options(body, (option, 69, 41))
    assert body[0][9][6][2] == [parameter, 3]


@pytest.mark.parametrize("option,parameter", [(26, 1), (27, 2), (69, 3)])
def test_all_observed_music_voice_choices_have_exact_native_enum(option, parameter):
    body = music_request()
    apply_music_companion_options(body, (42, option, 41))
    assert body[0][9][6][2] == [4, parameter]


@pytest.mark.parametrize("option", range(28, 41))
def test_fixed_genre_choices_preserve_the_observed_native_enum(option):
    body = music_request()
    apply_music_companion_options(body, (42, 27, option))
    assert body[0][9][6][2] == [4, 2, option - 27]


@pytest.mark.parametrize("ids", [(), (42, 42), (42, 43), (26, 27), (28, 41), (41,),
                                 (999,), (True,), ("42",), (None,), ([],)])
def test_invalid_or_conflicting_choices_do_not_partially_mutate_request(ids):
    body = music_request()
    original = deepcopy(body)
    with pytest.raises(WebWorkflowTransportChanged):
        apply_music_companion_options(body, ids)
    assert body == original


@pytest.mark.parametrize("field,value", [(0, None), (49, 14), (55, [[43, 26]])])
def test_conflicting_request_metadata_refuses_without_partial_mutation(field, value):
    body = music_request()
    body[field] = value
    original = deepcopy(body)
    with pytest.raises(WebWorkflowTransportChanged):
        apply_music_companion_options(body, (42, 69, 41))
    assert body == original


@pytest.mark.parametrize("media", [["video"], [None, [1]], [None, None, "changed"], [None, None, [5, 1]]])
def test_conflicting_content_context_refuses_without_mutating_original(media):
    body = music_request()
    body[0].extend([None, None, [None] * 6 + [media]])
    original = deepcopy(body)
    with pytest.raises(WebWorkflowTransportChanged):
        apply_music_companion_options(body, (42, 69, 41))
    assert body == original


def test_existing_nonmedia_context_is_preserved_and_identical_application_is_idempotent():
    body = music_request()
    body[0].extend([None, None, [None, ["unrelated context"]]])
    apply_music_companion_options(body, (42, 69, 41))
    assert body[0][9][1] == ["unrelated context"]
    first = deepcopy(body)
    apply_music_companion_options(body, (42, 69, 41))
    assert body == first


def test_web_request_adapter_preserves_selected_ids_and_adds_native_music_options():
    body = music_request()
    body[49] = None
    envelope = {"f.req": json.dumps([None, json.dumps(body)])}
    changed = inject_web_request_options(envelope, WebRequestOptions(
        music_generation=True, media_option_ids=(42, 69, 41)))
    request = json.loads(json.loads(changed["f.req"])[1])
    assert request[49] == 21 and request[55] == [[42, 69, 41]]
    assert request[0][9][6][2] == [4, 3]
    assert json.loads(json.loads(envelope["f.req"])[1]) == body


def test_actual_sdk_boundary_emits_both_confirmed_music_choice_representations(monkeypatch):
    client = discovered_client()
    groups = tuple(MediaCompanion(index, 21, "Synthetic group", 1, True, (
        MediaCompanionOption(option_id, "Synthetic option", True, True),
    )) for index, option_id in enumerate((42, 69, 41), 1))
    client._mcp_media_companions = MediaCompanionCatalog(MappingProxyType({21: groups}), frozenset())
    captured = []

    class StopBeforeNetwork(BaseException):
        pass

    class Transport:
        def stream(self, *_args, **kwargs):
            captured.append(json.loads(json.loads(kwargs["data"]["f.req"])[1]))
            raise StopBeforeNetwork()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    client.client = Transport()
    client._install_thinking_transport()

    async def run():
        with client.music_generation_scope(), model_policy_scope("media"):
            async for _ in client.generate_content_stream("synthetic music", model="hydrogen"):
                pass

    with pytest.raises(StopBeforeNetwork):
        asyncio.run(run())
    assert len(captured) == 1
    body = captured[0]
    assert body[49] == 21 and body[55] == [[42, 69, 41]]
    assert body[0][9][6][2] == [4, 3]
