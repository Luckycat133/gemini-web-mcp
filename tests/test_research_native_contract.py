"""Offline contract against the installed Gemini research helper implementation."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import GeminiError
from gemini_webapi.types import Candidate, DeepResearchPlan, ModelOutput
from gemini_webapi.utils import extract_deep_research_plan

from src.domain import CleanupObservation, CleanupState
from src.services.research import ResearchRequest, ResearchService, ResearchServiceDependencies


@pytest.fixture(autouse=True)
def _synthetic_account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-test-account")


class _NativeContractClient(GeminiClient):
    """Use the real SDK chat/plan/start methods with a deterministic transport."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def generate_content(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        chat = kwargs["chat"]
        chat.cid = "c_native_contract"
        plan = DeepResearchPlan(title="Plan", confirm_prompt="Begin research") if len(self.calls) == 1 else None
        response = ModelOutput(
            metadata=["c_native_contract", "r_native", "rc_native"],
            candidates=[Candidate(rcid="rc_native", text="Research reply", deep_research_plan=plan)],
        )
        chat.last_output = response
        return response


@pytest.mark.parametrize("blocked_phase", ["initialize", "read_plan"])
def test_existing_plan_readback_deadline_never_confirms(monkeypatch, tmp_path, blocked_phase):
    import src.services.research as research
    from src.infrastructure.operation_store import OperationStore

    handle = "op_" + "a" * 32
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
    store.update(handle, state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_fixture")
    monkeypatch.setattr(research, "OperationStore", lambda: store)
    monkeypatch.setattr(research, "phase_timeout", lambda _: 0.01)
    client = SimpleNamespace(start_deep_research=AsyncMock(), create_deep_research_plan=AsyncMock())
    async def blocked(*_args, **_kwargs):
        await asyncio.Event().wait()

    initializer = AsyncMock(side_effect=blocked if blocked_phase == "initialize" else None)
    read = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(research, "read_existing_research_plan", read)
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=initializer, cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda *args, **kwargs: None, resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="ignored", operation_id=handle)))
    assert result.data.operation_id == handle
    assert store.get(handle).phase == "plan_outcome_unverified"
    assert store.get(handle).cleanup_protected
    client.start_deep_research.assert_not_awaited()
    client.create_deep_research_plan.assert_not_awaited()
    assert read.await_count == (blocked_phase == "read_plan")


@pytest.mark.parametrize("protected", [False, True])
def test_expired_plan_only_recovers_when_protected(monkeypatch, tmp_path, protected):
    import src.services.research as research
    from src.infrastructure.operation_store import OperationStore, RETENTION_SECONDS

    handle = "op_" + "b" * 32
    store = OperationStore(tmp_path / "operations.sqlite", clock=lambda: 100.0)
    store.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
    store.update(handle, state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_fixture")
    if not protected:
        store.release_cleanup(handle)
    store.clock = lambda: 100.0 + RETENTION_SECONDS + 1
    monkeypatch.setattr(research, "OperationStore", lambda: store)
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: None, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda *args, **kwargs: None, resolve_model=lambda model: model,
    ))
    resume = AsyncMock(return_value=research.research_unknown_result(
        "gemini_research", handle, None, SimpleNamespace(cid="c_fixture"),
    ))
    monkeypatch.setattr(service, "_resume_existing_plan", resume)
    result = asyncio.run(service.start(ResearchRequest(query="ignored", operation_id=handle)))
    assert resume.await_count == protected
    if not protected:
        assert result.meta.verification_status == "operation_expired"


