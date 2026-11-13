#!/usr/bin/env python3
"""Synthetic receipts test the checker, not Gemini functionality or acceptance."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import plan


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / plan.ROOT.name
        shutil.copytree(plan.ROOT, self.root, ignore=shutil.ignore_patterns("__pycache__"))
        self.spec = plan.validate_plan(self.root)
        self.progress_path = self.directory / "private/progress.json"
        plan.initialize(self.spec, self.progress_path)
        self.progress = plan.load(self.progress_path)
        self.reports = self.directory / "private"

    def write_spec(self) -> None:
        (self.root / "assets/plan.json").write_text(json.dumps(self.spec), encoding="utf-8")

    def receipt(self, pid: str, cid: str, gate: str, *, result: str = "passed",
                revision: str = "a" * 40, second: int = 0) -> dict:
        observed = (datetime(2026, 11, 7, tzinfo=timezone.utc) + timedelta(seconds=second)).isoformat()
        report = {"case": cid, "gate": gate, "result": result, "revision": revision,
                  "at": observed, "scope": ["synthetic checker fixture only"],
                  "checks": [{"name": "synthetic assertion", "result": result}]}
        relative = f"reports/{pid}-{cid}-{gate}-{second}-{revision[:2]}.json"
        path = self.reports / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report), encoding="utf-8")
        return {key: report[key] for key in ("case", "gate", "result", "revision", "at")} | {
            "report": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def accept_all(self) -> None:
        cases = {c["id"]: c for c in self.spec["cases"]}
        for package in self.spec["packages"]:
            pid = package["id"]
            self.progress["packages"][pid].update(
                status="accepted", revision="a" * 40, implementation="Synthetic checker fixture, not production.",
                evidence=[self.receipt(pid, cid, gate) for cid in package["cases"] for gate in cases[cid]["gates"]])

    def test_plan_has_valid_links_and_graph(self) -> None:
        self.assertTrue(plan.validate_plan(self.root)["packages"])

    def test_cycle_is_rejected(self) -> None:
        self.spec["packages"][0]["depends_on"] = ["P01"]
        self.write_spec()
        with self.assertRaises(plan.PlanError):
            plan.validate_plan(self.root)

    def test_duplicate_package_rejected(self) -> None:
        self.spec["packages"].append(deepcopy(self.spec["packages"][0]))
        self.write_spec()
        with self.assertRaises(plan.PlanError):
            plan.validate_plan(self.root)

    def test_unknown_source_rejected(self) -> None:
        self.spec["packages"][0]["sources"].append("MISSING")
        self.write_spec()
        with self.assertRaises(plan.PlanError):
            plan.validate_plan(self.root)

    def test_unowned_case_rejected(self) -> None:
        self.spec["cases"].append({"id": "A999", "gates": ["offline"]})
        self.write_spec()
        with self.assertRaises(plan.PlanError):
            plan.validate_plan(self.root)

    def test_broken_reference_rejected(self) -> None:
        path = self.root / "SKILL.md"
        path.write_text(path.read_text() + "\n[broken](references/missing.md)\n")
        with self.assertRaises(plan.PlanError):
            plan.validate_plan(self.root)

    def test_initial_progress_valid_but_incomplete(self) -> None:
        result = plan.validate_progress(self.spec, self.progress)
        self.assertFalse(result["complete"])
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress, evidence_root=self.reports, complete=True)

    def test_init_refuses_overwrite(self) -> None:
        original = self.progress_path.read_bytes()
        with self.assertRaises(plan.PlanError):
            plan.initialize(self.spec, self.progress_path)
        self.assertEqual(original, self.progress_path.read_bytes())

    def test_next_starts_at_baseline(self) -> None:
        self.assertEqual(plan.next_packages(self.spec, self.progress)["ready_for_implementation"], ["P00"])

    def test_implemented_dependency_unblocks_code_not_acceptance(self) -> None:
        self.progress["packages"]["P00"].update(
            status="implemented", revision="a" * 40, implementation="Fixture baseline.",
            evidence=[self.receipt("P00", "A01", "docs")])
        result = plan.next_packages(self.spec, self.progress)
        self.assertEqual(result["ready_for_implementation"], ["P01"])
        self.assertEqual(result["awaiting_acceptance"], ["P00"])

    def test_complete_synthetic_receipts_validate(self) -> None:
        self.accept_all()
        self.assertTrue(plan.validate_progress(self.spec, self.progress, evidence_root=self.reports, complete=True)["complete"])

    def test_complete_requires_report_root(self) -> None:
        self.accept_all()
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress, complete=True)

    def test_missing_package_cannot_reduce_scope(self) -> None:
        del self.progress["packages"]["G04"]
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)

    def test_wrong_plan_revision_rejected(self) -> None:
        self.progress["plan_revision"] = "2020-01-01"
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)

    def test_accepted_dependency_not_ready(self) -> None:
        self.accept_all()
        self.progress["packages"]["P00"]["status"] = "implemented"
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)

    def test_missing_gate_is_not_accepted(self) -> None:
        self.accept_all()
        self.progress["packages"]["G04"]["evidence"].pop()
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)

    def test_skipped_failed_and_blocked_do_not_pass(self) -> None:
        for result in ("skipped", "failed", "blocked"):
            with self.subTest(result=result):
                self.accept_all()
                self.progress["packages"]["P00"]["evidence"][0]["result"] = result
                with self.assertRaises(plan.PlanError):
                    plan.validate_progress(self.spec, self.progress)

    def test_wrong_implementation_revision_cannot_reuse_receipt(self) -> None:
        self.accept_all()
        self.progress["packages"]["P00"]["revision"] = "b" * 40
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)

    def test_later_failure_overrides_old_success(self) -> None:
        self.accept_all()
        self.progress["packages"]["P00"]["evidence"].append(self.receipt("P00", "A01", "docs", result="failed", second=1))
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)

    def test_retry_keeps_history_and_latest_pass(self) -> None:
        self.accept_all()
        records = self.progress["packages"]["P00"]["evidence"]
        records.append(self.receipt("P00", "A01", "docs", result="failed", second=1))
        records.append(self.receipt("P00", "A01", "docs", second=2))
        plan.validate_progress(self.spec, self.progress, evidence_root=self.reports, complete=True)

    def test_duplicate_timestamp_rejected(self) -> None:
        self.accept_all()
        records = self.progress["packages"]["P00"]["evidence"]
        records.append(deepcopy(records[0]))
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)

    def test_corrupted_report_rejected(self) -> None:
        self.accept_all()
        record = self.progress["packages"]["P00"]["evidence"][0]
        (self.reports / record["report"]).write_text("{}")
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress, evidence_root=self.reports)

    def test_report_scope_and_checks_required(self) -> None:
        self.accept_all()
        record = self.progress["packages"]["P00"]["evidence"][0]
        path = self.reports / record["report"]
        report = plan.load(path)
        report["checks"] = []
        path.write_text(json.dumps(report))
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress, evidence_root=self.reports)

    def test_passed_report_cannot_hide_failed_check(self) -> None:
        self.accept_all()
        record = self.progress["packages"]["P00"]["evidence"][0]
        path = self.reports / record["report"]
        report = plan.load(path)
        report["checks"][0]["result"] = "failed"
        path.write_text(json.dumps(report))
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress, evidence_root=self.reports)

    def test_path_escape_rejected(self) -> None:
        for path in ("../../secret.json", "/etc/passwd", "https://example.invalid/a", "C:\\private\\a"):
            with self.subTest(path=path), self.assertRaises(plan.PlanError):
                plan.inside(self.reports, path)

    def test_symlink_escape_rejected(self) -> None:
        link = self.reports / "outside"
        try:
            link.symlink_to(self.directory)
        except OSError:
            self.skipTest("Symlinks unavailable on this platform.")
        with self.assertRaises(plan.PlanError):
            plan.inside(self.reports, "outside/receipt.json")

    def test_timestamp_requires_timezone(self) -> None:
        with self.assertRaises(plan.PlanError):
            plan.timestamp("2026-11-07T12:00:00")

    def test_blocked_requires_reason(self) -> None:
        self.progress["packages"]["P00"]["status"] = "blocked"
        with self.assertRaises(plan.PlanError):
            plan.validate_progress(self.spec, self.progress)


if __name__ == "__main__":
    unittest.main()
