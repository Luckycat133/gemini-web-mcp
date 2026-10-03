"""Native media selectors through the installed SDK's real request builder."""

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest
from gemini_webapi import GeminiClient
import gemini_webapi.client as upstream_client
from gemini_webapi.constants import Endpoint, MODEL_HEADER_KEY
from gemini_webapi.exceptions import APIError, GeminiError

import src.thinking_client as transport
from src.infrastructure.web_request_contracts import NativeMediaRequestShapeError
from src.thinking_client import (
    MediaRequestObservation,
    ThinkingLevelGeminiClient,
    WebRequestOptions,
    _web_request,
    inject_web_request_options,
)
from tests.test_thinking_client import _make_request_data, _new_client, _parse_inner


@pytest.fixture(autouse=True)
def reset_request_scope():
    token = _web_request.set(None)
    yield
    _web_request.reset(token)


@pytest.mark.parametrize("mode_id", [14, 11, 21])
def test_standalone_native_selector_changes_only_observed_feature_slot(mode_id):
    data = _make_request_data()
    original = _parse_inner(data)

    result = inject_web_request_options(data, WebRequestOptions(media_mode_id=mode_id))

    expected = list(original)
    expected[49] = mode_id
    assert _parse_inner(result) == expected
    assert _parse_inner(data) == original
    assert _parse_inner(result)[54:56] == [None, None]
    assert result["at"] == data["at"]


def test_native_media_and_thinking_selectors_coexist():
    inner = _parse_inner(inject_web_request_options(
        _make_request_data(),
        WebRequestOptions(media_mode_id=14, thinking_mode_id=2, thinking_level_id=2),
    ))
    assert inner[49] == 14
    assert inner[54:56] == [None, None]
    assert inner[79:81] == [2, 2]


@pytest.mark.parametrize("data", [
    {}, {"f.req": None}, {"f.req": "invalid"},
    {"f.req": "{}"}, {"f.req": "[]"}, {"f.req": "[null]"},
    {"f.req": "[null,{}]"}, {"f.req": '[null,"invalid"]'},
    _make_request_data({}), _make_request_data([]),
    _make_request_data([None] * 69),
    _make_request_data([[123]] + [None] * 68),
    _make_request_data([["prompt"]] + [None] * 48),
])
def test_native_request_shape_drift_fails_before_any_plain_chat(data):
    with pytest.raises(NativeMediaRequestShapeError) as error:
        inject_web_request_options(data, WebRequestOptions(media_mode_id=14))
    assert error.value.code == "UPSTREAM_CHANGED"


def test_native_request_refuses_a_conflicting_feature_selector():
    inner = [None] * 69
    inner[0] = ["prompt", 0]
    inner[49] = 1  # Installed upstream Deep Research selector.
    data = _make_request_data(inner)
    with pytest.raises(NativeMediaRequestShapeError, match="conflicts"):
        inject_web_request_options(data, WebRequestOptions(media_mode_id=14))
    assert _parse_inner(data)[49] == 1


class StopAtHTTPBoundary(GeminiError):
    pass


class SealedSession:
    """Real request-builder fixture with every session I/O path sealed."""

    def __init__(self, stream):
        self.stream = stream
        self.cookies = {}

    async def close(self):
        return None

    async def get(self, *_args, **_kwargs):
        raise AssertionError("Offline request builder must not make GET requests")

    async def post(self, *_args, **_kwargs):
        raise AssertionError("Offline request builder must not make POST requests")


def sdk_client_at_http_boundary(monkeypatch, captured):
    # Explicit fake cookie prevents optional browser-cookie discovery. init and
    # every HTTP/RPC path are sealed; the real SDK builds only an offline form.
    client = ThinkingLevelGeminiClient(secure_1psid="offline-fixture")
    client._running = True
    client.auto_close = False
    client.access_token = "offline-fixture-token"

    async def activity():
        return None

    async def forbidden_io(*_args, **_kwargs):
        raise AssertionError("Offline request builder must not initialize or make RPC requests")

    def stream(method, url, *args, **kwargs):
        captured.append({"method": method, "url": url, **kwargs})
        raise StopAtHTTPBoundary()

    # The actual pre-generation heartbeat was renamed in SDK2.1.1. Patch the
    # installed method, without inventing an attribute or bypassing its builder.
    activity_name = "_sync_activity" if hasattr(client, "_sync_activity") else "_send_bard_activity"
    monkeypatch.setattr(client, activity_name, activity)
    if hasattr(client, "_fetch_usage_info"):
        #2.1.1 refreshes account usage after a successful stream. Seal that
        # unrelated I/O while preserving its actual generation/parser path.
        monkeypatch.setattr(client, "_fetch_usage_info", activity)
    monkeypatch.setattr(client, "init", forbidden_io)
    monkeypatch.setattr(client, "_batch_execute", forbidden_io)
    monkeypatch.setattr(upstream_client, "save_cookies", lambda *_args: None)
    if hasattr(client, "_model_registry"):
        from gemini_webapi.types.availablemodel import AvailableModel

        model = AvailableModel(
            model_id="offline-model", model_name="gemini-3-flash", display_name="Offline model",
            description="Sealed request-builder fixture", capacity=1,
        )
        client._model_registry[model.model_id] = model
    client.client = SealedSession(stream)
    client._install_thinking_transport()
    return client


