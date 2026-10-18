"""Native Skill ZIP transport, typed inventory and verified mutation regressions."""

import asyncio
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services import native_skills as skills


FIXTURES = json.loads((Path(__file__).parent / "fixtures/rpc_management_cases.json").read_text())


def record(*, name="fixture-skill", enabled=True, drive_id="", entries=None, can_edit=None, can_delete=None):
    item = deepcopy(FIXTURES["native_skill_get"]["cases"]["success"]["body"][0])
    item.extend([None] * (14 - len(item)))
    item[1], item[4], item[11], item[13] = name, enabled, drive_id, "version/fixture"
    if entries is not None:
        item[6] = [entries]
    if can_edit is not None or can_delete is not None:
        item[12] = [None, None, None, [can_edit, True, True, can_delete]]
    return item


class UploadSession:
    def __init__(self, responses=None):
        self.responses = responses if responses is not None else [
            SimpleNamespace(status_code=200, text="", headers={
                "X-Goog-Upload-Status": "active", "X-Goog-Upload-URL": "https://content-push.googleapis.com/upload/session"}),
            SimpleNamespace(status_code=200, text="/contrib_service/fixture", headers={"X-Goog-Upload-Status": "final"}),
        ]
        self.calls = []

    async def post(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.client = UploadSession()
        self.push_id = "ordinary-bard-storage-push-id-must-not-be-used"

    async def _batch_execute(self, requests, *, source_path, close_on_error):
        request = requests[0]
        self.calls.append((request.rpcid, json.loads(request.payload), source_path))
        key, body, *options = self.responses.pop(0)
        assert request.rpcid == get_contract(key).rpc_id
        status, reject = options if options else (200, None)
        text = json.dumps([["wrb.fr", request.rpcid, json.dumps(body), None, None, [reject] if reject else None]])
        return SimpleNamespace(status_code=status, text=text)


def run(awaitable):
    return asyncio.run(awaitable)


def test_native_skills_list_is_separate_inventory_and_omits_contents():
    client = Client(("native_skills.list", [[record()]]))
    result = run(skills.list_native_skills(client))
    assert result.ok and result.data["items"][0]["name"] == "fixture-skill"
    assert "instructions" not in result.data["items"][0]
    assert "entries" not in result.data["items"][0]
    assert client.calls == [("cCSu7", [], "/customize/skills")]


@pytest.mark.parametrize("damage", ["id", "bool", "instructions", "bundle", "duplicate"])
def test_changed_native_skill_shape_or_identity_is_rejected(damage):
    item = record()
    if damage == "id":
        parsed = parse_contract_body("native_skills.get", [item], expected_id="other")
    elif damage == "bool":
        item[4] = "enabled"
        parsed = parse_contract_body("native_skills.get", [item])
    elif damage == "instructions":
        item[3] = {"unexpected": "object"}
        parsed = parse_contract_body("native_skills.get", [item])
    elif damage == "bundle":
        item[6] = False
        parsed = parse_contract_body("native_skills.get", [item])
    else:
        parsed = parse_contract_body("native_skills.list", [[item, item]])
    assert parsed.status == "changed_shape"


def test_create_uploads_ui_zip_with_native_tenant_and_observes_independent_skill():
    client = Client(("native_skills.save", ["skills/fixture", record()]), ("native_skills.get", [record()]))
    result = run(skills.create_native_skill(client, name="fixture-skill", description="Fixture description",
                                             instructions="Fixture instructions"))
    assert result.operation_state == "completed" and result.data["verified"] is True
    assert client.calls[0] == ("SMuXte", ["/contrib_service/fixture", None, 3, True], "/customize/skills")
    assert client.calls[1][0:2] == ("pvBRnd", ["skills/fixture"])
    start, finish = client.client.calls
    assert start["headers"]["X-Tenant-Id"] == "agent_skills"
    assert start["headers"]["Push-ID"] == skills.NATIVE_SKILL_PUSH_ID != client.push_id
    assert start["headers"]["X-Goog-Upload-Protocol"] == "resumable"
    assert start["headers"]["X-Goog-Upload-Command"] == "start"
    assert finish["headers"]["X-Goog-Upload-Command"] == "upload, finalize"
    assert finish["headers"]["X-Goog-Upload-Offset"] == "0"
    assert finish["headers"]["Content-Type"] == "application/zip"
    assert start["data"] == "File name: fixture-skill.zip"
    assert start["headers"]["X-Goog-Upload-Header-Content-Length"] == str(len(finish["data"]))
    assert start["allow_redirects"] is False and finish["allow_redirects"] is False
    with zipfile.ZipFile(io.BytesIO(finish["data"])) as archive:
        markdown = archive.read("SKILL.md").decode()
    assert 'name: fixture-skill' in markdown
    assert 'description: "Fixture description"' in markdown
    assert markdown.endswith("\n---\nFixture instructions")


@pytest.mark.parametrize("body,status,reject", [([], 200, None), ([], 503, None), ([], 200, 7), ([record(name="Stale")], 200, None)])
def test_create_preserves_id_without_resubmission_when_readback_is_missing_or_stale(body, status, reject):
    client = Client(("native_skills.save", ["skills/fixture"]), ("native_skills.get", body, status, reject))
    result = run(skills.create_native_skill(client, name="fixture-skill", description="Fixture description",
                                             instructions="Fixture instructions"))
    assert result.operation_state == "accepted" and result.data["id"] == "skills/fixture"
    assert result.data["verified"] is False and result.warnings[0].code == "MUTATION_UNVERIFIED"
    assert len(client.calls) == 2 and len(client.client.calls) == 2


def test_update_preserves_other_bundle_files_external_resources_frontmatter_and_disabled_state():
    previous = "---\nname: fixture-skill\ndescription: Fixture description\nlicense: MIT\n---\nFixture instructions"
    entries = [["SKILL.md", previous], ["references/readme.md", "Retain this"],
               ["images/icon.bin", None, "application/octet-stream", base64.b64encode(b"\x00\xff").decode()],
               ["sources/drive.pdf", None, "application/pdf", None, ["drive-file", "Drive source"]]]
    original = record(enabled=False, entries=entries)
    updated = record(name="updated-skill", enabled=False, entries=entries)
    client = Client(("native_skills.get", [original]), ("native_skills.save", ["skills/fixture"]),
                    ("native_skills.get", [updated]))
    result = run(skills.update_native_skill(client, "skills/fixture", name="updated-skill"))
    assert result.operation_state == "completed"
    assert client.calls[1][1][1:4] == ["skills/fixture", 2, False]
    assert client.calls[1][1][4] == [["sources/drive.pdf", "application/pdf", ["drive-file", "Drive source"]]]
    with zipfile.ZipFile(io.BytesIO(client.client.calls[1]["data"])) as archive:
        assert archive.read("references/readme.md") == b"Retain this"
        assert archive.read("images/icon.bin") == b"\x00\xff"
        assert "sources/drive.pdf" not in archive.namelist()
        assert "license: MIT" in archive.read("SKILL.md").decode()


@pytest.mark.parametrize("drive_id", ["", "drive/license"])
def test_enable_uses_ui_owned_or_licensed_path_then_reads_actual_boolean(drive_id):
    mutation = "native_skills.set_license_enabled" if drive_id else "native_skills.save"
    ack_body = [record(enabled=False, drive_id=drive_id)] if drive_id else ["skills/fixture"]
    client = Client(("native_skills.get", [record(drive_id=drive_id)]), (mutation, ack_body),
                    ("native_skills.get", [record(enabled=False, drive_id=drive_id)]))
    result = run(skills.set_native_skill_enabled(client, "skills/fixture", False))
    assert result.operation_state == "completed"
    expected = ["skills/fixture", False] if drive_id else ["", "skills/fixture", 2, False]
    assert client.calls[1][1] == expected
    assert not client.client.calls  # A toggle does not upload/rewrite content.


@pytest.mark.parametrize("drive_id", ["", "drive/license"])
def test_delete_or_remove_license_requires_complete_independent_list_absence(drive_id):
    mutation = "native_skills.remove_license" if drive_id else "native_skills.delete"
    client = Client(("native_skills.get", [record(drive_id=drive_id, can_delete=not bool(drive_id))]),
                    (mutation, []), ("native_skills.list", []))
    result = run(skills.delete_native_skill(client, "skills/fixture"))
    assert result.operation_state == "completed" and result.data["removed_license"] is bool(drive_id)
    assert client.calls[1][1] == ["skills/fixture"]


@pytest.mark.parametrize("body,status,reject", [([], 503, None), ([], 200, 7), ([[record()]], 200, None)])
def test_delete_failure_or_still_present_is_not_completed(body, status, reject):
    client = Client(("native_skills.get", [record()]), ("native_skills.delete", []),
                    ("native_skills.list", body, status, reject))
    result = run(skills.delete_native_skill(client, "skills/fixture"))
    assert result.operation_state == "accepted" and result.data["verified"] is False


@pytest.mark.parametrize("error", ["status", "missing_target", "external_target", "not_final", "empty_reference"])
def test_malformed_upload_response_stops_before_save_rpc(error):
    client = Client()
    start, finish = client.client.responses
    if error == "status":
        start.status_code = 503
    elif error == "missing_target":
        start.headers.pop("X-Goog-Upload-URL")
    elif error == "external_target":
        start.headers["X-Goog-Upload-URL"] = "https://attacker.example/collect"
    elif error == "not_final":
        finish.headers["X-Goog-Upload-Status"] = "active"
    else:
        finish.text = ""
    result = run(skills.create_native_skill(client, name="name", description="desc", instructions="instructions"))
    assert not result.ok and result.error_code == "UPSTREAM_CHANGED"
    assert not client.calls
    assert len(client.client.calls) == (2 if error in {"not_final", "empty_reference"} else 1)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "unsafe\\name"])
