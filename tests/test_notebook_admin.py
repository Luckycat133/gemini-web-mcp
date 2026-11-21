import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import OperationState
from src.infrastructure.notebook_admin import NOTEBOOK_FILE_MAX_BYTES, parse_created, parse_project, request, source_create_payload
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
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


_URL = "https://example.com/fixture"
_URL_CONTRACT = "notebook_admin.source_create_url"
_FILE_CONTRACT = "notebook_admin.source_create_file"
_UPLOAD_REFERENCE = "/contrib_service/ttl_1d/source-fixture"
_PROJECTS = [{"id": "project", "project_type": 2, "project_subtype": 1}]


def _url_project(*sources):
    metadata = ["Synthetic notebook", *([None] * 9), [None, list(sources)]]
    return [["project", metadata]]


def _url_row(source_id="source", *, state=2, url_field=None, title="Source"):
    return [source_id, [source_id, title, None, url_field, None, None, state, None, None]]


def _url_receipt(source_id="source", *, notebook_id="project"):
    return [[notebook_id, [None] * 14 + [[None, None, None, source_id]]]]


class _URLBatchClient:
    def __init__(self, *bodies):
        self.bodies = iter(bodies)
        self.calls = []

    async def _batch_execute(self, calls, *, source_path, close_on_error, current_retry):
        assert len(calls) == 1 and source_path == "/app" and close_on_error is False and current_retry == 0
        call = calls[0]
        self.calls.append((call.rpcid, json.loads(call.payload)))
        body, rejection = next(self.bodies)
        return SimpleNamespace(status_code=200, text=json.dumps([
            ["wrb.fr", call.rpcid, json.dumps(body), None, None, [rejection] if rejection else None],
        ]))


def _url_service(client):
    return NotebookAdminService(NotebookAdminDependencies(
        lambda: client, AsyncMock(), request, AsyncMock(return_value=(_PROJECTS, {})),
    ))


def test_url_contract_uses_only_the_observed_url_oneof():
    contract = get_contract(_URL_CONTRACT)
    assert contract.rpc_id == "ko3zcd"
    assert json.loads(contract.build_payload(notebook_id="project", title="Source", url=_URL)) == [
        "project", [None, "Source", None, [_URL]], [1, 3],
    ]
    assert parse_contract_body(_URL_CONTRACT, _url_receipt(), notebook_id="project").value == "source"
    assert parse_contract_body(_URL_CONTRACT, _url_receipt(notebook_id="other"), notebook_id="project").status == "changed_shape"


@pytest.mark.parametrize(("state", "url_field", "status", "operation_state", "verified", "url_verified"), [
    (1, [_URL], "source_processing", OperationState.ACCEPTED, True, True),
    (4, None, "source_processing", OperationState.ACCEPTED, True, False),
    (2, [_URL], "source_url_ready_verified", OperationState.COMPLETED, True, True),
    (2, None, "source_url_unobserved", OperationState.ACCEPTED, True, False),
    (2, ["https://example.com/other"], "source_metadata_mismatch", OperationState.FAILED, False, False),
    (2, {"url": _URL}, "source_metadata_unverified", OperationState.FAILED, False, False),
    (3, [_URL], "source_ingestion_failed", OperationState.FAILED, True, True),
    (42, [_URL], "source_state_unrecognized", OperationState.FAILED, True, True),
])
def test_url_add_verifies_exact_new_id_and_separates_index_and_content(
    monkeypatch, state, url_field, status, operation_state, verified, url_verified,
):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    existing = _url_row("existing")
    same_title = _url_row("concurrent", url_field=[_URL])
    client = _URLBatchClient(
        (_url_project(existing), None), (_url_receipt(), None),
        (_url_project(existing, same_title, _url_row(state=state, url_field=url_field)), None),
    )
    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", url=_URL))
    assert result.meta.verification_status == status and result.meta.operation_state is operation_state
    assert result.data.source_id == "source" and result.data.changed is True
    assert result.data.source_state == state
    assert result.data.verified is verified and result.data.url_verified is url_verified
    assert result.data.content_verified is False and _URL not in str(result.to_dict())
    assert client.calls == [
        ("HcT8bb", ["project"]), ("ko3zcd", ["project", [None, "Source", None, [_URL]], [1, 3]]),
        ("HcT8bb", ["project"]),
    ]


