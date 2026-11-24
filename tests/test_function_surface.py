"""The functional surface stays small and delegates to shared workflows."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from gemini_webapi.exceptions import TimeoutError as SDKTimeoutError
from mcp import Client

from src.domain import CleanupObservation, CleanupState, DomainResult, LongOperationData, OperationState
from src.onboarding import domain_result_from_call
from src.services import ChatOperationData, ChatService, ChatServiceDependencies
from src.services.cleanup_scope import observe_upstream_identity
from src.session_manager import SessionService
from src.surfaces import assist, function


EXPECTED = {
    "gemini_ask", "gemini_research", "gemini_run_task", "gemini_create_media",
    "gemini_get_operation_status", "gemini_get_operation_result",
}
EXPECTED_FULL = {
    "gemini_ask", "gemini_understand_image", "gemini_understand", "gemini_research",
    "gemini_start_chat", "gemini_send_message", "gemini_list_sessions", "gemini_reset_session",
    "gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_music",
    "gemini_get_operation_status", "gemini_get_operation_result",
    "gemini_list_research_report_actions", "gemini_create_from_research_report",
    "gemini_run_task",
}


@pytest.mark.parametrize("full", [False, True])
def test_function_catalog_and_safety_annotations(full):
    async def run():
        server = function.build_server(full=True) if full else function.mcp
        async with Client(server, mode="auto", cache=None) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}
    tools = asyncio.run(run())
    assert set(tools) == (EXPECTED_FULL if full else EXPECTED)
    assert all((tool.input_schema.get("additionalProperties") is False) is (not full) for tool in tools.values())
    ask_properties = tools["gemini_ask"].input_schema["properties"]
    assert ("start_session" in ask_properties and "inputs" in ask_properties) is (not full)
    assert ask_properties["model"]["default"] == ("flash" if full else None)
    assert not {"web_search", "enable_tools", "tools_enabled", "enable_apps"} & ask_properties.keys()
    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["gemini_get_operation_status"].annotations.read_only_hint is True
    assert tools["gemini_get_operation_result"].annotations.destructive_hint is True
    assert tools["gemini_research"].annotations.destructive_hint is True
    assert tools["gemini_run_task"].annotations.destructive_hint is True
    assert tools["gemini_run_task"].input_schema["properties"]["model"]["default"] == "flash"
    media_tool = tools["gemini_generate_video" if full else "gemini_create_media"]
    assert media_tool.input_schema["properties"]["model"]["default"] == ("flash" if full else None)
    assert "duration_seconds" in media_tool.input_schema["properties"]
    assert "aspect_ratio" in media_tool.input_schema["properties"]
    assert media_tool.input_schema["properties"]["thinking_level"]["default"] == "standard"
    assert media_tool.annotations.read_only_hint is False
    image_tool = tools["gemini_generate_image" if full else "gemini_create_media"]
    assert {"image_paths", "source_operation_id", "source_artifact_id"} <= image_tool.input_schema["properties"].keys()
    assert image_tool.annotations.read_only_hint is False
    research_properties = tools["gemini_research"].input_schema["properties"]
    assert research_properties["thinking_level"]["default"] == "standard"
    assert ("include_text" in research_properties) is full
    assert ("action" in research_properties and "background" in research_properties) is full


def test_creation_wrappers_submit_only_background_jobs(monkeypatch, tmp_path):
    handle = "op_" + "a" * 32
    submit = AsyncMock(return_value=DomainResult.success(
        LongOperationData(operation="test", state=OperationState.ACCEPTED, operation_id=handle),
        operation_state=OperationState.ACCEPTED,
    ))
    monkeypatch.setattr(function, "submit_media", submit)
    image = tmp_path / "input.png"
    image.write_bytes(b"fixture")
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            results = []
            for kind, arguments in (
                ("image", {"prompt": "image"}),
                ("edit_image", {"prompt": "edit", "image_path": str(image)}),
                ("video", {"prompt": "video"}),
                ("video", {"prompt": "video", "model": "argon"}),
                ("music", {"prompt": "music"}),
                ("music", {"prompt": "music", "thinking_level": "extended"}),
            ):
                results.append(await client.call_tool("gemini_create_media", {"kind": kind, **arguments}))
            return results
    responses = asyncio.run(run())
    assert submit.await_count == 6
    assert [call.args[0]["media_type"] for call in submit.await_args_list] == ["image", "image", "video", "video", "music", "music"]
    assert [call.args[0]["edit"] for call in submit.await_args_list] == [False, True, False, False, False, False]
    assert [call.kwargs["operation"] for call in submit.await_args_list] == [
        "gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_video",
        "gemini_generate_music", "gemini_generate_music",
    ]
    assert [submit.await_args_list[i].args[0]["model"] for i in (2, 3)] == [None, "argon"]
    assert [submit.await_args_list[i].args[0]["thinking_level"] for i in (4, 5)] == ["standard", "extended"]
    assert all(handle in result.content[0].text for result in responses)
    assert all(domain_result_from_call(result)["ok"] is True for result in responses)


@pytest.mark.parametrize("arguments", [
    {"kind": "image", "prompt": " ", "filename": "../bad"},
    {"kind": "edit_image", "prompt": "edit"},
    {"kind": "music", "prompt": "music", "image_path": "/tmp/image.png"},
    {"kind": "video", "prompt": "video", "image_paths": ["/tmp/image.png"]},
    {"kind": "edit_image", "prompt": "edit", "image_path": "/tmp/legacy.png", "image_paths": ["/tmp/image.png"]},
    {"kind": "edit_image", "prompt": "edit", "source_artifact_id": "artifact_without_parent"},
    {"kind": "image", "prompt": "image", "image_paths": []},
])
def test_invalid_creation_input_does_not_dispatch(monkeypatch, arguments):
    submit = AsyncMock()
    monkeypatch.setattr(function, "submit_media", submit)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_create_media", arguments)
    result = asyncio.run(run())
    assert domain_result_from_call(result)["ok"] is False
    submit.assert_not_awaited()


def test_image_workflow_fields_reach_the_same_worker_from_each_tool_surface(monkeypatch):
    from src import skill_server
    from src.adapters import MCPServer
    from src.services import background
    from src.tools.media import register_media_tools

    primary = MCPServer(name="Image media compatibility")
    register_media_tools(primary)
    source_handle = "op_" + "b" * 32
    handle = "op_" + "a" * 32
    submit = AsyncMock(return_value=DomainResult.success(
        LongOperationData(operation="test", state=OperationState.ACCEPTED, operation_id=handle),
    ))
    monkeypatch.setattr(function, "submit_media", submit)
    monkeypatch.setattr(background, "submit_media", submit)
    arguments = {"prompt": "Edit the saved image with references.", "image_paths": ["one.png", "two.png"],
                 "source_operation_id": source_handle, "source_artifact_id": "artifact_test"}

    async def run():
        for server, tool, extra in (
            (function.mcp, "gemini_create_media", {"kind": "edit_image"}),
            (function.build_server(full=True), "gemini_generate_image", {}),
            (primary, "gemini_generate_media", {"media_type": "image"}),
            (skill_server.mcp, "create", {"type": "image"}),
        ):
            async with Client(server, mode="auto", cache=None) as client:
                registered = {item.name: item for item in (await client.list_tools()).tools}[tool]
                assert registered.annotations.read_only_hint is False
                response = await client.call_tool(tool, {**arguments, **extra})
                assert domain_result_from_call(response)["data"]["operation_id"] == handle
                if server is primary or server is skill_server.mcp:
                    sync = await client.call_tool(tool, {**arguments, **extra, "background": False})
                    assert domain_result_from_call(sync)["error"]["code"] == "INVALID_ARGUMENT"

    asyncio.run(run())
    assert submit.await_count == 4
    for call in submit.await_args_list:
        assert call.args[0]["media_type"] == "image"
        assert all(call.args[0][key] == value for key, value in arguments.items())


def test_function_catalog_over_real_stdio(tmp_path):
    import os
    import sys
    from pathlib import Path
    from mcp import StdioServerParameters, stdio_client

    environment = os.environ.copy()
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC", "GEMINI_API_KEY"):
        environment.pop(name, None)
    environment.update(GEMINI_BROWSER_AUTO_LOAD="false", GEMINI_AUTO_REFRESH="false",
                       GEMINI_OPERATION_DB_PATH=str(tmp_path / "operations.sqlite"),
                       GEMINI_CLEANUP_DB_PATH=str(tmp_path / "cleanup.sqlite"))
    params = StdioServerParameters(command=sys.executable,
                                   args=["-c", "from src.surfaces.function import mcp; mcp.run()"],
                                   env=environment, cwd=Path(__file__).resolve().parents[1])
    async def run():
        async with Client(stdio_client(params), mode="auto", cache=None) as client:
            missing_key = await client.call_tool("gemini_create_media", {
                "kind": "video", "prompt": "A raindrop on glass.", "model": "gemini-omni-1.2-flash",
                "duration_seconds": 15,
            })
            invalid_duration = await client.call_tool("gemini_create_media", {
                "kind": "video", "prompt": "A raindrop on glass.", "model": "gemini-omni-1.2-flash",
                "duration_seconds": 16,
            })
            paid_model_wrong_kind = await client.call_tool("gemini_create_media", {
                "kind": "image", "prompt": "A raindrop on glass.", "model": "gemini-omni-1.2-flash",
            })
            invalid_image_sources = await client.call_tool("gemini_create_media", {
                "kind": "edit_image", "prompt": "Edit an exact image.", "source_artifact_id": "no_parent",
            })
            legacy_research_read = await client.call_tool("gemini_research", {
                "query": "Original synthetic query", "operation_id": "op_" + "a" * 32, "action": "result",
            })
            assert domain_result_from_call(missing_key)["error"]["code"] == "INVALID_ARGUMENT"
            assert domain_result_from_call(invalid_duration)["error"]["code"] == "INVALID_ARGUMENT"
            assert domain_result_from_call(paid_model_wrong_kind)["error"]["code"] == "INVALID_ARGUMENT"
            assert domain_result_from_call(invalid_image_sources)["error"]["code"] == "INVALID_ARGUMENT"
            assert legacy_research_read.is_error
            return {tool.name for tool in (await client.list_tools()).tools}
    assert asyncio.run(run()) == EXPECTED


def test_function_notebook_question_dispatches_background_chat_without_task(monkeypatch):
    from src.services import background
    submit = AsyncMock(return_value=DomainResult.success(
        LongOperationData(operation="gemini_notebook_chat", state=OperationState.ACCEPTED,
                          operation_id="op_" + "a" * 32, execution_mode="chat")))
    monkeypatch.setattr(background, "submit_notebook_chat", submit)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert tools["gemini_ask"].input_schema["properties"]["timeout_seconds"]["default"] == 600
            return await client.call_tool("gemini_ask", {"prompt": "read sources", "notebook_ids": ["notebooks/id"]})
    data = domain_result_from_call(asyncio.run(run()))
    assert data["ok"] and data["data"]["execution_mode"] == "chat"
    submit.assert_awaited_once()
    assert submit.await_args.args[0]["notebook_ids"] == ["notebooks/id"]
    assert submit.await_args.args[0]["timeout_seconds"] == 600


@pytest.mark.parametrize("legacy_arguments", [{"action": "result"}, {"action": "status"}, {"background": False}])
def test_lean_research_rejects_legacy_modes_before_submission(monkeypatch, legacy_arguments):
    from src.services import background

    submit = AsyncMock()
    monkeypatch.setattr(background, "submit_research", submit)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_research", {
                "query": "Original query", "operation_id": "op_" + "a" * 32, **legacy_arguments,
            })
    assert asyncio.run(run()).is_error
    submit.assert_not_awaited()


def test_function_ask_keeps_plain_flash_and_observed_grounding(monkeypatch):
    reply = SimpleNamespace(
        text="synthetic answer", citations=[{"url": "https://source.example/", "title": "Source"}],
        metadata=["c_test", "r_test"], rcid="rc_test", images=[], videos=[], media=[],
    )
    generate = AsyncMock(return_value=DomainResult.success(
        ChatOperationData("flash", "flash", "gemini-flash", response=reply, responses=(reply,)),
    ))
    monkeypatch.setattr(assist._chat_service, "generate_stream", generate)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_ask", {"prompt": "Use the web to find a source."})
    result = domain_result_from_call(asyncio.run(run()))
    assert result["ok"] and result["data"]["answer"] == "synthetic answer"
    assert result["data"]["grounding_state"] == "grounded"
    assert result["data"]["sources"][0]["url"] == "https://source.example/"
    assert "session_id" not in result["data"]
    request = generate.await_args.args[0]
    assert (request.model, request.thinking_level, request.retain_chat) == ("flash", "standard", False)


def test_function_ask_routes_mixed_inputs_and_retention_to_shared_understanding(monkeypatch, tmp_path):
    image = tmp_path / "input.png"
    image.write_bytes(b"fixture")
    generate = AsyncMock(return_value=DomainResult.success(
        ChatOperationData("flash", "flash", "gemini-flash",
                          response=SimpleNamespace(text="Compared [image] and [note].")),
    ))
    monkeypatch.setattr(assist._chat_service, "generate", generate)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_ask", {
                "prompt": "Compare", "context": "Synthetic test",
                "inputs": [
                    {"id": "image", "kind": "image", "path": str(image)},
                    {"id": "note", "kind": "text", "text": "Synthetic note"},
                ],
                "retain_chat": True, "delete_after_seconds": 30,
            })
    result = domain_result_from_call(asyncio.run(run()))
    assert result["ok"] and result["data"]["analysis"] == "Compared [image] and [note]."
    assert [(item["id"], item["outcome"]) for item in result["data"]["inputs"]] == [
        ("image", "analyzed"), ("note", "analyzed"),
    ]
    request = generate.await_args.args[0]
    assert request.files == (str(image.resolve()),)
    assert request.retain_chat and request.delete_after_seconds == 30
    assert request.cleanup_source == "gemini_ask" and "Synthetic test" in request.message


def test_function_ask_recovers_the_original_turn_only_for_its_owner(monkeypatch):
    account = ["test-owner"]
    upstream = SimpleNamespace(timeout=30, watchdog_timeout=30)
    chat = SimpleNamespace(geminiclient=upstream, cid="", metadata=[])
    upstream.start_chat = Mock(return_value=chat)
    sessions = SessionService()
    metadata = ("c_turn", "r_turn", "rc_turn")

    async def time_out(_session_id, **_kwargs):
        observe_upstream_identity(SimpleNamespace(cid=metadata[0], metadata=metadata))
        raise SDKTimeoutError("synthetic timeout")

    send = AsyncMock(side_effect=time_out)
    read = AsyncMock(return_value=None)
    service = ChatService(ChatServiceDependencies(
        client_provider=lambda: upstream, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        create_session=sessions.create_session, lookup_session=sessions.lookup_session,
        send_session_message=AsyncMock(), send_session_message_stream=send,
        schedule_response_cleanup=lambda *_a, **_k: None,
        schedule_chat_cleanup=lambda cid, **_k: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        normalize_model=lambda value: value, resolve_model=lambda value: value,
        account_key_provider=lambda: account[0],
    ))
    monkeypatch.setattr(assist, "_chat_service", service)
    monkeypatch.setattr("src.services.web_context_transport.fetch_workflow_turn", read)

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            async def ask(arguments):
                return domain_result_from_call(await client.call_tool("gemini_ask", arguments))

            first = await ask({"prompt": "First message", "start_session": True, "model": "pro",
                               "thinking_level": "high", "retain_chat": True})
            assert first["error"]["code"] == "TIMED_OUT"
            session_id = first["data"]["session_id"]
            assert session_id.startswith("sess_")
            assert read.await_count == 1 and send.await_count == 1
            assert send.await_args.kwargs["thinking_level"] == "high"
            upstream.start_chat.assert_called_once_with(model="pro")
            account[0] = "different-owner"
            rejected = await ask({"session_id": session_id, "recover_only": True})
            assert rejected["error"]["code"] == "AUTH_REQUIRED"
            assert read.await_count == 1 and send.await_count == 1
            account[0] = "test-owner"
            blocked = await ask({"session_id": session_id, "prompt": "Do not repeat the first turn"})
            assert blocked["meta"]["verification_status"] == "session_reply_unverified"
            read.return_value = {
                "chat_id": metadata[0], "rid": metadata[1], "rcid": metadata[2],
                "completed": True, "text": "original answer",
            }
            recovered = await ask({"session_id": session_id, "recover_only": True})
            assert recovered["ok"] and recovered["data"]["answer"] == "original answer"
            assert recovered["data"]["session_id"] == session_id
            assert recovered["data"]["lifecycle"]["retain_chat"]
            assert recovered["meta"]["verification_status"] == "original_session_reply_verified"
            assert read.await_count == 2 and send.await_count == 1
            assert all(call.args == (upstream, *metadata) for call in read.await_args_list)

    asyncio.run(run())


@pytest.mark.parametrize("arguments", [
    {"prompt": "combined", "notebook_ids": ["notebooks/id"], "start_session": True},
    {"prompt": "combined", "notebook_ids": ["notebooks/id"],
     "inputs": [{"id": "note", "kind": "text", "text": "fixture"}]},
    {"prompt": "combined", "session_id": "sess_original", "model": "pro"},
    {"prompt": "replay", "session_id": "sess_original", "recover_only": True},
    {"recover_only": True},
    {"prompt": "not background", "operation_id": "op_" + "a" * 32},
])
def test_function_ask_rejects_conflicting_modes_before_dispatch(monkeypatch, arguments):
    from src.services import background

    service = AsyncMock(spec=ChatService)
    understand = AsyncMock()
    notebook = AsyncMock()
    monkeypatch.setattr(assist, "_chat_service", service)
    monkeypatch.setattr(assist._understand_service, "understand", understand)
    monkeypatch.setattr(background, "submit_notebook_chat", notebook)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_ask", arguments)
    result = domain_result_from_call(asyncio.run(run()))
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    service.start_session.assert_not_awaited()
    service.send_session_stream.assert_not_awaited()
    service.generate_stream.assert_not_awaited()
    understand.assert_not_awaited()
    notebook.assert_not_awaited()


def test_function_create_media_uses_modality_wait_defaults_and_new_options(monkeypatch):
    from src.tools import operations as op_tools

    submitted = []
    waits = []

    async def fake_submit(request, *, operation, operation_id=None):
        submitted.append(request)
        return DomainResult.success(
            LongOperationData(operation=operation, state=OperationState.ACCEPTED, operation_id="op_" + "b" * 32)
        )

    class FakeService:
        store = SimpleNamespace(get=lambda handle: SimpleNamespace(operation_id=handle))

        async def get_result(self, handle, wait_seconds=0):
            waits.append((handle, wait_seconds))
            return DomainResult.success(
                LongOperationData(
                    operation="gemini_generate_media",
                    state=OperationState.COMPLETED,
                    operation_id=handle,
                )
            )

    monkeypatch.setattr(function, "submit_media", fake_submit)
    monkeypatch.setattr(op_tools, "_service", lambda: FakeService())

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            await client.call_tool("gemini_create_media", {"kind": "image", "prompt": "landscape", "aspect_ratio": "16:9"})
            await client.call_tool("gemini_create_media", {
                "kind": "music", "prompt": "folk tune", "music_duration": "short", "music_genre": "acoustic",
            })
            await client.call_tool("gemini_create_media", {"kind": "video", "prompt": "waves", "duration_seconds": 10})
            await client.call_tool("gemini_create_media", {"kind": "music", "prompt": "async", "wait_seconds": 0})
            rejected = await client.call_tool("gemini_create_media", {
                "kind": "video", "prompt": "paid", "model": "gemini-omni-1.2-flash",
            })
            assert domain_result_from_call(rejected)["error"]["code"] == "INVALID_ARGUMENT"

    asyncio.run(run())
    assert [wait for _, wait in waits] == [120, 240, 300]
    assert submitted[0]["aspect_ratio"] == "16:9"
    assert submitted[1]["music_genre"] == "acoustic"
    assert len(submitted) == 4


def test_function_ask_supports_gem_id_and_learning_mode_and_research_report_actions(monkeypatch, tmp_path):
    from src.domain import DomainErrorCode

    captured = []

    class FakeChatService:
        async def generate_stream(self, request):
            captured.append(request)
            return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "stop-after-capture")

    monkeypatch.setattr(assist, "_chat_service", FakeChatService())
    handle = "op_" + "c" * 32

    async def fake_actions(*, operation_id, report_action, output_dir):
        captured.append(("research_action", operation_id, report_action, output_dir))
        return [function.TextContent(type="text", text=f"action:{report_action}")]

    monkeypatch.setattr(assist, "_handle_research_report_action", fake_actions)

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            await client.call_tool("gemini_ask", {
                "prompt": "Quiz me", "gem_id": "gem_123", "learning_mode": "quiz",
            })
            res = await client.call_tool("gemini_research", {
                "operation_id": handle, "report_action": "quiz", "output_dir": str(tmp_path),
            })
            assert res.content[0].text == "action:quiz"
            bad = await client.call_tool("gemini_research", {
                "query": "cannot combine", "operation_id": handle, "report_action": "quiz",
            })
            assert domain_result_from_call(bad)["error"]["code"] == "INVALID_ARGUMENT"

    asyncio.run(run())
    assert captured[0].gem_id == "gem_123"
    assert captured[0].learning_mode == "quiz"
    assert captured[0].include_gem_argument is True
    assert captured[1] == ("research_action", handle, "quiz", str(tmp_path))
