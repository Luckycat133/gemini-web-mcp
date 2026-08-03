"""Run the stable, offline architecture and distribution contract checklist."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Keep this list deliberately narrower than the full suite. It is a fast,
# diagnostic gate for the contracts that have the largest downstream blast
# radius; CI still runs every test on each supported Python version.
CONTRACT_TESTS = (
    "tests/test_domain_results.py",
    "tests/test_mcp_sdk_v2.py",
    "tests/test_conversation_lifecycle.py",
    "tests/test_chat_service.py",
    "tests/test_artifacts.py",
    "tests/test_rpc_contracts.py",
    "tests/test_live_canary.py",
    "tests/test_package_integrity.py",
    "tests/test_version_consistency.py",
    "tests/test_evaluations.py",
    "tests/test_development_skill.py",
    "tests/test_skill_packaging.py",
    "tests/test_ci_contracts.py",
)


def main() -> None:
    command = [sys.executable, "-m", "pytest", "-q", *CONTRACT_TESTS]
    print("Contract checklist:", flush=True)
    for test_path in CONTRACT_TESTS:
        print(f"- {test_path}", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
