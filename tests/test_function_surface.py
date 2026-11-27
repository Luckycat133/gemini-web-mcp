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
    "ask-gemini",
    "generate-image",
    "generate-video",
    "generate-music",
    "run-research",
    "run-task",
    "get-operation",
    "check-quota",
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
    ask_name = "gemini_ask" if full else "ask-gemini"
    ask_properties = tools[ask_name].input_schema["properties"]
    assert ("start_session" in ask_properties) is (not full)
    if full:
        assert "inputs" not in ask_properties
        assert "inputs" in tools["gemini_understand"].input_schema["properties"]
    else:
        assert "inputs" in ask_properties
        assert "understand" not in tools
    assert ask_properties["model"]["default"] == ("flash" if full else None)
    assert not {"web_search", "enable_tools", "tools_enabled", "enable_apps"} & ask_properties.keys()
    assert all(tool.annotations is not None for tool in tools.values())
    if full:
        assert tools["gemini_get_operation_status"].annotations.read_only_hint is True
        assert tools["gemini_get_operation_result"].annotations.destructive_hint is True
        assert tools["gemini_run_task"].annotations.destructive_hint is True
        assert tools["gemini_run_task"].input_schema["properties"]["model"]["default"] == "flash"
        assert tools["gemini_generate_video"].input_schema["properties"]["thinking_level"]["default"] == "standard"
        edit_tool = tools["gemini_edit_image"]
        assert {"image_path", "image_paths", "source_operation_id", "source_artifact_id"} <= edit_tool.input_schema["properties"].keys()
    else:
        assert tools["get-operation"].annotations.destructive_hint is True
        assert tools["run-task"].annotations.destructive_hint is True
        assert tools["run-task"].input_schema["properties"]["model"]["default"] == "hydrogen"
        assert tools["run-task"].input_schema["properties"]["thinking_level"]["default"] == "medium"
        assert "model" not in tools["generate-video"].input_schema["properties"]
        assert "duration_seconds" in tools["generate-video"].input_schema["properties"]
        assert "aspect_ratio" in tools["generate-video"].input_schema["properties"]
        assert {"image_paths", "source_operation_id", "source_artifact_id"} <= tools["generate-image"].input_schema["properties"].keys()
        assert tools["generate-image"].annotations.read_only_hint is False
        assert tools["check-quota"].annotations.read_only_hint is True
    assert tools["gemini_research" if full else "run-research"].annotations.destructive_hint is True
    research_properties = tools["gemini_research" if full else "run-research"].input_schema["properties"]
    assert research_properties["thinking_level"]["default"] == ("standard" if full else "medium")
    assert ("include_text" in research_properties) is full
    assert ("action" in research_properties and "background" in research_properties) is full


def test_creation_wrappers_submit_only_background_jobs(monkeypatch, tmp_path):
    handle = "op_" + "a" * 32
    source_handle = "op_" + "b" * 32
    submit = AsyncMock(return_value=DomainResult.success(
        LongOperationData(operation="test", state=OperationState.ACCEPTED, operation_id=handle),
        operation_state=OperationState.ACCEPTED,
    ))
    monkeypatch.setattr(function, "submit_media", submit)
    image_file = tmp_path / "input.png"
    image_file.write_bytes(b"fixture")
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            results = []
            for tool_name, arguments in (
                ("generate-image", {"prompt": "image"}),
                ("generate-image", {"prompt": "edit", "source_operation_id": source_handle}),
                ("generate-video", {"prompt": "video"}),
                ("generate-music", {"prompt": "music", "duration": "short", "vocals": "instrumental", "genre": "acoustic"}),
            ):
                results.append(await client.call_tool(tool_name, arguments))
            return results
    responses = asyncio.run(run())
    assert submit.await_count == 4
    assert [call.args[0]["media_type"] for call in submit.await_args_list] == ["image", "image", "video", "music"]
    assert [call.args[0]["edit"] for call in submit.await_args_list] == [False, True, False, False]
    assert [call.kwargs["operation"] for call in submit.await_args_list] == [
        "gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_music",
    ]
    assert [call.args[0]["model"] for call in submit.await_args_list] == ["hydrogen"] * 4
    assert submit.await_args_list[3].args[0]["thinking_level"] == "standard"
    assert submit.await_args_list[3].args[0]["music_genre"] == "acoustic"
    assert all(handle in result.content[0].text for result in responses)
    assert all(domain_result_from_call(result)["ok"] is True for result in responses)


