"""Skill package, expected-routing examples and actual MCP schema contracts.

These reproducible offline cases validate documented calls and expected lane
boundaries. They do not claim an LLM trigger accuracy measurement or live media.
"""

import asyncio
import json
import re
import tomllib
import zipfile
from pathlib import Path

import jsonschema
import pytest
import yaml

from scripts.package_release import build_skill_zips
from scripts.release_metadata import CANONICAL_GIT_SOURCE, load_release_metadata
from scripts.smoke_profiles import ACCOUNT_TOOLS, CREATE_TOOLS
from src.surfaces import account, assist, create

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "evaluations/focused_skill_cases.json").read_text())["cases"]


@pytest.mark.parametrize("name,entry,catalog", [
    ("gemini-create", "gemini-mcp-create", CREATE_TOOLS),
    ("gemini-account", "gemini-mcp-account", ACCOUNT_TOOLS),
])
def test_focused_skill_complete_package_and_real_catalog(name, entry, catalog):
    directory = ROOT / ".agents/skills" / name
    expected = {Path("SKILL.md"), Path("agents/openai.yaml"), Path("references/examples.md")}
    assert {item.relative_to(directory) for item in directory.rglob("*") if item.is_file()} == expected
    text = (directory / "SKILL.md").read_text()
    frontmatter, body = text[4:].split("\n---\n", 1)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == name
    assert metadata["metadata"]["version"] == load_release_metadata(ROOT).version
    assert metadata["license"] == "MIT-0"
    assert entry in metadata["metadata"]["compatibility"] and CANONICAL_GIT_SOURCE in body
    assert "Use this Skill to complete the user's" in body
    assert "Do not use for repository implementation" in body
    assert len(text.splitlines()) < 500 and "TODO" not in text
    documented = set(re.findall(r"`(gemini_[a-z_]+)`", body))
    assert documented == catalog | {name.replace("-", "_") + "_mcp"}
    links = re.findall(r"\]\((references/[^)]+)\)", body)
    assert links and all((directory / link).is_file() for link in links)
    interface = yaml.safe_load((directory / "agents/openai.yaml").read_text())["interface"]
    assert 25 <= len(interface["short_description"]) <= 64
    assert f"${name}" in interface["default_prompt"]
    assert "seven days" in body and "no prompts" in body


def test_expected_routing_cases_use_real_tools_and_valid_action_schemas():
    async def tools():
        result = {}
        for lane, server in (("gemini-assist", assist.mcp), ("gemini-create", create.mcp), ("gemini-account", account.mcp)):
            result[lane] = {item.name: item for item in await server.list_tools()}
        return result
    catalogs = asyncio.run(tools())
    assert len(CASES) == 19 and len({case["id"] for case in CASES}) == 19
    for case in CASES:
        assert case["intent"] and case["handoff"]
        for call in case["calls"]:
            tool = catalogs[case["skill"]][call["tool"]]
            jsonschema.validate(call["arguments"], tool.input_schema)
    by_id = {case["id"]: case for case in CASES}
    assert [call["tool"] for call in by_id["music_timeout"]["calls"]] == ["gemini_get_operation_result"]
    assert by_id["negative_local_cleanup"]["calls"] == []
    assert all(by_id[name]["skill"] == "gemini-assist" for name in ("negative_understand", "negative_search", "negative_text_prompt"))


def test_skill_json_examples_validate_against_registered_schema():
    async def schemas():
        return {item.name: item.input_schema for server in (create.mcp, account.mcp) for item in await server.list_tools()}
    actual = asyncio.run(schemas())
    for name in ("gemini-create", "gemini-account"):
        examples = (ROOT / ".agents/skills" / name / "references/examples.md").read_text().split("```json", 1)[1].split("```", 1)[0]
        for line in examples.strip().splitlines():
            call = json.loads(line)
            jsonschema.validate(call["arguments"], actual[call["tool"]])


def test_all_four_runtime_skill_zips_are_complete(tmp_path):
    names = {"gemini-web-mcp", "gemini-assist", "gemini-create", "gemini-account"}
    assets = build_skill_zips(tmp_path, load_release_metadata(ROOT).version)
    assert len(assets) == 4
    for asset in assets:
        with zipfile.ZipFile(asset) as archive:
            name = archive.namelist()[0].split("/", 1)[0]
            assert name in names
            expected = {f"{name}/{item.relative_to(ROOT / '.agents/skills' / name).as_posix()}"
                        for item in (ROOT / ".agents/skills" / name).rglob("*") if item.is_file()}
            assert set(archive.namelist()) == expected


def test_focused_client_examples_use_installed_entrypoints_without_secrets():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    expected = {f"gemini-{lane}" for lane in ("assist", "create", "account")}
    configs = [tomllib.loads((ROOT / "examples/clients/codex.focused.config.toml").read_text())["mcp_servers"]]
    for filename, key in (("claude-desktop.focused.json", "mcpServers"), ("claude-code.focused.mcp.json", "mcpServers"), ("vscode.focused.mcp.json", "servers")):
        configs.append(json.loads((ROOT / "examples/clients" / filename).read_text())[key])
    for config in configs:
        assert set(config) == expected
        for name, item in config.items():
            assert item["command"] == "uvx" and item["args"] == ["--from", CANONICAL_GIT_SOURCE, name.replace("gemini-", "gemini-mcp-")]
            assert item["args"][-1] in project["project"]["scripts"]
            assert not any(key.startswith("GEMINI_PSID") for key in item.get("env", {}))


def test_runtime_guidance_matches_native_routes_restart_cleanup_and_cancellation_proof():
    umbrella = (ROOT / ".agents/skills/gemini-web-mcp/SKILL.md").read_text()
    creation = (ROOT / ".agents/skills/gemini-create/SKILL.md").read_text()
    operations = (ROOT / ".agents/skills/gemini-web-mcp/references/operations.md").read_text()
    default_prompt = yaml.safe_load((ROOT / ".agents/skills/gemini-web-mcp/agents/openai.yaml").read_text())["interface"]["default_prompt"]
    assert "delayed jobs remain in memory" not in umbrella
    assert "until a verified MCP video route" not in umbrella
    assert "SQLite" in umbrella and "survive restart" in umbrella
    assert "gemini-mcp-create" in default_prompt and "gemini-mcp-account" in default_prompt
    assert "dedicated Videos page for video" not in default_prompt
    for text in (umbrella, creation, operations):
        assert "local_cancelled_before_start" in text and "provider" in text and "cancel_requested" in text
    assert "Before each new video/music start" in creation
