from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.release_metadata import load_release_metadata


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
    assert "generate image/video/music artifacts, edit images" in frontmatter
    assert f'version: "{load_release_metadata(PROJECT_ROOT).version}"' in frontmatter
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
        "Start Deep Research without waiting for completion",
        "Focused Research exposes action=status/result/cancel",
        "The Skill supplies routing instructions; an MCP server must also be connected",
        "opaque, restart-safe handle",
        "local SQLite",
        "grounding_state = grounded | answer_only | unavailable | failed",
        "Markdown links written in the answer are model text, not grounding evidence",
        "gemini_understand_image",
        "gemini_understand",
    ):
        assert required in text

    assert "Prefer `gemini_get_tool_manifest` before choosing" not in text
    assert "current_enabled first" not in text


def test_runtime_skill_focused_assist_examples_match_registered_arguments() -> None:
    workflows = (PUBLIC_SKILL_DIR / "references" / "workflows.md").read_text(encoding="utf-8")
    focused_source = "\n".join((PROJECT_ROOT / "src" / "surfaces" / filename).read_text(encoding="utf-8")
                               for filename in ("assist.py", "create.py"))
    module = ast.parse(focused_source)
    registered = {
        node.name: {arg.arg for arg in node.args.args}
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            for decorator in node.decorator_list
        )
    }
    examples = {
        name: argument
        for name, argument in re.findall(r"focused: (gemini_\w+)\((\w+)=", workflows)
    }

    assert examples == {
        "gemini_ask": "prompt",
        "gemini_understand_image": "image",
        "gemini_understand": "task",
        "gemini_generate_image": "prompt",
        "gemini_edit_image": "image_path",
    }
    assert all(argument in registered[name] for name, argument in examples.items())


def test_compact_tool_reference_lists_exactly_the_registered_tools() -> None:
    reference = (PUBLIC_SKILL_DIR / "references" / "tool_surface.md").read_text(encoding="utf-8")
    compact_section = reference.split("## Low-token skill server facade", 1)[1].split(
        "## Tool group selection", 1
    )[0]
    documented = set(re.findall(r"^\| `(\w+)` \|", compact_section, re.MULTILINE))
    module = ast.parse((PROJECT_ROOT / "src" / "skill_server.py").read_text(encoding="utf-8"))
    registered = {
        node.name
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "mcp"
            for decorator in node.decorator_list
        )
    }

    assert documented == registered


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
    assert "choose the next Gemini capability" in metadata
    assert "use returned files in the user's task" in metadata
    assert "use the manifest only for discovery or recovery" in metadata
    assert "current_enabled first" not in metadata
    assert "TODO" not in metadata


def test_description_names_the_focused_siblings_and_states_the_preferences() -> None:
    skill = PUBLIC_SKILL_DIR / "SKILL.md"
    lines = skill.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    closing_index = lines[1:].index("---") + 1
    frontmatter = "\n".join(lines[1:closing_index])
    description = re.search(r"^description: (.+)$", frontmatter, re.MULTILINE)
    assert description is not None
    description_text = description.group(1)

    # Focused Skills own single-purpose intents; the compatibility router names
    # each actual sibling rather than competing with its trigger description.
    for preference in (
        "prefer gemini-assist for assistance/understanding", "gemini-create for creation",
        "gemini-account for explicit account work",
    ):
        assert preference in description_text


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
