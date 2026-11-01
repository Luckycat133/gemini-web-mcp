import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.infrastructure.notebook_admin import parse_created, parse_project, source_create_payload
from src.services.notebook_admin import NotebookAdminDependencies, NotebookAdminService


def test_project_strict_map_and_identity():
    metadata = ["Synthetic"] + [None] * 9 + [[None, [["source-fixture", ["source-fixture", "title", None, None, None, None, 1, None, "text/plain"]]]]]
    body = [["notebook-fixture", metadata]]
    result = parse_project(body, notebook_id="notebook-fixture")
    assert result.status == "success" and result.value["sources"][0]["state"] == 1
    assert parse_project(body, notebook_id="other").status == "changed_shape"
    metadata[10][1][0][0] = "other"
    assert parse_project(body).status == "changed_shape"
    assert parse_created(["fixture"]).value == "fixture"
    assert "synthetic" in source_create_payload(notebook_id="fixture", title="title", text="synthetic")


def test_add_source_only_verifies_observed_metadata():
    before = {"notebook_id": "project", "title": "Synthetic", "sources": []}
    after = {"notebook_id": "project", "title": "Synthetic", "sources": [{"source_id": "src", "title": "fixture", "state": 1}]}
    request = AsyncMock(side_effect=[SimpleNamespace(status="success", value=before), SimpleNamespace(status="success", value=None), SimpleNamespace(status="success", value=after)])
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, AsyncMock(return_value=([{"id": "project", "project_type": 2, "project_subtype": 1}], {})), lambda: "account"))
    result = asyncio.run(service.run("add_source", notebook_id="project", title="fixture", text="nonce"))
    assert result.ok and result.data.source_id == "src" and not result.data.content_verified
    assert request.await_count == 3


def test_mutation_timeout_never_replays_and_account_change_scrubs():
    before = {"notebook_id": "project", "title": "Synthetic", "sources": [{"source_id": "src", "title": "fixture", "state": 1}]}
    account = ["a"]
    async def request(_client, key, **_kw):
        if key.endswith("get"):
            return SimpleNamespace(status="success", value=before)
        account[0] = "b"
        raise TimeoutError
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, AsyncMock(return_value=([{"id": "project", "project_type": 2, "project_subtype": 1}], {})), lambda: account[0]))
    result = asyncio.run(service.run("delete_source", notebook_id="project", source_id="src"))
    assert not result.ok and result.data is None and result.meta.verification_status == "mutation_unverified"


def test_delete_requires_exact_project_list_membership_before_dispatch():
    before = {"notebook_id": "project", "title": "Synthetic", "sources": []}
    request = AsyncMock(return_value=SimpleNamespace(status="success", value=before))
    projects = AsyncMock(return_value=([{"id": "other"}], {}))
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, projects, lambda: "account"))
    result = asyncio.run(service.run("delete", notebook_id="project"))
    assert not result.ok and result.meta.verification_status == "read_unverified"
    assert request.await_count == 0


def test_delete_uses_fresh_complete_catalog_after_single_dispatch():
    before = {"notebook_id": "project", "title": "Synthetic", "sources": []}
    request = AsyncMock(side_effect=[SimpleNamespace(status="success", value=before), SimpleNamespace(status="empty", value=None)])
    projects = AsyncMock(side_effect=[([{"id": "project", "project_type": 2, "project_subtype": 1}, {"id": "other"}], {}), ([{"id": "other"}], {})])
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, projects, lambda: "account"))
    result = asyncio.run(service.run("delete", notebook_id="project"))
    assert result.ok and result.data.verified and request.await_count == projects.await_count == 2


def test_unknown_source_is_rejected_before_mutation():
    before = {"notebook_id": "project", "title": "Synthetic", "sources": []}
    request = AsyncMock(return_value=SimpleNamespace(status="success", value=before))
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, AsyncMock(return_value=([{"id": "project", "project_type": 2, "project_subtype": 1}], {})), lambda: "account"))
    result = asyncio.run(service.run("delete_source", notebook_id="project", source_id="unknown"))
    assert not result.ok and request.await_count == 1


