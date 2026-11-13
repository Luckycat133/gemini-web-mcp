"""Contracts for deterministic CI, installed-product smoke, and tag releases."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from scripts.run_contract_checklist import CONTRACT_TESTS
from scripts.smoke_profiles import ACCOUNT_TOOLS, ASSIST_TOOLS, COMPACT_TOOLS, FUNCTION_TOOLS, PRIMARY_PROFILE_TOOLS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
LIVE_CANARY_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "live-canary.yml"
SKILLS_REF_SHA = "38a2ff82958afee88dadf4831509e6f7e9d8ef4e"
SETUP_UV_SHA = "68559e99cc807c1eb7e9238fd43a15bba460eec2"
SKILLS_CLI_VERSION = "1.5.21"


def _pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_static_gates_are_declared_development_dependencies() -> None:
    development = _pyproject()["project"]["optional-dependencies"]["dev"]

    assert any(requirement.startswith("ruff>=") and "<1" in requirement for requirement in development)
    assert any(requirement.startswith("mypy>=") and "<3" in requirement for requirement in development)
    assert not any(requirement.startswith("fastmcp") for requirement in development)
    assert not any(requirement.startswith("pydantic-settings") for requirement in development)


def test_targeted_contract_checklist_covers_stable_architecture_boundaries() -> None:
    assert len(CONTRACT_TESTS) == len(set(CONTRACT_TESTS))
    assert {
        "tests/test_domain_results.py",
        "tests/test_mcp_sdk_v2.py",
        "tests/test_conversation_lifecycle.py",
        "tests/test_chat_service.py",
        "tests/test_chat_timeouts.py",
        "tests/test_session_sdk_recovery.py",
        "tests/test_session_cleanup_guards.py",
        "tests/test_model_policy.py",
        "tests/test_model_policy_integration.py",
        "tests/test_chat_management_service.py",
        "tests/test_chat_metadata.py",
        "tests/test_chat_management_tools.py",
        "tests/test_history_compact.py",
        "tests/test_cleanup_store.py",
        "tests/test_cleanup_runtime.py",
        "tests/test_remote_chat_cleanup_manager.py",
        "tests/test_task_owned_cleanup.py",
        "tests/test_owned_cleanup.py",
        "tests/test_operations.py",
        "tests/test_operation_store.py",
        "tests/test_research_identity.py",
        "tests/test_research_legacy_identity.py",
        "tests/test_research_result_storage.py",
        "tests/test_saved_research_derivatives.py",
        "tests/test_research_worker_recovery.py",
        "tests/test_background_operations.py",
        "tests/test_media_jobs.py",
        "tests/test_task_jobs.py",
        "tests/test_task_inputs.py",
        "tests/test_task_confirmation.py",
        "tests/test_task_setup.py",
        "tests/test_task_files.py",
        "tests/test_managed_skills.py",
        "tests/test_managed_skill_transport.py",
        "tests/test_notebook_admin.py",
        "tests/test_notebooklm_service.py",
        "tests/test_web_tasks_service.py",
        "tests/test_web_context_transport.py",
        "tests/test_task_progress.py",
        "tests/test_task_schedules_service.py",
        "tests/test_task_schedule_transport.py",
        "tests/test_remy_schedule_contracts.py",
        "tests/test_task_schedule_guards.py",
        "tests/test_task_schedule_lifecycle.py",
        "tests/test_task_schedule_timestamps.py",
        "tests/test_task_schedule_bounds.py",
        "tests/test_task_browser_service.py",
        "tests/test_task_browser_sessions.py",
        "tests/test_task_browser_turn.py",
        "tests/test_task_browser_retries.py",
        "tests/test_task_account_lifecycle.py",
        "tests/test_task_delete_readback.py",
        "tests/test_task_browser_transport.py",
        "tests/test_task_browser_tools.py",
        "tests/test_preference_transport.py",
        "tests/test_preferences_service.py",
        "tests/test_preference_tools.py",
        "tests/test_browser_action_store.py",
        "tests/test_rfb.py",
        "tests/test_rfb_regressions.py",
        "tests/test_operation_tools.py",
        "tests/test_compact_history_contract.py",
        "tests/test_artifacts.py",
        "tests/test_manage_gem_verification_contract.py",
        "tests/test_gem_custom_catalog.py",
        "tests/test_gem_mutation_retry_scope.py",
        "tests/test_history_mutation_fencing.py",
        "tests/test_account_read_fencing.py",
        "tests/test_notebook_move_fencing.py",
        "tests/test_scheduled_mutation_evidence.py",
        "tests/test_rpc_contracts.py",
        "tests/test_candidate_attribution.py",
        "tests/test_upstream_compatibility.py",
        "tests/test_live_canary.py",
        "tests/test_package_integrity.py",
        "tests/test_function_surface.py",
        "tests/test_account_surface.py",
        "tests/test_protocol_smoke.py",
        "tests/test_onboarding.py",
        "tests/test_onboarding_distribution.py",
        "tests/test_version_consistency.py",
        "tests/test_evaluations.py",
        "tests/test_development_skill.py",
        "tests/test_skill_packaging.py",
        "tests/test_assist_skill.py",
        "tests/test_ci_contracts.py",
    } == set(CONTRACT_TESTS)


def test_representative_profile_snapshots_are_explicit_and_exhaustive() -> None:
    from src.surfaces import catalogs

    assert FUNCTION_TOOLS == catalogs.FUNCTION_TOOLS
    assert ACCOUNT_TOOLS == catalogs.ACCOUNT_TOOLS
    assert len(FUNCTION_TOOLS) == 17 and len(ACCOUNT_TOOLS) == 17
    assert not FUNCTION_TOOLS & ACCOUNT_TOOLS
    assert set(PRIMARY_PROFILE_TOOLS) == {
        "core",
        "media",
        "model",
        "history",
        "history-organize",
        "account-read",
        "scheduled-admin",
        "all",
    }
    assert all(PRIMARY_PROFILE_TOOLS.values())
    assert len(PRIMARY_PROFILE_TOOLS["all"]) == 48
    assert {"gemini_get_operation_status", "gemini_get_operation_result"} <= PRIMARY_PROFILE_TOOLS["all"]
    assert COMPACT_TOOLS == {
        "chat",
        "create",
        "edit",
        "session",
    }
    assert ASSIST_TOOLS == {
        "gemini_ask",
        "gemini_research",
        "gemini_search",
        "gemini_understand",
        "gemini_understand_image",
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

    assert 'branches: [main, "agent/**", "codex/**"]' in workflow
    assert "concurrency:" in workflow
    assert "group: ${{ github.workflow }}-${{ github.ref }}" in workflow
    assert "cancel-in-progress: true" in workflow
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
        'uvx --from "$GITHUB_WORKSPACE"/dist/*.whl gemini-mcp-onboarding',
    ):
        assert command in workflow
    assert workflow.count("cache: pip") == 7
    assert 'GEMINI_AUTO_REFRESH: "false"' in workflow
    assert 'GEMINI_BROWSER_AUTO_LOAD: "false"' in workflow


def test_ci_pins_reference_skill_validator_and_checks_single_public_sources() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert SKILLS_REF_SHA in workflow
    assert workflow.count("skills-ref validate") == 5
    assert ".codex/skills" not in workflow
    assert f"skills@{SKILLS_CLI_VERSION} add \"$GITHUB_WORKSPACE\" --skill gemini-web-mcp-development" in workflow
    assert (
        'diff -ru "$GITHUB_WORKSPACE/.agents/skills/gemini-web-mcp-development" '
        ".agents/skills/gemini-web-mcp-development"
    ) in workflow


def test_ci_and_release_pin_uv_and_smoke_the_one_command_onboarding() -> None:
    ci_workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    release_workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    for workflow in (ci_workflow, release_workflow):
        assert SETUP_UV_SHA in workflow
        assert workflow.count('uvx --from "$GITHUB_WORKSPACE"/dist/*.whl gemini-mcp-onboarding') == 1
        assert "working-directory: ${{ runner.temp }}" in workflow


def test_live_canary_is_opt_in_offline_separated_and_reports_drift_to_one_issue() -> None:
    workflow = LIVE_CANARY_WORKFLOW.read_text(encoding="utf-8")
    offline_workflows = CI_WORKFLOW.read_text(encoding="utf-8") + RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "environment: gemini-live-canary" in workflow
    assert "vars.GEMINI_LIVE_CANARY_ENABLED == 'true'" in workflow
    assert "vars.GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT == 'true'" in workflow
    assert "inputs.run_live == true" in workflow
    assert "--allow-live-account" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "actions/github-script@v9" in workflow
    assert 'const title = "[live-canary] Gemini Web compatibility drift"' in workflow
    assert "issues.createComment" in workflow
    assert "issues.create" in workflow
    assert "issues.update" in workflow
    assert "raw responses, account content, credentials, and session identifiers are omitted" in workflow
    assert "JSON.stringify(report)" not in workflow
    assert "steps.canary.outcome == 'failure'" in workflow
    assert 'REPORT_PATH: ${{ github.workspace }}/gemini-web-live-canary.json' in workflow
    assert "${{ runner." not in workflow
    assert "run_live_canary.py" not in offline_workflows
    assert "GEMINI_PSID" not in offline_workflows


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