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
}


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


async def _check_tool_surfaces() -> tuple[int, int]:
    os.environ["GEMINI_TOOLS"] = "model"
    from src.server import mcp as primary_mcp
    from src.skill_server import mcp as compact_mcp

    primary_tools = {tool.name for tool in await primary_mcp.list_tools()}
    compact_tools = {tool.name for tool in await compact_mcp.list_tools()}
    if not {"gemini_chat", "gemini_doctor", "gemini_get_tool_manifest"} <= primary_tools:
        raise RuntimeError(f"Primary installed surface is incomplete: {sorted(primary_tools)}")
    if not {"chat", "doctor", "account"} <= compact_tools:
        raise RuntimeError(f"Compact installed surface is incomplete: {sorted(compact_tools)}")
    return len(primary_tools), len(compact_tools)


def _start_console_entrypoints() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment["GEMINI_AUTO_REFRESH"] = "false"
    environment["GEMINI_TOOLS"] = "model"

    with tempfile.TemporaryDirectory(prefix="gemini-wheel-smoke-") as directory:
        smoke_cwd = Path(directory)
        for name in EXPECTED_ENTRY_POINTS:
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


def main() -> None:
    os.environ["GEMINI_TOOLS"] = "model"
    package_path = _assert_installed_import()
    prompt_payload = _check_package_data()
    _check_entry_point_metadata()
    primary_count, compact_count = asyncio.run(_check_tool_surfaces())
    _start_console_entrypoints()
    print(
        json.dumps(
            {
                "distribution": "gemini-mcp-server",
                "version": version("gemini-mcp-server"),
                "package_path": str(package_path),
                "default_prompts": len(prompt_payload["prompts"]),
                "primary_tools": primary_count,
                "compact_tools": compact_count,
                "entrypoints_started": sorted(EXPECTED_ENTRY_POINTS),
                "status": "ok",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
