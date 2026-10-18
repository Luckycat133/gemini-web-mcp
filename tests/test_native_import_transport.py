"""Caller-selected Drive/Photos/NotebookLM references through real transport seams.

The SDK builds its real request, but upload, initialization, RPC and HTTP I/O
are sealed. NotebookLM fixtures are not Gemini Web bot-based Notebook IDs.
"""

import asyncio
from pathlib import Path

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import APIError

import gemini_webapi.client as upstream_client
import src.surfaces.assist as assist
from src.domain.inputs import NativeAttachment
from src.infrastructure.web_request_contracts import NativeToolRequestShapeError
from src.thinking_client import _web_request
from tests.test_assist_surface import _FakeUnderstandClient, _patch_ask_client_env
from tests.test_chat_submission_ownership import CID, _bind_adapter, runtime as runtime
from tests.test_native_media_transport import SealedSession, StopAtHTTPBoundary, sdk_client_at_http_boundary
from tests.test_thinking_client import _make_request_data, _parse_inner


DRIVE = NativeAttachment(
    kind="drive", resource_id="drive_fixture_selected", name="Selected plan",
    mime_type="application/vnd.google-apps.document", url="https://drive.google.com/file/d/drive_fixture_selected/view",
)
PHOTOS = NativeAttachment(
    kind="photos", resource_id="photos_fixture_selected", name="Selected photo.jpg", mime_type="image/jpeg",
)
NOTEBOOKLM = NativeAttachment(kind="notebook", resource_id="nblm_fixture_selected", name="Selected NotebookLM notes")

# Independent expected proto fields from the native SK oneof. These assertions
# intentionally do not call the pure builder to manufacture their expectation.
EXPECTED_DRIVE = [None, "Selected plan", None, None, [
    ["drive_fixture_selected"], 8, "https://drive.google.com/file/d/drive_fixture_selected/view",
    "application/vnd.google-apps.document",
]]
EXPECTED_PHOTOS = [None, "Selected photo.jpg", None, None, None, None, None, [
    ["photos_fixture_selected"], "image/jpeg",
]]
EXPECTED_NOTEBOOKLM = [None, "Selected NotebookLM notes", None, None, None, None, None, None, None, None, None, [
    ["nblm_fixture_selected"],
]]


@pytest.fixture(autouse=True)
def reset_native_request_scope():
    token = _web_request.set(None)
    yield
    _web_request.reset(token)


async def _submit(client, *, streaming=False, native_attachments=(), files=None):
    arguments = {"model": "gemini-3-flash", "native_attachments": native_attachments, "files": files}
    if streaming:
        async for _ in client.generate_content_stream("Selected sources fixture", **arguments):
            pass
    else:
        await client.generate_content("Selected sources fixture", **arguments)


@pytest.mark.parametrize("attachment,expected", [
    (DRIVE, EXPECTED_DRIVE), (PHOTOS, EXPECTED_PHOTOS), (NOTEBOOKLM, EXPECTED_NOTEBOOKLM),
])
@pytest.mark.parametrize("streaming", [False, True])
def test_native_reference_reaches_real_sdk_query_oneof_without_http_kwargs(monkeypatch, attachment, expected, streaming):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)

    async def run():
        with pytest.raises(StopAtHTTPBoundary):
            await _submit(client, streaming=streaming, native_attachments=(attachment,))
        # A later ordinary request must not inherit a selected private resource.
        with pytest.raises(StopAtHTTPBoundary):
            await _submit(client, streaming=streaming)

    asyncio.run(run())
    assert len(captured) == 2
    native, ordinary = captured
    native_inner, ordinary_inner = _parse_inner(native["data"]), _parse_inner(ordinary["data"])
    assert native_inner[0][3] == [expected]
    assert ordinary_inner[0][3] is None
    assert native_inner[0][0] == ordinary_inner[0][0] == "Selected sources fixture"
    assert native_inner[49] == ordinary_inner[49]  # Attachments are not a feature-mode selector.
    assert native.keys() == ordinary.keys()
    assert not {"native_attachments", "current_retry", "thinking_level"}.intersection(native)
    assert _web_request.get() is None


