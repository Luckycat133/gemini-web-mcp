"""Contract tests for the repository development Agent Skill."""

from __future__ import annotations

import re
from pathlib import Path


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


def _all_text(root: Path) -> str:
    return "\n".join(
        (root / relative_path).read_text(encoding="utf-8")
        for relative_path in sorted(EXPECTED_FILES)
    )


def test_development_skill_has_one_complete_public_source() -> None:
    assert _files(PUBLIC_SKILL) == EXPECTED_FILES
    assert not (ROOT / ".codex" / "skills" / "gemini-web-mcp-development" / "SKILL.md").exists()


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
    assert 'version: "0.2.0"' in frontmatter
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


def test_development_skill_tracks_current_baseline_and_version_lines() -> None:
    text = _all_text(PUBLIC_SKILL)

    for required in (
        "Authentication Boundary Is Settled",
        "Dedicated Full Live Baseline",
        "Long-Operation Persistence and Cancellation",
        "Shared Long-Operation Service",
        "bounded authorized run on 2026-08-08",
        "Python package currently declares `0.2.0`",
        "historical Git tags reach `0.2.0`",
        "ClawHub runtime skill preview is `0.2.0`",
        "clawhub install gemini-web-mcp",
        "tests._fastmcp_shim",
        "Do not hardcode volatile test counts",
    ):
        assert required in text

    for obsolete in (
        "Security Boundary for Cookie and Account Diagnostics",
        "What Is the Long-Term Role of Compact Server?",
        "P3 — Reliability, Release, and Adoption",
        "P0.1",
        "P1.7",
        "P2.3",
        "Immediate Suggested Issue Order",
    ):
        assert obsolete not in text


def test_development_skill_limits_owner_decisions_to_unsettled_contracts() -> None:
    skill = (PUBLIC_SKILL / "SKILL.md").read_text(encoding="utf-8")
    roadmap = (PUBLIC_SKILL / "references" / "roadmap.md").read_text(encoding="utf-8")

    assert "The Cookie boundary, compact-server direction, and reliability-before-UI-parity priority are already established" in skill
    assert "Settled Directions — Do Not Reopen by Default" in roadmap
    assert "Next Monotonic Python Package Release" in roadmap
    assert "Dedicated Live-Compatibility Account" in roadmap
    assert "Long-Operation Persistence and Cancellation" in roadmap
    assert "Durable Cleanup Semantics" in roadmap
    assert "Official Support and Distribution Matrix" in roadmap
    assert "Recommended Conversation Order" in roadmap


def test_development_skill_names_maintained_validation_and_experience_paths() -> None:
    testing = (PUBLIC_SKILL / "references" / "validation.md").read_text(encoding="utf-8")
    experience = (PUBLIC_SKILL / "references" / "tool-design.md").read_text(encoding="utf-8")

    for command in (
        "python -m ruff check src tests scripts",
        "python -m mypy src scripts",
        "python -m pytest -q",
        "python scripts/run_contract_checklist.py",
        "python scripts/smoke_profiles.py",
        "python scripts/smoke_mcp_protocol.py",
        "skills-ref validate .agents/skills/gemini-web-mcp-development",
    ):
        assert command in testing

    assert "credentials_accessed=false" in experience
    assert "--allow-live-account" in experience
    assert "verification.status=verified" in experience
    assert "REVIEWED_SHA=replace-with-reviewed-40-character-commit" in experience
    assert "<reviewed-sha>" not in experience
    assert "Do not encode a volatile passing-test number" in testing
    assert "runtime-skill preview is `0.2.0`" in testing
    assert "verified_absent" in experience
