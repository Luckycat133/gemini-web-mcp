"""Public MCP results expose runtime policy decisions without asking agents to gate them."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import UsageLimitExceededError
from gemini_webapi.types import AvailableModel, Candidate, ModelOutput
from mcp import Client

from src.adapters.mcp_sdk import MCPServer
from src.domain import CleanupObservation, CleanupState
from src.onboarding import domain_result_from_call
from src.surfaces import assist
from src.thinking_client import ThinkingLevelGeminiClient


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-mcp-policy")
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite3"))
    model_client = ThinkingLevelGeminiClient()
    model_client._running = True
    model_client._model_registry = {
        name: AvailableModel(model_id=name, model_name=name, display_name=name, description="", capacity=1)
        for name in ("gemini-flash", "gemini-flash-lite", "gemini-argon")
    }
    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    monkeypatch.setattr(GeminiClient, "close", AsyncMock())
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda *_a, **_kw: True)
    monkeypatch.setattr(assist, "get_gemini_client", lambda: model_client)
    monkeypatch.setattr(assist, "initialize_client", AsyncMock())
    monkeypatch.setattr(assist, "cleanup_due_remote_chats", AsyncMock())
    monkeypatch.setattr(assist, "schedule_remote_chat_cleanup", lambda cid, **_kw:
                        CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid))
    monkeypatch.setattr(assist, "finalize_remote_chat_cleanup_from_response", AsyncMock(
        return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_policy")))
    calls = []
    state = {"quota_error": False}

    async def generated(_self, *args, **kwargs):
        calls.append(kwargs)
        if state["quota_error"]:
            raise UsageLimitExceededError("synthetic model quota exhausted")
        if state.get("empty_stream"):
            return
        yield ModelOutput(metadata=["c_policy", "r_policy"], candidates=[Candidate(rcid="rc_policy", text="Synthetic answer")])

    monkeypatch.setattr(GeminiClient, "_generate", generated)
    server = MCPServer("runtime-policy-integration")
    assist.register_assist_tools(server)
    return server, calls, state


def test_mcp_explicit_lite_cannot_bypass_quota_gate(runtime):
    server, calls, _state = runtime

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_ask", {"prompt": "synthetic", "model": "flash-lite", "thinking_level": "extended"})

    result = domain_result_from_call(asyncio.run(run()))
    assert result["ok"] is False and result["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert calls == []


def test_mcp_future_query_fallback_reports_actual_model_and_locked_thinking(runtime):
    server, calls, state = runtime

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            state["quota_error"] = True
            rejected = domain_result_from_call(await client.call_tool("gemini_ask", {"prompt": "first synthetic query"}))
            assert rejected["ok"] is False and rejected["error"]["code"] == "RATE_LIMITED"
            assert len(calls) == 1
            state["quota_error"] = False
            return domain_result_from_call(await client.call_tool("gemini_ask", {
                "prompt": "new synthetic query", "thinking_level": "extended",
            }))

    result = asyncio.run(run())
    assert result["ok"] is True
    assert [call["model"].model_name for call in calls] == ["gemini-flash", "gemini-flash-lite"]
    assert calls[1]["extended_thinking"] is False
    assert result["data"]["requested_model"] == "flash"
    assert result["data"]["effective_model"] == result["meta"]["effective_backend"] == "gemini-flash-lite"
    selection = result["data"]["model_selection"]
    assert selection["fallback_reason"] == "flash_quota_exhausted"
    assert selection["thinking_selectable"] is False and selection["effective_thinking_level"] is None
    assert {warning["code"] for warning in result["warnings"]} >= {"model_fallback", "thinking_unavailable"}


def test_mcp_argon_selection_is_explicit_and_not_changed_by_flash_quota(runtime):
    server, calls, state = runtime

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            state["quota_error"] = True
            await client.call_tool("gemini_ask", {"prompt": "synthetic quota query"})
            state["quota_error"] = False
            return domain_result_from_call(await client.call_tool("gemini_ask", {
                "prompt": "synthetic writing task", "model": "4 Argon", "thinking_level": "extended",
            }))

    result = asyncio.run(run())
    assert result["ok"] is True
    assert calls[-1]["model"].model_name == result["data"]["effective_model"] == "gemini-argon"
    assert result["data"]["model_selection"]["thinking_selectable"] is True


def test_empty_fallback_stream_does_not_claim_an_effective_model_or_completion(runtime):
    server, calls, state = runtime

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            state["quota_error"] = True
            await client.call_tool("gemini_ask", {"prompt": "synthetic quota query"})
            state.update(quota_error=False, empty_stream=True)
            return domain_result_from_call(await client.call_tool("gemini_ask", {"prompt": "new synthetic query"}))

    result = asyncio.run(run())
    assert calls[-1]["model"].model_name == "gemini-flash-lite"
    assert result["ok"] is False and result["error"]["code"] == "UPSTREAM_CHANGED"
    assert result["meta"]["effective_backend"] is None
    assert result["meta"]["verification_status"] == "upstream_response_missing"


@pytest.mark.parametrize("model", ["pro", "argon"])
def test_research_missing_requested_model_never_dispatches_flash(runtime, model):
    from src.services.research import ResearchRequest, ResearchService, ResearchServiceDependencies

    _server, calls, _state = runtime
    model_client = assist.get_gemini_client()
    model_client._model_registry = {name: item for name, item in model_client._model_registry.items()
                                    if item.model_name == "gemini-flash"}
    service = ResearchService(ResearchServiceDependencies(
        client_provider=lambda: model_client, client_initializer=AsyncMock(),
        cleanup_due_remote_chats=AsyncMock(), schedule_chat_cleanup=lambda *_a, **_kw: None,
        resolve_model=assist.resolve_model_name,
    ))
    result = asyncio.run(service.start(ResearchRequest(query="synthetic research", model=model)))
    assert result.ok is False and result.error.code.value == "CAPABILITY_UNAVAILABLE"
    assert result.data.upstream_chat_id is None
    assert calls == []


@pytest.mark.parametrize("purpose", ["task", "notebook_chat"])
def test_notebook_and_task_cannot_use_lite_even_with_fresh_basic_quota_evidence(runtime, purpose):
    from src.services.model_policy import ModelPolicyError, model_policy_scope
    server, calls, state = runtime
    model_client = assist.get_gemini_client()
    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            state["quota_error"] = True
            await client.call_tool("gemini_ask", {"prompt": "synthetic basic quota query"})
        state["quota_error"] = False
        before = len(calls)
        with model_policy_scope(purpose, allow_fallback=False), pytest.raises(ModelPolicyError):
            await model_client.generate_content("synthetic notebook or task", model="flash-lite")
        assert len(calls) == before
    asyncio.run(run())


@pytest.mark.parametrize("purpose", ["query", "notebook_chat", "task"])
def test_task_rejects_pro_before_dispatch_but_chat_keeps_pro(runtime, purpose):
    from src.services.model_policy import ModelPolicyError, model_policy_scope

    _server, calls, _state = runtime
    client = assist.get_gemini_client()
    pro = AvailableModel(model_id="synthetic-pro", model_name="gemini-pro", display_name="Pro",
                         description="", capacity=1)
    client._model_registry[pro.model_id] = pro

    async def run():
        with model_policy_scope(purpose):
            if purpose == "task":
                with pytest.raises(ModelPolicyError) as error:
                    await client.generate_content("synthetic", model=pro)
                assert error.value.code == "CAPABILITY_UNAVAILABLE"
            else:
                await client.generate_content("synthetic", model=pro)

    asyncio.run(run())
    assert len(calls) == (0 if purpose == "task" else 1)
    if calls:
        assert calls[0]["model"] is pro


@pytest.mark.parametrize("labels,allowed", [
    (("gemini-custom", "Custom", ["4 Argon"]), True),
    (("gemini-flash", "Flash", []), True),
    (("gemini-custom", "Custom", []), False),
    (("gemini-pro", "Pro", ["argon"]), False),
])
def test_task_uses_unambiguous_discovered_model_family(runtime, labels, allowed):
    from src.services.model_policy import ModelPolicyError, model_policy_scope

    _server, calls, _state = runtime
    client = assist.get_gemini_client()
    name, display, aliases = labels
    model = AvailableModel(model_id="synthetic-selected", model_name=name, display_name=display,
                           description="", capacity=1, aliases=aliases)
    client._model_registry[model.model_id] = model

    async def run():
        with model_policy_scope("task"):
            if allowed:
                await client.generate_content("synthetic", model=model)
            else:
                with pytest.raises(ModelPolicyError):
                    await client.generate_content("synthetic", model=model)

    asyncio.run(run())
    assert len(calls) == int(allowed)


def test_task_accepts_discovered_argon_alias(runtime):
    from src.services.model_policy import model_policy_scope

    _server, calls, _state = runtime
    client = assist.get_gemini_client()
    client._model_registry["gemini-argon"].aliases = ["4 Argon"]

    async def run():
        with model_policy_scope("task"):
            await client.generate_content("synthetic", model="4 Argon")

    asyncio.run(run())
    assert len(calls) == 1 and calls[0]["model"].model_name == "gemini-argon"


@pytest.mark.parametrize(("requested", "expected"), [
    ("low", "low"), ("medium", "medium"), ("high", "high"),
    ("低", "low"), ("中", "medium"), ("高", "high"),
    ("standard", "standard"), ("extended", "extended"),
])
def test_selection_metadata_preserves_requested_thinking_level(runtime, requested, expected):
    from src.services.model_policy import model_selection_from_response

    _server, calls, _state = runtime
    client = assist.get_gemini_client()
    response = asyncio.run(client.generate_content("synthetic", model="flash", thinking_level=requested))
    selection = model_selection_from_response(response)
    assert selection is not None and selection.effective_thinking_level == expected
    assert len(calls) == 1


def test_medium_quota_error_does_not_trigger_lite_for_next_low_query(runtime):
    from src.services.model_policy import model_policy_scope

    _server, calls, state = runtime
    client = assist.get_gemini_client()

    async def run():
        with model_policy_scope("query", allow_fallback=True):
            state["quota_error"] = True
            with pytest.raises(UsageLimitExceededError):
                await client.generate_content("synthetic medium", model="flash", thinking_level="medium")
            state["quota_error"] = False
            await client.generate_content("synthetic low", model="flash", thinking_level="low")

    asyncio.run(run())
    assert [call["model"].model_name for call in calls] == ["gemini-flash", "gemini-flash"]


def test_medium_success_does_not_clear_basic_flash_quota_evidence(runtime):
    from src.infrastructure.account_identity import current_account_key
    from src.infrastructure.model_policy_store import ModelPolicyStore
    from src.services.model_policy import model_policy_scope

    _server, calls, _state = runtime
    client = assist.get_gemini_client()
    store = ModelPolicyStore()
    account = current_account_key()
    assert account is not None
    store.record_flash_exhausted(account)

    async def run():
        with model_policy_scope("query", allow_fallback=False):
            await client.generate_content("synthetic medium", model="flash", thinking_level="medium")

    asyncio.run(run())
    assert len(calls) == 1 and calls[0]["model"].model_name == "gemini-flash"
    assert store.flash_exhausted(account)
