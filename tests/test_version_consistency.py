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
    CANONICAL_GIT_SOURCE,
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


def test_public_onboarding_surfaces_use_the_canonical_source_install(tmp_path):
    metadata = load_release_metadata(PROJECT_ROOT)
    for relative_path in (
        "README.md",
        "README.zh-CN.md",
        "docs/quickstart.md",
        "docs/launch-kit.md",
        "docs/client-examples.md",
        "examples/clients/codex.config.toml",
        "examples/clients/claude-desktop.json",
        "examples/clients/claude-code.mcp.json",
        "examples/clients/vscode.mcp.json",
    ):
        source = PROJECT_ROOT / relative_path
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (tmp_path / "pyproject.toml").write_bytes((PROJECT_ROOT / "pyproject.toml").read_bytes())
    for relative_path in ("src/__init__.py", "src/server.py", "src/skill_server.py"):
        source = PROJECT_ROOT / relative_path
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())

    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(CANONICAL_GIT_SOURCE, "git+https://example.invalid/repo@main"),
        encoding="utf-8",
    )

    assert any("README.md: canonical source install" in error for error in repository_version_errors(tmp_path, metadata))


def test_versioned_wheel_url_is_optional_but_must_match_when_present(tmp_path):
    metadata = load_release_metadata(PROJECT_ROOT)
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text(CANONICAL_GIT_SOURCE, encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text(CANONICAL_GIT_SOURCE, encoding="utf-8")
    for relative_path in (
        "docs/quickstart.md",
        "docs/launch-kit.md",
        "docs/client-examples.md",
        "examples/clients/codex.config.toml",
        "examples/clients/claude-desktop.json",
        "examples/clients/claude-code.mcp.json",
        "examples/clients/vscode.mcp.json",
    ):
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(CANONICAL_GIT_SOURCE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_bytes((PROJECT_ROOT / "pyproject.toml").read_bytes())
    for relative_path in ("src/__init__.py", "src/server.py", "src/skill_server.py"):
        source = PROJECT_ROOT / relative_path
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())

    assert repository_version_errors(tmp_path, metadata) == []

    (docs / "example.md").write_text(
        "https://github.com/Luckycat133/gemini-web-mcp/releases/download/"
        "v9.8.7/gemini_mcp_server-9.8.7-py3-none-any.whl",
        encoding="utf-8",
    )
    errors = repository_version_errors(tmp_path, metadata)
    assert any("wheel URL versions tag='9.8.7', wheel='9.8.7'" in error for error in errors)


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