@pytest.mark.parametrize("identity", ["matching", "missing", "different", "claimed"])
def test_research_rejection_only_finalizes_exact_unclaimed_turn(monkeypatch, tmp_path, identity):
    import src.services.research as research
    from src.infrastructure.operation_store import OperationStore

    handle = "op_" + "c" * 32
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
    record = store.get(handle)
    store.update(handle, state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_fixture")
    if identity != "missing":
        assert store.bind_research_turn(handle, account_key=record.account_key, chat_id="c_fixture",
                                        response_id="r_other" if identity == "different" else "r_fixture",
                                        candidate_id="rc_fixture")
    if identity == "claimed":
        assert store.claim_research_confirmation(handle, expected_phase="plan_outcome_unverified")
    monkeypatch.setattr(research, "OperationStore", lambda: store)
    monkeypatch.setattr(research, "read_existing_research_plan", AsyncMock(return_value=research._ResearchPlanRejection(
        ("c_fixture", "r_fixture", "rc_fixture"),
    )))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_fixture"))
    client = SimpleNamespace(start_deep_research=AsyncMock(), create_deep_research_plan=AsyncMock())
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda *args, **kwargs: None, resolve_model=lambda model: model,
        finalize_chat_cleanup=finalize,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="ignored", operation_id=handle)))
    saved = store.get(handle)
    assert (saved.phase == "plan_rejected") == (identity == "matching")
    assert saved.cleanup_protected == (identity != "matching")
    assert finalize.await_count == (identity == "matching")
    if identity == "matching":
        assert result.error.code.value == "UPSTREAM_REJECTED"
    client.start_deep_research.assert_not_awaited()
    client.create_deep_research_plan.assert_not_awaited()


def test_research_rejection_and_confirmation_claim_are_mutually_exclusive(tmp_path):
    from src.infrastructure.operation_store import OperationStore

    first = OperationStore(tmp_path / "operations.sqlite")
    second = OperationStore(first.path)
    for index, reject_first in enumerate((True, False)):
        handle = "op_" + str(index) * 32
        first.create(handle, "gemini_research", requested_model="pro", thinking_level="extended")
        first.update(handle, state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_fixture")
        account = first.get(handle).account_key
        assert first.bind_research_turn(handle, account_key=account, chat_id="c_fixture",
                                        response_id="r_fixture", candidate_id="rc_fixture")
        def reject():
            return first.reject_research_turn(handle, account_key=account, chat_id="c_fixture",
                                              response_id="r_fixture", candidate_id="rc_fixture")
        def claim():
            return second.claim_research_confirmation(handle, expected_phase="plan_outcome_unverified")
        if reject_first:
            assert reject() and not claim()
        else:
            assert claim() and not reject()


def test_installed_native_research_helpers_create_plan_then_confirm_once(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    client = _NativeContractClient()
    scheduled = []

    def schedule(cid, *, retain_chat, delete_after_seconds, source):
        scheduled.append((cid, retain_chat, delete_after_seconds, source))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)

    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=schedule, resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="Compare two missions")))

    assert result.ok and result.data.upstream_chat_id == "c_native_contract"
    assert len(client.calls) == 2
    assert client.calls[0][0].startswith("Compare two missions")
    assert client.calls[1][0] == "Begin research"
    assert all(kwargs["deep_research"] is True for _, kwargs in client.calls)
    assert client.calls[0][1]["chat"] is client.calls[1][1]["chat"]
    assert scheduled == [("c_native_contract", False, 1800, "gemini_research")]


def test_plan_failure_reports_only_allowlisted_reason_and_phase(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    client = _NativeContractClient()
    client.create_deep_research_plan = AsyncMock(
        side_effect=GeminiError("Gemini did not return a deep research plan. Preview: 'PRIVATE OUTPUT'"),
    )
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda *args, **kwargs: None, resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="test")))
    assert not result.ok
    assert result.meta.details["exception_type"] == "GeminiError"
    assert result.meta.details["failure_phase"] == "plan"
    assert result.meta.details["failure_reason"] == "plan_missing"
    assert "PRIVATE OUTPUT" not in str(result.to_dict())
    assert "PRIVATE OUTPUT" not in caplog.text


def test_installed_read_chat_drops_structured_plan_from_a_synthetic_candidate(monkeypatch):
    """Characterize SDK recovery; this synthetic observation is not a live compatibility claim."""
    import gemini_webapi.components.chat_mixin as chat_mixin

    candidate = [None] * 13
    candidate[0] = "rc_synthetic"
    candidate[8] = [2]
    candidate[12] = [{"56": ["Synthetic plan", [[None, "Step", "Details"]], None, ["Start"]]}]
    assert extract_deep_research_plan(candidate) is not None
    turn = [[None, "r_synthetic"], None, None, [[candidate]]]
    part = [None, None, json.dumps([[turn]])]
    monkeypatch.setattr(chat_mixin, "extract_json_from_response", lambda _: [part])
    client = GeminiClient()
    monkeypatch.setattr(client, "_check_account_status", lambda **kwargs: True)
    monkeypatch.setattr(client, "_batch_execute", AsyncMock(return_value=SimpleNamespace(text="synthetic")))
    monkeypatch.setattr(client, "_parse_candidate", lambda *args: ("Plan text", None, [], [], [], [], []))

    history = asyncio.run(client.read_chat("c_synthetic"))
    assert history and history.turns
    assert history.turns[0].model_output.deep_research_plan is None