@pytest.mark.parametrize(("tool_name", "arguments"), [
    ("generate-image", {"prompt": " ", "filename": "../bad"}),
    ("generate-image", {"prompt": "edit", "source_artifact_id": "artifact_without_parent"}),
    ("generate-image", {"prompt": "image", "image_paths": []}),
    ("generate-video", {"prompt": "video", "image_path": "/tmp/frame.png", "duration_seconds": 15}),
])
def test_invalid_creation_input_does_not_dispatch(monkeypatch, tool_name, arguments):
    submit = AsyncMock()
    monkeypatch.setattr(function, "submit_media", submit)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool(tool_name, arguments)
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
            (function.mcp, "generate-image", {}),
            (function.build_server(full=True), "gemini_edit_image", {}),
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
            invalid_duration = await client.call_tool("generate-video", {
                "prompt": "A raindrop on glass.", "duration_seconds": 16,
            })
            invalid_image_ref_duration = await client.call_tool("generate-video", {
                "prompt": "A raindrop on glass.", "image_path": "/tmp/frame.png", "duration_seconds": 15,
            })
            invalid_image_sources = await client.call_tool("generate-image", {
                "prompt": "Edit an exact image.", "source_artifact_id": "no_parent",
            })
            legacy_research_read = await client.call_tool("run-research", {
                "query": "Original synthetic query", "operation_id": "op_" + "a" * 32, "action": "result",
            })
            assert invalid_duration.is_error
            assert domain_result_from_call(invalid_image_ref_duration)["error"]["code"] == "INVALID_ARGUMENT"
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
            assert "inputs" in tools["ask-gemini"].input_schema["properties"]
            return await client.call_tool("ask-gemini", {"prompt": "read sources", "notebook_ids": ["notebooks/id"]})
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
            return await client.call_tool("run-research", {
                "query": "Original query", "operation_id": "op_" + "a" * 32, **legacy_arguments,
            })
    assert asyncio.run(run()).is_error
    submit.assert_not_awaited()


def test_function_ask_keeps_plain_hydrogen_medium_and_observed_grounding(monkeypatch):
    reply = SimpleNamespace(
        text="synthetic answer", citations=[{"url": "https://source.example/", "title": "Source"}],
        metadata=["c_test", "r_test"], rcid="rc_test", images=[], videos=[], media=[],
    )
    generate = AsyncMock(return_value=DomainResult.success(
        ChatOperationData("hydrogen", "hydrogen", "gemini-4-hydrogen", response=reply, responses=(reply,)),
    ))
    monkeypatch.setattr(assist._chat_service, "generate_stream", generate)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("ask-gemini", {"prompt": "Use the web to find a source."})
    result = domain_result_from_call(asyncio.run(run()))
    assert result["ok"] and result["data"]["answer"] == "synthetic answer"
    assert result["data"]["grounding_state"] == "grounded"
    assert result["data"]["sources"][0]["url"] == "https://source.example/"
    assert "session_id" not in result["data"]
    request = generate.await_args.args[0]
    assert (request.model, request.thinking_level, request.retain_chat) == ("hydrogen", "medium", False)


