"""Snapshot representative primary and compact tool surfaces without live calls."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PRIMARY_PROFILE_TOOLS = {
    "model": frozenset(
        {
            "gemini_chat",
            "gemini_chat_stream",
            "gemini_doctor",
            "gemini_get_cookie_from_browser",
            "gemini_get_cookie_status",
            "gemini_get_tool_manifest",
            "gemini_list_browser_cookie_profiles",
            "gemini_list_sessions",
            "gemini_reset",
            "gemini_reset_session",
            "gemini_send_message",
            "gemini_send_message_stream",
            "gemini_start_chat",
        }
    ),
    "history": frozenset(
        {
            "gemini_doctor",
            "gemini_get_cookie_from_browser",
            "gemini_get_cookie_status",
            "gemini_get_tool_manifest",
            "gemini_history",
            "gemini_list_browser_cookie_profiles",
            "gemini_reset",
        }
    ),
    "history-organize": frozenset(
        {
            "gemini_doctor",
            "gemini_get_cookie_from_browser",
            "gemini_get_cookie_status",
            "gemini_get_tool_manifest",
            "gemini_history",
            "gemini_list_browser_cookie_profiles",
            "gemini_move_chat_to_notebook",
            "gemini_notebooks",
            "gemini_reset",
        }
    ),
    "account-read": frozenset(
        {
            "gemini_account_inventory",
            "gemini_doctor",
            "gemini_get_cookie_from_browser",
            "gemini_get_cookie_status",
            "gemini_get_tool_manifest",
            "gemini_list_browser_cookie_profiles",
            "gemini_reset",
        }
    ),
    "scheduled-admin": frozenset(
        {
            "gemini_create_scheduled_action",
            "gemini_delete_scheduled_action",
            "gemini_doctor",
            "gemini_get_cookie_from_browser",
            "gemini_get_cookie_status",
            "gemini_get_scheduled_action",
            "gemini_get_tool_manifest",
            "gemini_list_browser_cookie_profiles",
            "gemini_list_scheduled_actions",
            "gemini_reset",
        }
    ),
    "all": frozenset(
        {
            "gemini_account_inventory",
            "gemini_analyze_url",
            "gemini_chat",
            "gemini_chat_stream",
            "gemini_cleanup_test_artifacts",
            "gemini_create_from_research_report",
            "gemini_create_scheduled_action",
            "gemini_deep_research",
            "gemini_delete_chat",
            "gemini_delete_scheduled_action",
            "gemini_doctor",
            "gemini_export_chat",
            "gemini_generate_media",
            "gemini_generate_music",
            "gemini_get_cookie_from_browser",
            "gemini_get_cookie_status",
            "gemini_get_scheduled_action",
            "gemini_get_tool_manifest",
            "gemini_get_tool_mode_status",
            "gemini_get_usage_limits",
            "gemini_get_web_capabilities",
            "gemini_history",
            "gemini_inspect_account",
            "gemini_list_browser_cookie_profiles",
            "gemini_list_chats",
            "gemini_list_library_capabilities",
            "gemini_list_models",
            "gemini_list_notebook_chats",
            "gemini_list_notebooks",
            "gemini_list_public_links",
            "gemini_list_research_report_actions",
            "gemini_list_scheduled_actions",
            "gemini_list_sessions",
            "gemini_manage_gems",
            "gemini_move_chat_to_notebook",
            "gemini_notebooks",
            "gemini_probe_web_features",
            "gemini_read_chat",
            "gemini_reset",
            "gemini_reset_session",
            "gemini_scan_chat_history_sources",
            "gemini_search_chats",
            "gemini_send_message",
            "gemini_send_message_stream",
            "gemini_start_chat",
            "gemini_upload_file",
        }
    ),
}

COMPACT_TOOLS = frozenset(
    {
        "account",
        "chat",
        "cleanup",
        "cookie",
        "create",
        "doctor",
        "edit",
        "history",
        "prompts",
        "scheduled",
        "session",
    }
)

ASSIST_TOOLS = frozenset({"gemini_ask"})


async def _list_module_tools(module_name: str) -> list[str]:
    module = importlib.import_module(module_name)
    return sorted(tool.name for tool in await module.mcp.list_tools())


def _probe(module_name: str) -> None:
    print(json.dumps(asyncio.run(_list_module_tools(module_name))))


def _safe_environment(profile: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment["GEMINI_AUTO_REFRESH"] = "false"
    environment["GEMINI_TOOLS"] = profile
    return environment


def _installed_surface(module_name: str, profile: str) -> frozenset[str]:
    with tempfile.TemporaryDirectory(prefix="gemini-profile-smoke-") as directory:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--probe", module_name],
            cwd=directory,
            env=_safe_environment(profile),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Could not list {module_name} tools for profile {profile!r}:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    try:
        return frozenset(json.loads(completed.stdout))
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"Invalid profile probe output for {module_name} / {profile}: {completed.stdout!r}"
        ) from exc


def _require_exact_surface(label: str, actual: frozenset[str], expected: frozenset[str]) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise RuntimeError(f"{label} tool contract drifted; missing={missing}, unexpected={unexpected}")
    print(f"{label}: {len(actual)} tools ({', '.join(sorted(actual))})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", metavar="MODULE", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        _probe(args.probe)
        return

    for profile, expected in PRIMARY_PROFILE_TOOLS.items():
        actual = _installed_surface("src.server", profile)
        _require_exact_surface(f"primary/{profile}", actual, expected)

    compact = _installed_surface("src.skill_server", "model")
    _require_exact_surface("compact", compact, COMPACT_TOOLS)

    assist = _installed_surface("src.surfaces.assist", "model")
    _require_exact_surface("assist", assist, ASSIST_TOOLS)
    print("Representative profile contracts: OK")


if __name__ == "__main__":
    main()
