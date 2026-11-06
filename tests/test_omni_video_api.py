"""Fixture HTTP exchanges for the published Gemini video transport."""

import asyncio
import base64
import json

import httpx2
import pytest

from src.constants import OMNI_VIDEO_MODEL
from src.domain.results import DomainErrorCode
from src.infrastructure import omni_video_api
from src.infrastructure.omni_video_api import OmniVideoAPI, OmniVideoError, OmniVideoInteraction


_ID = "v1_example"
_HOST = "generativelanguage.googleapis.com"
_VIDEO = b"\x00\x00\x00\x18ftypmp42fixture"
_DATA = base64.b64encode(_VIDEO).decode()
_KEY = "fixture-api-key"


def _run(handler, action):
    async def checked_handler(request):
        assert request.url.scheme == "https" and request.url.host == _HOST
        assert request.headers["x-goog-api-key"] == _KEY
        assert request.headers["Api-Revision"] == "2026-05-20"
        assert "cookie" not in request.headers
        assert "key" not in request.url.params
        return handler(request)

    async def run():
        # Force redirects on for the injected client to verify per-call isolation.
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(checked_handler), follow_redirects=True) as http:
            return await action(OmniVideoAPI(http, _KEY))

    return asyncio.run(run())


def _completed(data=_DATA, **changes):
    return {
        "id": _ID, "status": "completed", "model": OMNI_VIDEO_MODEL,
        "steps": [
            {"type": "user_input", "content": [{"type": "video", "data": "discard-original-reference"}]},
            {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4", "data": data}]},
        ],
        **changes,
    }


@pytest.mark.parametrize("image,duration", [(None, None), (("image/png", b"reference"), 15)])
def test_create_sends_one_versioned_background_request(image, duration):
    calls = []

    def handle(request):
        calls.append(request.method)
        assert request.url.path == "/v1beta/interactions"
        expected_format = {"type": "video", "delivery": "uri"}
        expected_input = "Animate a blue marble."
        if duration:
            expected_format["duration"] = "15s"
        if image:
            expected_input = [
                {"type": "image", "mime_type": image[0], "data": base64.b64encode(image[1]).decode()},
                {"type": "text", "text": "Animate a blue marble."},
            ]
        assert json.loads(request.content) == {"model": OMNI_VIDEO_MODEL, "input": expected_input,
                                               "response_format": expected_format, "background": True, "store": True}
        return httpx2.Response(200, json={"id": _ID, "status": "in_progress"})

    assert _run(handle, lambda api: api.create("Animate a blue marble.", image=image, duration_seconds=duration)) == _ID
    assert calls == ["POST"]


@pytest.mark.parametrize("status,payload,code,rejected", [
    (400, {"error": {"code": "failed_precondition"}}, DomainErrorCode.CAPABILITY_UNAVAILABLE, True),
    (401, {}, DomainErrorCode.AUTH_REQUIRED, True),
    (402, {}, DomainErrorCode.CAPABILITY_UNAVAILABLE, True),
    (404, {"error": {"code": "model_not_found"}}, DomainErrorCode.CAPABILITY_UNAVAILABLE, True),
    (429, {}, DomainErrorCode.RATE_LIMITED, True),
    (504, {}, DomainErrorCode.TIMED_OUT, False),
    (503, {}, DomainErrorCode.NETWORK_ERROR, False),
    (200, {"status": "in_progress"}, DomainErrorCode.UPSTREAM_CHANGED, False),
    (200, {"id": "../other"}, DomainErrorCode.UPSTREAM_CHANGED, False),
])
def test_create_distinguishes_definitive_rejection_from_ambiguous_dispatch(status, payload, code, rejected):
    calls = []

    def handle(request):
        calls.append(request.method)
        payload.setdefault("error", {})["message"] = "private prompt or credential"
        return httpx2.Response(status, json=payload)

    with pytest.raises(OmniVideoError) as caught:
        _run(handle, lambda api: api.create("Private prompt."))
    assert (caught.value.code, caught.value.dispatch_rejected) == (code, rejected)
    assert "private" not in str(caught.value).lower()
    assert calls == ["POST"]


def test_create_timeout_does_not_replay_and_uses_safe_error():
    calls = []

    def handle(request):
        calls.append(request.method)
        raise httpx2.ReadTimeout("private prompt and key", request=request)

    with pytest.raises(OmniVideoError) as caught:
        _run(handle, lambda api: api.create("Private prompt."))
    assert caught.value.code == DomainErrorCode.TIMED_OUT and not caught.value.dispatch_rejected
    assert "private" not in str(caught.value)
    assert calls == ["POST"]


def test_google_array_error_reports_invalid_api_key_without_exposing_provider_message():
    response = [{"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "private metadata",
                           "details": [{"reason": "API_KEY_INVALID"}]}}]
    with pytest.raises(OmniVideoError) as caught:
        _run(lambda request: httpx2.Response(400, json=response), lambda api: api.create("Synthetic prompt."))
    assert caught.value.code == DomainErrorCode.AUTH_REQUIRED and caught.value.dispatch_rejected
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("duration", [True, 2, 16])
def test_invalid_duration_stops_before_generation(duration):
    with pytest.raises(OmniVideoError) as caught:
        _run(lambda request: pytest.fail("Dispatched invalid request"),
             lambda api: api.create("Animate a blue marble.", duration_seconds=duration))
    assert caught.value.code == DomainErrorCode.INVALID_ARGUMENT and caught.value.dispatch_rejected


