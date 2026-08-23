from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL_DIR = PROJECT_ROOT / ".agents" / "skills" / "gemini-web-mcp"
EXPECTED_FILES = {
    Path("SKILL.md"),
    Path("agents/openai.yaml"),
    Path("references/tool_surface.md"),
    Path("references/workflows.md"),
    Path("references/artifacts.md"),
    Path("references/operations.md"),
    Path("references/recovery.md"),
}


def _files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _all_text() -> str:
    return "\n".join(
        (PUBLIC_SKILL_DIR / relative_path).read_text(encoding="utf-8")
        for relative_path in sorted(EXPECTED_FILES)
    )


def test_project_skill_frontmatter_and_progressive_disclosure_are_complete() -> None:
    assert _files(PUBLIC_SKILL_DIR) == EXPECTED_FILES

    skill = PUBLIC_SKILL_DIR / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == "---"
    closing_index = lines[1:].index("---") + 1
    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])

    assert re.search(r"^name: gemini-web-mcp$", frontmatter, re.MULTILINE)
    assert "get a second opinion" in frontmatter
    assert "understand images/files/URLs" in frontmatter
    assert "generate or edit image/video/music artifacts" in frontmatter
    assert 'version: "0.2.1"' in frontmatter
    assert "license: MIT-0" in frontmatter
    assert "openclaw:" in frontmatter
    assert "- uvx" in frontmatter
    assert "primaryEnv: GEMINI_PSID" in frontmatter
    assert len(lines) < 500

    for optional_env in (
        "GEMINI_PSID",
        "GEMINI_PSIDTS",
        "GEMINI_PSIDCC",
        "GEMINI_PROXY",
        "GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS",
        "GEMINI_TOOLS",
    ):
        assert f"name: {optional_env}" in frontmatter

    reference_links = re.findall(r"\]\((references/[^)]+)\)", body)
    assert set(reference_links) == {
        "references/tool_surface.md",
        "references/workflows.md",
        "references/artifacts.md",
        "references/operations.md",
        "references/recovery.md",
    }
    for relative_link in reference_links:
        assert (PUBLIC_SKILL_DIR / relative_link).is_file()

    assert "TODO" not in text


def test_runtime_skill_is_task_first_and_artifact_aware() -> None:
    text = _all_text()

    for required in (
        "Agent assistance and multimodal understanding",
        "Generated artifacts",
        "Explicit Gemini account management",
        "Choose the Capability Lane",
        "gemini-assist",
        "gemini-create",
        "gemini-account",
        "Do **not** call the manifest before every known workflow",
        "pass that file or URI to the next relevant tool",
        "Start them asynchronously by default",
        "opaque, restart-safe handle",
        "local SQLite",
        "grounding_state = grounded | answer_only | unavailable | failed",
        "gemini_understand_image",
        "gemini_understand",
    ):
        assert required in text

    assert "Prefer `gemini_get_tool_manifest` before choosing" not in text
    assert "current_enabled first" not in text


def test_clawhub_security_audit_findings_are_addressed() -> None:
    published_text = _all_text()

    assert "sensitive account-authentication material" in published_text
    assert "explicit user approval" in published_text
    assert "restrict file access" in published_text
    assert "agent memory or agent instructions" in published_text
    assert "arbitrary credential files" in published_text
    assert "reset state" not in published_text.lower()
    assert "Keychain" not in published_text


def test_project_skill_openai_metadata_is_task_first() -> None:
    metadata = (PUBLIC_SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert 'display_name: "Gemini Web MCP"' in metadata
    assert "$gemini-web-mcp" in metadata
    assert "choose one user-intent lane" in metadata
    assert "continue the user's workflow" in metadata
    assert "Use the manifest only for discovery or recovery" in metadata
    assert "current_enabled first" not in metadata
    assert "TODO" not in metadata


def test_description_names_the_assist_sibling_and_states_the_preference() -> None:
    skill = PUBLIC_SKILL_DIR / "SKILL.md"
    lines = skill.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    closing_index = lines[1:].index("---") + 1
    frontmatter = "\n".join(lines[1:closing_index])
    description = re.search(r"^description: (.+)$", frontmatter, re.MULTILINE)
    assert description is not None
    description_text = description.group(1)

    # The gemini-assist Skill claims the same assistance lanes, so this
    # description must disambiguate by naming the focused sibling and deferring
    # to it when only assistance and understanding are needed.
    assert "gemini-assist" in description_text
    assert (
        "prefer the focused gemini-assist skill when only assistance"
        " and understanding are needed" in description_text
    )


def test_project_skill_names_are_unique_across_discovery_roots() -> None:
    skill_files = sorted(
        path
        for root in (PROJECT_ROOT / ".agents" / "skills", PROJECT_ROOT / ".codex" / "skills")
        if root.exists()
        for path in root.glob("*/SKILL.md")
    )
    discovered_names = []
    for skill_file in skill_files:
        match = re.search(r"^name:\s*(\S+)\s*$", skill_file.read_text(encoding="utf-8"), re.MULTILINE)
        assert match is not None
        name = match.group(1)
        if name.startswith("gemini-web-mcp"):
            discovered_names.append(name)

    assert len(discovered_names) == len(set(discovered_names))
