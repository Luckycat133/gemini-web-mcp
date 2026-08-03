"""Build deterministic MCP SDK v2 tool-list and schema fingerprints."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _snapshot_module(module_name: str) -> list[dict[str, Any]]:
    module = importlib.import_module(module_name)
    snapshot = []
    for tool in sorted(await module.mcp.list_tools(), key=lambda item: item.name):
        wire = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        input_schema = wire["inputSchema"]
        output_schema = wire.get("outputSchema")
        snapshot.append(
            {
                "name": tool.name,
                "input_schema_sha256": _digest(input_schema),
                "output_schema_sha256": _digest(output_schema) if output_schema is not None else None,
                "annotations_sha256": _digest(wire.get("annotations")),
            }
        )
    return snapshot


def _safe_environment(profile: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment["GEMINI_AUTO_REFRESH"] = "false"
    environment["GEMINI_TOOLS"] = profile
    return environment


def _probe(module_name: str, profile: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--module", module_name],
        cwd=PROJECT_ROOT,
        env=_safe_environment(profile),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise TypeError(f"Invalid MCP contract snapshot returned by {module_name}")
    return cast(list[dict[str, Any]], payload)


def build_snapshot() -> dict[str, object]:
    """Return the checked contract for representative primary and compact surfaces."""

    return {
        "format": 1,
        "sdk_major": 2,
        "surfaces": {
            "primary:model": _probe("src.server", "model"),
            "compact": _probe("src.skill_server", "model"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module")
    args = parser.parse_args()
    payload = asyncio.run(_snapshot_module(args.module)) if args.module else build_snapshot()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