def test_create_does_not_follow_redirect_or_forward_key():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx2.Response(307, headers={"location": "https://unrelated.example/token-sink"})

    with pytest.raises(OmniVideoError) as caught:
        _run(handle, lambda api: api.create("Animate a blue marble."))
    assert caught.value.code == DomainErrorCode.UPSTREAM_CHANGED
    assert not caught.value.dispatch_rejected
    assert calls == [f"https://{_HOST}/v1beta/interactions"]


@pytest.mark.parametrize("payload,expected", [
    (_completed(), OmniVideoInteraction("completed", _VIDEO, OMNI_VIDEO_MODEL)),
    (_completed(model=None), OmniVideoInteraction("completed", _VIDEO, None)),
    (_completed(steps=[]), OmniVideoInteraction("completed", None, OMNI_VIDEO_MODEL)),
    (_completed(steps=[{"type": "user_input", "content": [{"type": "video", "data": _DATA}]}]),
     OmniVideoInteraction("completed", None, OMNI_VIDEO_MODEL)),
    (_completed(status="in_progress"), OmniVideoInteraction("in_progress", None, OMNI_VIDEO_MODEL)),
    (_completed(status="failed"), OmniVideoInteraction("failed", None, OMNI_VIDEO_MODEL)),
])
def test_read_checks_exact_interaction_and_projects_output_only(payload, expected):
    def handle(request):
        assert request.method == "GET" and request.url.path == f"/v1beta/interactions/{_ID}"
        return httpx2.Response(200, json=payload)

    assert _run(handle, lambda api: api.read(_ID)) == expected


@pytest.mark.parametrize("payload", [
    _completed(id="v1_other"),
    _completed(model="gemini-omni-1.1-flash"),
    _completed(status="provider-added-state"),
    _completed(steps={"model_output": []}),
    _completed("invalid-base64"),
    _completed(_DATA + "\n"),
    _completed(""),
    _completed(steps=[{"type": "model_output", "content": [
        {"type": "video", "uri": "https://unrelated.example/video"},
    ]}]),
    _completed(steps=[{"type": "model_output", "content": [
        {"type": "video", "mime_type": "video/webm", "data": _DATA},
    ]}]),
    _completed(steps=[{"type": "model_output", "content": [
        {"type": "video", "data": _DATA}, {"type": "video", "data": "AAA="},
    ]}]),
])
def test_read_rejects_conflicting_or_unsafe_evidence(payload):
    with pytest.raises(OmniVideoError) as caught:
        _run(lambda _: httpx2.Response(200, json=payload), lambda api: api.read(_ID))
    assert caught.value.code == DomainErrorCode.UPSTREAM_CHANGED


@pytest.mark.parametrize("response", [
    httpx2.Response(200, content=b"malformed JSON"),
    httpx2.Response(200, json=_completed(), headers={"Content-Length": "800"}),
    httpx2.Response(200, json=_completed(), headers={"Content-Length": str(omni_video_api._MAX_JSON_BYTES + 1)}),
])
def test_read_rejects_malformed_truncated_or_oversized_json(response):
    with pytest.raises(OmniVideoError) as caught:
        _run(lambda _: response, lambda api: api.read(_ID))
    assert caught.value.code == DomainErrorCode.UPSTREAM_CHANGED


@pytest.mark.parametrize("data", [_DATA, "AAEC"])
def test_read_bounds_video_before_and_after_base64_decode(monkeypatch, data):
    monkeypatch.setattr(omni_video_api, "_MAX_VIDEO_BYTES", 2)
    with pytest.raises(OmniVideoError) as caught:
        _run(lambda _: httpx2.Response(200, json=_completed(data)), lambda api: api.read(_ID))
    assert caught.value.code == DomainErrorCode.UPSTREAM_CHANGED


@pytest.mark.parametrize("mutation_status,read_status,read_payload,expected", [
    (204, 404, {"error": {"code": "not_found"}}, True),
    (503, 404, {"error": {"code": 404, "status": "NOT_FOUND"}}, True),
    (204, 200, _completed(), False),
    (204, 404, {}, False),
    (403, 403, {}, False),
])
def test_delete_requires_exact_independent_absence(mutation_status, read_status, read_payload, expected):
    calls = []

    def handle(request):
        calls.append(request.method)
        assert request.url.path == f"/v1beta/interactions/{_ID}"
        if request.method == "DELETE":
            return httpx2.Response(mutation_status)
        return httpx2.Response(read_status, json=read_payload)

    assert _run(handle, lambda api: api.delete_and_verify(_ID)) is expected
    assert calls == ["DELETE", "GET"]


def test_unverified_cleanup_does_not_download_the_existing_output_again():
    class UnexpectedBody(httpx2.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Cleanup read consumed the still-present video")
            yield b""

    def handle(request):
        if request.method == "DELETE":
            return httpx2.Response(204)
        return httpx2.Response(200, stream=UnexpectedBody())

    assert _run(handle, lambda api: api.delete_and_verify(_ID)) is False