@pytest.mark.parametrize("streaming", [False, True])
def test_native_references_merge_with_real_sdk_local_file_slot(monkeypatch, tmp_path, streaming):
    captured, uploads = [], []
    client = sdk_client_at_http_boundary(monkeypatch, captured)
    local = tmp_path / "local_fixture.txt"
    local.write_text("Local source fixture", encoding="utf-8")

    async def upload(file, **_kwargs):
        uploads.append(Path(file))
        return "offline-upload://local-fixture"

    monkeypatch.setattr(upstream_client, "upload_file", upload)

    async def run():
        # Keep the SDK's real local-file request construction as the baseline.
        with pytest.raises(StopAtHTTPBoundary):
            if streaming:
                async for _ in GeminiClient.generate_content_stream(client, "Local baseline", model="gemini-3-flash", files=[local]):
                    pass
            else:
                await GeminiClient.generate_content(client, "Local baseline", model="gemini-3-flash", files=[local])
        with pytest.raises(StopAtHTTPBoundary):
            await _submit(client, streaming=streaming, files=[local], native_attachments=(DRIVE, PHOTOS, NOTEBOOKLM))

    asyncio.run(run())
    assert uploads == [local, local]
    assert len(captured) == 2
    local_slot = _parse_inner(captured[0]["data"])[0][3]
    assert local_slot == [[["offline-upload://local-fixture"], "local_fixture.txt"]]
    assert _parse_inner(captured[1]["data"])[0][3] == [*local_slot, EXPECTED_DRIVE, EXPECTED_PHOTOS, EXPECTED_NOTEBOOKLM]
    assert not {"native_attachments", "current_retry"}.intersection(captured[1])


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("native_count", [9, 10])
def test_combined_attachment_limit_stops_before_generation_http(monkeypatch, tmp_path, streaming, native_count):
    captured, uploads = [], []
    client = sdk_client_at_http_boundary(monkeypatch, captured)
    local = tmp_path / "local_fixture.txt"
    local.write_text("Local fixture", encoding="utf-8")

    async def upload(file, **_kwargs):
        uploads.append(file)
        return "offline-upload://local-fixture"

    monkeypatch.setattr(upstream_client, "upload_file", upload)
    selected = tuple(NativeAttachment(kind="drive", resource_id=f"drive_fixture_{index}", name=f"Selected {index}")
                     for index in range(native_count))
    if native_count == 9:
        with pytest.raises(StopAtHTTPBoundary):
            asyncio.run(_submit(client, streaming=streaming, files=[local], native_attachments=selected))
        assert len(captured) == 1
        assert len(_parse_inner(captured[0]["data"])[0][3]) == 10
    else:
        # The installed SDK wraps an interceptor ValueError in APIError. The
        # request must still stop before the sealed generation HTTP boundary.
        with pytest.raises(APIError, match="combined local/native") as error:
            asyncio.run(_submit(client, streaming=streaming, files=[local], native_attachments=selected))
        assert isinstance(error.value.__cause__, ValueError)
        assert captured == []
    assert uploads == [local]
    assert _web_request.get() is None


@pytest.mark.parametrize("corrupt_data", [
    None,
    {},
    {"f.req": "not-json"},
    {"f.req": "[null]"},
    _make_request_data({}),
    _make_request_data([None] * 69),
    _make_request_data([["fixture", 0, None]] + [None] * 68),
    _make_request_data([["fixture", 0, None, "not-an-attachment-list"]] + [None] * 68),
])
def test_changed_sdk_body_cannot_silently_drop_native_references(monkeypatch, corrupt_data):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, captured)
    installed_stream = client.client.stream

    def corrupt_request(method, url, *args, **kwargs):
        kwargs["data"] = corrupt_data
        return installed_stream(method, url, *args, **kwargs)

    client.client.stream = corrupt_request
    with pytest.raises(NativeToolRequestShapeError) as error:
        asyncio.run(_submit(client, native_attachments=(NOTEBOOKLM,)))
    assert error.value.code == "UPSTREAM_CHANGED"
    assert captured == []
    assert _web_request.get() is None


@pytest.mark.parametrize("streaming", [False, True])
def test_native_reference_generation_api_failure_is_never_replayed(monkeypatch, streaming):
    captured = []
    client = sdk_client_at_http_boundary(monkeypatch, [])

    def fail_once(method, url, **kwargs):
        captured.append(kwargs)
        raise APIError("Offline native reference failure")

    client.client = SealedSession(fail_once)
    client._install_thinking_transport()

    async def no_retry_delay(*_args, **_kwargs):
        raise AssertionError("A selected-reference request must not be resubmitted automatically")

    monkeypatch.setattr(asyncio, "sleep", no_retry_delay)
    with pytest.raises(APIError):
        asyncio.run(_submit(client, streaming=streaming, native_attachments=(DRIVE,)))
    assert len(captured) == 1
    assert "current_retry" not in captured[0]
    assert _web_request.get() is None


