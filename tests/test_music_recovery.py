"""Music read-back must not attribute old turns to a new generation."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.infrastructure.rpc_contracts import get_contract
from src.services.music_recovery import fetch_music_media_from_chat, media_from_music_card, music_response_identity


def test_music_readback_filters_to_matching_turn_and_candidate():
    def turn(rid, rcid, title):
        candidate = [rcid] + [None] * 11 + [[{"87": [[None, [None, 4, title, None, None, None, None,
                                                                ["thumb", "https://example.test/" + title]]]]}]]
        return [[None, rid], None, None, [[candidate]]]

    body = [[turn("r_old", "rc_old", "old.mp3"), turn("r_new", "rc_new", "new.mp3"),
             turn("r_new", "rc_other", "other.mp3"), turn("r_new", "rc_new", "new.mp4")]]
    rpc_id = get_contract("media.music_chat").rpc_id
    wire = json.dumps([["wrb.fr", rpc_id, json.dumps(body)]])

    class Client:
        calls = 0

        async def _batch_execute(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(text=wire)

    client = Client()
    media = asyncio.run(fetch_music_media_from_chat(client, "c_chat", rid="r_new", rcid="rc_new"))
    assert [item.title for item in media] == ["new.mp3", "new.mp4"]
    assert asyncio.run(fetch_music_media_from_chat(client, "c_chat")) == []
    assert client.calls == 1


def test_music_response_identity_requires_observed_turn():
    assert music_response_identity(SimpleNamespace(metadata=["c_a"])) == ("c_a", "", "")
    assert music_response_identity(SimpleNamespace(metadata=["c_a", "r_a", "rc_a"])) == (
        "c_a", "r_a", "rc_a"
    )


def test_music_recovery_detects_mp4_from_url_when_title_has_no_extension():
    media = media_from_music_card(
        {"title": "song", "url": "https://example.test/download?filename=recording%2Emp4"},
        client=SimpleNamespace(), cid="c_a",
    )
    assert media is not None
    assert media.url.endswith("recording%2Emp4")
    assert media.mp3_url == ""


@pytest.mark.parametrize("body", [[], {"changed": True}])
def test_music_readback_never_promotes_empty_or_changed_shape(body):
    rpc_id = get_contract("media.music_chat").rpc_id
    wire = json.dumps([["wrb.fr", rpc_id, json.dumps(body)]])

    class Client:
        async def _batch_execute(self, *_args, **_kwargs):
            return SimpleNamespace(text=wire)

    assert asyncio.run(fetch_music_media_from_chat(Client(), "c_a", rid="r_a")) == []


def _strict_music_turn(rid="r_target", rcid="rc_target", *, completion=2, cards=None):
    candidate = [rcid] + [None] * 12
    candidate[8] = [completion]
    if cards is not None:
        candidate[12] = [{"87": cards}]
    return [[None, rid], None, None, [[candidate]]]


def _strict_music_client(body, *, rejection=False):
    rpc_id = get_contract("media.music_chat").rpc_id
    wire = json.dumps([["wrb.fr", rpc_id, json.dumps(body)]]) if not rejection else "invalid envelope"
    class Client:
        async def _batch_execute(self, *_args, **_kwargs):
            return SimpleNamespace(text=wire)
    return Client()


def test_strict_music_readback_proves_completed_target_and_empty_cards():
    body = [[_strict_music_turn(cards=[])]]
    assert asyncio.run(fetch_music_media_from_chat(_strict_music_client(body), "c_chat", rid="r_target",
                                                  rcid="rc_target", strict=True)) == []


def test_strict_music_readback_returns_same_turn_cards():
    card = [None, [None, 4, "track.mp3", None, None, None, None, ["thumb", "https://example.test/track.mp3"]]]
    body = [[_strict_music_turn(cards=[card])]]
    media = asyncio.run(fetch_music_media_from_chat(_strict_music_client(body), "c_chat", rid="r_target",
                                                   rcid="rc_target", strict=True))
    assert [item.title for item in media] == ["track.mp3"]


@pytest.mark.parametrize("change", ["none", "rid", "rcid", "incomplete", "shape", "label", "asset", "duplicate"])
def test_strict_music_error_card_requires_unique_completed_target_and_exact_shape(change):
    from src.services.music_recovery import MusicErrorCardObserved, MusicReadbackUnverified

    turn = _strict_music_turn()
    candidate = turn[3][0][0]
    candidate[12] = [{"8": [], "73": [None, "Generating your track", None, 3]}]
    if change == "rid":
        turn[0][1] = "r_other"
    elif change == "rcid":
        candidate[0] = "rc_other"
    elif change == "incomplete":
        candidate[8] = [1]
    elif change == "shape":
        candidate[12][0]["73"][3] = True
    elif change == "label":
        candidate[12][0]["73"][1] = "Different card"
    elif change == "asset":
        candidate[12][0]["87"] = [["asset"]]
    body = [[turn, turn] if change == "duplicate" else [turn]]
    expected = MusicErrorCardObserved if change == "none" else MusicReadbackUnverified
    with pytest.raises(expected):
        asyncio.run(fetch_music_media_from_chat(_strict_music_client(body), "c_chat", rid="r_target",
                                                rcid="rc_target", strict=True))


@pytest.mark.parametrize("body", [
    [], {"changed": True}, [[]],
    [[_strict_music_turn(rid="r_old", cards=[])]],
    [[_strict_music_turn(rcid="rc_old", cards=[])]],
    [[_strict_music_turn(completion=1, cards=[])]],
    [[_strict_music_turn(cards=[[None, [None]]])]],
])
def test_strict_music_readback_does_not_confuse_unverified_with_empty(body):
    from src.services.music_recovery import MusicReadbackUnverified
    with pytest.raises(MusicReadbackUnverified):
        asyncio.run(fetch_music_media_from_chat(_strict_music_client(body), "c_chat", rid="r_target",
                                                rcid="rc_target", strict=True))


def test_strict_music_readback_rejects_invalid_envelope_and_missing_capability():
    from src.services.music_recovery import MusicReadbackUnverified
    for client in (_strict_music_client([], rejection=True), SimpleNamespace()):
        with pytest.raises(MusicReadbackUnverified):
            asyncio.run(fetch_music_media_from_chat(client, "c_chat", rid="r_target", rcid="rc_target", strict=True))
