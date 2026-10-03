"""Contract tests for the repository development Agent Skill."""

from __future__ import annotations

import asyncio
import re
import tomllib
from pathlib import Path

import yaml

from scripts.release_metadata import load_release_metadata
from scripts.smoke_profiles import ACCOUNT_TOOLS, ASSIST_TOOLS, CREATE_TOOLS
from src.surfaces import account, assist, create


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL = ROOT / ".agents" / "skills" / "gemini-web-mcp-development"
EXPECTED_FILES = {
    Path("SKILL.md"),
    Path("agents/openai.yaml"),
    Path("references/architecture.md"),
    Path("references/roadmap.md"),
    Path("references/tool-design.md"),
    Path("references/validation.md"),
}


def _files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def test_development_skill_has_one_complete_public_source() -> None:
    assert EXPECTED_FILES <= _files(PUBLIC_SKILL)
    assert not (ROOT / ".codex" / "skills" / "gemini-web-mcp-development" / "SKILL.md").exists()


def test_development_skill_frontmatter_and_progressive_disclosure() -> None:
    skill_path = PUBLIC_SKILL / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == "---"
    closing_index = lines[1:].index("---") + 1
    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])

    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == PUBLIC_SKILL.name
    assert isinstance(metadata["description"], str) and metadata["description"].strip()
    assert metadata["license"] == "AGPL-3.0-only"
    compatibility = metadata.get("compatibility", metadata["metadata"].get("compatibility"))
    assert isinstance(compatibility, str) and compatibility.strip()
    assert metadata["metadata"]["scope"] == "development"
    assert metadata["metadata"]["version"] == load_release_metadata(ROOT).version
    reference_links = re.findall(r"\]\((references/[^)]+)\)", body)
    assert reference_links
    for relative_link in reference_links:
        reference = PUBLIC_SKILL / relative_link.split("#", 1)[0]
        assert reference.is_file()
        assert reference.read_text(encoding="utf-8").strip()


def test_development_skill_has_no_machine_specific_paths() -> None:
    for relative_path in EXPECTED_FILES:
        text = (PUBLIC_SKILL / relative_path).read_text(encoding="utf-8")
        assert text.strip()
        assert "/Users/" not in text
        assert "C:\\Users\\" not in text


def test_focused_product_catalogs_have_installed_entrypoints() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for lane, server, expected in (
        ("assist", assist.mcp, ASSIST_TOOLS),
        ("create", create.mcp, CREATE_TOOLS),
        ("account", account.mcp, ACCOUNT_TOOLS),
    ):
        registered = {tool.name for tool in asyncio.run(server.list_tools())}
        assert registered == expected
        entrypoint = f"gemini-mcp-{lane}"
        assert project["project"]["scripts"][entrypoint] == f"src.surfaces.{lane}:main"


def test_development_skill_ui_metadata_can_invoke_the_skill() -> None:
    metadata = yaml.safe_load((PUBLIC_SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    interface = metadata["interface"]
    for field in ("display_name", "short_description", "default_prompt"):
        assert isinstance(interface[field], str) and interface[field].strip()
    assert f"${PUBLIC_SKILL.name}" in interface["default_prompt"]