def test_unsafe_bundle_path_fails_before_upload(path):
    client = Client(("native_skills.get", [record(entries=[[path, "data"]])]))
    result = run(skills.update_native_skill(client, "skills/fixture", name="updated"))
    assert not result.ok and result.error_code == "INVALID_ARGUMENT" and not client.client.calls


def test_typed_update_rejects_unobserved_licensed_content_edit_without_upload():
    client = Client(("native_skills.get", [record(drive_id="drive/license", can_edit=False)]))
    result = run(skills.update_native_skill(client, "skills/fixture", instructions="Updated"))
    assert not result.ok and result.error_code == "CAPABILITY_UNAVAILABLE" and not client.client.calls


def test_owned_drive_skill_delete_uses_delete_permission_instead_of_drive_id():
    client = Client(("native_skills.get", [record(drive_id="owned-drive-file", can_delete=True)]),
                    ("native_skills.delete", []), ("native_skills.list", []))
    result = run(skills.delete_native_skill(client, "skills/fixture"))
    assert result.operation_state == "completed" and result.data["removed_license"] is False
    assert client.calls[1][0] == "SL49ud"


def test_blank_create_arguments_fail_before_network():
    client = Client()
    result = run(skills.create_native_skill(client, name=" ", description="desc", instructions="body"))
    assert not result.ok and result.error_code == "INVALID_ARGUMENT"
    assert not client.calls and not client.client.calls


