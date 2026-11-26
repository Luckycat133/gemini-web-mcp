"""Web video controls reach the actual generation scope on every media surface."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp import Client

from src.constants import OMNI_VIDEO_MODEL, resolve_media_request
from src.domain import CleanupObservation, CleanupState, DomainResult, LongOperationData, OperationState
from src.domain.operations import OperationReadData
from src.onboarding import domain_result_from_call
from src.services.media_generation import VideoModeUnavailable, generate_media_response, video_options_error


def test_web_video_and_explicit_api_options_stay_separate():
    def validate(**options):
        return video_options_error(media_type="video", model=None, thinking_level="standard",
                                   duration_seconds=None, delete_after_seconds=None, **options)

    assert validate() is None
    assert validate(aspect_ratio="9:16") is None
    for seconds in (10, 15):
        assert video_options_error(media_type="video", model="hydrogen", thinking_level="standard",
                                   duration_seconds=seconds, aspect_ratio="16:9", delete_after_seconds=None) is None
    for seconds in (3, 16, True, "15"):
        assert video_options_error(media_type="video", model="hydrogen", thinking_level="standard",
                                   duration_seconds=seconds, delete_after_seconds=None)
    for ratio in ("square", " 16:9 ", 1):
        assert validate(aspect_ratio=ratio)
    assert video_options_error(media_type="image", model=None, thinking_level="standard",
                               duration_seconds=None, aspect_ratio="9:16", delete_after_seconds=None) is None
    assert video_options_error(media_type="image", model=None, thinking_level="standard",
                               duration_seconds=None, aspect_ratio="wide", delete_after_seconds=None)
    assert video_options_error(media_type="music", model=None, thinking_level="standard",
                               duration_seconds=None, aspect_ratio="9:16", delete_after_seconds=None)
    assert video_options_error(media_type=OMNI_VIDEO_MODEL and "video", model=OMNI_VIDEO_MODEL,
                               thinking_level="standard", duration_seconds=3, delete_after_seconds=None)


def test_video_options_reach_generation_scope_and_unsupported_client_refuses():
    events = []

    class VideoClient:
        @contextmanager
        def video_generation_scope(self, *, duration_seconds=None, aspect_ratio=None):
            events.append(("enter", duration_seconds, aspect_ratio))
            try:
                yield
            finally:
                events.append(("exit",))

        async def generate_content_stream(self, **arguments):
            events.append(("generate",))
            yield SimpleNamespace(metadata=["c_video", "r_video"], rcid="rc_video")

    class OldVideoClient(VideoClient):
        @contextmanager
        def video_generation_scope(self):
            yield

    options = dict(arguments={}, schedule_cleanup=lambda *a, **kw: CleanupObservation(
        state=CleanupState.PENDING, upstream_chat_id="c_video"), retain_chat=False,
        delete_after_seconds=None, source="test", media_type="video", duration_seconds=15, aspect_ratio="9:16")
    asyncio.run(generate_media_response(VideoClient(), **options))
    assert events == [("enter", 15, "9:16"), ("generate",), ("exit",)]
    events.clear()
    with pytest.raises(VideoModeUnavailable, match="duration_seconds"):
        asyncio.run(generate_media_response(OldVideoClient(), **options))
    assert events == []


@pytest.mark.parametrize("media_type", ["image", "music"])
def test_media_generation_enters_only_the_matching_native_scope(media_type):
    events = []

    class NativeClient:
        @contextmanager
        def image_generation_scope(self):
            events.append(("enter", "image"))
            try:
                yield
            finally:
                events.append(("exit", "image"))

        @contextmanager
        def music_generation_scope(self):
            events.append(("enter", "music"))
            try:
                yield
            finally:
                events.append(("exit", "music"))

        async def generate_content_stream(self, **arguments):
            assert events == [("enter", media_type)]
            events.append(("generate", media_type))
            yield SimpleNamespace(metadata=["c_native", "r_native"], rcid="rc_native")

    asyncio.run(generate_media_response(
        NativeClient(), arguments={}, schedule_cleanup=lambda *a, **kw: CleanupObservation(
            state=CleanupState.PENDING, upstream_chat_id="c_native"), retain_chat=False,
        delete_after_seconds=None, source="test", media_type=media_type,
    ))
    assert events == [("enter", media_type), ("generate", media_type), ("exit", media_type)]


def test_web_video_options_have_primary_full_and_compact_parity(monkeypatch):
    from src import skill_server
    from src.adapters import MCPServer
    from src.services import background
    from src.surfaces import function
    from src.tools.media import register_media_tools

    handle = "op_" + "a" * 32
    submit = AsyncMock(return_value=DomainResult.success(LongOperationData(
        operation="video", state=OperationState.ACCEPTED, operation_id=handle)))
    monkeypatch.setattr(function, "submit_media", submit)
    monkeypatch.setattr(background, "submit_media", submit)
    primary = MCPServer("video-options-compatibility")
    register_media_tools(primary)

    async def run():
        for server, tool, extra, send_model in (
            (function.mcp, "video", {"wait_seconds": 0}, False),
            (function.build_server(full=True), "gemini_generate_video", {"wait_seconds": 0}, True),
            (primary, "gemini_generate_media", {"media_type": "video"}, True),
            (skill_server.mcp, "create", {"type": "video"}, True),
        ):
            async with Client(server, mode="auto", cache=None) as client:
                registered = {item.name: item for item in (await client.list_tools()).tools}[tool]
                assert registered.annotations.read_only_hint is False
                assert {"duration_seconds", "aspect_ratio"} <= registered.input_schema["properties"].keys()
                payload = {
                    "prompt": "A synthetic video",
                    "duration_seconds": 15,
                    "aspect_ratio": "9:16",
                    **extra,
                }
                if send_model:
                    payload["model"] = "hydrogen"
                else:
                    assert "model" not in registered.input_schema["properties"]
                response = await client.call_tool(tool, payload)
                assert domain_result_from_call(response)["data"]["operation_id"] == handle

    asyncio.run(run())
    assert submit.await_count == 4
    for call in submit.await_args_list:
        assert call.args[0]["duration_seconds"] == 15
        assert call.args[0]["aspect_ratio"] == "9:16"
        assert call.args[0]["model"] == "hydrogen"


def test_background_video_validates_controls_before_dispatch(monkeypatch, tmp_path):
    from src.services import background

    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-account")
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite3"))
    launch = Mock(return_value=123)
    monkeypatch.setattr(background, "_launch_detached_worker", launch)

    async def run():
        invalid = await background.submit_media({"media_type": "video", "prompt": "synthetic",
                                                "duration_seconds": 15, "aspect_ratio": "square"}, operation="video")
        assert not invalid.ok and invalid.error.code.value == "INVALID_ARGUMENT"
        launch.assert_not_called()
        accepted = await background.submit_media({"media_type": "video", "prompt": "synthetic",
                                                 "duration_seconds": 15, "aspect_ratio": "9:16"}, operation="video")
        assert accepted.ok

    asyncio.run(run())
    launch.assert_called_once()
    assert launch.call_args.args[0]["request"]["duration_seconds"] == 15
    assert launch.call_args.args[0]["request"]["aspect_ratio"] == "9:16"


def test_worker_passes_web_controls_to_generation_without_replacing_references(monkeypatch, tmp_path):
    from src import client_wrapper
    from src.infrastructure.operation_store import OperationStore
    from src.services import media_jobs

    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    settings = resolve_media_request("hydrogen", "video", "standard")
    store.create_or_get("job", "video", media_type="video", output_dir=str(tmp_path), filename="job",
                        request_model=settings["request_model"], effective_backend=settings["backend_label"])
    assert store.claim_worker("job", "worker")
    generate = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(media_jobs, "generate_media_response", generate)
    monkeypatch.setattr(media_jobs, "_save_response", AsyncMock())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: SimpleNamespace())
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    asyncio.run(media_jobs.execute_media_job({"prompt": "synthetic", "model": "hydrogen", "media_type": "video",
                                             "duration_seconds": 15, "aspect_ratio": "9:16",
                                             "output_dir": str(tmp_path)},
                                            operation_id="job", store=store, worker_token="worker"))
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["duration_seconds"] == 15
    assert generate.await_args.kwargs["aspect_ratio"] == "9:16"
    assert generate.await_args.kwargs["arguments"]["model"] == settings["request_model"]


def test_inline_wait_reads_the_original_media_handle_without_resubmitting(monkeypatch):
    from src.surfaces import function
    from src.tools import operations

    handle = "op_" + "b" * 32
    submit = AsyncMock(return_value=DomainResult.success(LongOperationData(
        operation="video", state=OperationState.ACCEPTED, operation_id=handle)))
    waited = DomainResult.success(LongOperationData(operation="video", state=OperationState.RUNNING,
                                                    operation_id=handle))
    get_result = AsyncMock(return_value=waited)
    service = SimpleNamespace(store=SimpleNamespace(get=lambda operation_id: object()), get_result=get_result)
    monkeypatch.setattr(function, "submit_media", submit)
    monkeypatch.setattr(operations, "_service", lambda: service)
    present = Mock(return_value=[])
    monkeypatch.setattr(operations, "_present", present)
    asyncio.run(function.gemini_generate_video(prompt="synthetic", wait_seconds=35))
    submit.assert_awaited_once()
    get_result.assert_awaited_once_with(handle, wait_seconds=35)
    present.assert_called_once_with(waited, include_text=False)


def test_result_wait_does_not_read_remote_while_original_worker_is_active(monkeypatch, tmp_path):
    from src.infrastructure.operation_store import OperationStore
    from src.services.operations import OperationService

    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create_or_get("job", "video", media_type="video")
    assert store.claim_worker("job", "worker")
    initialize = AsyncMock()
    service = OperationService(store, lambda: object(), initialize)
    read = AsyncMock()
    monkeypatch.setattr(service, "_read", read)
    result = asyncio.run(service.get_result("job", wait_seconds=1))
    assert result.ok and result.data.verification_status == "wait_elapsed"
    assert result.data.worker_active and result.data.continuation_possible
    read.assert_not_awaited()
    initialize.assert_not_awaited()
    assert store.get("job").worker_token == "worker"


def test_result_wait_returns_task_input_without_waiting_for_worker_exit(monkeypatch, tmp_path):
    from src.infrastructure.operation_store import OperationStore
    from src.services.operations import OperationService

    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create_or_get("job", "gemini_run_task")
    assert store.claim_worker("job", "worker")
    store.update("job", state="partial", phase="task_input_required", verification_status="task_input_required")
    service = OperationService(store, lambda: object(), AsyncMock())
    answer = DomainResult.success(OperationReadData(
        "job", "gemini_run_task", OperationState.PARTIAL, True, False,
        "task_input_required", input_required=True, worker_active=True,
    ))
    read = AsyncMock(return_value=answer)
    monkeypatch.setattr(service, "_read", read)
    result = asyncio.run(service.get_result("job", wait_seconds=1))
    assert result is answer and result.data.input_required
    read.assert_awaited_once_with("job", include_report=True)


def test_media_checkpoint_persists_dispatched_selector_without_claiming_generator(monkeypatch, tmp_path):
    from src.infrastructure.operation_store import OperationStore
    from src.services.media_jobs import _checkpoint
    from src.services.model_policy import ModelSelection

    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create_or_get("job", "image", media_type="image", request_model="gemini-3-flash",
                        effective_backend="Gemini Web image mode")
    assert store.claim_worker("job", "worker")
    response = SimpleNamespace(metadata=["c_video", "r_video"], rcid="rc_video",
                               _mcp_model_selection=ModelSelection(
                                   "flash", "gemini-4-hydrogen", None, True, "standard"))
    _checkpoint(store, "job", "worker", response)
    record = store.get("job")
    assert record.request_model == "gemini-4-hydrogen"
    assert record.requested_model is None
    assert record.effective_backend == "Gemini Web image mode"
    assert record.upstream_candidate_id == "rc_video"
    assert not store.checkpoint_media("job", worker_token="other", chat_id="c_video", selected_model="gemini-neon")
    assert store.get("job").request_model == "gemini-4-hydrogen"
