"""Contract tests for the repository development Agent Skill."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL = ROOT / ".agents" / "skills" / "gemini-web-mcp-development"
CODEX_SKILL = ROOT / ".codex" / "skills" / "gemini-web-mcp-development"
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


def test_development_skill_copies_are_complete_and_identical() -> None:
    assert _files(PUBLIC_SKILL) == EXPECTED_FILES
    assert _files(CODEX_SKILL) == EXPECTED_FILES

    for relative_path in EXPECTED_FILES:
        assert (PUBLIC_SKILL / relative_path).read_bytes() == (
            CODEX_SKILL / relative_path
        ).read_bytes()


def test_development_skill_frontmatter_and_progressive_disclosure() -> None:
    skill_path = PUBLIC_SKILL / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == "---"
    closing_index = lines[1:].index("---") + 1
    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])

    assert re.search(r"^name: gemini-web-mcp-development$", frontmatter, re.MULTILINE)
    assert re.search(r"^description: .+Use for .+$", frontmatter, re.MULTILINE)
    assert "scope: development" in frontmatter
    assert len(lines) < 500

    reference_links = re.findall(r"\]\((references/[^)]+)\)", body)
    assert set(reference_links) == {
        "references/architecture.md",
        "references/roadmap.md",
        "references/tool-design.md",
        "references/validation.md",
    }
    for relative_link in reference_links:
        assert (PUBLIC_SKILL / relative_link).is_file()


def test_development_skill_has_no_machine_specific_paths() -> None:
    for relative_path in EXPECTED_FILES:
        text = (PUBLIC_SKILL / relative_path).read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "C:\\Users\\" not in text