def test_focused_typed_understand_keeps_selected_ids_and_local_sources(monkeypatch, tmp_path):
    local = tmp_path / "local_spec.txt"
    local.write_text("Local specification fixture", encoding="utf-8")
    client = _FakeUnderstandClient(response_text="[drive_doc], [photo], [notebooklm_notes] and [local_spec] agree with [inline].")
    _patch_ask_client_env(monkeypatch, client)
    response = asyncio.run(assist.mcp.call_tool("gemini_understand", {
        "task": "Compare selected content.",
        "inputs": [
            {"id": "drive_doc", "kind": "drive", "resource_id": DRIVE.resource_id, "name": DRIVE.name,
             "mime_type": DRIVE.mime_type, "url": DRIVE.url},
            {"id": "photo", "kind": "photos", "resource_id": PHOTOS.resource_id, "name": PHOTOS.name,
             "mime_type": PHOTOS.mime_type},
            {"id": "notebooklm_notes", "kind": "notebook", "resource_id": NOTEBOOKLM.resource_id, "name": NOTEBOOKLM.name},
            {"id": "local_spec", "kind": "file", "path": str(local)},
            {"id": "inline", "kind": "text", "text": "Inline fixture"},
            {"id": "missing_selection", "kind": "drive", "resource_id": "", "name": "Unselected resource"},
        ],
    }))
    assert response.is_error is False
    sent = client.captured_generate_kwargs
    assert sent["native_attachments"] == (DRIVE, PHOTOS, NOTEBOOKLM)
    assert sent["files"] == [str(local)]
    assert "[notebooklm_notes] native notebook attachment Selected NotebookLM notes" in sent["prompt"]
    payload = response.content[0].meta["domain_result"]
    assert payload["ok"] is True
    assert [(item["id"], item["kind"], item["outcome"]) for item in payload["data"]["inputs"]] == [
        ("drive_doc", "drive", "analyzed"), ("photo", "photos", "analyzed"),
        ("notebooklm_notes", "notebook", "analyzed"), ("local_spec", "file", "analyzed"),
        ("inline", "text", "analyzed"), ("missing_selection", "drive", "skipped"),
    ]


def test_focused_native_understand_timeout_preserves_source_and_failed_input_ids(monkeypatch, runtime):
    _bind_adapter(monkeypatch, assist, runtime)
    observed = []
    upstream_submit = GeminiClient.generate_content

    async def observe_request(*args, **kwargs):
        observed.append(_web_request.get().native_attachments)
        return await upstream_submit(*args, **kwargs)

    monkeypatch.setattr(GeminiClient, "generate_content", observe_request)
    response = asyncio.run(assist.mcp.call_tool("gemini_understand", {
        "task": "Read selected NotebookLM and Drive content.",
        "inputs": [
            {"id": "notebooklm_notes", "kind": "notebook", "resource_id": NOTEBOOKLM.resource_id, "name": NOTEBOOKLM.name},
            {"id": "drive_doc", "kind": "drive", "resource_id": DRIVE.resource_id, "name": DRIVE.name},
            {"id": "missing_selection", "kind": "photos", "resource_id": ""},
        ],
    }))
    payload = response.content[0].meta["domain_result"]
    assert payload["ok"] is False
    assert payload["error"]["code"] == "TIMED_OUT"
    assert payload["error"]["retryable"] is False
    assert payload["warnings"][0]["code"] == "SOURCE_RETAINED"
    assert payload["data"]["lifecycle"]["upstream_chat_id"] == CID
    assert payload["data"]["lifecycle"]["cleanup"]["state"] == "retained"
    assert payload["meta"]["details"]["lifecycle"]["upstream_chat_id"] == CID
    assert [(item["id"], item["outcome"]) for item in payload["data"]["inputs"]] == [
        ("notebooklm_notes", "failed"), ("drive_doc", "failed"), ("missing_selection", "skipped"),
    ]
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["current_retry"] == 0
    # The wrapper consumes the domain option into its ContextVar; it must not
    # leak an unknown keyword to the installed SDK or its eventual HTTP call.
    assert "native_attachments" not in runtime.calls[0]
    assert observed == [(EXPECTED_NOTEBOOKLM, [None, DRIVE.name, None, None, [
        [DRIVE.resource_id], 0, None, "application/octet-stream",
    ]])]
    assert runtime.cleanup.list_pending_cleanup() == {}
    runtime.delete.assert_not_awaited()
