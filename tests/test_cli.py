"""Tests for the zero-context-tax gemini-web-cli companion CLI."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock

import pytest

from src import cli


def test_load_mcp_env_defaults_respects_no_config_and_existing_env(monkeypatch, tmp_path):
    config = tmp_path / "mcp_config.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "gemini-web-mcp": {
                        "env": {
                            "GEMINI_COOKIE_PATH": "/tmp/from-config",
                            "GEMINI_BROWSER_COOKIE_PROFILE": "Default",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("GEMINI_COOKIE_PATH", raising=False)
    monkeypatch.setenv("GEMINI_BROWSER_COOKIE_PROFILE", "Work")
    monkeypatch.setenv("GEMINI_WEB_CLI_NO_CONFIG", "1")
    cli.load_mcp_env_defaults(config)
    assert "GEMINI_COOKIE_PATH" not in os.environ

    monkeypatch.delenv("GEMINI_WEB_CLI_NO_CONFIG")
    cli.load_mcp_env_defaults(config)
    assert os.environ["GEMINI_COOKIE_PATH"] == "/tmp/from-config"
    assert os.environ["GEMINI_BROWSER_COOKIE_PROFILE"] == "Work"


def test_cli_dispatch_spec_routes_all_resource_families():
    parser = cli._build_parser()

    assert cli._dispatch_spec(parser.parse_args(["models"])) == (
        "gemini_account_inventory",
        {"surface": "models", "locale": "zh-CN", "response_format": "json"},
    )
    assert cli._dispatch_spec(parser.parse_args(["notebooks", "list", "--source", "gemini"]))[0] == "gemini_notebooks"
    assert cli._dispatch_spec(parser.parse_args(["gems", "create", "--name", "N", "--instructions", "I"])) == (
        "gemini_manage_gems",
        {"action": "create", "gem_id": None, "name": "N", "description": None, "instructions": "I"},
    )
    assert cli._dispatch_spec(parser.parse_args(["skills", "enable", "--skill-id", "sk1", "--enabled", "false"])) == (
        "gemini_skills",
        {
            "action": "enable",
            "skill_id": "sk1",
            "include_instructions": False,
            "limit": 20,
            "offset": 0,
            "max_chars": 4000,
            "title": None,
            "description": None,
            "instructions": None,
            "enabled": False,
        },
    )
    assert cli._dispatch_spec(parser.parse_args(["cleanup", "retry", "--chat-id", "c_1"])) == (
        "gemini_cleanup",
        {"action": "retry", "chat_id": "c_1", "limit": 50, "offset": 0, "include_completed": False},
    )


def test_cli_main_doctor_and_failure_exit_codes(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_WEB_CLI_NO_CONFIG", "1")
    monkeypatch.setattr(
        cli,
        "doctor_payload",
        lambda **_k: {"overall_status": "ok", "checks": [], "browser_profiles": [], "recommendations": []},
    )
    with pytest.raises(SystemExit) as exc_ok:
        cli.main(["--compact", "doctor"])
    assert exc_ok.value.code == 0
    out_ok = json.loads(capsys.readouterr().out)
    assert out_ok["ok"] is True and out_ok["command"] == "doctor"

    monkeypatch.setattr(
        cli,
        "_call_account_tool",
        AsyncMock(return_value={"ok": False, "command": "gems list", "tool": "gemini_manage_gems", "data": None, "text": None, "error": {"code": "AUTH_REQUIRED"}}),
    )
    with pytest.raises(SystemExit) as exc_fail:
        cli.main(["gems", "list"])
    assert exc_fail.value.code == 1
    out_fail = json.loads(capsys.readouterr().out)
    assert out_fail["ok"] is False and out_fail["error"]["code"] == "AUTH_REQUIRED"
