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
    assert expected <= {item.relative_to(directory) for item in directory.rglob("*") if item.is_file()}
    text = (directory / "SKILL.md").read_text()
    frontmatter, body = text[4:].split("\n---\n", 1)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == name
    assert metadata["metadata"]["version"] == load_release_metadata(ROOT).version
    assert metadata["license"] == "MIT-0"
    assert isinstance(metadata["description"], str) and metadata["description"].strip()
    compatibility = metadata.get("compatibility", metadata["metadata"].get("compatibility"))
    assert isinstance(compatibility, str) and compatibility.strip()
    assert f"uvx --from {CANONICAL_GIT_SOURCE} {entry}" in text
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lane = name.removeprefix("gemini-")
    assert project["project"]["scripts"][entry] == f"src.surfaces.{lane}:main"
    openclaw = metadata["metadata"]["openclaw"]
    assert "uvx" in openclaw["requires"]["bins"]
    assert openclaw["primaryEnv"] == "GEMINI_PSID"
    env_vars = {item["name"]: item for item in openclaw["envVars"]}
    for env_name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC", "GEMINI_PROXY", "GEMINI_STATE_DB_PATH"):
        assert env_vars[env_name]["required"] is False
    assert "TODO" not in text
    server = {"gemini-create": create.mcp, "gemini-account": account.mcp}[name]
    registered = {tool.name for tool in asyncio.run(server.list_tools())}
    assert registered == catalog
    documentation = "\n".join(path.read_text() for path in directory.rglob("*.md"))
    documented = set(re.findall(r"\bgemini_[a-z_]+\b", documentation))
    assert documented == registered | {server.name}
    links = re.findall(r"\]\((references/[^)]+)\)", body)
    assert links
    for link in links:
        reference = directory / link.split("#", 1)[0]
        assert reference.is_file()
        assert reference.read_text().strip()
    interface = yaml.safe_load((directory / "agents/openai.yaml").read_text())["interface"]
    for field in ("display_name", "short_description", "default_prompt"):
        assert isinstance(interface[field], str) and interface[field].strip()
    assert 25 <= len(interface["short_description"]) <= 64
    assert f"${name}" in interface["default_prompt"]


def test_expected_routing_cases_use_real_tools_and_valid_action_schemas():
    async def tools():
        result = {}
        for lane, server in (("gemini-assist", assist.mcp), ("gemini-create", create.mcp), ("gemini-account", account.mcp)):
            result[lane] = {item.name: item for item in await server.list_tools()}
        return result
    catalogs = asyncio.run(tools())
    assert CASES and len({case["id"] for case in CASES}) == len(CASES)
    for case in CASES:
        assert case["intent"] and case["handoff"]
        for call in case["calls"]:
            tool = catalogs[case["skill"]][call["tool"]]
            jsonschema.validate(call["arguments"], tool.input_schema)
    by_id = {case["id"]: case for case in CASES}
    assert [call["tool"] for call in by_id["music_timeout"]["calls"]] == ["gemini_get_operation_result"]
    assert by_id["negative_local_cleanup"]["calls"] == []
    assert all(by_id[name]["skill"] == "gemini-assist" for name in ("negative_understand", "negative_search", "negative_text_prompt"))
    assert by_id["negative_repo"]["skill"] == "gemini-web-mcp-development"
    assert by_id["negative_repo"]["calls"] == []


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
    packaged_names = set()
    for asset in assets:
        with zipfile.ZipFile(asset) as archive:
            name = archive.namelist()[0].split("/", 1)[0]
            assert name in names
            packaged_names.add(name)
            expected = {f"{name}/{item.relative_to(ROOT / '.agents/skills' / name).as_posix()}"
                        for item in (ROOT / ".agents/skills" / name).rglob("*") if item.is_file()}
            assert set(archive.namelist()) == expected
            for item in (ROOT / ".agents/skills" / name).rglob("*"):
                if item.is_file():
                    member = f"{name}/{item.relative_to(ROOT / '.agents/skills' / name).as_posix()}"
                    assert archive.read(member) == item.read_bytes()
    assert packaged_names == names


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