@pytest.mark.parametrize("name,valid", [
    ("mcp-acceptance-f1h48krq", True), ("中文技能", True), ("á-skill", True),
    ("MCP skill", False), ("mcp--skill", False), ("-skill", False), ("skill-", False),
    ("a" * 64, True), ("a" * 65, False),
])
def test_current_manual_editor_unicode_name_rule(name, valid):
    assert skills.native_skill_name_valid(name) is valid


@pytest.mark.parametrize("replace", [False, True])
def test_zip_import_or_replacement_verifies_uploaded_files_without_executing_them(tmp_path, replace):
    markdown = "---\nname: fixture-skill\ndescription: fixture\n---\nFixture instructions\n"
    path = tmp_path / "skill.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture-skill/SKILL.md", markdown)
        archive.writestr("fixture-skill/references/data.txt", "Resource fixture")
    after = record(enabled=not replace, entries=[["SKILL.md", markdown], ["references/data.txt", "Resource fixture"]])
    responses = [("native_skills.save", ["skills/fixture"]), ("native_skills.get", [after])]
    if replace:
        responses.insert(0, ("native_skills.get", [record(enabled=False)]))
    client = Client(*responses)
    result = run(skills.import_native_skill_archive(client, str(path), skill_id="skills/fixture" if replace else ""))
    assert result.operation_state == "completed"
    save = next(call for call in client.calls if call[0] == "SMuXte")
    assert save[1][1:4] == (["skills/fixture", 2, False] if replace else [None, 3, True])
    assert client.client.calls[1]["data"] == path.read_bytes()


