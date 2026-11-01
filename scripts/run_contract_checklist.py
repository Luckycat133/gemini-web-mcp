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
