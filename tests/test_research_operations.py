"""Research handles recover reports across clients without repeating queries."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.client_wrapper as client_wrapper
import src.services.operations as operation_module
import src.surfaces.assist as assist
import src.tools.research as research_tools
from src.adapters.mcp_sdk import MCPServer
from src.domain import ArtifactState, CleanupObservation, CleanupState, OperationState
from src.infrastructure.state_store import StateStore
from src.services.operations import OperationService
from src.services.research import ResearchRequest, ResearchService, ResearchServiceDependencies, research_report_body


class Client:
    def __init__(self, *, body=None, fail_start=False):
        self.body = body
        self.fail_start = fail_start
        self.calls = []
        self.start_entered = None
        self.release_start = None

    def start_chat(self, **kwargs):
        return SimpleNamespace(cid="", rid="", rcid="")

    async def create_deep_research_plan(self, query, *, chat, model):
        self.calls.append("plan")
        chat.cid = "c_owned"
        return SimpleNamespace(cid=chat.cid, research_id="research_owned", title="private title")

    async def start_deep_research(self, plan, *, chat):
        self.calls.append("start")
        if self.start_entered:
            self.start_entered.set()
        if self.release_start:
            await self.release_start.wait()
        if self.fail_start:
            raise RuntimeError("provider request interrupted")
        return SimpleNamespace(state="queued", text="I'm on it", metadata=["c_owned"])

    async def wait_for_deep_research(self, *args, **kwargs):
        raise AssertionError("focused recovery must not poll a replayed plan")

    async def fetch_latest_chat_response(self, cid, limit=5, match=None):
        assert cid == "c_owned"
        self.calls.append("read")
        return SimpleNamespace(metadata=[cid], text="You've reached the daily limit" if self.body is None else "report ready",
                               deep_research_document=None if self.body is None else SimpleNamespace(content=self.body))


def _build(tmp_path, client, monkeypatch, *, scope=lambda: "scope_a", operations=None):
    operations = operations or OperationService(StateStore(tmp_path / "state.sqlite3"), scope_provider=scope)
    cleanup = []
    async def initialize():
        return None
    async def cleanup_due(client):
        return 0
    async def finalize(response, **kwargs):
        stored = operations.store.operations.get("scope_a", next(iter(_known_ids(operations))))
        assert stored.artifacts and stored.state == OperationState.COMPLETED
        assert all(Path(item.local_path).is_file() for item in stored.artifacts)
        cleanup.append((response.cid, kwargs))
        return CleanupObservation(state=CleanupState.RETAINED if kwargs["retain_chat"] else CleanupState.COMPLETED)
    monkeypatch.setattr(client_wrapper, "finalize_generated_chat_cleanup", finalize)
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=initialize, cleanup_due_remote_chats=cleanup_due,
        schedule_chat_cleanup=lambda cid, **kwargs: cleanup.append((cid, kwargs)), resolve_model=lambda model: model,
    ), operations=operations)
    monkeypatch.setattr(service, "_destination", lambda request: request.output_dir or str(tmp_path / "reports"))
    return service, operations, cleanup


def _known_ids(operations):
    with operations.store.transaction() as conn:
        return [row[0] for row in conn.execute("SELECT operation_id FROM operations").fetchall()]


def test_immediate_start_and_durable_cid_survive_interrupted_start(tmp_path, monkeypatch):
    async def run():
        client = Client(body="# Private durable report\n\nObserved source body.")
        client.start_entered, client.release_start = asyncio.Event(), asyncio.Event()
        service, operations, cleanup = _build(tmp_path, client, monkeypatch)
        started = await service.start(ResearchRequest("private original query", output_dir=str(tmp_path / "reports"),
                                                     retain_chat=False, delete_after_seconds=45))
        assert started.data.state == OperationState.ACCEPTED
        assert client.calls == []
        await client.start_entered.wait()
        before = StateStore(operations.store.path).operations.get("scope_a", started.data.operation_id)
        assert before.upstream_chat_id == "c_owned"
        assert before.provider_operation_id == "research_owned"
        task = operations._tasks[started.data.operation_id]
        task.cancel()
        await task
        assert operations.store.operations.get("scope_a", started.data.operation_id).state == OperationState.TIMED_OUT
        client.release_start.set()
        restarted_service, restarted, _ = _build(tmp_path, client, monkeypatch)
        result = await restarted_service.result(started.data.operation_id)
        assert result.data.state == OperationState.COMPLETED
        assert result.data.report_available
        assert result.data.artifacts[0].state == ArtifactState.LOCAL
        assert Path(result.data.artifacts[0].local_path).read_text() == client.body
        assert client.calls.count("plan") == client.calls.count("start") == 1
        assert client.calls.count("read") == 1
        for private in (b"private original query", b"private title", b"Observed source body"):
            assert private not in restarted.store.path.read_bytes()
        # Initial plan retention protects the source despite requested disposal.
        assert cleanup[0][1]["retain_chat"] is True
    asyncio.run(run())


def test_failed_source_can_recover_but_quota_text_never_becomes_report(tmp_path, monkeypatch):
    async def run():
        client = Client(fail_start=True)
        service, operations, cleanup = _build(tmp_path, client, monkeypatch)
        started = await service.start(ResearchRequest("question", output_dir=str(tmp_path / "reports"), retain_chat=False))
        await operations._tasks[started.data.operation_id]
        failed = operations.store.operations.get("scope_a", started.data.operation_id)
        assert failed.state == OperationState.FAILED
        unknown = await service.result(started.data.operation_id)
        assert unknown.data.state == OperationState.FAILED
        assert unknown.data.artifacts == ()
        assert not (tmp_path / "reports").exists()
        assert len(cleanup) == 1 and cleanup[0][1]["retain_chat"] is True
        client.body = "# Completed report\n\nDurable report body."
        result = await service.result(started.data.operation_id)
        assert result.data.state == OperationState.COMPLETED
        assert cleanup[-1][1]["retain_chat"] is False
        assert client.calls.count("plan") == client.calls.count("start") == 1
    asyncio.run(run())


def test_save_failure_keeps_source_and_original_cleanup_policy(tmp_path, monkeypatch):
    async def run():
        destination = tmp_path / "cannot_be_directory"
        destination.write_text("caller file")
        client = Client(body="# Report\n\nBody.")
        service, operations, cleanup = _build(tmp_path, client, monkeypatch)
        started = await service.start(ResearchRequest("question", output_dir=str(destination), retain_chat=False))
        await operations._tasks[started.data.operation_id]
        result = await service.result(started.data.operation_id)
        assert not result.ok and result.error.code.value == "ARTIFACT_SAVE_FAILED"
        assert result.data.upstream_chat_id == "c_owned"
        assert destination.read_text() == "caller file"
        assert len(cleanup) == 1 and cleanup[0][1]["retain_chat"] is True
    asyncio.run(run())


def test_readback_identity_mismatch_does_not_save_or_delete_foreign_chat(tmp_path, monkeypatch):
    async def run():
        class ForeignClient(Client):
            async def fetch_latest_chat_response(self, cid, limit=5, match=None):
                return SimpleNamespace(metadata=["c_foreign"], deep_research_document=SimpleNamespace(content="# Foreign report"))
        client = ForeignClient()
        service, operations, cleanup = _build(tmp_path, client, monkeypatch)
        started = await service.start(ResearchRequest("question", output_dir=str(tmp_path / "reports"), retain_chat=False))
        await operations._tasks[started.data.operation_id]
        result = await service.result(started.data.operation_id)
        assert result.error.code.value == "UPSTREAM_CHANGED"
        assert result.data.upstream_chat_id == "c_owned"
        assert not (tmp_path / "reports").exists()
        assert len(cleanup) == 1 and cleanup[0][0] == "c_owned"
    asyncio.run(run())


def test_primary_and_assist_actions_share_same_restart_safe_handle(tmp_path, monkeypatch):
    async def run():
        client = Client(body="# Completed report\n\nShared result.")
        service, operations, _ = _build(tmp_path, client, monkeypatch)
        monkeypatch.setattr(operation_module, "_service", operations)
        monkeypatch.setattr(research_tools, "_build_research_service", lambda: service)
        monkeypatch.setattr(assist, "_research_service", service)
        monkeypatch.setattr(research_tools, "get_gemini_client", lambda: client)
        async def initialize():
            return None
        async def cleanup_due(_client):
            return 0
        monkeypatch.setattr(research_tools, "initialize_client", initialize)
        monkeypatch.setattr(research_tools, "cleanup_due_remote_chats", cleanup_due)
        mcp = MCPServer("research_test")
        research_tools.register_research_tools(mcp)
        initial = await mcp.call_tool("gemini_deep_research", {"query": "question", "wait_for_completion": False,
                                                              "idempotency_key": "opaque-retry"})
        operation_id = initial.content[0].meta["domain_result"]["data"]["operation_id"]
        retry = await mcp.call_tool("gemini_deep_research", {"query": "question", "wait_for_completion": False,
                                                            "idempotency_key": "opaque-retry"})
        assert retry.content[0].meta["domain_result"]["data"]["operation_id"] == operation_id
        cancelled = await assist.mcp.call_tool("gemini_research", {"action": "cancel", "operation_id": operation_id})
        assert cancelled.content[0].meta["domain_result"]["data"]["state"] == "cancel_requested"
        assert cancelled.content[0].meta["domain_result"]["data"]["cancellation_confirmed"] is False
        result = await assist.mcp.call_tool("gemini_research", {"action": "result", "operation_id": operation_id})
        data = result.content[0].meta["domain_result"]["data"]
        assert data["state"] == "completed" and data["report_available"]
        again = await mcp.call_tool("gemini_deep_research", {"action": "result", "operation_id": operation_id})
        assert again.content[0].meta["domain_result"]["data"]["artifacts"][0]["id"] == data["artifacts"][0]["id"]
        assert client.calls == ["plan", "start", "read"]
    asyncio.run(run())


@pytest.mark.parametrize("text", ["I'm on it", "I've finished the research", "You've reached the daily limit", "Quota exceeded"])
def test_acknowledgement_and_quota_text_are_not_saved_as_reports(text):
    assert research_report_body(SimpleNamespace(text=text), provider_completed=True) is None
