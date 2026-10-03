from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from scripts.release_metadata import load_release_metadata
from scripts.smoke_profiles import ACCOUNT_TOOLS, ASSIST_TOOLS, CREATE_TOOLS, PRIMARY_PROFILE_TOOLS


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


def test_project_skill_frontmatter_and_progressive_disclosure_are_complete() -> None:
    assert EXPECTED_FILES <= _files(PUBLIC_SKILL_DIR)

    skill = PUBLIC_SKILL_DIR / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == "---"
    closing_index = lines[1:].index("---") + 1
    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])

    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == PUBLIC_SKILL_DIR.name
    assert isinstance(metadata["description"], str) and metadata["description"].strip()
    compatibility = metadata.get("compatibility", metadata["metadata"].get("compatibility"))
    assert isinstance(compatibility, str) and compatibility.strip()
    assert metadata["metadata"]["version"] == load_release_metadata(PROJECT_ROOT).version
    assert metadata["license"] == "MIT-0"
    openclaw = metadata["metadata"]["openclaw"]
    assert "uvx" in openclaw["requires"]["bins"]
    assert openclaw["primaryEnv"] == "GEMINI_PSID"
    env_vars = {item["name"]: item for item in openclaw["envVars"]}
    for optional_env in (
        "GEMINI_PSID",
        "GEMINI_PSIDTS",
        "GEMINI_PSIDCC",
        "GEMINI_PROXY",
        "GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS",
        "GEMINI_TOOLS",
    ):
        assert env_vars[optional_env]["required"] is False

    reference_links = re.findall(r"\]\((references/[^)]+)\)", body)
    assert reference_links
    for relative_link in reference_links:
        reference = PUBLIC_SKILL_DIR / relative_link.split("#", 1)[0]
        assert reference.is_file()
        assert reference.read_text(encoding="utf-8").strip()

    assert "TODO" not in text


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
    documented_calls = re.findall(r"\b(gemini_\w+)\((\w+)=", workflows)
    known_tools = set(registered) | PRIMARY_PROFILE_TOOLS["all"] | ACCOUNT_TOOLS
    assert all(name in known_tools for name, _argument in documented_calls)
    examples = [
        (name, argument)
        for name, argument in documented_calls
        if name in registered
    ]
    assert examples
    assert all(argument in registered[name] for name, argument in examples)


def test_tool_reference_uses_registered_tools_and_profiles() -> None:
    reference = (PUBLIC_SKILL_DIR / "references" / "tool_surface.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `(\w+)` \|", reference, re.MULTILINE))
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

    assert registered <= documented
    known_tools = registered | PRIMARY_PROFILE_TOOLS["all"] | ASSIST_TOOLS | CREATE_TOOLS | ACCOUNT_TOOLS
    assert documented <= known_tools | PRIMARY_PROFILE_TOOLS.keys()


def test_project_skill_ui_metadata_can_invoke_the_skill() -> None:
    metadata = yaml.safe_load((PUBLIC_SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    interface = metadata["interface"]
    for field in ("display_name", "short_description", "default_prompt"):
        assert isinstance(interface[field], str) and interface[field].strip()
    assert f"${PUBLIC_SKILL_DIR.name}" in interface["default_prompt"]


def test_project_skill_names_are_unique_across_discovery_roots() -> None:
    skill_files = sorted(
        path
        for root in (PROJECT_ROOT / ".agents" / "skills", PROJECT_ROOT / ".codex" / "skills")
        if root.exists()
        for path in root.glob("*/SKILL.md")
    )
    discovered_names = []
    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        frontmatter = text[4:].split("\n---\n", 1)[0]
        name = yaml.safe_load(frontmatter)["name"]
        if name.startswith("gemini-"):
            discovered_names.append(name)

    assert set(discovered_names) == {
        "gemini-web-mcp", "gemini-web-mcp-development", "gemini-assist", "gemini-create", "gemini-account",
    }
    assert len(discovered_names) == len(set(discovered_names))