def test_registration_reports_destructive_union():
    from mcp import Client
    from src.adapters.mcp_sdk import MCPServer
    from src.tools.notebook_admin import register_notebook_admin_tools
    server = MCPServer("notebook-admin-test")
    register_notebook_admin_tools(server)
    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            return await client.list_tools()
    tool = next(x for x in asyncio.run(run()).tools if x.name == "gemini_manage_notebook")
    assert tool.annotations.destructive_hint is True


def test_sources_are_bounded_and_extra_content_is_never_returned():
    sources = [{"source_id": f"source-{n}", "title": "Synthetic", "state": 1,
                "mime": "text/plain", "download_url": "private", "text": "secret"} for n in range(25)]
    body = {"notebook_id": "project", "title": "Synthetic notebook", "sources": sources}
    request = AsyncMock(return_value=SimpleNamespace(status="success", value=body))
    projects = AsyncMock(return_value=([{"id": "project", "project_type": 2, "project_subtype": 1}], {}))
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, projects, lambda: "account"))
    for action in ("get", "list_sources"):
        result = asyncio.run(service.run(action, notebook_id="project"))
        assert result.ok and len(result.data["sources"]) == 20
        assert result.data["source_count"] == 25 and result.data["next_offset"] == 20
        assert "secret" not in str(result.data) and "private" not in str(result.data)
        result = asyncio.run(service.run(action, notebook_id="project", offset=20, limit=100))
        assert result.ok and len(result.data["sources"]) == 5 and result.data["next_offset"] is None


def test_invalid_page_rejected_before_initialization():
    initializer, request = AsyncMock(), AsyncMock()
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), initializer, request, AsyncMock(), lambda: "account"))
    for limit, offset in ((0, 0), (101, 0), (True, 0), (20, -1)):
        result = asyncio.run(service.run("get", notebook_id="project", limit=limit, offset=offset))
        assert not result.ok and result.error.code.value == "INVALID_ARGUMENT"
    assert initializer.await_count == request.await_count == 0


def test_non_notebook_and_ambiguous_identity_never_reach_project_get_or_mutation():
    for rows in ([{"id": "project", "project_type": 1, "project_subtype": 1}],
                 [{"id": "project", "project_type": 2}],
                 [{"id": "project", "project_type": True, "project_subtype": 1}],
                 [{"id": "project", "project_type": 2, "project_subtype": 1}] * 2):
        for action in ("get", "list_sources", "add_source", "delete_source", "delete"):
            request = AsyncMock()
            service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request,
                                                   AsyncMock(return_value=(rows, {})), lambda: "account"))
            args = {"notebook_id": "project"}
            if action == "add_source":
                args.update(title="source", text="content")
            if action == "delete_source":
                args["source_id"] = "source"
            result = asyncio.run(service.run(action, **args))
            assert not result.ok and request.await_count == 0


def test_account_change_during_catalog_prevents_read_and_scrubs_response():
    account = ["a"]
    async def list_projects(_client):
        account[0] = "b"
        return ([{"id": "project", "project_type": 2, "project_subtype": 1}], {})
    request = AsyncMock()
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, list_projects, lambda: account[0]))
    result = asyncio.run(service.run("get", notebook_id="project"))
    assert not result.ok and result.data is None and request.await_count == 0


def test_unauthenticated_initialization_stops_notebook_reads_and_mutations():
    from src.client_manager import ClientAuthenticationRequired
    initializer = AsyncMock(side_effect=ClientAuthenticationRequired("synthetic"))
    request, projects = AsyncMock(), AsyncMock()
    service = NotebookAdminService(NotebookAdminDependencies(
        lambda: object(), initializer, request, projects, lambda: "account"))
    for action in ("get", "delete"):
        result = asyncio.run(service.run(action, notebook_id="fixture"))
        assert not result.ok and result.error.code.value == "AUTH_REQUIRED"
    assert request.await_count == projects.await_count == 0
