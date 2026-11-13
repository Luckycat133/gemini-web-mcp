"""Fast adapter contracts for detached generation and handle reads."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.adapters.mcp_sdk import MCPServer
from src.domain import Artifact, ArtifactKind, ArtifactState, DomainResult, LongOperationData, OperationState
from src.domain.operations import OperationReadData
from src.services import background
from src.services import operations
from src.tools import media, research
from src.surfaces import assist
from src import skill_server


def accepted():
    return DomainResult.success(LongOperationData(operation="test", state=OperationState.ACCEPTED,
                                                   operation_id="op_" + "a" * 32),
                                operation_state=OperationState.ACCEPTED)


def test_primary_media_defaults_to_background_without_client_initialization(monkeypatch):
    submit = AsyncMock(return_value=accepted())
    monkeypatch.setattr(background, "submit_media", submit)
    monkeypatch.setattr(media, "get_gemini_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
    server = MCPServer("test")
    media.register_media_tools(server)
    async def run():
        return await server.call_tool("gemini_generate_media", {"prompt": "synthetic", "media_type": "video"})
    result = asyncio.run(run())
    assert result.content[0].meta["domain_result"]["data"]["operation_id"].startswith("op_")
    assert submit.await_args.args[0]["media_type"] == "video"
    assert submit.await_args.kwargs["operation"] == "gemini_generate_media"


def test_compact_media_routes_defaults_and_input_paths(monkeypatch, tmp_path):
    submit = AsyncMock(return_value=accepted())
    monkeypatch.setattr(background, "submit_media", submit)
    path = tmp_path / "input.png"
    path.write_bytes(b"synthetic")
    async def run():
        await skill_server.create(prompt="image", type="image")
        await skill_server.edit(image_path=str(path), prompt="edit")
    asyncio.run(run())
    assert submit.await_count == 2
    assert submit.await_args_list[0].args[0]["edit"] is False
    assert submit.await_args_list[1].args[0]["edit"] is True
    assert submit.await_args_list[1].args[0]["image_path"] == str(path)


def test_research_surfaces_default_background_and_status_action(monkeypatch):
    submit = AsyncMock(return_value=accepted())
    monkeypatch.setattr(background, "submit_research", submit)
    monkeypatch.setattr(research, "get_gemini_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
    handle = "op_" + "a" * 32
    data = OperationReadData(operation_id=handle, operation="research", state=OperationState.COMPLETED,
                             continuation_possible=True, report_available=True,
                             verification_status="report_observed", report="Synthetic report",
                             artifacts=(Artifact(id="test", kind=ArtifactKind.IMAGE,
                                                 state=ArtifactState.LOCAL, local_path="/tmp/result.png"),))
    class FakeOperationService:
        def __init__(self, *args): pass
        async def get_status(self, operation_id): return DomainResult.success(data)
        async def get_result(self, operation_id): return DomainResult.success(data)
    monkeypatch.setattr(operations, "OperationService", FakeOperationService)
    server = MCPServer("test")
    research.register_research_tools(server)
    async def run():
        primary = await server.call_tool("gemini_deep_research", {"query": "synthetic"})
        focused = await assist.mcp.call_tool("gemini_research", {"query": "synthetic"})
        status = await assist.mcp.call_tool("gemini_research", {"action": "result", "operation_id": handle})
        compact = await skill_server.session("operation_result", session_id=handle)
        return primary, focused, status, compact
    primary, focused, status, compact = asyncio.run(run())
    assert primary.content[0].meta["domain_result"]["ok"]
    assert focused.content[0].meta["domain_result"]["ok"]
    assert "Synthetic report" in status.content[0].text and "Synthetic report" in compact[0].text
    assert "/tmp/result.png" in status.content[0].text and "/tmp/result.png" in compact[0].text
    assert submit.await_count == 2


def test_research_worker_requires_claimed_token_before_start(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    from src.domain import CleanupObservation, CleanupState
    from src.infrastructure.operation_store import OperationStore
    from src.services.research import ResearchRequest, ResearchService, ResearchServiceDependencies

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "ops.sqlite"))
    handle = "op_" + "b" * 32
    store = OperationStore()
    store.create(handle, "gemini_research", requested_model="flash", thinking_level="extended")
    assert store.claim_worker(handle, "owner")
    client = SimpleNamespace(
        start_chat=lambda **kwargs: SimpleNamespace(cid="", rid="", rcid=""),
        create_deep_research_plan=AsyncMock(return_value=SimpleNamespace(cid="c_fixture", research_id=None)),
        start_deep_research=AsyncMock(return_value=SimpleNamespace(text="accepted")),
        wait_for_deep_research=AsyncMock(),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kw: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda value: value,
    ))
    request = ResearchRequest(query="synthetic", operation_id=handle)
    rejected = asyncio.run(service.start(request, worker_token="wrong"))
    assert not rejected.ok
    client.create_deep_research_plan.assert_not_awaited()
    result = asyncio.run(service.start(request, worker_token="owner"))
    assert result.ok and result.data.operation_id == handle
    client.create_deep_research_plan.assert_awaited_once()
    client.start_deep_research.assert_awaited_once()


def test_sync_media_rejects_operation_handle_before_client_or_dispatch(monkeypatch):
    submit = AsyncMock(return_value=accepted())
    monkeypatch.setattr(background, "submit_media", submit)
    monkeypatch.setattr(media, "get_gemini_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
    server = MCPServer("test")
    media.register_media_tools(server)
    handle = "op_" + "a" * 32
    async def run():
        primary = await server.call_tool("gemini_generate_media", {
            "prompt": "p", "media_type": "image", "background": False, "operation_id": handle,
        })
        music = await server.call_tool("gemini_generate_music", {
            "prompt": "p", "background": False, "operation_id": handle,
        })
        compact_create = await skill_server.create(prompt="p", background=False, operation_id=handle)
        compact_edit = await skill_server.edit(image_path="missing", prompt="p", background=False, operation_id=handle)
        return primary.content, music.content, compact_create, compact_edit
    for content in asyncio.run(run()):
        assert content[0].meta["domain_result"]["error"]["code"] == "INVALID_ARGUMENT"
    submit.assert_not_awaited()


def test_research_wait_override_and_sync_handle_rejection_are_preflight(monkeypatch):
    submit = AsyncMock(return_value=accepted())
    monkeypatch.setattr(background, "submit_research", submit)
    entered = []
    def stop_at_sync_client():
        entered.append(True)
        raise RuntimeError("synthetic sync boundary")
    monkeypatch.setattr(research, "get_gemini_client", stop_at_sync_client)
    server = MCPServer("test")
    research.register_research_tools(server)
    handle = "op_" + "a" * 32
    async def run():
        for args in ({"query": "q", "background": False, "operation_id": handle},
                     {"query": "q", "wait_for_completion": True, "operation_id": handle}):
            result = await server.call_tool("gemini_deep_research", args)
            assert result.content[0].meta["domain_result"]["error"]["code"] == "INVALID_ARGUMENT"
        try:
            await server.call_tool("gemini_deep_research", {"query": "q", "wait_for_completion": True})
        except Exception:
            pass
    asyncio.run(run())
    assert entered == [True]
    submit.assert_not_awaited()