def test_installed_sdk_ready_document_without_plan_is_preserved_and_never_confirmed(tmp_path, monkeypatch):
    from gemini_webapi.types import DeepResearchDocument
    from src.domain import OperationState
    from src.infrastructure.operation_store import OperationStore
    from src.services.operations import OperationService

    for has_text in (False, True):
        class ReadyClient(_NativeContractClient):
            async def generate_content(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                chat = kwargs["chat"]
                chat.cid = "c_ready"
                output = ModelOutput(
                    metadata=["c_ready", "r_ready", "rc_ready"],
                    candidates=[Candidate(rcid="rc_ready", text="Finished" if has_text else "",
                                          deep_research_document=DeepResearchDocument(content="Actual report"))],
                )
                chat.last_output = output
                self.output = output
                return output

        client = ReadyClient()
        # Empty text causes the actual SDK helper to attempt its ordinary
        # read-back; simulate a failed read-back without another generation.
        client.fetch_latest_chat_response = AsyncMock(side_effect=[None, None, client] if not has_text else [None])
        path = tmp_path / f"ready-{has_text}.sqlite"
        monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
        store = OperationStore(path)
        cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_ready"))
        service = ResearchService(ResearchServiceDependencies(
            client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
            schedule_chat_cleanup=lambda cid, **kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
            resolve_model=lambda model: model, finalize_chat_cleanup=cleanup,
        ))
        result = asyncio.run(service.start(ResearchRequest(query="Synthetic", operation_id="op_" + "a" * 32)))
        assert result.ok and result.data.state is OperationState.COMPLETED and result.data.report_available
        assert len(client.calls) == 1
        assert not store.is_cleanup_protected("c_ready")
        assert store.get_media_artifacts(result.data.operation_id)
        cleanup.assert_awaited_once()
        client.fetch_latest_chat_response = AsyncMock(return_value=client.output)
        read = asyncio.run(OperationService(store, lambda: client, AsyncMock(), cleanup).get_result(result.data.operation_id))
        assert read.ok and read.data.report == "Actual report"
        assert not store.is_cleanup_protected("c_ready")
        cleanup.assert_awaited_once()
        client.fetch_latest_chat_response.assert_not_awaited()


def test_installed_sdk_plan_missing_does_not_promote_ordinary_or_empty_document(tmp_path, monkeypatch):
    from gemini_webapi.types import DeepResearchDocument
    from src.infrastructure.operation_store import OperationStore

    for with_document in (False, True):
        class NotReadyClient(_NativeContractClient):
            async def generate_content(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                chat = kwargs["chat"]
                chat.cid = "c_not_ready"
                output = ModelOutput(metadata=["c_not_ready", "r", "rc"], candidates=[Candidate(
                    rcid="rc", text="ordinary reply", deep_research_document=(
                        DeepResearchDocument(content="") if with_document else None),
                )])
                chat.last_output = output
                return output

        path = tmp_path / f"negative-{with_document}.sqlite"
        monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
        client = NotReadyClient()
        cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_not_ready"))
        service = ResearchService(ResearchServiceDependencies(
            client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
            schedule_chat_cleanup=lambda cid, **kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
            resolve_model=lambda model: model, finalize_chat_cleanup=cleanup,
        ))
        result = asyncio.run(service.start(ResearchRequest(query="Synthetic")))
        assert result.ok and result.data.state.value == "partial"
        assert result.meta.verification_status == "start_outcome_unverified"
        assert len(client.calls) == 1
        assert OperationStore(path).is_cleanup_protected("c_not_ready")
        cleanup.assert_not_awaited()


def test_late_plan_cid_is_checkpointed_without_replay_or_timeout_regression(tmp_path, monkeypatch):
    import src.services.research as research
    from src.infrastructure.operation_store import OperationStore

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "late.sqlite"))
    monkeypatch.setattr(research, "phase_timeout", lambda _: 0.02)
    client = _NativeContractClient()
    entered = asyncio.Event()
    async def late_plan(query, chat=None, model=None):
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0.03)
            chat.cid = "c_late"
            return DeepResearchPlan(title="late")
    client.create_deep_research_plan = AsyncMock(side_effect=late_plan)
    client.start_deep_research = AsyncMock()
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda model: model,
    ))
    async def run():
        result = await service.start(ResearchRequest(query="synthetic", operation_id="op_" + "b" * 32))
        assert entered.is_set() and result.data.state.value == "timed_out"
        await asyncio.sleep(0.08)
        store = OperationStore()
        record = store.get(result.data.operation_id)
        assert record.state == "timed_out" and record.phase == "plan_outcome_unverified"
        assert record.upstream_chat_id == "c_late"
        assert store.is_cleanup_protected("c_late")
        assert client.create_deep_research_plan.await_count == 1
        client.start_deep_research.assert_not_awaited()
    asyncio.run(run())