def test_url_add_requires_receipt_not_a_same_title_match(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    client = _URLBatchClient(
        (_url_project(), None), (_url_receipt(), None),
        (_url_project(_url_row("different", url_field=[_URL])), None),
    )
    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", url=_URL))
    assert not result.ok and result.meta.verification_status == "mutation_unverified"
    assert result.data.source_id == "source" and result.data.changed is None
    assert len(client.calls) == 3


@pytest.mark.parametrize(("ack", "rejection", "status"), [
    ([], 7, "source_create_rejected"),
    ([], None, "mutation_unverified"),
    (_url_receipt(notebook_id="other"), None, "mutation_unverified"),
    (_url_receipt("existing"), None, "source_identity_unverified"),
])
def test_unverified_url_creation_does_not_replay_or_adopt_an_old_source(monkeypatch, ack, rejection, status):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    client = _URLBatchClient((_url_project(_url_row("existing")), None), (ack, rejection))
    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", url=_URL))
    assert not result.ok and result.meta.verification_status == status
    assert result.data.source_id is None and result.data.changed is None
    assert len(client.calls) == 2


def test_url_add_scrubs_exact_receipt_if_account_changes_during_readback(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")

    class SwitchingClient(_URLBatchClient):
        async def _batch_execute(self, *args, **kwargs):
            response = await super()._batch_execute(*args, **kwargs)
            if len(self.calls) == 3:
                monkeypatch.setenv("GEMINI_PSID", "different-synthetic-notebook-account")
            return response

    client = SwitchingClient((_url_project(), None), (_url_receipt(), None),
                             (_url_project(_url_row(url_field=[_URL])), None))
    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", url=_URL))
    assert not result.ok and result.meta.verification_status == "mutation_unverified" and result.data is None
    assert len(client.calls) == 3


@pytest.mark.parametrize("arguments", [
    {"url": _URL, "text": "source"}, {"url": ""}, {},
    {"url": "file:///tmp/example"}, {"url": "https://user:pass@example.com"},
    {"url": "https://example.com:bad"}, {"url": "https://example.com/\nprivate"},
])
def test_invalid_url_source_inputs_fail_before_client_initialization(arguments):
    initializer, call = AsyncMock(), AsyncMock()
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), initializer, call, AsyncMock()))
    result = asyncio.run(service.run("add_source", notebook_id="project", title="Source", **arguments))
    assert not result.ok and result.error_code == "INVALID_ARGUMENT"
    assert initializer.await_count == call.await_count == 0


class _FileBatchClient(_URLBatchClient):
    def __init__(self, *bodies, upload_status=200, upload_reference=_UPLOAD_REFERENCE):
        super().__init__(*bodies)
        self._running = True
        self.push_id = "feeds/synthetic"
        self.client = SimpleNamespace(post=self._post)
        self.upload_response = SimpleNamespace(status_code=upload_status, text=upload_reference)
        self.uploads = []

    async def _post(self, endpoint, *, headers, multipart, allow_redirects):
        assert self.calls == [("HcT8bb", ["project"])]
        self.uploads.append((endpoint, headers, multipart, allow_redirects))
        return self.upload_response


@pytest.fixture
def fake_multipart(monkeypatch):
    class Multipart:
        def __init__(self):
            self.parts = []
            self.closed = False

        def addpart(self, **part):
            self.parts.append(part)

        def close(self):
            self.closed = True

    monkeypatch.setattr("curl_cffi.CurlMime", Multipart)


def _file_row(*, state, reference, mime="text/plain"):
    return ["source", ["source", "Source", None, None, reference, None, state, None, mime]]