def test_function_understand_routes_mixed_inputs_and_retention_to_shared_understanding(monkeypatch, tmp_path):
    image_file = tmp_path / "input.png"
    image_file.write_bytes(b"fixture")
    generate = AsyncMock(return_value=DomainResult.success(
        ChatOperationData("hydrogen", "hydrogen", "gemini-4-hydrogen",
                          response=SimpleNamespace(text="Compared [image] and [note].")),
    ))
    monkeypatch.setattr(assist._chat_service, "generate", generate)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("ask-gemini", {
                "prompt": "Compare",
                "inputs": [
                    {"id": "image", "kind": "image", "path": str(image_file)},
                    {"id": "note", "kind": "text", "text": "Synthetic note"},
                ],
                "retain_chat": True,
            })
    result = domain_result_from_call(asyncio.run(run()))
    assert result["ok"] and result["data"]["analysis"] == "Compared [image] and [note]."
    assert [(item["id"], item["outcome"]) for item in result["data"]["inputs"]] == [
        ("image", "analyzed"), ("note", "analyzed"),
    ]
    request = generate.await_args.args[0]
    assert request.files == (str(image_file.resolve()),)
    assert request.retain_chat and request.delete_after_seconds is None
    assert request.cleanup_source == "gemini_understand" and "Compare" in request.message


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
            async def call_ask(arguments):
                return domain_result_from_call(await client.call_tool("ask-gemini", arguments))

            first = await call_ask({"prompt": "First message", "start_session": True, "model": "neon",
                                    "thinking_level": "high", "retain_chat": True})
            assert first["error"]["code"] == "TIMED_OUT"
            session_id = first["data"]["session_id"]
            assert session_id.startswith("sess_")
            assert read.await_count == 1 and send.await_count == 1
            assert send.await_args.kwargs["thinking_level"] == "high"
            upstream.start_chat.assert_called_once_with(model="neon")
            blocked = await call_ask({"session_id": session_id, "prompt": "Do not repeat the first turn"})
            assert blocked["meta"]["verification_status"] == "session_reply_unverified"
            assert read.await_count == 1 and send.await_count == 1

    asyncio.run(run())


@pytest.mark.parametrize("arguments", [
    {"prompt": "combined", "notebook_ids": ["notebooks/id"], "start_session": True},
    {"prompt": "combined", "session_id": "sess_original", "model": "neon"},
    {"prompt": "combined", "notebook_ids": ["notebooks/id"], "inputs": ["note"]},
    {"prompt": " ", "start_session": True},
    {"prompt": "bad url", "start_session": True, "inputs": ["https://example.com"]},
])
def test_function_ask_rejects_conflicting_modes_before_dispatch(monkeypatch, arguments):
    from src.services import background

    service = AsyncMock(spec=ChatService)
    notebook = AsyncMock()
    monkeypatch.setattr(assist, "_chat_service", service)
    monkeypatch.setattr(background, "submit_notebook_chat", notebook)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("ask-gemini", arguments)
    result = domain_result_from_call(asyncio.run(run()))
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    service.start_session.assert_not_awaited()
    service.send_session_stream.assert_not_awaited()
    service.generate_stream.assert_not_awaited()
    notebook.assert_not_awaited()


def test_function_split_media_tools_use_modality_wait_defaults_and_options(monkeypatch):
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

        async def get_result(self, handle, wait_seconds=0, on_progress=None):
            waits.append((handle, wait_seconds, callable(on_progress)))
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
            await client.call_tool("generate-image", {"prompt": "landscape", "aspect_ratio": "16:9"})
            await client.call_tool("generate-music", {
                "prompt": "folk tune", "duration": "short", "genre": "acoustic",
            })
            await client.call_tool("generate-video", {"prompt": "waves", "duration_seconds": 10})
            await client.call_tool("generate-music", {"prompt": "async", "wait_seconds": 0})

    asyncio.run(run())
    assert [(wait, has_cb) for _, wait, has_cb in waits] == [(120, True), (240, True), (300, True)]
    assert submitted[0]["aspect_ratio"] == "16:9"
    assert submitted[1]["music_genre"] == "acoustic"
    assert len(submitted) == 4


def test_function_research_report_actions_and_rejected_legacy_ask_params(monkeypatch, tmp_path):
    handle = "op_" + "c" * 32
    captured = []

    async def fake_actions(*, operation_id, report_action, output_dir):
        captured.append(("research_action", operation_id, report_action, output_dir))
        return [function.TextContent(type="text", text=f"action:{report_action}")]

    monkeypatch.setattr(assist, "_handle_research_report_action", fake_actions)

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            res = await client.call_tool("run-research", {
                "operation_id": handle, "report_action": "quiz", "output_dir": str(tmp_path),
            })
            assert res.content[0].text == "action:quiz"
            bad = await client.call_tool("run-research", {
                "query": "cannot combine", "operation_id": handle, "report_action": "quiz",
            })
            assert domain_result_from_call(bad)["error"]["code"] == "INVALID_ARGUMENT"

    asyncio.run(run())
    assert captured[0] == ("research_action", handle, "quiz", str(tmp_path))
