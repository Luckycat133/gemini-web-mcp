"""Account-bound model policy and synthetic SDK generation contracts."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.constants import MODEL_HEADER_KEY, Model
from gemini_webapi.exceptions import UsageLimitExceededError
from gemini_webapi.types import AvailableModel, Candidate, ModelOutput

from src.infrastructure.account_identity import current_account_key
from src.infrastructure.model_policy_store import ModelPolicyStore
from src.services.model_policy import ModelPolicyError, model_policy_scope, model_selection_from_response
from src.thinking_client import ThinkingLevelGeminiClient


@pytest.fixture(autouse=True)
def synthetic_account(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-policy-a")
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))


def client_for_models(*names):
    client = ThinkingLevelGeminiClient()
    client._running = True
    client._model_registry = {
        name: AvailableModel(model_id=name, model_name=name, display_name=name, description="", capacity=1)
        for name in names
    }
    return client


def test_policy_store_is_account_scoped_and_expires(tmp_path):
    now = [100.0]
    store = ModelPolicyStore(tmp_path / "policy.sqlite", clock=lambda: now[0])
    store.record_flash_exhausted("a")
    assert store.flash_exhausted("a") and not store.flash_exhausted("b")
    now[0] = 160.0
    assert not store.flash_exhausted("a")
    now[0] = 50.0
    assert not store.flash_exhausted("a")


@pytest.mark.parametrize("value", ["flash-lite", "gemini-flash-lite"])
def test_explicit_lite_without_evidence_is_rejected_before_generation(monkeypatch, value):
    calls = []
    async def fake(_client, **kwargs):
        calls.append(kwargs)
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    async def run():
        async for _ in client._generate(prompt="synthetic", model=value):
            pass
    with pytest.raises(ModelPolicyError) as failure:
        asyncio.run(run())
    assert calls == []
    from src.domain.results import _classify_exception
    assert _classify_exception(failure.value)[0].value == "CAPABILITY_UNAVAILABLE"


def test_quota_evidence_enables_only_future_query_fallback_without_thinking(monkeypatch):
    calls = []
    async def fake(_client, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise UsageLimitExceededError("synthetic quota")
        yield ModelOutput(metadata=["c_fixture", "r_fixture"], candidates=[Candidate(rcid="rc", text="answer")])
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    async def invoke(extended=False):
        with model_policy_scope("query", allow_fallback=True):
            return [response async for response in client._generate(prompt="synthetic", model="flash", extended_thinking=extended)]
    with pytest.raises(UsageLimitExceededError):
        asyncio.run(invoke())
    assert len(calls) == 1 and calls[0]["model"].model_name == "gemini-flash"
    response = asyncio.run(invoke(True))[0]
    assert len(calls) == 2 and calls[1]["model"].model_name == "gemini-flash-lite"
    assert calls[1]["extended_thinking"] is False
    selection = model_selection_from_response(response)
    assert (selection.requested_model, selection.effective_model, selection.fallback_reason) == (
        "flash", "gemini-flash-lite", "flash_quota_exhausted",
    )
    assert selection.thinking_selectable is False and selection.effective_thinking_level is None


def test_thinking_tier_quota_does_not_prove_base_flash_exhausted(monkeypatch):
    async def fake(_client, **_kwargs):
        raise UsageLimitExceededError("synthetic thinking quota")
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    async def invoke():
        with model_policy_scope("query", allow_fallback=True):
            return [item async for item in client._generate(prompt="synthetic", model="flash", extended_thinking=True)]
    with pytest.raises(UsageLimitExceededError):
        asyncio.run(invoke())
    assert not ModelPolicyStore().flash_exhausted(current_account_key())


@pytest.mark.parametrize("alias", ["argon", "4 argon", "gemini-4-argon"])
def test_argon_alias_uses_exact_discovered_family(monkeypatch, alias):
    seen = []
    async def fake(_client, **kwargs):
        seen.append(kwargs["model"].model_name)
        yield ModelOutput(metadata=["c_fixture", "r_fixture"], candidates=[Candidate(rcid="rc", text="answer")])
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-argon")
    async def invoke():
        return [item async for item in client._generate(prompt="synthetic", model=alias)]
    asyncio.run(invoke())
    assert seen == ["gemini-argon"]


def test_timeout_and_nonquery_quota_cannot_authorize_lite(monkeypatch):
    async def fake(_client, **kwargs):
        if kwargs.get("deep_research"):
            raise UsageLimitExceededError("synthetic")
        raise TimeoutError("synthetic")
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    async def invoke(research):
        with model_policy_scope("query", allow_fallback=True):
            return [item async for item in client._generate(prompt="synthetic", model="flash", deep_research=research)]
    with pytest.raises(TimeoutError):
        asyncio.run(invoke(False))
    with pytest.raises(UsageLimitExceededError):
        asyncio.run(invoke(True))
    assert not ModelPolicyStore().flash_exhausted(current_account_key())


def test_default_model_requires_discovered_flash(monkeypatch):
    async def fake(_client, **_kwargs):
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash-lite")
    async def invoke(model):
        return [item async for item in client._generate(prompt="synthetic", model=model)]
    for model in (None, Model.UNSPECIFIED):
        with pytest.raises(ModelPolicyError):
            asyncio.run(invoke(model))


def test_actual_sdk_lite_transport_keeps_fixed_default_thinking(monkeypatch):
    captured = []
    class Stop(BaseException):
        pass
    class Transport:
        def stream(self, *_args, **kwargs):
            body = json.loads(json.loads(kwargs["data"]["f.req"])[1])
            header = json.loads(kwargs["headers"][MODEL_HEADER_KEY])
            captured.append((body[80], header[-2], kwargs))
            raise Stop()
    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    client._running = True
    client.client = Transport()
    client._install_thinking_transport()
    ModelPolicyStore().record_flash_exhausted(current_account_key())
    async def invoke():
        with model_policy_scope("query", allow_fallback=True):
            async for _ in client.generate_content_stream("synthetic", model="flash", thinking_level="extended"):
                pass
    with pytest.raises(Stop):
        asyncio.run(invoke())
    assert len(captured) == 1 and captured[0][:2] == (1, 1)
    assert "current_retry" not in captured[0][2]


def test_descriptor_and_dict_cannot_disguise_lite(monkeypatch):
    async def fake(_client, **_kwargs):
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    lite = client._model_registry["gemini-flash-lite"]
    disguised = AvailableModel(model_id=lite.model_id, model_name="gemini-pro", display_name="Pro",
                               description="", capacity=1)
    custom = {"model_name": "gemini-pro", "model_header": lite.model_header}
    async def invoke(model):
        return [item async for item in client._generate(prompt="synthetic", model=model)]
    for model in (lite, disguised, custom, Model.BASIC_LITE):
        with pytest.raises(ModelPolicyError):
            asyncio.run(invoke(model))


@pytest.mark.parametrize(("name", "display", "aliases"), [
    ("gemini-flash-lite-preview", "Preview", []),
    ("gemini-3.5-flash-lite-latest", "Preview", []),
    ("gemini-experimental", "Preview", ["flash-lite"]),
    ("gemini-experimental", "Gemini Flash Lite", []),
])
def test_registry_lite_metadata_is_gated_for_all_model_forms(monkeypatch, name, display, aliases):
    calls = []
    async def fake(_client, **kwargs):
        calls.append(kwargs)
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash")
    lite = AvailableModel(model_id="synthetic-lite-id", model_name=name, display_name=display,
                          description="", capacity=1, aliases=aliases)
    client._model_registry[lite.model_id] = lite
    custom = {"model_name": "untrusted-description", "model_header": lite.model_header}
    async def invoke(model):
        return [item async for item in client._generate(prompt="synthetic", model=model)]
    for model in (name, lite.model_id, lite, custom):
        with pytest.raises(ModelPolicyError, match="verified Flash quota exhaustion"):
            asyncio.run(invoke(model))
    assert calls == []


def test_default_flash_rejects_ambiguous_registry_model_marked_lite(monkeypatch):
    calls = []
    async def fake(_client, **kwargs):
        calls.append(kwargs)
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash")
    client._model_registry["gemini-flash"].aliases = ["flash-lite"]
    async def invoke():
        return [item async for item in client._generate(prompt="synthetic", model=None)]
    with pytest.raises(ModelPolicyError, match="verified Flash quota exhaustion"):
        asyncio.run(invoke())
    assert not calls


def test_existing_lite_evidence_never_changes_research_or_session_flash(monkeypatch):
    calls = []
    async def fake(_client, **kwargs):
        calls.append(kwargs)
        yield ModelOutput(metadata=["c_fixture", "r_fixture"], candidates=[Candidate(rcid="rc", text="answer")])
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    store = ModelPolicyStore()
    store.record_flash_exhausted(current_account_key())
    async def invoke(purpose):
        with model_policy_scope(purpose):
            return [item async for item in client._generate(prompt="synthetic", model="flash")]
    for purpose in ("research", "session"):
        asyncio.run(invoke(purpose))
    assert [call["model"].model_name for call in calls] == ["gemini-flash", "gemini-flash"]
    assert store.flash_exhausted(current_account_key())


def test_confirmed_flash_success_clears_evidence_and_account_switch_does_not_share(monkeypatch):
    async def fake(_client, **_kwargs):
        yield ModelOutput(metadata=["c_fixture", "r_fixture"], candidates=[Candidate(rcid="rc", text="answer")])
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    store = ModelPolicyStore()
    account_a = current_account_key()
    store.record_flash_exhausted(account_a)
    async def invoke():
        with model_policy_scope("query", allow_fallback=False):
            return [item async for item in client._generate(prompt="synthetic", model="flash")]
    monkeypatch.setenv("GEMINI_PSID", "synthetic-policy-b")
    asyncio.run(invoke())
    assert store.flash_exhausted(account_a) and not store.flash_exhausted(current_account_key())
    monkeypatch.setenv("GEMINI_PSID", "synthetic-policy-a")
    asyncio.run(invoke())
    assert not store.flash_exhausted(account_a)


def test_quota_error_after_account_change_does_not_create_evidence(monkeypatch):
    async def fake(_client, **_kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-policy-b")
        raise UsageLimitExceededError("synthetic")
        yield None
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    account_a = current_account_key()
    async def invoke():
        with model_policy_scope("query", allow_fallback=True):
            return [item async for item in client._generate(prompt="synthetic", model="flash")]
    with pytest.raises(UsageLimitExceededError):
        asyncio.run(invoke())
    assert not ModelPolicyStore().flash_exhausted(account_a)
    assert not ModelPolicyStore().flash_exhausted(current_account_key())


def test_newer_concurrent_quota_evidence_is_not_cleared_by_older_success(tmp_path):
    now = [100.0]
    store = ModelPolicyStore(tmp_path / "policy.sqlite", clock=lambda: now[0])
    start = now[0]
    now[0] = 101.0
    store.record_flash_exhausted("a")
    store.clear_flash_exhausted("a", observed_before=start)
    assert store.flash_exhausted("a")


@pytest.mark.parametrize("variant", ["extended", "learning"])
def test_extended_or_learning_flash_success_does_not_clear_base_quota(monkeypatch, variant):
    from src.thinking_client import WebRequestOptions, _web_request

    async def fake(_client, **_kwargs):
        yield ModelOutput(metadata=["c_fixture", "r_fixture"], candidates=[Candidate(rcid="rc", text="answer")])
    monkeypatch.setattr(GeminiClient, "_generate", fake)
    client = client_for_models("gemini-flash", "gemini-flash-lite")
    account = current_account_key()
    store = ModelPolicyStore()
    store.record_flash_exhausted(account)
    async def invoke():
        options = WebRequestOptions(learning_mode_id=3) if variant == "learning" else None
        token = _web_request.set(options)
        try:
            with model_policy_scope("query"):
                return [item async for item in client._generate(prompt="synthetic", model="flash",
                                                               extended_thinking=variant == "extended")]
        finally:
            _web_request.reset(token)
    asyncio.run(invoke())
    assert store.flash_exhausted(account)