@pytest.mark.parametrize(("state", "reference", "mime", "expected", "state_expected", "reference_verified"), [
    (1, None, "text/plain", "source_processing", OperationState.ACCEPTED, False),
    (2, [_UPLOAD_REFERENCE, "text/plain"], "text/plain", "source_file_ready_verified", OperationState.COMPLETED, True),
    (2, None, None, "source_file_reference_unobserved", OperationState.ACCEPTED, False),
    (2, [_UPLOAD_REFERENCE, "application/pdf"], "text/plain", "source_metadata_mismatch", OperationState.FAILED, True),
    (2, ["/contrib_service/other", "text/plain"], "text/plain", "source_metadata_mismatch", OperationState.FAILED, False),
    (2, {}, "text/plain", "source_metadata_unverified", OperationState.FAILED, False),
    (3, None, "text/plain", "source_ingestion_failed", OperationState.FAILED, False),
])
def test_file_source_uploads_once_and_verifies_exact_registration(
    monkeypatch, tmp_path, fake_multipart, state, reference, mime, expected, state_expected, reference_verified,
):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    path = tmp_path / "local-fixture-name.txt"
    content = "Synthetic UTF-8 source. 中文".encode()
    path.write_bytes(content)
    client = _FileBatchClient((_url_project(), None), (_url_receipt(), None),
                             (_url_project(_file_row(state=state, reference=reference, mime=mime)), None))

    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", file_path=str(path)))
    assert result.meta.verification_status == expected and result.meta.operation_state is state_expected
    assert result.data.source_id == "source" and result.data.source_state == state and result.data.changed is True
    assert result.data.upload_reference_verified is reference_verified and result.data.content_verified is False
    assert all(value not in str(result.to_dict()) for value in (_UPLOAD_REFERENCE, str(path), content.decode()))
    assert len(client.uploads) == 1
    endpoint, headers, multipart, redirects = client.uploads[0]
    assert endpoint == "https://content-push.googleapis.com/upload" and headers["Push-ID"] == "feeds/synthetic"
    assert redirects is False and multipart.closed
    assert multipart.parts == [{"name": "file", "content_type": "text/plain", "filename": "notebook-source.txt", "data": content}]
    assert client.calls == [
        ("HcT8bb", ["project"]),
        ("ko3zcd", ["project", [None, "Source", None, None, [_UPLOAD_REFERENCE, "text/plain"],
                                None, None, None, "text/plain"], [1, 3]]),
        ("HcT8bb", ["project"]),
    ]
    assert get_contract(_FILE_CONTRACT).parser == get_contract(_URL_CONTRACT).parser


@pytest.mark.parametrize(("status", "reference"), [(307, _UPLOAD_REFERENCE), (200, "https://example.com/not-an-upload")])
def test_unverified_upload_is_not_followed_or_registered(monkeypatch, tmp_path, fake_multipart, status, reference):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    path = tmp_path / "source.txt"
    path.write_text("synthetic")
    client = _FileBatchClient((_url_project(), None), upload_status=status, upload_reference=reference)
    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", file_path=str(path)))
    assert not result.ok and result.meta.verification_status == "source_upload_unverified"
    assert result.data.source_id is None and result.data.changed is None
    assert len(client.uploads) == len(client.calls) == 1


def test_notebook_must_be_read_back_before_file_upload(monkeypatch, tmp_path, fake_multipart):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    path = tmp_path / "source.txt"
    path.write_text("synthetic")
    client = _FileBatchClient(([], None))
    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", file_path=str(path)))
    assert not result.ok and result.meta.verification_status == "read_unverified"
    assert len(client.calls) == 1 and client.uploads == []


def test_account_switch_after_upload_prevents_notebook_mutation(monkeypatch, tmp_path, fake_multipart):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    path = tmp_path / "source.txt"
    path.write_text("synthetic")

    class SwitchingClient(_FileBatchClient):
        async def _post(self, *args, **kwargs):
            response = await super()._post(*args, **kwargs)
            monkeypatch.setenv("GEMINI_PSID", "different-synthetic-notebook-account")
            return response

    client = SwitchingClient((_url_project(), None))
    result = asyncio.run(_url_service(client).run("add_source", notebook_id="project", title="Source", file_path=str(path)))
    assert not result.ok and result.meta.verification_status == "source_upload_unverified" and result.data is None
    assert len(client.uploads) == len(client.calls) == 1


