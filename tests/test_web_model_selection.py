"""Model discovery-to-SDK boundary tests with no account or network access."""

import asyncio
import json
from unittest.mock import AsyncMock

from gemini_webapi import GeminiClient
from gemini_webapi.constants import AccountStatus, MODEL_HEADER_KEY, Model
from gemini_webapi.types import AvailableModel, Candidate, ModelOutput
import pytest

from src.services.model_policy import ModelPolicyError, model_policy_scope, model_selection_from_response
from src.thinking_client import ThinkingLevelGeminiClient, WebRequestOptions, _web_request


@pytest.fixture(autouse=True)
def isolated_model_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-web-model-account")
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite3"))
    token = _web_request.set(None)
    yield
    _web_request.reset(token)


def discovered_client(*, old_flash=False):
    """Observed public registry identities with deliberately distinct test numbers."""
    rows = [
        ("1d44b34bcaa1c04d", "gemini-自动", "自动", ["自动"], 21),
        ("56fdd199312815e2", "gemini-4-hydrogen", "日常帮助", ["4 hydrogen"], 22),
        ("797f3d0293f288ad", "gemini-neon", "更深入的分析", ["neon"], 23),
        ("95a4c0abcba24364", "gemini-carbon", "代码和复杂工作", ["carbon"], 24),
    ]
    if old_flash:
        rows.append(("synthetic-legacy-flash-id", "gemini-flash", "Flash", ["flash"], 25))
    client = ThinkingLevelGeminiClient()
    client._running = True
    client.account_status = AccountStatus.AVAILABLE
    client._usage_info = {"tier": {"id": 6, "label": "ULTRA"}}
    client._model_registry = {
        model_id: AvailableModel(model_id=model_id, model_name=name, display_name=display,
                                 aliases=aliases, description="", capacity=2,
                                 model_number=number)
        for model_id, name, display, aliases, number in rows
    }
    return client


def observe_sdk_dispatch(monkeypatch):
    seen = []

    async def fake(_client, **kwargs):
        seen.append((kwargs, _web_request.get()))
        yield ModelOutput(metadata=["c_synthetic", "r_synthetic"],
                          candidates=[Candidate(rcid="rc_synthetic", text="Synthetic verified reply")])

    monkeypatch.setattr(GeminiClient, "_generate", fake)
    return seen


def invoke(client, model, *, purpose="query", extended=False):
    async def run():
        with model_policy_scope(purpose):
            return [reply async for reply in client._generate(prompt="synthetic model test", model=model,
                                                              extended_thinking=extended)]
    return asyncio.run(run())


@pytest.mark.parametrize("purpose", ["query", "task"])
@pytest.mark.parametrize("alias", ["neon", "4 Neon", "gemini-neon", "gemini-4-neon"])
def test_neon_dispatches_the_exact_discovered_descriptor_for_chat_and_task(monkeypatch, purpose, alias):
    client = discovered_client()
    seen = observe_sdk_dispatch(monkeypatch)
    replies = invoke(client, alias, purpose=purpose)
    assert len(seen) == 1
    assert seen[0][0]["model"] is client._model_registry["797f3d0293f288ad"]
    selection = model_selection_from_response(replies[0])
    assert selection.effective_model == "gemini-neon"
    assert selection.fallback_reason is None


@pytest.mark.parametrize("alias", ["argon", "4 Argon", "gemini-argon", "pro"])
def test_absent_legacy_model_refuses_without_dispatch_or_substitution(monkeypatch, alias):
    client = discovered_client()
    seen = observe_sdk_dispatch(monkeypatch)
    with pytest.raises(ModelPolicyError):
        invoke(client, alias)
    assert not seen


@pytest.mark.parametrize("default", [None, Model.UNSPECIFIED, "flash", "fast"])
def test_default_flash_prefers_hydrogen_when_legacy_flash_is_also_discovered(monkeypatch, default):
    client = discovered_client(old_flash=True)
    seen = observe_sdk_dispatch(monkeypatch)
    invoke(client, default)
    assert seen[0][0]["model"] is client._model_registry["56fdd199312815e2"]


@pytest.mark.parametrize("name", ["neon-typo", "unavailable-model", "gemini-neon-future"])
def test_unknown_selection_cannot_use_sdk_fuzzy_fallback(monkeypatch, name):
    client = discovered_client()
    seen = observe_sdk_dispatch(monkeypatch)
    with pytest.raises(ModelPolicyError):
        invoke(client, name)
    assert not seen


