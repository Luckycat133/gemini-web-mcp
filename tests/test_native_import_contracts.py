"""Native reference fixtures exercise wire fields without selecting account files."""

from dataclasses import dataclass

import pytest

from src.infrastructure.import_contracts import build_native_attachment, build_native_attachments, native_file_type


def test_exact_drive_reference_has_only_the_native_drive_oneof():
    assert build_native_attachment(kind="drive", resource_id="drive_fixture", name="Fixture document",
                                   mime_type="application/vnd.google-apps.document",
                                   url="https://docs.google.com/document/d/drive_fixture/edit") == [
        None, "Fixture document", None, None,
        [["drive_fixture"], 8, "https://docs.google.com/document/d/drive_fixture/edit", "application/vnd.google-apps.document"],
    ]


def test_exact_photos_reference_has_no_drive_id_or_guessed_download_url():
    assert build_native_attachment(kind="photos", resource_id="media_fixture", name="Fixture.jpg", mime_type="image/jpeg") == [
        None, "Fixture.jpg", None, None, None, None, None, [["media_fixture"], "image/jpeg"],
    ]


def test_exact_notebook_reference_preserves_its_opaque_provider_prefix():
    assert build_native_attachment(kind="notebook", resource_id="notebooks/notebook_fixture", name="Fixture notebook") == [
        None, "Fixture notebook", None, None, None, None, None, None, None, None, None, [["notebooks/notebook_fixture"]],
    ]


@pytest.mark.parametrize("kind,resource_id,name", [
    ("drive", "", "Fixture"), ("photos", "  ", "Fixture"), ("notebook", None, "Fixture"),
    ("drive", " id ", "Fixture"), ("drive", "fixture", " "), ("repository", "fixture", "Fixture"),
    (None, "fixture", "Fixture"), ([], "fixture", "Fixture"),
])
def test_unselected_or_changed_resource_reference_is_rejected(kind, resource_id, name):
    with pytest.raises(ValueError):
        build_native_attachment(kind=kind, resource_id=resource_id, name=name)


def test_batch_builders_accept_domain_shaped_attribute_records_without_importing_domain():
    @dataclass(frozen=True)
    class Reference:
        kind: str
        resource_id: str
        name: str
        mime_type: str = ""
        url: str | None = None

    result = build_native_attachments([
        Reference("notebook", "notebooks/notebook_fixture", "Notebook"),
        {"kind": "photos", "resource_id": "media_fixture", "name": "Photo.heic"},
    ])
    assert result[0][11] == [["notebooks/notebook_fixture"]]
    assert result[1][7] == [["media_fixture"], "image/heic"]


def test_empty_batch_is_empty_and_more_than_ten_is_rejected():
    assert build_native_attachments([]) == []
    with pytest.raises(ValueError, match="ten"):
        build_native_attachments({"kind": "drive", "resource_id": f"fixture_{i}", "name": "Fixture"} for i in range(11))


@pytest.mark.parametrize("mime_type,name,expected", [
    ("application/vnd.google-apps.document", "Document", 8),
    ("application/vnd.google-apps.spreadsheet", "Sheet", 6),
    ("application/vnd.google-apps.presentation", "Slides", 13),
    ("application/pdf", "Fixture.pdf", 11), ("application/json", "Data", 5),
    ("application/json", "code.json", 16), ("text/plain", "makefile", 16),
    ("image/jpeg", "Fixture.jpg", 1), ("video/mp4", "Fixture.mp4", 2),
    ("audio/mpeg", "Fixture.mp3", 4), ("text/plain", "Fixture.txt", 3),
    ("application/zip", "Fixture.zip", 9), ("application/octet-stream", "Unknown", 0),
])
def test_file_type_matches_observed_mime_and_code_filename_priority(mime_type, name, expected):
    assert native_file_type(mime_type, name) == expected


def test_missing_mime_uses_native_picker_fallback_and_never_invents_access_tokens():
    result = build_native_attachment(kind="drive", resource_id="fixture", name="Unknown")
    assert result[4] == [["fixture"], 0, None, "application/octet-stream"]
    assert len(result) == 5
