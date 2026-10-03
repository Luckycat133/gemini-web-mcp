"""Structural, installation and catalog contracts for the assistance Skill.

Routing vocabulary and prose layout do not measure model trigger accuracy.
Expected call/schema fixtures live in test_focused_skills; runtime grounding,
operation and annotation behavior remains covered by test_assist_surface.
"""

from __future__ import annotations

import asyncio
import re
import tomllib
from pathlib import Path

import yaml

from scripts.release_metadata import CANONICAL_GIT_SOURCE, load_release_metadata
from scripts.smoke_profiles import ASSIST_TOOLS
from src.surfaces import assist

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSIST_SKILL_DIR = PROJECT_ROOT / ".agents" / "skills" / "gemini-assist"
EXPECTED_FILES = {
    Path("SKILL.md"),
    Path("agents/openai.yaml"),
}
ASSIST_SERVER_NAME = "gemini_assist_mcp"
ASSIST_ENTRYPOINT = "gemini-mcp-assist"


def _files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _skill_text() -> str:
    return (ASSIST_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter_and_body() -> tuple[dict, str]:
    text = _skill_text()
    assert text.startswith("---\n")
    frontmatter, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(frontmatter), body


def test_assist_catalog_is_the_registered_assistance_surface() -> None:
    registered = {tool.name for tool in asyncio.run(assist.mcp.list_tools())}
    assert registered == ASSIST_TOOLS
    assert assist.mcp.name == ASSIST_SERVER_NAME


def test_assist_skill_package_and_frontmatter_are_complete() -> None:
    assert EXPECTED_FILES <= _files(ASSIST_SKILL_DIR)
    metadata, body = _frontmatter_and_body()
    assert metadata["name"] == ASSIST_SKILL_DIR.name
    assert metadata["metadata"]["version"] == load_release_metadata(PROJECT_ROOT).version
    assert metadata["license"] == "MIT-0"
    assert isinstance(metadata["description"], str) and metadata["description"].strip()
    compatibility = metadata.get("compatibility", metadata["metadata"].get("compatibility"))
    assert isinstance(compatibility, str) and compatibility.strip()
    openclaw = metadata["metadata"]["openclaw"]
    assert "uvx" in openclaw["requires"]["bins"]
    assert openclaw["primaryEnv"] == "GEMINI_PSID"
    env_vars = {item["name"]: item for item in openclaw["envVars"]}
    for env_name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC", "GEMINI_PROXY"):
        assert env_vars[env_name]["required"] is False
    assert "TODO" not in _skill_text()
    for link in re.findall(r"\]\((references/[^)]+)\)", body):
        reference = ASSIST_SKILL_DIR / link.split("#", 1)[0]
        assert reference.is_file()
        assert reference.read_text(encoding="utf-8").strip()


def test_assist_skill_documents_the_real_surface_catalog() -> None:
    _, body = _frontmatter_and_body()
    registered = {tool.name for tool in asyncio.run(assist.mcp.list_tools())}
    documented = set(re.findall(r"gemini_[a-z_]+", body))
    assert documented == registered | {ASSIST_SERVER_NAME}


def test_assist_skill_documents_the_installed_entrypoint() -> None:
    text = _skill_text()
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"][ASSIST_ENTRYPOINT] == "src.surfaces.assist:main"
    assert f"uvx --from {CANONICAL_GIT_SOURCE} {ASSIST_ENTRYPOINT}" in text
    assert ASSIST_SERVER_NAME in text


def test_assist_skill_ui_metadata_can_invoke_the_skill() -> None:
    metadata = yaml.safe_load((ASSIST_SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    interface = metadata["interface"]
    for field in ("display_name", "short_description", "default_prompt"):
        assert isinstance(interface[field], str) and interface[field].strip()
    assert f"${ASSIST_SKILL_DIR.name}" in interface["default_prompt"]
