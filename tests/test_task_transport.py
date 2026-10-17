"""Native Task selectors at a sealed SDK transport boundary."""

import asyncio

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.constants import MODEL_HEADER_KEY
from gemini_webapi.exceptions import APIError
import orjson

import src.thinking_client as transport
from src.infrastructure.web_request_contracts import NativeTaskRequestShapeError
from src.thinking_client import MediaRequestObservation, WebRequestOptions, _web_request, inject_web_request_options
from tests.test_native_media_transport import SealedSession, StopAtHTTPBoundary, sdk_client_at_http_boundary
from tests.test_thinking_client import _make_request_data, _new_client, _parse_inner


@pytest.fixture(autouse=True)
def isolated_request_scope():
    token = _web_request.set(None)
    yield
    _web_request.reset(token)


def test_task_selection_matches_the_official_mode_and_request_flag():
    data = _make_request_data()
    original = _parse_inner(data)
    selected = inject_web_request_options(data, WebRequestOptions(task_mode=True))
    expected = list(original)
    expected.extend([None] * (84 - len(expected)))
    expected[49] = 40
    expected[83] = True
    assert _parse_inner(selected) == expected
    assert _parse_inner(data) == original
    assert selected["at"] == data["at"]
    assert _parse_inner(inject_web_request_options(selected, WebRequestOptions(task_mode=True))) == expected


@pytest.mark.parametrize("flag", [None, False, True])
def test_explicit_task_selection_sets_its_observed_boolean(flag):
    inner = _parse_inner(_make_request_data())
    inner.extend([None] * (84 - len(inner)))
    inner[83] = flag
    data = _make_request_data(inner)
    selected = inject_web_request_options(data, WebRequestOptions(task_mode=True))
    assert _parse_inner(selected)[83] is True
    assert _parse_inner(data)[83] is flag
    normal = inject_web_request_options(data, WebRequestOptions())
    assert _parse_inner(normal)[83] is flag
    assert _parse_inner(normal)[49] is None


@pytest.mark.parametrize("flag", [0, 1, "false", [], {}, [True]])
def test_task_rejects_a_changed_request_flag_shape(flag):
    inner = _parse_inner(_make_request_data())
    inner.extend([None] * (84 - len(inner)))
    inner[83] = flag
    data = _make_request_data(inner)
    with pytest.raises(NativeTaskRequestShapeError, match="flag shape"):
        inject_web_request_options(data, WebRequestOptions(task_mode=True))
    assert _parse_inner(data)[83] == flag


def test_owned_task_session_preserves_gem_and_observed_chat_after_rollback():
    observed = []
    client = _new_client()
    chat = client.start_owned_chat(model="gemini-3-flash", gem="gem-fixture", on_chat_observed=observed.append)
    assert chat.model == "gemini-3-flash"
    assert chat.gem == "gem-fixture"
    chat.cid = "c_task_owned"
    chat.metadata = ["", "", "", None, None, None, None, None, None, ""]
    assert chat.observed_chat_id == "c_task_owned"
    assert observed == ["c_task_owned"]


@pytest.mark.parametrize("options", [
    WebRequestOptions(task_mode=True, media_mode_id=14),
    WebRequestOptions(task_mode=True, media_mode_id=11),
    WebRequestOptions(task_mode=True, media_mode_id=21),
    WebRequestOptions(task_mode=True, learning_mode_id=18),
])
def test_task_cannot_be_combined_with_media_or_learning(options):
    with pytest.raises(ValueError, match="cannot be combined"):
        inject_web_request_options(_make_request_data(), options)


@pytest.mark.parametrize("selector", [1, 14, 11, 21, 45])
def test_task_refuses_an_existing_conflicting_feature(selector):
    data = _make_request_data()
    inner = _parse_inner(data)
    inner[49] = selector
    data = _make_request_data(inner)
    with pytest.raises(NativeTaskRequestShapeError, match="conflicts"):
        inject_web_request_options(data, WebRequestOptions(task_mode=True))
    assert _parse_inner(data)[49] == selector


@pytest.mark.parametrize("data", [
    {}, {"f.req": None}, {"f.req": "invalid"}, {"f.req": "{}"},
    {"f.req": "[]"}, {"f.req": "[null]"}, {"f.req": "[null,{}]"},
    {"f.req": '[null,"invalid"]'}, _make_request_data({}), _make_request_data([]),
    _make_request_data([None] * 69), _make_request_data([[123]] + [None] * 68),
    _make_request_data([["prompt"]] + [None] * 48),
])
def test_changed_task_request_shape_is_rejected(data):
    with pytest.raises(NativeTaskRequestShapeError) as error:
        inject_web_request_options(data, WebRequestOptions(task_mode=True))
    assert error.value.code == "UPSTREAM_CHANGED"


def test_task_scope_keeps_thinking_and_rejects_inherited_feature_conflicts():
    client = _new_client()
    with client.thinking_scope("flash", "medium"):
        original = _web_request.get()
        with client.task_scope():
            selected = _web_request.get()
            assert selected.task_mode is True
            assert selected.thinking_level_id == 4
            with pytest.raises(ValueError, match="cannot be combined"):
                with client.media_scope("image"):
                    pass
            with pytest.raises(ValueError, match="cannot be combined"):
                client._set_web_request("flash", None, "quiz")
            assert _web_request.get() is selected
        assert _web_request.get() is original
    assert _web_request.get() is None
    with client.media_scope("image"):
        with pytest.raises(ValueError, match="cannot be combined"):
            with client.task_scope():
                pass
    assert _web_request.get() is None