@pytest.mark.parametrize("mode,mode_id", [("image", 14), ("music", 21)])
def test_real_sdk_builder_receives_native_selector_without_http_kwarg_or_model_change(monkeypatch, mode, mode_id):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)

    async def run():
        with pytest.raises(StopAtHTTPBoundary):
            await GeminiClient.generate_content(client, "baseline", model="gemini-3-flash")
        with pytest.raises(StopAtHTTPBoundary):
            await client.generate_content("native", model="gemini-3-flash", media_mode=mode)

    asyncio.run(run())
    assert len(captured) == 2
    baseline, native = captured
    assert native["url"] == Endpoint.GENERATE
    assert native["headers"].keys() == baseline["headers"].keys()
    assert native["headers"][MODEL_HEADER_KEY] == baseline["headers"][MODEL_HEADER_KEY]
    # The SDK independently generates a new request nonce header each time.
    assert native["headers"].get("x-goog-ext-73010990-jspb") == baseline["headers"].get("x-goog-ext-73010990-jspb")
    assert "media_mode" not in native
    assert "current_retry" not in native
    assert "thinking_level" not in native
    inner = _parse_inner(native["data"])
    assert inner[0][0] == "native"
    assert inner[49] == mode_id
    assert inner[54:56] == [None, None]
    assert _web_request.get() is None


def test_native_api_error_does_not_retry_a_generation(monkeypatch):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, [])

    def failing_stream(method, url, **kwargs):
        captured.append(kwargs)
        raise APIError("offline fixture upstream request failed")

    client.client = SealedSession(failing_stream)
    client._install_thinking_transport()

    async def no_retry_delay(*_args, **_kwargs):
        raise AssertionError("Native creation must not blindly retry")

    monkeypatch.setattr(asyncio, "sleep", no_retry_delay)

    with pytest.raises(APIError):
        asyncio.run(client.generate_content("native", model="gemini-3-flash", media_mode="image"))

    assert len(captured) == 1
    assert "media_mode" not in captured[0]
    assert "current_retry" not in captured[0]
    assert _web_request.get() is None


@pytest.mark.parametrize("mode", ["image", "music"])
@pytest.mark.parametrize("thinking_level,level_id", [("standard", 1), ("extended", 2)])
@pytest.mark.parametrize("streaming", [False, True])
def test_real_sdk_native_thinking_header_and_body_agree(monkeypatch, mode, thinking_level, level_id, streaming):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)

    async def run():
        with pytest.raises(StopAtHTTPBoundary):
            if streaming:
                async for _ in client.generate_content_stream(
                    "fixture", model="gemini-3-flash", media_mode=mode, thinking_level=thinking_level,
                ):
                    pass
            else:
                await client.generate_content(
                    "fixture", model="gemini-3-flash", media_mode=mode, thinking_level=thinking_level,
                )

    asyncio.run(run())
    assert len(captured) == 1
    request = captured[0]
    assert _parse_inner(request["data"])[80] == level_id
    assert "extended_thinking" not in request
    method = GeminiClient.generate_content_stream if streaming else GeminiClient.generate_content
    if "extended_thinking" in inspect.signature(method).parameters:
        # The real2.1 builder appends [thinking_level, sessionid] to the model
        # header.2.0 has no appended field; its native body remains verified.
        assert json.loads(request["headers"][MODEL_HEADER_KEY])[-2] == level_id
    assert _web_request.get() is None


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("thinking_level,level_id", [("standard", 1), ("extended", 2)])
def test_real_sdk_positional_thinking_argument_remains_compatible(monkeypatch, streaming, thinking_level, level_id):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)
    method = GeminiClient.generate_content_stream if streaming else GeminiClient.generate_content
    args = ("fixture", None, "gemini-3-flash", None, None, False, False)
    # Exercise every legal positional SDK field; 2.1.1 appended this eighth
    # argument. An opposing positional value must be replaced, not rebound.
    if "extended_thinking" in inspect.signature(method).parameters:
        args = (*args, thinking_level != "extended")

    async def run():
        observation = MediaRequestObservation()
        with pytest.raises(StopAtHTTPBoundary):
            if streaming:
                async for _ in client.generate_content_stream(
                    *args, media_mode="image", thinking_level=thinking_level, media_observation=observation,
                ):
                    pass
            else:
                await client.generate_content(
                    *args, media_mode="image", thinking_level=thinking_level, media_observation=observation,
                )

    asyncio.run(run())
    assert len(captured) == 1
    request = captured[0]
    assert "extended_thinking" not in request
    if "extended_thinking" in inspect.signature(method).parameters:
        assert _parse_inner(request["data"])[80] == level_id
        assert json.loads(request["headers"][MODEL_HEADER_KEY])[-2] == level_id
    assert _web_request.get() is None


