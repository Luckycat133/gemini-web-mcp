"""Smoke an installed wheel from outside the source tree without live Gemini calls."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import entry_points, version
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ENTRY_POINTS = {
    "gemini-mcp-server": "src.server:main",
    "gemini-mcp-skill-server": "src.skill_server:main",
    "gemini-mcp-onboarding": "src.onboarding:main",
    "gemini-mcp-assist": "src.surfaces.assist:main",
    "gemini-mcp-create": "src.surfaces.create:main",
    "gemini-mcp-account": "src.surfaces.account:main",
}
SERVER_ENTRY_POINTS = ("gemini-mcp-server", "gemini-mcp-skill-server", "gemini-mcp-assist", "gemini-mcp-create", "gemini-mcp-account")


def _assert_installed_import() -> Path:
    import src

    package_path = Path(src.__file__).resolve()
    if package_path == PROJECT_ROOT or PROJECT_ROOT in package_path.parents:
        raise RuntimeError(f"Smoke imported the source checkout instead of the installed wheel: {package_path}")
    return package_path


def _check_package_data() -> dict[str, Any]:
    resource = files("src").joinpath("data", "prompts_default.json")
    if not resource.is_file():
        raise RuntimeError("Installed wheel is missing src/data/prompts_default.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Installed default prompt catalog must be a JSON object")
    prompts = payload.get("prompts")
    if not isinstance(prompts, dict) or not prompts:
        raise RuntimeError("Installed default prompt catalog is empty or malformed")
    return cast(dict[str, Any], payload)


def _check_entry_point_metadata() -> None:
    installed = {item.name: item for item in entry_points(group="console_scripts")}
    for name, expected_value in EXPECTED_ENTRY_POINTS.items():
        entry_point = installed.get(name)
        if entry_point is None:
            raise RuntimeError(f"Installed wheel is missing console entrypoint {name!r}")
        if entry_point.value != expected_value:
            raise RuntimeError(
                f"Console entrypoint {name!r} targets {entry_point.value!r}, expected {expected_value!r}"
            )
        if not callable(entry_point.load()):
            raise TypeError(f"Console entrypoint {name!r} does not resolve to a callable")


async def _check_tool_surfaces() -> tuple[int, int, int, int, int]:
    os.environ["GEMINI_TOOLS"] = "model"
    from src.server import mcp as primary_mcp
    from src.skill_server import mcp as compact_mcp
    from src.surfaces.assist import mcp as assist_mcp
    from src.surfaces.create import mcp as create_mcp
    from src.surfaces.account import mcp as account_mcp

    primary_tools = {tool.name for tool in await primary_mcp.list_tools()}
    compact_tools = {tool.name for tool in await compact_mcp.list_tools()}
    assist_tools = {tool.name for tool in await assist_mcp.list_tools()}
    if not {"gemini_chat", "gemini_doctor", "gemini_get_tool_manifest"} <= primary_tools:
        raise RuntimeError(f"Primary installed surface is incomplete: {sorted(primary_tools)}")
    if not {"chat", "doctor", "account"} <= compact_tools:
        raise RuntimeError(f"Compact installed surface is incomplete: {sorted(compact_tools)}")
    if not {"gemini_ask", "gemini_search", "gemini_understand", "gemini_understand_image"} <= assist_tools:
        raise RuntimeError(f"Assist installed surface is incomplete: {sorted(assist_tools)}")
    create_tools = {tool.name for tool in await create_mcp.list_tools()}
    account_tools = {tool.name for tool in await account_mcp.list_tools()}
    if create_tools != {"gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_music",
                        "gemini_get_operation_status", "gemini_get_operation_result", "gemini_cancel_operation"}:
        raise RuntimeError(f"Create installed surface drifted: {sorted(create_tools)}")
    if account_tools != {"gemini_history", "gemini_notebooks", "gemini_scheduled", "gemini_gems", "gemini_prompts", "gemini_account", "gemini_cleanup"}:
        raise RuntimeError(f"Account installed surface drifted: {sorted(account_tools)}")
    return len(primary_tools), len(compact_tools), len(assist_tools), len(create_tools), len(account_tools)


def _start_console_entrypoints() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment["GEMINI_AUTO_REFRESH"] = "false"
    environment["GEMINI_TOOLS"] = "model"

    with tempfile.TemporaryDirectory(prefix="gemini-wheel-smoke-") as directory:
        smoke_cwd = Path(directory)
        environment["GEMINI_STATE_DB_PATH"] = str(smoke_cwd / "state.sqlite3")
        for name in SERVER_ENTRY_POINTS:
            executable = shutil.which(name, path=str(Path(sys.executable).parent))
            if executable is None:
                raise RuntimeError(f"Cannot locate installed console entrypoint {name!r}")
            try:
                completed = subprocess.run(
                    [executable],
                    cwd=smoke_cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Console entrypoint {name!r} did not stop after stdio EOF") from exc
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Console entrypoint {name!r} exited {completed.returncode}:\n"
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
                )

        initialized_prompts = smoke_cwd / ".gemini" / "prompts.json"
        if not initialized_prompts.is_file():
            raise RuntimeError("Compact console did not initialize prompts from installed package data")


def _call_offline_text_tool() -> dict[str, Any]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment["GEMINI_AUTO_REFRESH"] = "false"

    executable = shutil.which("gemini-mcp-onboarding", path=str(Path(sys.executable).parent))
    if executable is None:
        raise RuntimeError("Cannot locate installed gemini-mcp-onboarding entrypoint")
    with tempfile.TemporaryDirectory(prefix="gemini-onboarding-smoke-") as directory:
        environment["GEMINI_STATE_DB_PATH"] = str(Path(directory) / "state.sqlite3")
        completed = subprocess.run(
            [executable],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            "Installed onboarding preflight failed:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Onboarding preflight returned invalid JSON: {completed.stdout!r}") from exc
    if payload.get("status") != "ok" or payload.get("text_tool") != "gemini_get_tool_manifest":
        raise RuntimeError(f"Onboarding preflight did not call the expected text tool: {payload!r}")
    if payload.get("credentials_accessed") is not False or payload.get("mode") != "offline":
        raise RuntimeError(f"Onboarding preflight did not preserve its offline boundary: {payload!r}")
    return cast(dict[str, Any], payload)


def main() -> None:
    os.environ["GEMINI_TOOLS"] = "model"
    package_path = _assert_installed_import()
    prompt_payload = _check_package_data()
    _check_entry_point_metadata()
    primary_count, compact_count, assist_count, create_count, account_count = asyncio.run(_check_tool_surfaces())
    _start_console_entrypoints()
    onboarding = _call_offline_text_tool()
    print(
        json.dumps(
            {
                "distribution": "gemini-mcp-server",
                "version": version("gemini-mcp-server"),
                "package_path": str(package_path),
                "default_prompts": len(prompt_payload["prompts"]),
                "primary_tools": primary_count,
                "compact_tools": compact_count,
                "assist_tools": assist_count,
                "create_tools": create_count,
                "account_tools": account_count,
                "entrypoints_started": sorted(EXPECTED_ENTRY_POINTS),
                "onboarding_text_tool": onboarding["text_tool"],
                "onboarding_mode": onboarding["mode"],
                "status": "ok",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