def test_discovered_unknown_family_is_rejected_by_verified_membership_policy(monkeypatch):
    client = discovered_client()
    client._model_registry["synthetic-future"] = AvailableModel(
        model_id="synthetic-future", model_name="gemini-future", display_name="Future model",
        aliases=[], description="", capacity=2,
    )
    seen = observe_sdk_dispatch(monkeypatch)
    with pytest.raises(ModelPolicyError):
        invoke(client, "gemini-future")
    assert not seen


def test_auto_keeps_its_own_descriptor_and_disables_selectable_thinking(monkeypatch):
    client = discovered_client()
    seen = observe_sdk_dispatch(monkeypatch)
    _web_request.set(WebRequestOptions(thinking_mode_id=9, thinking_level_id=2, thinking_level_name="high"))
    replies = invoke(client, "auto", extended=True)
    kwargs, options = seen[0]
    assert kwargs["model"] is client._model_registry["1d44b34bcaa1c04d"]
    assert kwargs["extended_thinking"] is False
    assert options.thinking_mode_id is None and options.thinking_level_id is None
    selection = model_selection_from_response(replies[0])
    assert selection.thinking_selectable is False
    assert selection.effective_thinking_level is None


@pytest.mark.parametrize("explicit", ["hydrogen", "56fdd199312815e2", "日常帮助"])
def test_explicit_hydrogen_is_not_replaced_after_compatibility_flash_quota_exhaustion(monkeypatch, explicit):
    from src.infrastructure.account_identity import current_account_key
    from src.infrastructure.model_policy_store import ModelPolicyStore

    client = discovered_client()
    client._model_registry["synthetic-lite"] = AvailableModel(
        model_id="synthetic-lite", model_name="gemini-flash-lite", display_name="Flash-Lite",
        aliases=["lite"], description="", capacity=1,
    )
    ModelPolicyStore().record_flash_exhausted(current_account_key())
    seen = observe_sdk_dispatch(monkeypatch)

    async def run():
        with model_policy_scope("query", allow_fallback=True):
            return [reply async for reply in client._generate(prompt="synthetic", model=explicit)]

    replies = asyncio.run(run())
    assert seen[0][0]["model"] is client._model_registry["56fdd199312815e2"]
    assert model_selection_from_response(replies[0]).fallback_reason is None


@pytest.mark.parametrize("purpose", ["query", "task"])
def test_unverified_membership_cannot_dispatch_advanced_models(monkeypatch, purpose):
    client = discovered_client()
    client._usage_info = {}
    seen = observe_sdk_dispatch(monkeypatch)
    with pytest.raises(ModelPolicyError, match="membership"):
        invoke(client, "neon", purpose=purpose)
    assert not seen


@pytest.mark.parametrize(("model", "model_id"), [
    ("hydrogen", "56fdd199312815e2"), ("neon", "797f3d0293f288ad"),
    ("carbon", "95a4c0abcba24364"), ("auto", "1d44b34bcaa1c04d"),
])
def test_installed_sdk_request_uses_registry_id_and_number_without_network(monkeypatch, model, model_id):
    client = discovered_client()
    captured = []

    class StopBeforeNetwork(BaseException):
        pass

    class Transport:
        def stream(self, *_args, **kwargs):
            envelope = json.loads(kwargs["data"]["f.req"])
            captured.append((json.loads(envelope[1]), json.loads(kwargs["headers"][MODEL_HEADER_KEY])))
            raise StopBeforeNetwork()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    client.client = Transport()
    client._install_thinking_transport()

    async def run():
        with model_policy_scope("query"):
            async for _ in client.generate_content_stream("synthetic request capture", model=model):
                pass

    with pytest.raises(StopBeforeNetwork):
        asyncio.run(run())
    assert len(captured) == 1
    body, header = captured[0]
    descriptor = client._model_registry[model_id]
    assert header[4] == descriptor.model_id
    assert header[14] == descriptor.model_number
    assert body[79] == descriptor.model_number
    # The SDK still emits its fixed standard default even for Auto. Disabling
    # selectable thinking in metadata does not prove this field is absent.
    if model == "auto":
        assert body[80] == 1 and header[15] == 1
