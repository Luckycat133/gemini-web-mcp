#!/usr/bin/env python3
"""Offline plan/evidence integrity checks; never call Gemini or mark tests passed."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"planned", "active", "implemented", "accepted", "blocked"}
RESULTS = {"passed", "failed", "skipped", "blocked"}
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")


class PlanError(ValueError):
    """A plan or receipt is invalid; messages exclude private report contents."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PlanError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError("Cannot read a required JSON object.") from exc
    require(isinstance(value, dict), "Expected a JSON object.")
    return value


def items_by_id(items: Any, label: str) -> dict[str, dict[str, Any]]:
    require(isinstance(items, list) and bool(items), f"{label}: expected nonempty list.")
    result = {}
    for item in items:
        require(isinstance(item, dict), f"{label}: invalid entry.")
        key = item.get("id")
        require(isinstance(key, str) and bool(key), f"{label}: missing ID.")
        require(key not in result, f"{label}: duplicate ID {key}.")
        result[key] = item
    return result


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    require(isinstance(value, list), f"{label}: expected list.")
    require(allow_empty or bool(value), f"{label}: empty list.")
    require(all(nonempty(v) for v in value), f"{label}: invalid string.")
    require(len(set(value)) == len(value), f"{label}: duplicate value.")
    return value


def inside(root: Path, relative: Any) -> Path:
    require(isinstance(relative, str) and bool(relative), "Missing relative file path.")
    require("\\" not in relative and not urlparse(relative).scheme,
            "Expected a relative local file, not a URL or platform path.")
    part = Path(relative)
    require(not part.is_absolute(), "Absolute file paths are not accepted.")
    resolved = (root / part).resolve()
    require(resolved.is_relative_to(root.resolve()), "File path escapes the permitted root.")
    return resolved


