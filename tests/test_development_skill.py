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
    assert "three focused agent capability products" in frontmatter
    assert "scope: development" in frontmatter
    assert 'version: "0.2.1"' in frontmatter
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


def test_development_skill_defines_focused_products_and_tool_catalogs() -> None:
    text = _all_text(PUBLIC_SKILL)

    for required in (
        "gemini-assist",
        "gemini-create",
        "gemini-account",
        "gemini-mcp-assist",
        "gemini-mcp-create",
        "gemini-mcp-account",
        "gemini_assist_mcp",
        "gemini_create_mcp",
        "gemini_account_mcp",
        "gemini_ask",
        "gemini_search",
        "gemini_understand_image",
        "gemini_understand",
        "gemini_research",
        "gemini_generate_image",
        "gemini_edit_image",
        "gemini_generate_video",
        "gemini_generate_music",
        "gemini_get_operation_status",
        "gemini_get_operation_result",
        "gemini_cancel_operation",
        "gemini_history",
        "gemini_notebooks",
        "gemini_scheduled",
        "gemini_gems",
        "gemini_prompts",
        "gemini_account",
        "gemini_cleanup",
    ):
        assert required in text


def test_development_skill_encodes_current_best_practice_contracts() -> None:
    text = _all_text(PUBLIC_SKILL)

    for required in (
        "Small, Deterministic Tool Catalogs",
        "Task-First Skills",
        "Structured State Is Authoritative",
        "Accepted Is Not Verified",
        "Opaque, Explicit Handles",
        "Local SQLite Persistence Is Settled",
        "Artifact-First Creation",
        "seven-day retention",
        "restart and cross-client resume",
        "grounding_state = grounded | answer_only | unavailable | failed",
        "positive trigger evaluations and near-miss negative evaluations",
        "Do not publish a dedicated Skill before its MCP surface is actually installable and usable",
        "Do not expose a global unbounded operation list",
        "bounded 2026-08-08",
        "tests._fastmcp_shim",
        "Do not hardcode volatile passing-test counts",
    ):
        assert required in text

    for obsolete in (
        "What Is the Long-Term Role of Compact Server?",
        "Long-Operation Persistence and Cancellation",
        "Durable Cleanup Semantics",
        "P3 — Reliability, Release, and Adoption",
        "Immediate Suggested Issue Order",
    ):
        assert obsolete not in text


def test_development_skill_keeps_settled_decisions_out_of_owner_questions() -> None:
    skill = (PUBLIC_SKILL / "SKILL.md").read_text(encoding="utf-8")
    roadmap = (PUBLIC_SKILL / "references" / "roadmap.md").read_text(encoding="utf-8")

    assert "Settled Product Decisions" in skill
    assert "Package A — Task-First Compatibility Skill" in roadmap
    assert "Package B — `gemini-assist`" in roadmap
    assert "Package C — `gemini-create` Image Vertical Slice" in roadmap
    assert "Package D — Shared SQLite OperationService" in roadmap
    assert "Package F — `gemini-account`" in roadmap
    assert "Remaining Owner Choice" in roadmap
    assert "The remaining owner-level choice is the first officially supported client/OS/distribution matrix" in skill
    assert "Next Monotonic Python Package Release" not in roadmap


def test_development_skill_names_maintained_validation_and_agent_use_paths() -> None:
    testing = (PUBLIC_SKILL / "references" / "validation.md").read_text(encoding="utf-8")
    design = (PUBLIC_SKILL / "references" / "tool-design.md").read_text(encoding="utf-8")

    for command in (
        "python -m ruff check src tests scripts",
        "python -m mypy src scripts",
        "python -m pytest -q",
        "python scripts/run_contract_checklist.py",
        "python scripts/smoke_profiles.py",
        "python scripts/smoke_mcp_protocol.py",
        "skills-ref validate .agents/skills/<skill-name>",
    ):
        assert command in testing

    for required in (
        "Trigger Evaluations",
        "near-miss negative cases",
        "End-to-End Agent Evaluations",
        "downstream handoff",
        "no duplicate upstream start",
        "MCP Tasks extension",
        "Compatibility Runtime Skill",
    ):
        assert required in f"{testing}\n{design}"

    assert "REVIEWED_SHA=replace-with-reviewed-40-character-commit" in testing
    assert "<reviewed-sha>" not in testing
    assert "v0.2.1" in testing or "0.2.1" in testing
