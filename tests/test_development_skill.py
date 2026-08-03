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


def _all_text(root: Path) -> str:
    return "\n".join(
        (root / relative_path).read_text(encoding="utf-8")
        for relative_path in sorted(EXPECTED_FILES)
    )


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
    assert 'version: "1.1"' in frontmatter
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


def test_development_skill_tracks_the_current_implemented_foundation() -> None:
    text = _all_text(PUBLIC_SKILL)

    for required in (
        "MCP Python SDK v2",
        "SessionService",
        "DomainResult",
        "src/infrastructure/rpc_contracts.py",
        "gemini-mcp-onboarding",
        "P3 — Reliability, Release, and Adoption",
        "compatibility text",
        "structured result",
    ):
        assert required in text

    for stale_claim in (
        "currently uses a synchronous `threading.Lock` around an awaited network initialization",
        "Current compact session IDs are derived from `len(_sessions) + 1`",
        "The management module is also carrying too many domains",
        "the compact server does not have a clear console entrypoint",
        "Do not start a broad MCP v2 rewrite before the P0 lifecycle",
    ):
        assert stale_claim not in text


def test_development_skill_names_the_maintained_validation_gates() -> None:
    text = (PUBLIC_SKILL / "references/validation.md").read_text(encoding="utf-8")

    assert "python -m ruff check src tests scripts" in text
    assert "python -m mypy src scripts" in text
    assert "python -m pytest -q" in text
    assert "python scripts/run_contract_checklist.py" in text
    assert "Do not describe Ruff or Mypy as future gates" in text
