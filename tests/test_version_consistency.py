from __future__ import annotations

import subprocess
import sys
import zipfile
from importlib import import_module
from importlib.metadata import version as distribution_version
from pathlib import Path

import pytest

import src
from scripts.release_metadata import (
    ReleaseMetadataError,
    find_product_version_errors,
    load_release_metadata,
    release_artifact_errors,
    release_tag_from_environment,
    repository_version_errors,
    validate_release_tag,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_is_the_runtime_and_distribution_version_source():
    metadata = load_release_metadata(PROJECT_ROOT)

    assert src.__version__ == metadata.version
    assert distribution_version(metadata.project_name) == metadata.version

    server = import_module("src.server")
    skill_server = import_module("src.skill_server")

    assert f"(v{metadata.version})" in server.mcp.instructions
    assert f"(v{metadata.version})" in skill_server.mcp.instructions


def test_repository_version_consumers_are_consistent():
    metadata = load_release_metadata(PROJECT_ROOT)

    assert repository_version_errors(PROJECT_ROOT, metadata) == []


def test_product_version_scan_detects_stale_non_historical_reference():
    metadata = load_release_metadata(PROJECT_ROOT)
    errors = find_product_version_errors(
        Path("docs/example.md"),
        "I released Gemini Web MCP v9.8.7.",
        metadata.version,
    )

    assert errors == [f"docs/example.md:1: product version v9.8.7 != v{metadata.version}"]


def test_release_tag_validation_uses_pyproject_version():
    metadata = load_release_metadata(PROJECT_ROOT)

    validate_release_tag(metadata.tag, metadata)
    with pytest.raises(ReleaseMetadataError, match="does not match pyproject.toml"):
        validate_release_tag("v9.8.7", metadata)


def test_release_tag_is_only_inferred_for_tag_builds():
    metadata = load_release_metadata(PROJECT_ROOT)

    assert release_tag_from_environment({"GITHUB_REF": "refs/heads/main", "GITHUB_REF_NAME": "main"}) is None
    assert release_tag_from_environment({"GITHUB_REF": f"refs/tags/{metadata.tag}"}) == metadata.tag
    assert release_tag_from_environment({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": metadata.tag}) == metadata.tag


def test_release_asset_names_and_wheel_metadata_are_derived_from_pyproject(tmp_path):
    metadata = load_release_metadata(PROJECT_ROOT)
    wheel_path = tmp_path / metadata.wheel_filename
    dist_info = f"{metadata.distribution_basename}-{metadata.version}.dist-info/METADATA"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        archive.writestr(
            dist_info,
            f"Metadata-Version: 2.1\nName: {metadata.project_name}\nVersion: {metadata.version}\n",
        )
    (tmp_path / metadata.sdist_filename).touch()
    (tmp_path / metadata.skill_filename).touch()

    assert release_artifact_errors(tmp_path, metadata) == []

    stale_asset = tmp_path / "gemini-web-mcp-skill-9.8.7.zip"
    stale_asset.touch()
    assert release_artifact_errors(tmp_path, metadata) == [
        f"{tmp_path}: stale or mismatched release artifact {stale_asset.name}"
    ]


def test_version_checker_cli_passes_and_release_packager_rejects_stale_tag(tmp_path):
    metadata = load_release_metadata(PROJECT_ROOT)
    checker = subprocess.run(
        [sys.executable, "scripts/check_version_consistency.py"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert checker.returncode == 0, checker.stderr
    assert f"Version consistency OK: {metadata.project_name} {metadata.version}" in checker.stdout

    packager = subprocess.run(
        [
            sys.executable,
            "scripts/package_release.py",
            "--skip-python",
            "--outdir",
            str(tmp_path),
            "--tag",
            "v9.8.7",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert packager.returncode != 0
    assert "does not match pyproject.toml version" in packager.stderr
    assert list(tmp_path.iterdir()) == []
