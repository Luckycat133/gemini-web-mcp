"""Trigger and packaging contracts for the ``gemini-assist`` runtime Skill.

The Skill description is the trigger boundary (see
``.agents/skills/gemini-web-mcp-development/references/tool-design.md``), so
these tests pin both directions:

- positive assistance intents select the assistance Skill and route to one of
  its five documented tools;
- near-miss negatives — pure generation, Gemini account administration, and
  repository development — are explicitly routed away, and the assist catalog
  itself contains no creation, account, or maintenance tool.

The packaging contracts mirror ``tests/test_skill_packaging.py`` for the
compatibility Skill: an exact file set, complete frontmatter, a body under 500
lines, and a documented tool catalog that matches the real
``gemini_assist_mcp`` surface snapshot.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.smoke_profiles import ASSIST_TOOLS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSIST_SKILL_DIR = PROJECT_ROOT / ".agents" / "skills" / "gemini-assist"
EXPECTED_FILES = {
    Path("SKILL.md"),
    Path("agents/openai.yaml"),
}
ASSIST_SERVER_NAME = "gemini_assist_mcp"
ASSIST_ENTRYPOINT_COMMAND = "uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-assist"

# The five assist positives from tool-design.md, each mapped to the activation
# term that must appear in the trigger description and the owning tool that
# must be documented in the Skill body.
ASSIST_POSITIVE_INTENTS = {
    "Check the latest framework documentation and give me sourced migration advice.": (
        "grounded current-web search",
        "gemini_search",
    ),
    "Explain the error in this screenshot and tell me what code to change.": (
        "image or screenshot understanding",
        "gemini_understand_image",
    ),
    "Compare these two UI screenshots with the implementation.": (
        "file, URL, or mixed-input understanding",
        "gemini_understand",
    ),
    "Ask another strong model to criticize this architecture.": (
        "second opinion",
        "gemini_ask",
    ),
    "Research this technical market and produce a sourced report.": (
        "Deep Research",
        "gemini_research",
    ),
}

# The three assist near-miss negatives from tool-design.md, each mapped to the
# exclusion term that must appear in the trigger description.
ASSIST_NEGATIVE_INTENTS = {
    "Generate a hero image for this landing page.": "pure image, video, or music generation",
    "Delete my old Gemini conversations.": "Gemini account administration",
    "Refactor the MCP repository.": "repository development",
}


def _files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _skill_text() -> str:
    return (ASSIST_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter_and_body() -> tuple[str, str]:
    lines = _skill_text().splitlines()
    assert lines[0] == "---"
    closing_index = lines[1:].index("---") + 1
    return "\n".join(lines[1:closing_index]), "\n".join(lines[closing_index + 1 :])


# ---------------------------------------------------------------------------
# Trigger boundary: positive assistance intents
# ---------------------------------------------------------------------------


def test_description_activates_on_assistance_intents() -> None:
    frontmatter, _ = _frontmatter_and_body()
    description = re.search(r"^description: (.+)$", frontmatter, re.MULTILINE)
    assert description is not None

    for activation_term in (
        "second opinion",
        "critique",
        "code or design review",
        "grounded current-web search with observed sources",
        "image or screenshot understanding",
        "file, URL, or mixed-input understanding",
        "Deep Research",
    ):
        assert activation_term in description.group(1)


def test_positive_assistance_intents_select_documented_assist_tools() -> None:
    frontmatter, body = _frontmatter_and_body()
    description = re.search(r"^description: (.+)$", frontmatter, re.MULTILINE)
    assert description is not None

    for intent, (activation_term, owning_tool) in ASSIST_POSITIVE_INTENTS.items():
        # The description's activation term covers the intent's task language.
        assert activation_term in description.group(1), intent
        # The owning tool is a real assist-surface tool and is documented.
        assert owning_tool in ASSIST_TOOLS, intent
        assert f"`{owning_tool}`" in body, intent


def test_skill_routes_each_assistance_intent_to_exactly_one_tool() -> None:
    _, body = _frontmatter_and_body()
    routing_table = body.split("## Choose The Tool", 1)[1].split("##", 1)[0]

    for _intent, (_activation_term, owning_tool) in ASSIST_POSITIVE_INTENTS.items():
        assert routing_table.count(f"`{owning_tool}`") >= 1


# ---------------------------------------------------------------------------
# Trigger boundary: near-miss negatives routed away
# ---------------------------------------------------------------------------


def test_description_routes_generation_account_and_development_away() -> None:
    frontmatter, _ = _frontmatter_and_body()
    description = re.search(r"^description: (.+)$", frontmatter, re.MULTILINE)
    assert description is not None
    description_text = description.group(1)

    assert "Do not use it for" in description_text
    for exclusion_term in ASSIST_NEGATIVE_INTENTS.values():
        assert exclusion_term in description_text
    # The routed-away lanes are named as capabilities, not as products that
    # would have to exist for the routing to work.
    assert "route those requests to the creation, account, or development capability instead" in description_text


def test_assist_catalog_rejects_generation_account_and_maintenance_tools() -> None:
    assert ASSIST_TOOLS == {
        "gemini_ask",
        "gemini_search",
        "gemini_understand",
        "gemini_understand_image",
        "gemini_research",
    }

    for foreign_tool in (
        "gemini_generate_media",
        "gemini_generate_music",
        "gemini_generate_image",
        "gemini_history",
        "gemini_delete_chat",
        "gemini_manage_gems",
        "gemini_manage_prompts",
        "gemini_cleanup_test_artifacts",
        "gemini_get_tool_manifest",
        "gemini_doctor",
        "gemini_get_cookie_status",
    ):
        assert foreign_tool not in ASSIST_TOOLS


def test_skill_boundaries_repeat_the_exclusions_without_claiming_other_products() -> None:
    _, body = _frontmatter_and_body()
    boundaries = body.split("## Boundaries", 1)[1].split("##", 1)[0]

    for exclusion_term in (
        "generation",
        "Gemini account administration",
        "development work",
    ):
        assert exclusion_term in boundaries

    # The assist Skill must not claim the create/account products exist.
    full_text = _skill_text()
    assert "gemini-create" not in full_text
    assert "gemini-account" not in full_text


# ---------------------------------------------------------------------------
# Packaging contracts (mirrors tests/test_skill_packaging.py)
# ---------------------------------------------------------------------------


def test_assist_skill_file_set_is_exact() -> None:
    assert _files(ASSIST_SKILL_DIR) == EXPECTED_FILES


def test_assist_skill_frontmatter_is_complete() -> None:
    frontmatter, _ = _frontmatter_and_body()
    text = _skill_text()
    lines = text.splitlines()

    assert re.search(r"^name: gemini-assist$", frontmatter, re.MULTILINE)
    assert 'version: "0.2.1"' in frontmatter
    assert "license: MIT-0" in frontmatter
    assert "license: AGPL-3.0-only" not in frontmatter
    assert "openclaw:" in frontmatter
    assert "- uvx" in frontmatter
    assert "primaryEnv: GEMINI_PSID" in frontmatter
    for env_name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC", "GEMINI_PROXY"):
        assert f"name: {env_name}" in frontmatter
    assert len(lines) < 500
    assert "TODO" not in text


def test_assist_skill_documents_the_real_surface_catalog() -> None:
    _, body = _frontmatter_and_body()

    # Only the five real assist tools (plus the server name) may appear as
    # gemini_-prefixed identifiers; no phantom tool may be documented.
    documented = set(re.findall(r"gemini_[a-z_]+", body))
    assert documented == ASSIST_TOOLS | {ASSIST_SERVER_NAME}
    assert ASSIST_TOOLS <= documented


def test_assist_skill_documents_entrypoint_and_core_contracts() -> None:
    text = _skill_text()

    # Entrypoint and installation.
    assert "gemini-mcp-assist" in text
    assert ASSIST_ENTRYPOINT_COMMAND in text
    assert ASSIST_SERVER_NAME in text

    # Truthful grounding states.
    assert "grounded | answer_only | unavailable | failed" in text
    assert "never labeled grounded" in text

    # Artifact-less information-return semantics for search/understanding.
    assert "Information, Not Artifacts" in text
    assert "return information to the calling agent, not files" in text

    # Asynchronous research start with operation handle preservation.
    assert "Starts Asynchronously" in text
    assert "operation_id" in text
    assert "upstream_operation_id" in text
    assert "upstream_chat_id" in text
    assert "never start a duplicate run" in text


def test_assist_skill_openai_metadata_is_assistance_only() -> None:
    metadata = (ASSIST_SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert 'display_name: "Gemini Assist"' in metadata
    assert "$gemini-assist" in metadata
    assert ASSIST_ENTRYPOINT_COMMAND in metadata
    for tool_name in sorted(ASSIST_TOOLS):
        assert tool_name in metadata
    assert "only for assistance intents" in metadata
    assert "Do not use it for pure generation, Gemini account administration, or repository development" in metadata
    assert "TODO" not in metadata