def test_explicit_plan_rejection_cleans_up_without_claiming_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "rejected.sqlite"))
    client = _NativeContractClient()
    async def reject(query, chat=None, model=None):
        raise GeminiError("Permission denied. Account status: restricted")
    client.create_deep_research_plan = AsyncMock(side_effect=reject)
    cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_rejected"))
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda model: model, finalize_chat_cleanup=cleanup,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="synthetic")))
    assert not result.ok and result.meta.details["failure_reason"] == "account_restricted"
    cleanup.assert_not_awaited()


def test_cancelled_plan_late_cid_gets_protected_without_restart(tmp_path, monkeypatch):
    from src.infrastructure.operation_store import OperationStore

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "cancel-late.sqlite"))
    client = _NativeContractClient()
    entered = asyncio.Event()
    async def late_plan(query, chat=None, model=None):
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0.02)
            chat.cid = "c_cancel_late"
            return DeepResearchPlan(title="late")
    client.create_deep_research_plan = AsyncMock(side_effect=late_plan)
    client.start_deep_research = AsyncMock()
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda model: model,
    ))
    async def run():
        task = asyncio.create_task(service.start(ResearchRequest(query="synthetic", operation_id="op_" + "c" * 32)))
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.06)
        store = OperationStore()
        record = store.get("op_" + "c" * 32)
        assert record.upstream_chat_id == "c_cancel_late"
        assert store.is_cleanup_protected("c_cancel_late")
        assert client.create_deep_research_plan.await_count == 1
        client.start_deep_research.assert_not_awaited()
    asyncio.run(run())


def test_plan_missing_reads_original_plan_then_confirms_once(tmp_path, monkeypatch):
    import src.services.research as research
    from src.domain import OperationState
    from src.infrastructure.rpc_parsers import RPCEnvelope, RPCParseResult

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "recover-plan.sqlite"))
    class MissingSDKPlan(_NativeContractClient):
        async def generate_content(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            chat = kwargs["chat"]
            chat.cid = "c_existing"
            output = ModelOutput(metadata=["c_existing", "r_existing", "rc_existing"],
                                 candidates=[Candidate(rcid="rc_existing", text="Plan reply")])
            chat.last_output = output
            return output
    client = MissingSDKPlan()
    client.start_deep_research = AsyncMock(return_value=SimpleNamespace(state="running", text="started"))
    monkeypatch.setattr(research, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=200, text="fixture")))
    monkeypatch.setattr(research, "parse_rpc_envelope", lambda *_: RPCEnvelope(True, 1, ([None],)))
    monkeypatch.setattr(research, "parse_contract_body", lambda *a, **k: RPCParseResult("success", {
        "plan": {"title": "Existing plan", "confirm_prompt": "Start research"},
        "metadata": ["c_existing", "r_existing", "rc_existing"],
    }))
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kw: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="synthetic")))
    assert result.ok and result.data.state is OperationState.RUNNING
    assert len(client.calls) == 1
    assert client.start_deep_research.await_count == 1
    plan = client.start_deep_research.await_args.args[0]
    chat = client.start_deep_research.await_args.kwargs["chat"]
    assert plan.metadata == ["c_existing", "r_existing", "rc_existing"]
    assert (chat.cid, chat.rid, chat.rcid) == tuple(plan.metadata)