def timestamp(value: Any) -> datetime:
    require(isinstance(value, str), "Timestamp must be a string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanError("Invalid timestamp.") from exc
    require(parsed.tzinfo is not None, "Timestamp must include a timezone.")
    return parsed


def validate_plan(root: Path) -> dict[str, Any]:
    plan = load(root / "assets/plan.json")
    require(plan.get("schema_version") == 1, "Unsupported plan schema version.")
    require(bool(SHA.fullmatch(str(plan.get("baseline_commit", "")))), "Invalid baseline SHA.")
    try:
        date.fromisoformat(plan["plan_revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError("Invalid plan revision date.") from exc
    gates = set(string_list(plan.get("gates"), "gates"))
    require(gates == {"docs", "offline", "protocol", "live", "agent", "package"}, "Unexpected gate registry.")
    packages = items_by_id(plan.get("packages"), "packages")
    cases = items_by_id(plan.get("cases"), "cases")
    capabilities = items_by_id(plan.get("capabilities"), "capabilities")
    sources = items_by_id(load(root / "assets/sources.json").get("sources"), "sources")
    for item in sources.values():
        require(urlparse(item.get("url", "")).scheme == "https", "Source requires HTTPS URL.")
        require(nonempty(item.get("title")) and nonempty(item.get("use")), "Source lacks rationale/title.")
        try:
            date.fromisoformat(item.get("checked_at", ""))
        except (TypeError, ValueError) as exc:
            raise PlanError("Source has invalid check date.") from exc
    for cid, case in cases.items():
        require(set(string_list(case.get("gates"), cid)) <= gates, f"{cid}: unknown gate.")
    assigned = []
    for pid, package in packages.items():
        deps = string_list(package.get("depends_on"), pid, allow_empty=True)
        require(set(deps) <= packages.keys() and pid not in deps, f"{pid}: invalid dependency.")
        mapped = string_list(package.get("cases"), pid)
        require(set(mapped) <= cases.keys(), f"{pid}: unknown case.")
        assigned.extend(mapped)
        require(set(string_list(package.get("sources"), pid)) <= sources.keys(), f"{pid}: unknown source.")
        string_list(package.get("base_paths"), pid)
        ref = inside(root, package.get("reference"))
        require(ref.is_file(), f"{pid}: missing implementation reference.")
        require(bool(re.search(r"^#{1,6}\s+" + re.escape(pid) + r"\b", ref.read_text(), re.M)),
                f"{pid}: reference lacks package section.")
    require(set(assigned) == cases.keys() and len(assigned) == len(cases), "Cases need exactly one package owner.")
    for cap in capabilities.values():
        require(cap.get("owner") in packages, "Capability has unknown owner.")
        require(set(string_list(cap.get("cases"), cap["id"])) <= cases.keys(), "Capability has unknown case.")
    visited: set[str] = set()
    active: set[str] = set()

    def visit(pid: str) -> None:
        require(pid not in active, "Dependency graph has a cycle.")
        if pid in visited:
            return
        active.add(pid)
        for dep in packages[pid]["depends_on"]:
            visit(dep)
        active.remove(pid)
        visited.add(pid)

    for pid in packages:
        visit(pid)
    acceptance = (root / "references/acceptance.md").read_text(encoding="utf-8")
    for cid in cases:
        require(bool(re.search(r"^##\s+" + re.escape(cid) + r"\b", acceptance, re.M)), f"{cid}: missing acceptance procedure.")
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    require(len(skill.splitlines()) < 500, "SKILL.md must use progressive disclosure.")
    require(f"name: {root.name}" in skill, "Skill name differs from folder.")
    require(f'version: "{plan.get("package_version")}"' in skill, "Skill/package version mismatch.")
    for file in root.rglob("*.md"):
        text = file.read_text(encoding="utf-8")
        require(not re.search(r"^(<{7}|={7}|>{7})( |$)", text, re.M), "Unresolved merge marker.")
        for target in re.findall(r"\]\(([^\s)]+)\)", text):
            if urlparse(target).scheme or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            resolved = inside(root, str(file.parent.relative_to(root) / target))
            require(resolved.is_file(), f"Broken local link in {file.name}.")
    return plan


def initialize(plan: dict[str, Any], path: Path) -> None:
    value = {"schema_version": 1, "plan_revision": plan["plan_revision"], "packages": {
        p["id"]: {"status": "planned", "revision": None, "implementation": "", "blocker": None, "evidence": []}
        for p in plan["packages"]}}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise PlanError("Progress already exists; refusing to overwrite.") from exc


def verify_report(record: dict[str, Any], root: Path) -> None:
    path = inside(root, record["report"])
    require(path.is_file(), "Missing evidence report.")
    require(hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"], "Evidence report hash mismatch.")
    report = load(path)
    for key in ("case", "gate", "result", "revision", "at"):
        require(report.get(key) == record[key], f"Evidence report {key} mismatch.")
    string_list(report.get("scope"), "report.scope")
    checks = report.get("checks")
    require(isinstance(checks, list) and bool(checks), "Report needs independent checks.")
    for check in checks:
        require(isinstance(check, dict) and nonempty(check.get("name")) and check.get("result") in RESULTS,
                "Malformed independent check.")
        if record["result"] == "passed":
            require(check["result"] == "passed", "A passed report contains an unpassed check.")


def validate_progress(plan: dict[str, Any], progress: dict[str, Any], *,
                      evidence_root: Path | None = None, complete: bool = False) -> dict[str, Any]:
    require(progress.get("schema_version") == 1, "Unsupported progress schema.")
    require(progress.get("plan_revision") == plan["plan_revision"], "Progress uses another plan revision.")
    packages = items_by_id(plan["packages"], "packages")
    cases = items_by_id(plan["cases"], "cases")
    states = progress.get("packages")
    require(isinstance(states, dict) and states.keys() == packages.keys(), "Progress must include exactly all packages.")
    require(not complete or evidence_root is not None, "--complete requires --evidence-root.")
    for pid, spec in packages.items():
        state = states[pid]
        require(isinstance(state, dict) and state.get("status") in STATUSES, f"{pid}: invalid status.")
        status = state["status"]
        revision = state.get("revision")
        if revision is not None:
            require(isinstance(revision, str) and bool(SHA.fullmatch(revision)), f"{pid}: invalid implementation SHA.")
        if status in {"implemented", "accepted"}:
            require(revision is not None and nonempty(state.get("implementation")), f"{pid}: missing implementation evidence.")
            allowed = {"accepted"} if status == "accepted" else {"implemented", "accepted"}
            require(all(isinstance(states[d], dict) and states[d].get("status") in allowed for d in spec["depends_on"]),
                    f"{pid}: dependencies are not ready.")
        if status == "blocked":
            require(nonempty(state.get("blocker")), f"{pid}: blocked needs a reason.")
        evidence = state.get("evidence")
        require(isinstance(evidence, list), f"{pid}: evidence must be a list.")
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        times: set[tuple[str, str, str, datetime]] = set()
        for record in evidence:
            require(isinstance(record, dict), f"{pid}: malformed evidence.")
            cid, gate = record.get("case"), record.get("gate")
            require(isinstance(cid, str) and cid in spec["cases"], f"{pid}: evidence case not owned.")
            require(gate in cases[cid]["gates"], f"{pid}: unknown case gate.")
            require(record.get("result") in RESULTS, f"{pid}: invalid result.")
            require(bool(SHA.fullmatch(str(record.get("revision", "")))), f"{pid}: invalid evidence SHA.")
            require(bool(DIGEST.fullmatch(str(record.get("sha256", "")))), f"{pid}: invalid report hash.")
            observed = timestamp(record.get("at"))
            key = (cid, gate)
            stamp = (cid, gate, record["revision"], observed)
            require(stamp not in times, f"{pid}: ambiguous evidence timestamp.")
            times.add(stamp)
            inside(evidence_root or Path.cwd(), record.get("report"))
            if evidence_root is not None:
                verify_report(record, evidence_root)
            if record["revision"] == revision and (key not in latest or observed > timestamp(latest[key]["at"])):
                latest[key] = record
        required = {(cid, gate) for cid in spec["cases"] for gate in cases[cid]["gates"]
                    if status == "accepted" or (status == "implemented" and gate in {"docs", "offline"})}
        require(all(latest.get(key, {}).get("result") == "passed" for key in required),
                f"{pid}: missing current-revision passed case/gate evidence.")
        require(not complete or status == "accepted", f"{pid}: not accepted; plan is incomplete.")
    return {"valid": True, "complete": complete, "reports_verified": evidence_root is not None,
            "states": {name: sum(s["status"] == name for s in states.values()) for name in sorted(STATUSES)}}


def next_packages(plan: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    validate_progress(plan, progress)
    states = progress["packages"]
    ready = [p["id"] for p in plan["packages"] if states[p["id"]]["status"] in {"planned", "active"}
             and all(states[d]["status"] in {"implemented", "accepted"} for d in p["depends_on"])]
    return {"ready_for_implementation": ready,
            "awaiting_acceptance": [p for p, s in states.items() if s["status"] == "implemented"],
            "blocked": [p for p, s in states.items() if s["status"] == "blocked"],
            "note": "Code readiness is not live acceptance. Evidence files were not read by next."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    for command in ("init", "next", "check-progress"):
        child = commands.add_parser(command)
        child.add_argument("--progress", type=Path, required=True)
        if command == "check-progress":
            child.add_argument("--evidence-root", type=Path)
            child.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    try:
        plan = validate_plan(args.root)
        if args.command == "check":
            result = {"valid": True, "plan_revision": plan["plan_revision"],
                      **{key: len(plan[key]) for key in ("packages", "cases", "capabilities")}}
        elif args.command == "init":
            initialize(plan, args.progress)
            result = {"created": True, "packages": len(plan["packages"])}
        elif args.command == "next":
            result = next_packages(plan, load(args.progress))
        else:
            result = validate_progress(plan, load(args.progress), evidence_root=args.evidence_root, complete=args.complete)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (PlanError, OSError, TypeError, KeyError) as exc:
        message = str(exc) if isinstance(exc, PlanError) else "Input or filesystem validation failed."
        print(json.dumps({"valid": False, "error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
