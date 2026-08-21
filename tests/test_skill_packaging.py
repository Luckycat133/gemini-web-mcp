import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL_DIR = PROJECT_ROOT / ".agents" / "skills" / "gemini-web-mcp"


def test_project_skill_frontmatter_and_guidance_are_complete():
    skill = PUBLIC_SKILL_DIR / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "name: gemini-web-mcp" in text
    assert "description:" in text
    assert 'version: "0.2.1"' in text
    assert "license: MIT-0" in text
    assert "openclaw:" in text
    assert "bins:" in text
    assert "- uvx" in text
    assert "primaryEnv: GEMINI_PSID" in text
    for optional_env in (
        "GEMINI_PSID",
        "GEMINI_PSIDTS",
        "GEMINI_PSIDCC",
        "GEMINI_PROXY",
        "GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS",
        "GEMINI_TOOLS",
    ):
        assert f"name: {optional_env}" in text
    assert "TODO" not in text
    assert "gemini_get_tool_manifest" in text
    assert "current_enabled" in text
    assert "privacy=reads_private_chat_text" in text
    assert "Delete only with explicit confirmation" in text
    assert "evaluations/gemini_web_mcp_contract.xml" in text


def test_clawhub_security_audit_findings_are_addressed():
    skill = (PUBLIC_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    tool_surface = (PUBLIC_SKILL_DIR / "references" / "tool_surface.md").read_text(encoding="utf-8")
    published_text = f"{skill}\n{tool_surface}"

    assert "sensitive account-authentication material" in published_text
    assert "explicit user approval" in published_text
    assert "restrict file access" in published_text
    assert "agent memory or agent instructions" in published_text
    assert "arbitrary credential files" in published_text
    assert "reset state" not in published_text.lower()
    assert "Keychain" not in published_text


def test_project_skill_openai_metadata_points_to_skill():
    metadata = (PUBLIC_SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert 'display_name: "Gemini Web MCP"' in metadata
    assert "$gemini-web-mcp" in metadata
    assert "TODO" not in metadata


def test_project_skill_names_are_unique_across_discovery_roots():
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