def test_plan_missing_without_verified_readback_never_confirms(tmp_path, monkeypatch):
    import src.services.research as research
    from src.infrastructure.rpc_parsers import RPCEnvelope, RPCParseResult

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "empty-plan.sqlite"))
    client = _NativeContractClient()
    async def missing(query, chat=None, model=None):
        chat.cid = "c_unknown_plan"
        raise GeminiError("Gemini did not return a deep research plan.")
    client.create_deep_research_plan = AsyncMock(side_effect=missing)
    client.start_deep_research = AsyncMock()
    monkeypatch.setattr(research, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=200, text="fixture")))
    monkeypatch.setattr(research, "parse_rpc_envelope", lambda *_: RPCEnvelope(True, 1, ([None],)))
    monkeypatch.setattr(research, "parse_contract_body", lambda *a, **k: RPCParseResult("empty"))
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kw: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda model: model,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="synthetic")))
    assert result.data.state.value == "partial"
    assert client.create_deep_research_plan.await_count == 1
    client.start_deep_research.assert_not_awaited()


def test_confirmation_timeout_never_reopens_plan_confirmation(tmp_path, monkeypatch):
    import src.services.research as research
    from src.infrastructure.operation_store import OperationStore

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "confirmation-timeout.sqlite"))
    monkeypatch.setattr(research, "phase_timeout", lambda _: 0.02)
    client = _NativeContractClient()
    async def plan(query, chat=None, model=None):
        chat.cid = "c_confirm_timeout"
        return DeepResearchPlan(cid=chat.cid, confirm_prompt="Start research")
    async def blocked(plan, chat=None):
        await asyncio.sleep(30)
    client.create_deep_research_plan = AsyncMock(side_effect=plan)
    client.start_deep_research = AsyncMock(side_effect=blocked)
    client.fetch_latest_chat_response = AsyncMock(return_value=None)
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
        schedule_chat_cleanup=lambda cid, **kw: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
        resolve_model=lambda model: model,
    ))
    handle = "op_" + "f" * 32
    result = asyncio.run(service.start(ResearchRequest(query="synthetic", operation_id=handle)))
    assert result.data.state.value == "timed_out"
    record = OperationStore().get(handle)
    assert record.phase != "plan_outcome_unverified"
    assert record.cleanup_protected
    assert client.start_deep_research.await_count == 1
    asyncio.run(service.start(ResearchRequest(query="ignored", operation_id=handle)))
    assert client.start_deep_research.await_count == 1


def test_unknown_plan_errors_with_observed_chat_remain_recoverable(tmp_path, monkeypatch):
    from gemini_webapi.exceptions import APIError
    from src.infrastructure.operation_store import OperationStore

    for index, error in enumerate((APIError("synthetic parse failure"), ConnectionError("synthetic disconnect"),
                                   RuntimeError("unknown upstream failure"),
                                   ConnectionError("x is not available for use. Account status: AVAILABLE"),
                                   GeminiError("Permission denied. Account status: UNAUTHENTICATED"))):
        monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / f"unknown-{index}.sqlite"))
        client = _NativeContractClient()
        async def fail_after_chat(query, chat=None, model=None):
            chat.cid = "c_unknown_error"
            raise error
        client.create_deep_research_plan = AsyncMock(side_effect=fail_after_chat)
        client.start_deep_research = AsyncMock()
        client._batch_execute = AsyncMock(side_effect=RuntimeError("offline"))
        cleanup = AsyncMock()
        service = ResearchService(ResearchServiceDependencies(
            client_provider=lambda: client, client_initializer=AsyncMock(), cleanup_due_remote_chats=AsyncMock(),
            schedule_chat_cleanup=lambda cid, **kw: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid),
            resolve_model=lambda model: model, finalize_chat_cleanup=cleanup,
        ))
        handle = "op_" + str(index) * 32
        result = asyncio.run(service.start(ResearchRequest(query="synthetic", operation_id=handle)))
        record = OperationStore().get(handle)
        assert result.ok and result.data.state.value == "partial" and result.data.continuation_possible
        assert record.phase == "plan_outcome_unverified" and record.cleanup_protected
        assert record.upstream_chat_id == "c_unknown_error"
        cleanup.assert_not_awaited()
        client.start_deep_research.assert_not_awaited()
        retry = asyncio.run(service.start(ResearchRequest(query="ignored", operation_id=handle)))
        assert retry.data.operation_id == handle and client.create_deep_research_plan.await_count == 1
        client.start_deep_research.assert_not_awaited()
