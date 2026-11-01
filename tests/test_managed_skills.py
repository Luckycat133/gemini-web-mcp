import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain.managed_skills import ManagedSkill
from src.infrastructure.managed_skills import get_payload, parse_get, parse_list
from src.services.managed_skills import ManagedSkillDependencies, ManagedSkillService


CASES = json.loads((Path(__file__).parent / "fixtures/rpc_contracts/managed_skills.json").read_text())


def test_list_projects_metadata_and_rejects_changed_or_duplicate_shapes():
    parsed = parse_list(CASES["managed_skills_list"]["cases"]["success"]["body"])
    assert parsed.status == "success" and parsed.value[0].instructions is None
    assert parse_list(CASES["managed_skills_list"]["cases"]["empty"]["body"]).status == "empty"
    assert parse_list(CASES["managed_skills_list"]["cases"]["changed_shape"]["body"]).status == "changed_shape"
    assert parse_list([[CASES["managed_skills_get"]["cases"]["success"]["body"][0]] * 2]).status == "changed_shape"


def test_exact_get_requires_explicit_instructions_and_rejects_mismatch():
    body = CASES["managed_skills_get"]["cases"]["success"]["body"]
    assert parse_get(body, skill_id="skill-fixture").value.instructions is None
    assert parse_get(body, skill_id="skill-fixture", include_instructions=True).value.instructions == "private instructions"
    assert parse_get(body, skill_id="another").status == "changed_shape"
    assert parse_get(CASES["managed_skills_get"]["cases"]["empty"]["body"], skill_id="skill-fixture").status == "empty"
    assert parse_get(CASES["managed_skills_get"]["cases"]["changed_shape"]["body"], skill_id="skill-fixture").status == "changed_shape"
    assert json.loads(get_payload(skill_id="skill-fixture")) == ["skill-fixture"]
    with pytest.raises(ValueError):
        get_payload(skill_id=" ")


def test_plain_skill_file_allows_observed_markdown_mime_but_not_other_content():
    body = [["skill-fixture", "Synthetic", "Description", "instruction", True, None,
             [[["SKILL.md", "synthetic body", "text/markdown"]]]]]
    assert parse_get(body, skill_id="skill-fixture", include_instructions=True).value.plain_editable
    body[0][6][0].append(["extra.txt", "extra"])
    assert not parse_get(body, skill_id="skill-fixture", include_instructions=True).value.plain_editable


def test_service_redacts_list_and_fences_account_change():
    account = ["synthetic-a"]
    item = ManagedSkill("skill-fixture", "Synthetic", "Description", True, None, "secret")
    async def read(*_args, **_kwargs):
        return (item,)
    deps = ManagedSkillDependencies(lambda: object(), AsyncMock(), read, lambda: account[0])
    result = asyncio.run(ManagedSkillService(deps).read())
    assert result.ok and result.data.items[0].instructions is None
    async def switch(*_args, **_kwargs):
        account[0] = "synthetic-b"
        return (item,)
    result = asyncio.run(ManagedSkillService(ManagedSkillDependencies(
        lambda: object(), AsyncMock(), switch, lambda: account[0])).read())
    assert not result.ok and result.data is None


def test_service_requires_exact_id_for_instructions_and_bounds_content():
    item = ManagedSkill("skill-fixture", "Synthetic", "Description", True, None, "0123456789")
    read = AsyncMock(return_value=item)
    service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), read, lambda: "account"))
    result = asyncio.run(service.read(include_instructions=True))
    assert not result.ok and read.await_count == 0
    result = asyncio.run(service.read(skill_id="skill-fixture", include_instructions=True, max_chars=4))
    assert result.ok and result.data.instructions == "0123" and result.meta.details["instructions_truncated"]


def test_transport_checks_envelope_and_does_not_assume_empty_get_is_absence(monkeypatch):
    from types import SimpleNamespace
    from src.infrastructure import managed_skills as wire
    from src.infrastructure.rpc_parsers import RPCEnvelope

    monkeypatch.setattr(wire, "current_account_key", lambda: "account")
    monkeypatch.setattr(wire, "get_contract", lambda _key: SimpleNamespace(rpc_id="synthetic"))
    execute = AsyncMock(return_value=SimpleNamespace(status_code=200, text="synthetic"))
    monkeypatch.setattr(wire, "execute_contract", execute)
    envelopes = [RPCEnvelope(True, 1, (CASES["managed_skills_list"]["cases"]["success"]["body"],)),
                 RPCEnvelope(True, 1, (), reject_code=403),
                 RPCEnvelope(True, 1, ([],))]
    monkeypatch.setattr(wire, "parse_rpc_envelope", lambda *_args: envelopes.pop(0))

    async def run():
        result = await wire.read_skills(object(), "managed_skills.list")
        assert result[0].instructions is None
        with pytest.raises(ValueError):
            await wire.read_skills(object(), "managed_skills.list")
        with pytest.raises(ValueError):
            await wire.read_skills(object(), "managed_skills.get", skill_id="skill-fixture")

    asyncio.run(run())
    assert execute.await_count == 3
    assert all(call.kwargs["retry_count"] == 0 for call in execute.await_args_list)


def test_tool_registration_and_destructive_annotation():
    from mcp import Client
    from src.adapters.mcp_sdk import MCPServer
    from src.tools.managed_skills import register_managed_skill_tools

    server = MCPServer("test-managed-skills")
    register_managed_skill_tools(server)

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            return await client.list_tools()

    listed = asyncio.run(run())
    tool = next(item for item in listed.tools if item.name == "gemini_skills")
    assert tool.annotations.destructive_hint is True