def test_zip_import_missing_skill_file_fails_before_network(tmp_path):
    path = tmp_path / "invalid.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("README.md", "Not a Skill")
    client = Client()
    result = run(skills.import_native_skill_archive(client, str(path)))
    assert result.error_code == "INVALID_ARGUMENT"
    assert not client.calls and not client.client.calls


def test_zip_import_readback_missing_resource_is_only_accepted(tmp_path):
    path = tmp_path / "skill.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SKILL.md", "Fixture Skill document")
        archive.writestr("references/data.txt", "Required resource")
    client = Client(("native_skills.save", ["skills/fixture"]),
                    ("native_skills.get", [record(entries=[["SKILL.md", "Fixture Skill document"]])]))
    result = run(skills.import_native_skill_archive(client, str(path)))
    assert result.operation_state == "accepted" and result.data["id"] == "skills/fixture"
    assert len(client.calls) == 2


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("mismatch", ["stale_extra_file", "different_bytes"])
def test_zip_import_requires_complete_matching_canonical_file_set(tmp_path, replace, mismatch):
    markdown = "---\nname: fixture-skill\ndescription: fixture\n---\nFixture instructions\n"
    path = tmp_path / "replacement.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture-skill/SKILL.md", markdown)
        archive.writestr("fixture-skill/references/data.bin", b"\x00\xff")
    observed_bytes = b"\x00\xff" if mismatch == "stale_extra_file" else b"\x00\xfe"
    entries = [["SKILL.md", markdown],
               ["references/data.bin", None, "application/octet-stream", base64.b64encode(observed_bytes).decode()]]
    if mismatch == "stale_extra_file":
        entries.append(["references/stale.txt", "Old resource must not survive a whole ZIP replacement."])
    responses = [("native_skills.save", ["skills/fixture"]),
                 ("native_skills.get", [record(enabled=not replace, entries=entries)])]
    if replace:
        responses.insert(0, ("native_skills.get", [record(enabled=False)]))
    client = Client(*responses)

    result = run(skills.import_native_skill_archive(client, str(path), skill_id="skills/fixture" if replace else ""))

    assert result.ok and result.operation_state == "accepted"
    assert result.data["verified"] is False and result.data["id"] == "skills/fixture"
    assert result.warnings[0].code == "MUTATION_UNVERIFIED"
    assert sum(call[0] == get_contract("native_skills.save").rpc_id for call in client.calls) == 1
    save = next(call for call in client.calls if call[0] == get_contract("native_skills.save").rpc_id)
    assert save[1][2:4] == [2 if replace else 3, not replace]


def test_zip_replacement_matches_bytes_after_ui_root_and_desktop_file_canonicalization(tmp_path):
    markdown = "---\nname: fixture-skill\ndescription: fixture\n---\nFixture instructions\n"
    path = tmp_path / "replacement.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture-skill/SKILL.md", markdown)
        archive.writestr("fixture-skill/references/data.bin", b"\x00\xff")
        archive.writestr("fixture-skill/.DS_Store", b"Ignored desktop metadata")
        archive.writestr("__MACOSX/fixture-skill/._SKILL.md", b"Ignored resource fork")
    # The provider may retain a common bundle folder in its read-back. Apply the
    # same public UI canonicalization to both sides before exact byte comparison.
    after = record(enabled=False, entries=[
        ["returned-root/SKILL.md", markdown],
        ["returned-root/references/data.bin", None, "application/octet-stream", base64.b64encode(b"\x00\xff").decode()],
        ["returned-root/Thumbs.db", "Ignored desktop metadata"],
    ])
    client = Client(("native_skills.get", [record(enabled=False)]),
                    ("native_skills.save", ["skills/fixture"]), ("native_skills.get", [after]))

    result = run(skills.import_native_skill_archive(client, str(path), skill_id="skills/fixture"))

    assert result.ok and result.operation_state == "completed" and result.data["verified"] is True
    assert result.data["item"]["enabled"] is False
    assert client.client.calls[1]["data"] == path.read_bytes()
