"""Contracts for copyable client configuration and public skill onboarding."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from scripts.release_metadata import CANONICAL_GIT_SOURCE


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENTS = PROJECT_ROOT / "examples" / "clients"
SKILLS_CLI = "skills@1.5.21"


def _server_from_json(name: str, root_key: str) -> tuple[dict, dict]:
    payload = json.loads((CLIENTS / name).read_text(encoding="utf-8"))
    server = payload[root_key]["gemini"]
    return payload, server


def _assert_primary_model_server(server: dict) -> None:
    if "type" in server:
        assert server["type"] == "stdio"
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", CANONICAL_GIT_SOURCE, "gemini-mcp-server"]
    assert server["env"]["GEMINI_TOOLS"] == "model"
    assert server["env"]["GEMINI_AUTO_REFRESH"] == "false"


def test_codex_configuration_parses_and_forwards_host_secrets() -> None:
    payload = tomllib.loads((CLIENTS / "codex.config.toml").read_text(encoding="utf-8"))
    server = payload["mcp_servers"]["gemini"]

    assert server["command"] == "uvx"
    assert server["args"] == ["--from", CANONICAL_GIT_SOURCE, "gemini-mcp-server"]
    assert server["env"] == {"GEMINI_TOOLS": "model", "GEMINI_AUTO_REFRESH": "false"}
    assert server["env_vars"] == ["GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"]
    assert server["startup_timeout_sec"] == 60
    assert server["tool_timeout_sec"] == 720


def test_claude_desktop_configuration_uses_explicit_local_placeholders() -> None:
    _, server = _server_from_json("claude-desktop.json", "mcpServers")

    _assert_primary_model_server(server)
    assert server["env"]["GEMINI_PSID"] == "REPLACE_WITH_SECURE_1PSID"
    assert server["env"]["GEMINI_PSIDTS"] == "REPLACE_OR_REMOVE_IF_UNAVAILABLE"


def test_claude_code_configuration_expands_environment_variables() -> None:
    _, server = _server_from_json("claude-code.mcp.json", "mcpServers")

    _assert_primary_model_server(server)
    assert server["env"]["GEMINI_PSID"] == "${GEMINI_PSID}"
    assert server["env"]["GEMINI_PSIDTS"] == "${GEMINI_PSIDTS:-}"
    assert server["timeout"] == 720000


def test_vscode_configuration_uses_password_inputs_and_servers_root() -> None:
    payload, server = _server_from_json("vscode.mcp.json", "servers")

    _assert_primary_model_server(server)
    assert {item["id"] for item in payload["inputs"]} == {"gemini-psid", "gemini-psidts"}
    assert all(item["type"] == "promptString" and item["password"] is True for item in payload["inputs"])
    assert server["env"]["GEMINI_PSID"] == "${input:gemini-psid}"
    assert server["env"]["GEMINI_PSIDTS"] == "${input:gemini-psidts}"


def test_public_docs_distinguish_expected_routing_from_observation() -> None:
    client_doc = (PROJECT_ROOT / "docs" / "client-examples.md").read_text(encoding="utf-8")
    changelog = (PROJECT_ROOT / "docs" / "changelog.md").read_text(encoding="utf-8")

    for field in ("requested_model", "request_model", "effective_backend", "observed_backend"):
        assert f"`{field}`" in client_doc
    assert "not evidence" in client_doc
    assert "no live Gemini account or backend behavior was observed" in changelog


def test_runtime_and_development_skills_have_distinct_roles_and_install_paths() -> None:
    runtime = (PROJECT_ROOT / ".agents" / "skills" / "gemini-web-mcp" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    development = (
        PROJECT_ROOT / ".agents" / "skills" / "gemini-web-mcp-development" / "SKILL.md"
    ).read_text(encoding="utf-8")
    client_doc = (PROJECT_ROOT / "docs" / "client-examples.md").read_text(encoding="utf-8")

    assert "Operate an installed Gemini Web MCP server safely" in runtime
    assert "do not use for repository implementation" in runtime
    assert "Develop, refactor, test, package, and release" in development
    assert "--skill gemini-web-mcp-development --agent codex --copy --yes" in development
    assert client_doc.count(SKILLS_CLI) == 2
    assert "--skill gemini-web-mcp" in client_doc
    assert "--skill gemini-web-mcp-development" in client_doc


def test_public_onboarding_docs_do_not_depend_on_the_broken_release_url() -> None:
    paths = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "README.zh-CN.md",
        PROJECT_ROOT / "docs" / "quickstart.md",
        PROJECT_ROOT / "docs" / "launch-kit.md",
        PROJECT_ROOT / "docs" / "client-examples.md",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "releases/download/v0.2.0" not in combined
    assert combined.count(CANONICAL_GIT_SOURCE) >= len(paths)
    assert "commit SHA" in combined
