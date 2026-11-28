"""Pinned agent-skills-setup migration followed by the installed stdio entrypoint."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tomllib

import pytest
from mcp import Client, StdioServerParameters, stdio_client

from src import __version__
from src.onboarding import domain_result_from_call

MIGRATION_SHA = "0d8449810fe0fd857598840258aecc73e7c604f6"


def test_pinned_skills_repo_migrates_and_starts_unified_entrypoint(tmp_path):
    root = os.environ.get("GEMINI_SKILLS_MIGRATION_SOURCE")
    if not root:
        pytest.skip("requires the pinned skills-repo v0.11.1 checkout (CI skills job)")
    source = Path(root).resolve()
    assert subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True, timeout=10,
    ).strip() == MIGRATION_SHA
    script = source / "skills/agent-skills-setup/scripts/migrate_context.py"
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    home.mkdir()
    cursor = workspace / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    synthetic_secret = "fixture-not-a-credential-1234"
    initial = {"mcpServers": {"gemini": {
        "command": "gemini-web-mcp", "args": [], "env": {"GEMINI_PSID": synthetic_secret},
    }}}
    original = json.dumps(initial)
    cursor.write_text(original, encoding="utf-8")
    target = workspace / ".codex" / "config.toml"
    env = {
        "PATH": str(sysconfig.get_path("scripts")) + os.pathsep + os.defpath,
        "HOME": str(home), "USERPROFILE": str(home),
        "GEMINI_AUTO_REFRESH": "false", "GEMINI_BROWSER_AUTO_LOAD": "false",
        "GEMINI_TASK_AUTO_APPROVE": "false", "GEMINI_TASK_WEB_APPROVALS": "false",
        "GEMINI_OPERATION_DB_PATH": str(tmp_path / "operations.sqlite"),
        "GEMINI_CLEANUP_DB_PATH": str(tmp_path / "cleanup.sqlite"),
    }
    def migrate(*flags):
        result = subprocess.run(
            [sys.executable, str(script), "migrate", "--source", "cursor", "--target", "codex",
             "--workspace", str(workspace), "--scope", "project", "--redact-secrets", *flags],
            env=env, cwd=workspace, check=True, capture_output=True, text=True, timeout=30,
        )
        assert synthetic_secret not in result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["version"] == "0.11.1"
        return report

    assert migrate("--dry-run")["dry_run"] is True
    assert not target.exists()
    assert migrate()["summary"]["migrated"] == 1
    config = target.read_text(encoding="utf-8")
    assert synthetic_secret not in config
    assert cursor.read_text(encoding="utf-8") == original
    assert migrate()["summary"]["noop"] >= 1
    migrated = tomllib.loads(config)["mcp_servers"]["gemini"]
    assert migrated["env_vars"] == ["GEMINI_PSID"]
    assert migrated["command"] == "gemini-web-mcp"
    params = StdioServerParameters(command=migrated["command"], args=migrated.get("args", []),
                                   env=env, cwd=workspace)

    async def run():
        async with Client(stdio_client(params), mode="auto", cache=None) as client:
            assert client.server_info.version == __version__
            tools = (await client.list_tools()).tools
            assert len(tools) == 8
            response = await client.call_tool("ask-gemini", {"prompt": " "})
            assert domain_result_from_call(response)["error"]["code"] == "INVALID_ARGUMENT"

    asyncio.run(run())
