"""Offline vertical contracts for creation, durable recovery and account facades."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jsonschema
import pytest
from mcp import Client
from pydantic import TypeAdapter, ValidationError

import src.services.account_facade as account_service
import src.services.operations as operations_service
import src.surfaces.account as account
import src.surfaces.create as create
from scripts.smoke_profiles import ACCOUNT_TOOLS, CREATE_TOOLS
from src.domain import ArtifactResultData, ArtifactState, DomainResult
from src.domain.account_requests import AccountRequest, GemsRequest, HistoryRequest, NotebooksRequest, PromptsRequest, ScheduledRequest
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.state_store import StateStore, StateStoreError
from src.services.account_facade import AccountFacadeService
from src.services.creation import CreationService
from src.services.operations import OperationContext, OperationService
from tests.media_fixtures import fake_finalize_generated_cleanup, write_audio, write_image
from tests.test_media_generation_workflows import Image
from tests.test_media_tools import _FakeMediaClient


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("surface,catalog,name,args,ok", [
    (create, CREATE_TOOLS, "gemini_generate_image", {"prompt": ""}, False),
    (account, ACCOUNT_TOOLS, "gemini_account", {"request": {"action": "capabilities"}}, True),
])
def test_focused_sdk_catalog_direct_output_and_text_compatibility(mode, surface, catalog, name, args, ok):
    async def run():
        async with Client(surface.mcp, mode=mode, cache=None) as client:
            listed = await client.list_tools()
            result = await client.call_tool(name, args)
            assert client.server_info.name == surface.SERVER_NAME
        assert {tool.name for tool in listed.tools} == catalog
        tool = next(tool for tool in listed.tools if tool.name == name)
        assert all(item.output_schema for item in listed.tools)
        jsonschema.validate(result.structured_content, tool.output_schema)
        assert result.is_error is False
        assert result.structured_content["ok"] is ok
        assert result.content[0].meta["domain_result"] == result.structured_content
        if not ok:
            assert result.structured_content["error"]["code"] == "INVALID_ARGUMENT"
    asyncio.run(run())


def test_focused_image_and_edit_verify_real_local_bytes(monkeypatch, tmp_path):
    image = Image()
    client = _FakeMediaClient(images=[image])
    finalizer = AsyncMock(side_effect=fake_finalize_generated_cleanup)
    service = CreationService(client_provider=lambda: client, initializer=AsyncMock(),
                              cleanup_due=AsyncMock(), finalizer=finalizer)
    monkeypatch.setattr(create, "get_creation_service", lambda: service)
    source = write_image(tmp_path / "input.png")

    async def run():
        async with Client(create.mcp, cache=None) as connection:
            generated = await connection.call_tool("gemini_generate_image", {"prompt": "blue roof", "output_dir": str(tmp_path)})
            edited = await connection.call_tool("gemini_edit_image", {"prompt": "yellow roof", "image_path": str(source), "output_dir": str(tmp_path), "model": "pro"})
        for response in (generated, edited):
            data = response.structured_content["data"]
            assert data["state"] == "local"
            item = data["artifacts"][0]
            assert item["verification"]["status"] == "verified"
            assert Path(item["local_path"]).read_bytes().startswith(b"\x89PNG")
            assert response.structured_content["meta"]["details"]["cleanup"]["state"] == "completed"
        assert edited.structured_content["data"]["input_artifacts"][0]["id"] != edited.structured_content["data"]["artifacts"][0]["id"]
        assert client.captured_generate_kwargs["media_mode"] == "image"
        assert client.captured_generate_kwargs["model"] == "gemini-3-flash"
        assert edited.structured_content["data"]["effective_backend"] == "Nano Banana 2"
        assert client.captured_generate_kwargs["files"] == [str(source)]
    asyncio.run(run())
    assert finalizer.await_count == 2


def test_async_media_response_loss_idempotency_restart_recovery_and_no_prompt_persistence(monkeypatch, tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    old = OperationService(store, lambda: "scope_fixture")
    observed = asyncio.Event()
    calls = []
    prompt = "PRIVATE_PROMPT_MUST_NOT_PERSIST"

    class PausedCreation(CreationService):
        async def generate(self, request, on_chat_observed=None, **_kwargs):
            calls.append(request)
            on_chat_observed("c_focused")
            observed.set()
            await asyncio.Event().wait()
            return DomainResult.success(ArtifactResultData(state=ArtifactState.QUEUED))

    class Music:
        mp3_url = "https://cdn.test/music.wav"
        url = ""
        title = "fixture"

        async def save(self, *, path, filename, **_kwargs):
            return {"audio": str(write_audio(Path(path) / filename))}

    output = SimpleNamespace(metadata=["c_focused"], images=[], videos=[], media=[Music()], text="ready")
    remote = SimpleNamespace(
        read_chat=AsyncMock(return_value=SimpleNamespace(cid="c_focused", turns=[SimpleNamespace(role="model", model_output=output)])),
        generate_content=AsyncMock(side_effect=AssertionError("recovery must never generate")),
    )
    resumed = CreationService(client_provider=lambda: remote, initializer=AsyncMock(), cleanup_due=AsyncMock(),
                              finalizer=AsyncMock(side_effect=fake_finalize_generated_cleanup))
    monkeypatch.setattr(create, "get_creation_service", lambda: PausedCreation())
    monkeypatch.setattr(create, "get_operation_service", lambda: old)

    async def run():
        async with Client(create.mcp, cache=None) as connection:
            args = {"prompt": prompt, "output_dir": str(tmp_path / "media"), "retain_chat": True,
                    "delete_after_seconds": 60, "idempotency_key": "start_fixture_1"}
            first = await connection.call_tool("gemini_generate_music", args)
            handle = first.structured_content["data"]["operation_id"]
            await asyncio.wait_for(observed.wait(), 2)
            duplicate = await connection.call_tool("gemini_generate_music", args)
            assert duplicate.structured_content["data"]["operation_id"] == handle
            record = store.operations.get("scope_fixture", handle)
            assert record.upstream_chat_id == "c_focused"
            assert record.output_dir == str((tmp_path / "media").absolute())
            assert record.retain_chat is True and record.delete_after_seconds == 60
            assert calls[0].filename == handle
            assert len(calls) == 1
            pending = old._tasks[handle]
            pending.cancel()
            await pending
        fresh = OperationService(StateStore(store.path), lambda: "scope_fixture")
        monkeypatch.setattr(operations_service, "_service", fresh)
        monkeypatch.setattr(create, "get_creation_service", lambda: resumed)
        monkeypatch.setattr(create, "get_operation_service", lambda: fresh)
        async with Client(create.mcp, cache=None) as connection:
            recovered = await connection.call_tool("gemini_get_operation_result", {"operation_id": handle})
        payload = recovered.structured_content
        assert payload["ok"] is True and payload["data"]["state"] == "completed"
        artifact = payload["data"]["artifacts"][0]
        assert artifact["verification"]["status"] == "verified"
        assert Path(artifact["local_path"]).is_file()
        assert resumed._finalizer.call_args.kwargs["retain_chat"] is True
        assert remote.generate_content.await_count == 0 and remote.read_chat.await_count == 1
    asyncio.run(run())
    assert prompt.encode() not in store.path.read_bytes()


@pytest.mark.parametrize("contract_key,action_request", [
    ("sharing.public_links", {"action": "links"}),
    ("usage.quota", {"action": "usage", "scope": "quota"}),
    ("library.locale_capabilities", {"action": "library"}),
    ("tool_modes.status", {"action": "modes"}),
])
@pytest.mark.parametrize("case", ["missing", "rejected", "changed_shape", "empty"])
def test_account_inventory_never_invents_empty_after_missing_or_rejected_rpc(contract_key, action_request, case):
    contract = get_contract(contract_key)
    fixture = json.loads((Path(__file__).parent / "fixtures/rpc_management_cases.json").read_text())[contract.parser]
    body = fixture["cases"]["empty" if case == "empty" else "changed_shape"]["body"]
    text = json.dumps([["wrb.fr", contract.rpc_id, json.dumps(body)]]) if case != "missing" else "[]"
    if case == "rejected":
        text = json.dumps([["er", contract.rpc_id, 7]])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=SimpleNamespace(text=text, status_code=200)))
    service = AccountFacadeService(client_provider=lambda: client, initializer=AsyncMock())
    result = asyncio.run(service.execute("account", TypeAdapter(AccountRequest).validate_python(action_request)))
    assert result.ok is (case == "empty")
    if case != "empty":
        assert result.error.code.value in {"UPSTREAM_REJECTED", "UPSTREAM_CHANGED"}
        assert result.meta.operation_state.value == "failed"


def test_account_mutation_accepted_does_not_claim_verified(monkeypatch):
    monkeypatch.setattr(account_service, "move_chat_to_notebook", AsyncMock(return_value={
        "accepted": True, "verified_in_target_notebook": False, "verification_status": "read_back_unavailable",
    }))
    service = AccountFacadeService(client_provider=lambda: object(), initializer=AsyncMock())
    request = TypeAdapter(NotebooksRequest).validate_python({"action": "move", "chat_id": "c_target", "notebook_id": "nb_target"})
    result = asyncio.run(service.execute("notebooks", request))
    assert result.ok and result.meta.operation_state.value == "accepted"
    assert result.meta.verification_status != "verified"
    assert result.meta.details["effect"] == "mutation"


@pytest.mark.parametrize("payload,completed", [
    ({"ok": True, "verification_status": "deleted_state_by_id", "deleted_by_id_after_delete": True,
      "task_state_id_after_delete": 6, "get_task_diagnostic": {"read_back_valid": True}}, True),
    ({"ok": True, "verification_status": "not_visible_not_readable_by_id", "visible_after_delete": False,
      "readable_by_id_after_delete": False, "registry_diagnostic": {"read_back_valid": True},
      "get_task_diagnostic": {"read_back_valid": True, "parser_status": "empty"}}, True),
    ({"ok": True, "verification_status": "deleted_state_by_id"}, False),
    ({"ok": True, "verification_status": "not_visible_not_readable_by_id", "visible_after_delete": False,
      "readable_by_id_after_delete": False, "registry_diagnostic": {"read_back_valid": False},
      "get_task_diagnostic": {"read_back_valid": True, "parser_status": "empty"}}, False),
    ({"ok": True, "verification_status": "registry_empty_not_readable_by_id"}, False),
    ({"ok": True, "verification_status": "verified_deleted"}, True),
    ({"ok": False, "verification_status": "verified_deleted"}, False),
])
def test_account_mutation_only_promotes_precise_accepted_read_back_proof(payload, completed):
    result = account_service._mutation(payload)
    assert (result.meta.operation_state.value == "completed") is completed


def test_notebook_facade_preserves_incomplete_pagination(monkeypatch):
    diagnostic = {"read_back_valid": True, "complete": False, "incomplete_reason": "pagination_not_progressing"}
    monkeypatch.setattr(account_service, "fetch_notebook_chats", AsyncMock(return_value=([], {
        "diagnostic": diagnostic, "next_offset": None,
    })))
    service = AccountFacadeService(client_provider=lambda: object(), initializer=AsyncMock())
    request = TypeAdapter(NotebooksRequest).validate_python({"action": "chats", "notebook_id": "nb_target"})
    result = asyncio.run(service.execute("notebooks", request))
    assert result.ok and result.meta.operation_state.value == "partial"
    assert result.data["diagnostic"] == diagnostic and result.data["next_offset"] is None
    assert result.meta.details["incomplete_reason"] == "pagination_not_progressing"
    assert result.warnings[0].code == "PAGINATION_INCOMPLETE"


def test_focused_gem_list_does_not_hide_missing_custom_registry():
    from gemini_webapi import GeminiClient

    class Client(GeminiClient):
        async def _batch_execute(self, requests, **_kwargs):
            assert len(requests) == 2
            return SimpleNamespace(status_code=200, text=json.dumps([[
                "wrb.fr", get_contract("gems.system_registry").rpc_id,
                json.dumps([None, None, [["g_system", ["System", "Description"], ["Instructions"]]]]),
                None, None, None, "system",
            ]]))

    service = AccountFacadeService(client_provider=lambda: Client(secure_1psid="fake-offline-cookie"), initializer=AsyncMock())
    request = TypeAdapter(GemsRequest).validate_python({"action": "list"})
    result = asyncio.run(service.execute("gems", request))
    assert result.ok and result.meta.operation_state.value == "partial"
    assert result.data["items"][0]["id"] == "g_system"
    assert result.data["diagnostic"]["complete"] is False
    assert result.data["diagnostic"]["custom"]["read_back_valid"] is False
    assert result.warnings[0].code == "GEM_REGISTRY_INCOMPLETE"


@pytest.mark.parametrize("persistence_fails", [False, True])
def test_async_creation_persists_verified_artifacts_before_source_cleanup(monkeypatch, tmp_path, persistence_fails):
    store = StateStore(tmp_path / "state.sqlite3")
    operations = OperationService(store, lambda: "scope_fixture")

    class Music:
        mp3_url = "https://cdn.test/created.wav"
        url = ""
        title = "fixture"

        async def save(self, *, path, filename, **_kwargs):
            return {"audio": str(write_audio(Path(path) / filename))}

    client = _FakeMediaClient(media=[Music()])
    retained = []

    async def finalizer(response, **kwargs):
        # This seam represents the delete RPC boundary, after actual saving and
        # verification. A process crash here must still leave local locators.
        assert len(operations._tasks) == 1
        record = store.operations.get("scope_fixture", next(iter(operations._tasks)))
        assert record is not None
        if persistence_fails:
            assert record.artifacts == ()
            assert kwargs["preserve_for_recovery"] is True
        else:
            assert record.artifacts[0].verification == "verified"
            assert Path(record.artifacts[0].local_path).is_file()
            assert kwargs["preserve_for_recovery"] is False
        retained.append(kwargs["preserve_for_recovery"])
        return await fake_finalize_generated_cleanup(response, **kwargs)

    creation = CreationService(client_provider=lambda: client, initializer=AsyncMock(),
                               cleanup_due=AsyncMock(), finalizer=finalizer)
    monkeypatch.setattr(create, "get_creation_service", lambda: creation)
    monkeypatch.setattr(create, "get_operation_service", lambda: operations)
    if persistence_fails:
        def fail_persistence(*_args, **_kwargs):
            raise StateStoreError("Offline persistence failure")
        monkeypatch.setattr(OperationContext, "observe_artifacts_sync", fail_persistence)

    async def run():
        async with Client(create.mcp, cache=None) as connection:
            result = await connection.call_tool("gemini_generate_music", {"prompt": "fixture", "output_dir": str(tmp_path)})
            handle = result.structured_content["data"]["operation_id"]
            await asyncio.wait_for(operations._tasks[handle], 3)
        assert retained == [persistence_fails]
    asyncio.run(run())


def test_local_prompt_facade_round_trip_without_client_access(tmp_path):
    service = AccountFacadeService(client_provider=lambda: (_ for _ in ()).throw(AssertionError("no remote calls")),
                                   prompts_path=tmp_path / "prompts.json")
    async def call(payload):
        return await service.execute("prompts", TypeAdapter(PromptsRequest).validate_python(payload))
    async def run():
        created = await call({"action": "create", "name": "Review", "content": "Review {code}"})
        identifier = created.data["item"]["id"]
        rendered = await call({"action": "render", "prompt_id": identifier, "variables": {"code": "print(1)"}})
        assert rendered.data["text"] == "Review print(1)"
        invalid = await call({"action": "render", "prompt_id": identifier})
        assert invalid.error.code.value == "INVALID_ARGUMENT"
        updated = await call({"action": "update", "prompt_id": identifier, "description": "fixture"})
        assert updated.data["item"]["content"] == "Review {code}"
        deleted = await call({"action": "delete", "prompt_id": identifier})
        assert deleted.data["deleted"] and deleted.meta.verification_status == "verified_absent"
    asyncio.run(run())


@pytest.mark.parametrize("model,payload", [
    (HistoryRequest, {"action": "delete", "chat_id": "  "}),
    (HistoryRequest, {"action": "list", "chat_id": "c_forbidden"}),
    (ScheduledRequest, {"action": "create_daily", "title": "T", "instructions": "I", "hour": 24}),
    (GemsRequest, {"action": "update", "gem_id": "g_1"}),
])
def test_action_specific_schema_rejects_ambiguous_or_irrelevant_inputs(model, payload):
    with pytest.raises(ValidationError):
        TypeAdapter(model).validate_python(payload)