@pytest.mark.parametrize("streaming", [False, True])
def test_real_sdk_task_selector_has_no_media_observation_or_argument_leak(monkeypatch, streaming):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)

    def forbidden_media_observation(*args, **kwargs):
        raise AssertionError("Task requests must not enter media observation")

    monkeypatch.setattr(transport, "_with_owned_media_session", forbidden_media_observation)

    async def invoke(task_mode=False):
        with pytest.raises(StopAtHTTPBoundary):
            if streaming:
                async for _ in client.generate_content_stream("fixture", model="gemini-3-flash", task_mode=task_mode):
                    pass
            else:
                await client.generate_content("fixture", model="gemini-3-flash", task_mode=task_mode)

    async def run():
        await invoke()
        await invoke(task_mode=True)
        assert _web_request.get() is None
        await invoke()

    asyncio.run(run())
    assert len(captured) == 3
    before, selected, after = [_parse_inner(item["data"]) for item in captured]
    expected = list(before)
    expected.extend([None] * (84 - len(expected)))
    expected[49], expected[59] = 40, selected[59]
    expected[83] = True
    assert selected == expected
    expected = list(before)
    expected[59] = after[59]
    assert after == expected
    assert captured[0]["headers"][MODEL_HEADER_KEY] == captured[1]["headers"][MODEL_HEADER_KEY]
    assert all("task_mode" not in item and "current_retry" not in item for item in captured)
    assert _web_request.get() is None


@pytest.mark.parametrize("streaming", [False, True])
def test_task_rejects_media_artifact_observation_before_submission(monkeypatch, streaming):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)

    async def run():
        with pytest.raises(ValueError, match="media artifact observation"):
            if streaming:
                async for _ in client.generate_content_stream(
                    "fixture", task_mode=True, media_observation=MediaRequestObservation(),
                ):
                    pass
            else:
                await client.generate_content("fixture", task_mode=True, media_observation=MediaRequestObservation())

    asyncio.run(run())
    assert captured == []
    assert _web_request.get() is None


@pytest.mark.parametrize("streaming", [False, True])
def test_task_scope_reaches_sdk_helpers_with_the_exact_thinking_level(monkeypatch, streaming):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)

    async def run():
        with client.thinking_scope("gemini-3-flash", "medium"):
            original = _web_request.get()
            with pytest.raises(StopAtHTTPBoundary):
                with client.task_scope():
                    if streaming:
                        async for _ in client.generate_content_stream("fixture", model="gemini-3-flash"):
                            pass
                    else:
                        await client.generate_content("fixture", model="gemini-3-flash")
            assert _web_request.get() is original

    asyncio.run(run())
    request = captured[0]
    assert _parse_inner(request["data"])[49] == 40
    assert _parse_inner(request["data"])[83] is True
    assert _parse_inner(request["data"])[80] == 4
    assert orjson.loads(request["headers"][MODEL_HEADER_KEY])[15] == 4
    assert _web_request.get() is None


def test_concurrent_task_and_plain_chat_scopes_do_not_leak(monkeypatch):
    client = _new_client()

    async def run():
        both_entered = asyncio.Event()
        active = []

        async def fake_generate(self, *args, **kwargs):
            request = _web_request.get()
            selected = bool(request and request.task_mode)
            active.append(selected)
            if len(active) == 2:
                both_entered.set()
            await both_entered.wait()
            assert _web_request.get() is request
            assert "task_mode" not in kwargs
            return selected

        monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
        assert await asyncio.gather(
            client.generate_content("fixture", task_mode=True),
            client.generate_content("fixture"),
        ) == [True, False]

    asyncio.run(run())
    assert _web_request.get() is None


@pytest.mark.parametrize("corrupt", ["form", "payload", "flag"])
def test_task_wire_shape_drift_stops_before_network(monkeypatch, corrupt):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)
    installed_stream = client.client.stream

    def corrupt_request(method, url, *args, **kwargs):
        if corrupt == "flag":
            inner = _parse_inner(kwargs["data"])
            inner.extend([None] * (84 - len(inner)))
            inner[83] = "unexpected-shape"
            kwargs["data"] = _make_request_data(inner)
        else:
            kwargs["data"] = None if corrupt == "form" else {"f.req": "[null]"}
        return installed_stream(method, url, *args, **kwargs)

    client.client.stream = corrupt_request
    with pytest.raises(NativeTaskRequestShapeError):
        asyncio.run(client.generate_content("fixture", model="gemini-3-flash", task_mode=True))
    assert captured == []
    assert _web_request.get() is None


def test_task_api_error_does_not_resubmit_generation(monkeypatch):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, [])

    def failing_stream(*args, **kwargs):
        captured.append(kwargs)
        raise APIError("offline fixture")

    async def forbidden_retry(*args, **kwargs):
        raise AssertionError("Task requests must not retry generation")

    client.client = SealedSession(failing_stream)
    client._install_thinking_transport()
    monkeypatch.setattr(asyncio, "sleep", forbidden_retry)
    with pytest.raises(APIError):
        asyncio.run(client.generate_content("fixture", model="gemini-3-flash", task_mode=True))
    assert len(captured) == 1
    assert _web_request.get() is None