def test_changed_sdk_retry_control_cannot_leak_to_http_transport():
    called = []
    client = _new_client()
    client.client = SimpleNamespace(stream=lambda *args, **kwargs: called.append(kwargs))
    client._install_thinking_transport()

    with client.media_scope("image"):
        with pytest.raises(NativeMediaRequestShapeError, match="not consumed"):
            client.client.stream("POST", Endpoint.GENERATE, data=_make_request_data(), current_retry=0)
    assert called == []


def test_shape_drift_survives_real_upstream_exception_wrapper_without_retry(monkeypatch):
    captured, injections = [], []
    client = sdk_client_at_http_boundary(monkeypatch, captured)
    original = inject_web_request_options

    def drifted_form(data, options):
        injections.append(options)
        return original({**data, "f.req": "[null,{}]"}, options)

    monkeypatch.setattr(transport, "inject_web_request_options", drifted_form)

    with pytest.raises(NativeMediaRequestShapeError) as error:
        asyncio.run(client.generate_content("native", model="gemini-3-flash", media_mode="image"))

    assert error.value.code == "UPSTREAM_CHANGED"
    assert len(injections) == 1
    assert captured == []
    assert _web_request.get() is None


@pytest.mark.parametrize("data", [None, "invalid", []])
def test_native_scope_rejects_changed_transport_form_without_calling_stream(data):
    called = []
    client = _new_client()
    client.client = SimpleNamespace(stream=lambda *args, **kwargs: called.append(kwargs))
    client._install_thinking_transport()

    with client.media_scope("image"):
        with pytest.raises(NativeMediaRequestShapeError):
            client.client.stream("POST", Endpoint.GENERATE, data=data)
    assert called == []


def test_native_modes_stay_isolated_in_concurrent_generations(monkeypatch):
    client = _new_client()

    async def run():
        entered = {mode: asyncio.Event() for mode in ("image", "music")}

        async def generate(_client, prompt, **kwargs):
            assert "media_mode" not in kwargs
            entered[prompt].set()
            await entered["music" if prompt == "image" else "image"].wait()
            options = _web_request.get()
            assert options is not None
            return options.media_mode_id

        monkeypatch.setattr(GeminiClient, "generate_content", generate)
        result = await asyncio.gather(
            client.generate_content("image", media_mode="image", thinking_level="extended", model="flash"),
            client.generate_content("music", media_mode="music", thinking_level="standard", model="pro"),
        )
        assert result == [14, 21]
        assert _web_request.get() is None

    asyncio.run(run())


def test_native_scope_inherits_thinking_and_restores_after_nested_scope_and_cancel():
    client = _new_client()

    async def run():
        entered = asyncio.Event()
        observed = []

        async def child():
            try:
                options = _web_request.get()
                assert options is not None
                assert options.media_mode_id == 14
                with client.media_scope("music"):
                    with client.thinking_scope("pro", "standard"):
                        options = _web_request.get()
                        assert options is not None
                        assert options.media_mode_id == 21
                        assert options.thinking_level_id == 1
                        entered.set()
                        await asyncio.Event().wait()
            finally:
                observed.append(_web_request.get())

        with client.thinking_scope("flash", "extended"):
            with client.media_scope("image"):
                options = _web_request.get()
                assert options is not None
                assert options.thinking_level_id == 2
                task = asyncio.create_task(child())
                await entered.wait()
                assert _web_request.get().media_mode_id == 14
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert observed[0] == options
        assert _web_request.get() is None

    asyncio.run(run())


def test_unobserved_native_mode_and_learning_combination_are_rejected():
    client = _new_client()
    with pytest.raises(ValueError, match="only image, video or music"):
        with client.media_scope("audio"):
            pass
    with pytest.raises(ValueError, match="cannot be combined"):
        client._set_web_request("flash", "standard", "quiz", "image")
    assert _web_request.get() is None


def test_stream_consumes_native_mode_and_resets_scope_after_exception(monkeypatch):
    client = _new_client()

    async def generate(_client, *args, **kwargs):
        assert "media_mode" not in kwargs
        assert _web_request.get().media_mode_id == 21
        yield SimpleNamespace(text="accepted")
        raise StopAtHTTPBoundary()

    monkeypatch.setattr(GeminiClient, "generate_content_stream", generate)

    async def run():
        with pytest.raises(StopAtHTTPBoundary):
            async for _ in client.generate_content_stream("music", media_mode="music"):
                pass
        assert _web_request.get() is None

    asyncio.run(run())
