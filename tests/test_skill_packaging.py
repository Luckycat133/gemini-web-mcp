from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1] / ".codex" / "skills" / "gemini-web-mcp"


def test_project_skill_frontmatter_and_guidance_are_complete():
    skill = SKILL_DIR / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "name: gemini-web-mcp" in text
    assert "description:" in text
    assert "TODO" not in text
    assert "gemini_get_tool_manifest" in text
    assert "current_enabled" in text
    assert "privacy=reads_private_chat_text" in text
    assert "Delete only with explicit confirmation" in text
    assert "evaluations/gemini_web_mcp_contract.xml" in text


def test_project_skill_openai_metadata_points_to_skill():
    metadata = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert 'display_name: "Gemini Web MCP"' in metadata
    assert "$gemini-web-mcp" in metadata
    assert "TODO" not in metadata
