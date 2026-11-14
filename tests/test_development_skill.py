"""Offline integrity of the development Skill and its actual source contracts."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL = ROOT / ".agents" / "skills" / "gemini-web-mcp-development"
REFERENCES = {"architecture.md", "roadmap.md", "tool-design.md", "validation.md"}


def _body() -> tuple[str, str]:
    text = (PUBLIC_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter, body = text[4:].split("\n---\n", 1)
    return frontmatter, body


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _catalogs() -> dict[str, set[str]]:
    """Read literal membership without importing any runtime/authentication code."""
    source = (ROOT / "src/surfaces/catalogs.py").read_text(encoding="utf-8")
    result = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in {"FUNCTION_TOOLS", "ACCOUNT_TOOLS"}:
            continue
        assert isinstance(node.value, ast.Call)
        assert isinstance(node.value.func, ast.Name) and node.value.func.id == "frozenset"
        values = ast.literal_eval(node.value.args[0])
        assert isinstance(values, set) and all(isinstance(value, str) for value in values)
        result[target.id] = values
    assert set(result) == {"FUNCTION_TOOLS", "ACCOUNT_TOOLS"}
    return result


def test_development_skill_has_one_complete_public_source() -> None:
    for relative in ("SKILL.md", "agents/openai.yaml", "assets/plan.json", "scripts/plan.py"):
        assert (PUBLIC_SKILL / relative).is_file()
    assert not (ROOT / ".codex/skills/gemini-web-mcp-development/SKILL.md").exists()


def test_frontmatter_matches_checkout_not_a_frozen_release_version() -> None:
    frontmatter, body = _body()
    assert re.search(r"^name: " + re.escape(PUBLIC_SKILL.name) + r"$", frontmatter, re.M)
    description = re.search(r'^description: "(.+)"$', frontmatter, re.M)
    assert description is not None and 1 <= len(description[1]) <= 1024
    assert "Use for" in description[1]
    assert "two focused business tool surfaces" in description[1]
    assert re.search(r"^license: AGPL-3.0-only$", frontmatter, re.M)
    assert re.search(r"^  scope: development$", frontmatter, re.M)
    assert f'  version: "{_project()["version"]}"' in frontmatter
    assert len((frontmatter + body).splitlines()) < 500


def test_primary_references_are_portable_and_resolve() -> None:
    _, body = _body()
    links = re.findall(r"\]\((references/[^)]+)\)", body)
    assert set(links) == {f"references/{name}" for name in REFERENCES}
    for target in links:
        path = (PUBLIC_SKILL / target).resolve()
        assert path.is_relative_to(PUBLIC_SKILL.resolve())
        assert path.is_file() and path.read_text(encoding="utf-8").strip()


def test_reference_catalogs_cover_the_actual_business_tools() -> None:
    architecture = (PUBLIC_SKILL / "references/architecture.md").read_text(encoding="utf-8")
    names = set(re.findall(r"`(gemini_[a-z_]+)`", architecture))
    for tools in _catalogs().values():
        assert tools <= names
    # Check the documented entrypoints against package metadata, not a hardcoded count.
    for command in ("gemini-mcp-function", "gemini-mcp-account", "gemini-mcp-assist",
                    "gemini-mcp-skill-server", "gemini-mcp-server", "gemini-mcp-onboarding"):
        assert command in _project()["scripts"] and f"`{command}`" in architecture


def test_plan_version_and_work_package_references_are_current() -> None:
    plan = json.loads((PUBLIC_SKILL / "assets/plan.json").read_text(encoding="utf-8"))
    assert plan["package_version"] == _project()["version"]
    assert re.fullmatch(r"[0-9a-f]{40}", plan["baseline_commit"])
    for package in plan["packages"]:
        reference = PUBLIC_SKILL / package["reference"]
        assert reference.is_file()
        assert re.search(r"^#{1,6}\s+" + re.escape(package["id"]) + r"\b",
                         reference.read_text(encoding="utf-8"), re.M)


def test_bundled_plan_integrity_checker() -> None:
    subprocess.run([sys.executable, str(PUBLIC_SKILL / "scripts/plan.py"), "check"],
                   check=True, timeout=30, capture_output=True, text=True)


def test_ui_metadata_invokes_the_skill() -> None:
    text = (PUBLIC_SKILL / "agents/openai.yaml").read_text(encoding="utf-8")
    for field in ("display_name", "short_description", "default_prompt"):
        assert re.search(r"^  " + field + r': ".+"$', text, re.M)
    assert f"${PUBLIC_SKILL.name}" in text


def test_guidance_has_no_machine_paths_or_obsolete_release_instruction() -> None:
    text = "\n".join((PUBLIC_SKILL / "references" / name).read_text(encoding="utf-8")
                     for name in REFERENCES) + (PUBLIC_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "/Users/" not in text and "C:\\Users\\" not in text
    assert "use `v0.2.1` for this patch line" not in text
    assert "#codex/gemini-tools-polish-20261101" not in text