@pytest.mark.parametrize(("suffix", "content", "extra"), [
    (".txt", b"", {}), (".txt", b"\x00bin", {}), (".txt", b"\xff", {}),
    (".txt", b"text", {"text": "duplicate"}), (".txt", b"text", {"url": _URL}), (".pdf", b"text", {}),
    pytest.param(".txt", b"x" * (NOTEBOOK_FILE_MAX_BYTES + 1), {}, id="oversize"),
])
def test_invalid_local_notebook_source_fails_before_authentication(tmp_path, suffix, content, extra):
    path = tmp_path / ("source" + suffix)
    path.write_bytes(content)
    initializer, batch, upload = AsyncMock(), AsyncMock(), AsyncMock()
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), initializer, batch, AsyncMock(), upload_file=upload))
    result = asyncio.run(service.run("add_source", notebook_id="project", title="Source", file_path=str(path), **extra))
    assert not result.ok and result.error_code == "INVALID_ARGUMENT"
    assert initializer.await_count == batch.await_count == upload.await_count == 0


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
    assert {"url", "file_path"} <= set(tool.input_schema["properties"])
    assert not {"url", "file_path"} & set(tool.input_schema["required"])


@pytest.mark.parametrize("file_source", [False, True])
def test_mcp_source_import_reports_pending_indexing_with_exact_source_id(monkeypatch, tmp_path, fake_multipart, file_source):
    from mcp import Client
    from src.adapters.mcp_sdk import MCPServer
    from src.onboarding import domain_result_from_call
    from src.tools import notebook_admin

    monkeypatch.setenv("GEMINI_PSID", "synthetic-notebook-test-account")
    if file_source:
        path = tmp_path / "source.txt"
        path.write_text("synthetic source")
        source_arguments = {"file_path": str(path)}
        remote = _FileBatchClient((_url_project(), None), (_url_receipt(), None),
                                 (_url_project(_file_row(state=1, reference=[_UPLOAD_REFERENCE, "text/plain"])), None))
        reference_key = "upload_reference_verified"
    else:
        source_arguments = {"url": _URL}
        remote = _URLBatchClient((_url_project(), None), (_url_receipt(), None),
                                 (_url_project(_url_row(state=1, url_field=[_URL])), None))
        reference_key = "url_verified"
    monkeypatch.setattr(notebook_admin, "_service", lambda: _url_service(remote))
    server = MCPServer("notebook-admin-url-test")
    notebook_admin.register_notebook_admin_tools(server)

    async def run():
        async with Client(server, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_manage_notebook", {
                "action": "add_source", "notebook_id": "project", "title": "Source", **source_arguments,
            })

    result = asyncio.run(run())
    payload = domain_result_from_call(result)
    assert payload["ok"] and payload["meta"]["operation_state"] == "accepted"
    assert payload["data"]["source_id"] == "source" and payload["data"]["source_state"] == 1
    assert payload["data"][reference_key] is True and payload["data"]["content_verified"] is False
    assert "Indexing is in progress." in result.content[0].text


def test_sources_are_bounded_and_extra_content_is_never_returned():
    sources = [{"source_id": f"source-{n}", "title": "Synthetic", "state": 1,
                "mime": "text/plain", "download_url": "private", "text": "secret", "source_url": _URL}
               for n in range(25)]
    body = {"notebook_id": "project", "title": "Synthetic notebook", "sources": sources}
    request = AsyncMock(return_value=SimpleNamespace(status="success", value=body))
    projects = AsyncMock(return_value=([{"id": "project", "project_type": 2, "project_subtype": 1}], {}))
    service = NotebookAdminService(NotebookAdminDependencies(lambda: object(), AsyncMock(), request, projects, lambda: "account"))
    for action in ("get", "list_sources"):
        result = asyncio.run(service.run(action, notebook_id="project"))
        assert result.ok and len(result.data["sources"]) == 20
        assert result.data["source_count"] == 25 and result.data["next_offset"] == 20
        assert all(value not in str(result.data) for value in ("secret", "private", _URL))
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
