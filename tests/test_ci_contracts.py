"""Contracts for deterministic CI, installed-product smoke, and tag releases."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from scripts.run_contract_checklist import CONTRACT_TESTS
from scripts.smoke_profiles import COMPACT_TOOLS, PRIMARY_PROFILE_TOOLS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
SKILLS_REF_SHA = "38a2ff82958afee88dadf4831509e6f7e9d8ef4e"


def _pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_static_gates_are_declared_development_dependencies() -> None:
    development = _pyproject()["project"]["optional-dependencies"]["dev"]

    assert any(requirement.startswith("ruff>=") and "<1" in requirement for requirement in development)
    assert any(requirement.startswith("mypy>=") and "<3" in requirement for requirement in development)


def test_targeted_contract_checklist_covers_stable_architecture_boundaries() -> None:
    assert len(CONTRACT_TESTS) == len(set(CONTRACT_TESTS))
    assert {
            "tests/test_domain_results.py",
            "tests/test_mcp_sdk_v2.py",
        "tests/test_conversation_lifecycle.py",
        "tests/test_chat_service.py",
        "tests/test_artifacts.py",
        "tests/test_rpc_contracts.py",
        "tests/test_package_integrity.py",
        "tests/test_version_consistency.py",
        "tests/test_evaluations.py",
        "tests/test_development_skill.py",
        "tests/test_skill_packaging.py",
        "tests/test_ci_contracts.py",
    } == set(CONTRACT_TESTS)


def test_representative_profile_snapshots_are_explicit_and_exhaustive() -> None:
    assert set(PRIMARY_PROFILE_TOOLS) == {
        "model",
        "history",
        "history-organize",
        "account-read",
        "scheduled-admin",
        "all",
    }
    assert all(PRIMARY_PROFILE_TOOLS.values())
    assert len(PRIMARY_PROFILE_TOOLS["all"]) == 46
    assert COMPACT_TOOLS == {
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


def test_profile_snapshot_smoke_passes_from_the_installed_environment() -> None:
    environment = os.environ.copy()
    environment["GEMINI_AUTO_REFRESH"] = "false"
    completed = subprocess.run(
        [sys.executable, "scripts/smoke_profiles.py"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Representative profile contracts: OK" in completed.stdout


def test_ci_workflow_has_separate_diagnostic_offline_gates() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    for job in ("lint", "type", "test", "contracts", "protocol", "skills", "package"):
        assert re.search(rf"^  {job}:$", workflow, re.MULTILINE)
    for command in (
        "python -m ruff check src tests scripts",
        "python -m mypy src scripts",
        "python -m pytest -q",
        "python scripts/run_contract_checklist.py",
        "python scripts/smoke_profiles.py",
        "python scripts/smoke_mcp_protocol.py",
        "python scripts/package_release.py --outdir dist",
        "python scripts/check_version_consistency.py --artifacts-dir dist",
        "-m pip check",
    ):
        assert command in workflow
    assert workflow.count("cache: pip") == 7
    assert 'GEMINI_AUTO_REFRESH: "false"' in workflow


def test_ci_pins_reference_skill_validator_and_checks_both_mirrors() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert SKILLS_REF_SHA in workflow
    assert workflow.count("skills-ref validate") == 4
    assert "diff -ru .agents/skills/gemini-web-mcp-development .codex/skills/gemini-web-mcp-development" in workflow
    assert "diff -ru .agents/skills/gemini-web-mcp .codex/skills/gemini-web-mcp" in workflow


def test_tag_release_reverifies_assets_before_the_only_publish_command() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert '      - "v*.*.*"' in workflow
    assert SKILLS_REF_SHA in workflow
    assert "needs: verify-and-build" in workflow
    assert workflow.count("gh release create") == 1
    publish_index = workflow.index("gh release create")
    reverify_index = workflow.index("Re-verify downloaded assets before publishing")
    assert reverify_index < publish_index
    assert 'check_version_consistency.py --tag "$GITHUB_REF_NAME" --artifacts-dir dist' in workflow
    assert "--verify-tag --generate-notes" in workflow
