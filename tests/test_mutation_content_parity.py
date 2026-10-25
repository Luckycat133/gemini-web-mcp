"""Typed MCP mutations preserve exact content and require matching read-back."""

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

from mcp import Client
from pydantic import TypeAdapter
import pytest

from src.adapters.mcp_sdk import MCPServer
from src.domain.account_requests import AccountRequest, NotebooksRequest
from src.infrastructure.canvas_contracts import replace_plain_document
from src.onboarding import domain_result_from_call
from src.services.account_facade import AccountFacadeService
import src.skill_server as compact
import src.surfaces.account as focused
import src.tools.manage as primary
from tests.test_canvas_edits import ARTIFACT_ID, calls, document, response
from tests.test_native_notebook_crud import Client as NotebookClient, record, source_record
from tests.test_native_skills_service import Client as SkillClient, record as skill_record


def server_for(monkeypatch, surface, remote):
    service = AccountFacadeService(client_provider=lambda: remote, initializer=AsyncMock())
    module = {"primary": primary, "compact": compact, "focused": focused}[surface]
    monkeypatch.setattr(module, "get_account_facade_service", lambda: service)
    if surface == "primary":
        server = MCPServer("mutation-content-primary")
        primary.register_manage_tools(server)
        return server
    return module.mcp


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
@pytest.mark.parametrize("text", ["  indented\n\n", " \n\t ", ""])
def test_canvas_replacement_preserves_exact_text_through_mcp_and_id_normalization(monkeypatch, surface, text):
    before = document()
    after = replace_plain_document(before, text)
    remote = SimpleNamespace(_batch_execute=AsyncMock(side_effect=[
        response("canvas.get", before), response("canvas.update", []), response("canvas.get", after),
    ]))
    server = server_for(monkeypatch, surface, remote)

    async def run():
        async with Client(server, cache=None) as connection:
            return domain_result_from_call(await connection.call_tool(
                "account" if surface == "compact" else "gemini_account",
                {"request": {"action": "canvas", "request": {
                    "action": "edit", "artifact_id": f" {ARTIFACT_ID} ", "text": text,
                }}},
            ))

    result = asyncio.run(run())
    assert result["ok"] and result["meta"]["operation_state"] == "completed"
    assert result["data"]["artifact_id"] == ARTIFACT_ID and result["data"]["updated"] is True
    submitted = calls(remote)
    assert len(submitted) == 3 and submitted[1][0] == "taAw7b"
    updated = submitted[1][1][1]
    assert updated[4] == updated[13][0] == text
    assert updated[8] == updated[13][2] == [[[None, [None, 0, text]]]]


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
def test_notebook_source_wrong_readback_kind_stays_accepted_across_mcp(monkeypatch, surface):
    remote = NotebookClient(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [record()]),
                            ("notebooks.get", [record()]))
    server = server_for(monkeypatch, surface, remote)

    async def run():
        async with Client(server, cache=None) as connection:
            request = {"action": "add_source", "notebook_id": "notebooks/fixture", "display_name": "Fixture source",
                       "url": "https://example.com/requested"}
            return domain_result_from_call(await connection.call_tool(
                "account" if surface == "compact" else "gemini_notebooks",
                {"notebook_request" if surface == "compact" else "request": request},
            ))

    result = asyncio.run(run())
    assert result["ok"] and result["meta"]["operation_state"] == "accepted"
    assert result["data"]["verified"] is False and result["data"]["source_name"] == "sources/fixture"
    assert len(remote.calls) == 3


@pytest.mark.parametrize("surface", ["primary", "compact", "focused"])
def test_notebook_source_preserves_code_indentation_and_blank_lines_through_mcp(monkeypatch, surface):
    text = "    print('fixture')\n\n"
    observed = source_record(text=text)
    remote = NotebookClient(("notebooks.get", [record(sources=False)]), ("notebooks.add_source", [observed]),
                            ("notebooks.get", [observed]))
    server = server_for(monkeypatch, surface, remote)

    async def run():
        async with Client(server, cache=None) as connection:
            request = {"action": "add_source", "notebook_id": " notebooks/fixture ", "display_name": " Fixture source ",
                       "text": text}
            return domain_result_from_call(await connection.call_tool(
                "account" if surface == "compact" else "gemini_notebooks",
                {"notebook_request" if surface == "compact" else "request": request},
            ))

    result = asyncio.run(run())
    assert result["ok"] and result["meta"]["operation_state"] == "completed"
    assert result["data"]["item"]["text"] == text
    assert remote.calls[1][1][0] == "notebooks/fixture"
    assert remote.calls[1][1][1][1] == "Fixture source"
    assert remote.calls[1][1][1][5] == [text, 1]


def test_notebook_source_whitespace_is_preserved_by_schema_and_rejected_by_service():
    request = TypeAdapter(NotebooksRequest).validate_python({
        "action": "add_source", "notebook_id": "notebooks/fixture", "display_name": "Fixture source", "text": " \n\t ",
    })
    assert request.text == " \n\t "
    remote = NotebookClient()
    service = AccountFacadeService(client_provider=lambda: remote, initializer=AsyncMock())
    result = asyncio.run(service.execute("notebooks", request))
    assert result.error_code == "INVALID_ARGUMENT" and not remote.calls


@pytest.mark.parametrize("resource", ["notebooks", "skills"])
@pytest.mark.parametrize("action", ["create", "update"])
def test_new_instruction_fields_preserve_exact_body_through_schema_and_request_builder(resource, action):
    text = "    print('fixture instruction')\n\n"
    if resource == "notebooks":
        payload = {"action": action, "title": " Fixture Notebook ", "instructions": text}
        if action == "update":
            payload["notebook_id"] = " notebooks/fixture "
        request = TypeAdapter(NotebooksRequest).validate_python(payload)
        responses = [("notebooks.get", [record(instructions="", sources=False)]), ("notebooks.update", [True]),
                     ("notebooks.get", [record(instructions=text, sources=False)])]
        if action == "create":
            responses.insert(0, ("notebooks.create", ["notebooks/fixture"]))
        remote = NotebookClient(*responses)
        service = AccountFacadeService(client_provider=lambda: remote, initializer=AsyncMock())
        result = asyncio.run(service.execute("notebooks", request))
        assert result.operation_state == "completed"
        written = next(payload for rpc, payload, _path in remote.calls if rpc == "kHv0Vd")
        assert written[0] == "notebooks/fixture" and written[1][0] == "Fixture Notebook"
        assert written[1][2] == text
    else:
        payload = {"action": action, "name": " fixture-skill ", "description": " Fixture description ", "instructions": text}
        if action == "update":
            payload["skill_id"] = " skills/fixture "
        request = TypeAdapter(AccountRequest).validate_python({"action": "skills", "request": payload})
        observed = skill_record()
        observed[3] = text
        responses = [("native_skills.save", ["skills/fixture"]), ("native_skills.get", [observed])]
        if action == "update":
            responses.insert(0, ("native_skills.get", [skill_record()]))
        remote = SkillClient(*responses)
        service = AccountFacadeService(client_provider=lambda: remote, initializer=AsyncMock())
        result = asyncio.run(service.execute("account", request))
        assert result.operation_state == "completed"
        with zipfile.ZipFile(io.BytesIO(remote.client.calls[1]["data"])) as archive:
            markdown = archive.read("SKILL.md").decode("utf-8")
        assert markdown.partition("\n---\n")[2] == text
        assert "name: fixture-skill\n" in markdown
        assert remote.calls[-1][1] == ["skills/fixture"]