def test_create_uploads_single_skill_md_once_and_verifies_readback():
    import io
    import zipfile

    calls = []
    async def upload(_client, content, filename):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert archive.namelist() == ["SKILL.md"]
            text = archive.read("SKILL.md").decode()
            assert 'name: synthetic-skill' in text and "fixture instruction" in text
        calls.append(("upload", filename))
        return "private-upload-handle"
    async def mutate(_client, action, **kwargs):
        calls.append((action, kwargs["upload_handle"]))
        return "skill-fixture"
    async def read(_client, key, **kwargs):
        calls.append(("read", key))
        return ManagedSkill("skill-fixture", "synthetic-skill", "synthetic description", True, None,
                            "fixture instruction", True)
    service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), read, lambda: "account", upload, mutate))
    result = asyncio.run(service.mutate("create", title="synthetic-skill", description="synthetic description",
                                        instructions="fixture instruction"))
    assert result.ok and result.data.verified and result.data.skill_id == "skill-fixture"
    assert [entry[0] for entry in calls] == ["upload", "create", "read"]
    assert "private-upload-handle" not in str(result)


def test_sent_lost_mutation_is_not_replayed_and_preserves_unknown():
    calls = AsyncMock(side_effect=TimeoutError())
    read = AsyncMock(return_value=ManagedSkill("skill-fixture", "test", "d", True, None))
    service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), read, lambda: "account",
                                                            mutate=calls))
    result = asyncio.run(service.mutate("delete", skill_id="skill-fixture"))
    assert not result.ok and result.data.changed is None and result.meta.verification_status == "mutation_unverified"
    assert calls.await_count == 1 and read.await_count == 1


def test_delete_requires_complete_fresh_list_absence():
    item = ManagedSkill("skill-fixture", "test", "d", True, None)
    async def read(_client, key, **_kwargs):
        return item if key.endswith("get") else (item,)
    mutation = AsyncMock()
    service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), read, lambda: "account", mutate=mutation))
    result = asyncio.run(service.mutate("delete", skill_id="skill-fixture"))
    assert not result.ok and result.data.changed is None and mutation.await_count == 1


def test_update_refuses_attached_or_unknown_skill_files():
    item = ManagedSkill("skill-fixture", "test", "d", True, None, "instructions", False)
    upload, mutation = AsyncMock(), AsyncMock()
    service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), AsyncMock(return_value=item),
                                                            lambda: "account", upload, mutation))
    result = asyncio.run(service.mutate("update", skill_id="skill-fixture", title="new-name"))
    assert not result.ok and result.error.code.value == "CAPABILITY_UNAVAILABLE"
    assert upload.await_count == mutation.await_count == 0


def test_account_switch_after_mutation_scrubs_identity_and_never_reads_new_account():
    account = ["a"]
    read = AsyncMock(return_value=ManagedSkill("skill-fixture", "test", "d", True, None))
    async def mutation(*_args, **_kwargs):
        account[0] = "b"
    service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), read,
                                                            lambda: account[0], mutate=mutation))
    result = asyncio.run(service.mutate("delete", skill_id="skill-fixture"))
    assert not result.ok and result.data is None and read.await_count == 1


def test_enable_and_delete_require_independent_exact_readback():
    for action in ("enable", "delete"):
        before = ManagedSkill("skill-fixture", "test", "description", False, None)
        after = ManagedSkill("skill-fixture", "test", "description", True, None)
        read = AsyncMock(side_effect=[before, after if action == "enable" else ()])
        mutation = AsyncMock()
        service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), read,
                                                                lambda: "account", mutate=mutation))
        result = asyncio.run(service.mutate(action, skill_id="skill-fixture",
                                             enabled=True if action == "enable" else None))
        assert result.ok and result.data.verified and result.meta.verification_status == "independent_readback"
        assert mutation.await_count == 1 and read.await_count == 2


def test_plain_skill_update_preserves_unspecified_fields():
    before = ManagedSkill("skill-fixture", "old-name", "old description", False, None, "original instructions", True)
    after = ManagedSkill("skill-fixture", "new-name", "old description", False, None, "original instructions", True)
    read = AsyncMock(side_effect=[before, after])
    upload = AsyncMock(return_value="private-handle")
    mutation = AsyncMock()
    service = ManagedSkillService(ManagedSkillDependencies(lambda: object(), AsyncMock(), read,
                                                            lambda: "account", upload, mutation))
    result = asyncio.run(service.mutate("update", skill_id="skill-fixture", title="new-name"))
    assert result.ok and result.data.verified
    assert mutation.await_args.kwargs["enabled"] is False
    assert upload.await_count == mutation.await_count == 1


def test_native_skill_description_yaml_matches_editor():
    from src.services.managed_skills import _skill_description_yaml

    assert _skill_description_yaml("simple") == "simple"
    assert _skill_description_yaml("one: two") == '"one: two"'
    assert _skill_description_yaml("line one\nline two") == "|\n  line one\n  line two"
    assert _skill_description_yaml('quote " and \\') == '"quote \\" and \\\\"'


def test_unauthenticated_initialization_stops_skill_reads_and_mutations():
    from src.client_manager import ClientAuthenticationRequired
    initializer = AsyncMock(side_effect=ClientAuthenticationRequired("synthetic"))
    read, upload, mutate = AsyncMock(), AsyncMock(), AsyncMock()
    service = ManagedSkillService(ManagedSkillDependencies(
        lambda: object(), initializer, read, lambda: "account", upload, mutate))
    for result in (asyncio.run(service.read()), asyncio.run(service.mutate("delete", skill_id="fixture"))):
        assert not result.ok and result.error.code.value == "AUTH_REQUIRED"
    assert read.await_count == upload.await_count == mutate.await_count == 0
