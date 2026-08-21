from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "0.2.0"
NEW = "0.2.1"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_all(path: str) -> None:
    write(path, read(path).replace(OLD, NEW))


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        raise SystemExit(f"Expected text not found for {label}")
    return text.replace(old, new, 1)


def main() -> None:
    pyproject = read("pyproject.toml")
    if 'version = "0.2.0"' not in pyproject and 'version = "0.2.1"' not in pyproject:
        raise SystemExit("Unexpected project version")

    for path in (
        "pyproject.toml",
        ".agents/skills/gemini-web-mcp/SKILL.md",
        "README.md",
        "README.zh-CN.md",
        "docs/README.md",
        "docs/architecture.md",
        "docs/launch-kit.md",
        "scripts/check_version_consistency.py",
        "tests/test_skill_packaging.py",
        ".agents/skills/gemini-web-mcp-development/SKILL.md",
        ".agents/skills/gemini-web-mcp-development/references/architecture.md",
        ".agents/skills/gemini-web-mcp-development/references/validation.md",
        ".agents/skills/gemini-web-mcp-development/references/tool-design.md",
    ):
        replace_all(path)

    # Keep v0.2.0 as immutable history instead of claiming the rewrite moved with 0.2.1.
    for path in (
        "README.md",
        "README.zh-CN.md",
        ".agents/skills/gemini-web-mcp-development/SKILL.md",
        ".agents/skills/gemini-web-mcp-development/references/tool-design.md",
    ):
        text = read(path)
        text = text.replace(
            "Rewritten Git history and release refs use the canonical `0.2.1` version.",
            "The existing `v0.2.0` tag remains immutable; new release refs use `0.2.1`.",
        )
        text = text.replace(
            "all rewritten Git history and release refs use the canonical `0.2.1` project version.",
            "the existing `v0.2.0` tag remains immutable and new release refs use `0.2.1`.",
        )
        text = text.replace(
            "rewritten history and release refs use the same canonical project version.",
            "the existing `v0.2.0` tag remains immutable and new release refs use `0.2.1`.",
        )
        text = text.replace(
            "Rewritten package history and release refs use the canonical `0.2.1` version.",
            "The existing `v0.2.0` tag remains immutable; new release refs use `0.2.1`.",
        )
        write(path, text)

    changelog = read("docs/changelog.md")
    if "## [0.2.1]" not in changelog:
        section = """## [0.2.1] - 2026-08-21

### Changed
- Kept the existing `v0.2.0` tag immutable and advanced the active Python package, runtime Skill, development Skill, release assets, and current documentation to `0.2.1`.
- Recorded the owner-approved development order: first establish a complete live Gemini baseline using maintainer-supplied account Cookies, then implement the shared long-operation service.
- Settled local persistence on SQLite for long-operation recovery and delayed cleanup; the database remains local-only and stores recovery metadata rather than prompts, chat text, Cookies, or raw upstream responses.

### Fixed
- Added the repository AGPL-3.0-only license to wheel and source-distribution metadata and made release verification compare embedded license contents with the repository source.
- Parsed Deep Research source URLs before matching exact Google asset hostnames, preventing path, userinfo, and lookalike-domain text from being mistaken for Google-hosted resources.
- Refreshed the development Skill and status documentation so completed foundations are not repeatedly re-planned and settled owner decisions are no longer presented as open questions.

"""
        changelog = replace_once(
            changelog,
            "## [Unreleased]\n\n",
            "## [Unreleased]\n\n" + section,
            label="changelog release section",
        )
        write("docs/changelog.md", changelog)

    skill_path = ".agents/skills/gemini-web-mcp-development/SKILL.md"
    skill = read(skill_path)
    active_order = """## Active Development Order

Unless the owner explicitly changes product priority:

1. establish the dedicated full live baseline for media, files, URLs, research, disposable account mutations, and cleanup;
2. implement one shared SQLite-backed long-operation `start/status/result/cancel` service for Deep Research, video, and music;
3. complete typed results for primary deep history and remaining account/admin surfaces;
4. finish the read-back audit for every remote mutation;
5. implement SQLite-backed restart-durable cleanup;
6. add end-to-end multimodal onboarding and the official client/platform matrix;
7. pursue Drive, Canvas, richer Notebook/scheduled/sharing, and other UI parity only after core reliability.

"""
    skill = re.sub(
        r"## Active Development Order\n.*?(?=## Owner Decisions That Still Block Product Contracts)",
        active_order,
        skill,
        flags=re.DOTALL,
    )
    sqlite_contract = """### Local SQLite Persistence Is Settled

Long-operation recovery and delayed cleanup may use a local-only SQLite database. Persist only operation/resource IDs, provider IDs, state, timestamps, attempts, error/verification codes, and artifact locators. Never persist prompts, chat text, Cookies, or raw upstream responses.

Long operations default to seven-day retention, support restart and cross-client resume by operation ID, and expose best-effort cancellation unless provider cancellation is positively observed. Prefer provider-native temporary chats; only non-temporary cleanup work enters the durable queue.

"""
    if "### Local SQLite Persistence Is Settled" not in skill:
        skill = replace_once(
            skill,
            "### Test Doubles Do Not Prove the Product\n",
            sqlite_contract + "### Test Doubles Do Not Prove the Product\n",
            label="SQLite invariant insertion",
        )
    decision_block = """## Settled Owner Decisions and Remaining Product Choice

The following are settled and must not be reopened during routine work:

- preserve `v0.2.0` and publish the completed patch as `v0.2.1`;
- the maintainer supplies account Cookies through a protected environment for explicitly authorized live testing;
- complete the full live baseline before the shared long-operation service;
- use local-only SQLite for long-operation recovery and delayed cleanup;
- keep the compact low-token facade while execution moves into shared services;
- prioritize reliability and agent task completion before broad UI parity.

Load [roadmap.md](references/roadmap.md) only when selecting the first official client/OS/distribution support matrix.

"""
    skill = re.sub(
        r"## Owner Decisions That Still Block Product Contracts\n.*?(?=## Testing and Real Experience)",
        decision_block,
        skill,
        flags=re.DOTALL,
    )
    skill = skill.replace(
        "Do not let the active package and Skill versions drift; keep rewritten history and release refs at the canonical `0.2.1` version.",
        "Do not let the active package and Skill versions drift; preserve `v0.2.0` and create new release refs at `0.2.1` or later.",
    )
    write(skill_path, skill)

    architecture_path = ".agents/skills/gemini-web-mcp-development/references/architecture.md"
    architecture = read(architecture_path)
    architecture = architecture.replace(
        "The persistence mechanism still requires an owner decision.",
        "The owner approved a local-only SQLite queue. It stores identifiers and recovery state, never prompts, chat text, Cookies, or raw upstream responses.",
    )
    architecture = architecture.replace(
        "The service must define provider-backed identifiers, local registry state, persistence, cancellation, expiry, and behavior after process restart.",
        "Use a local-only SQLite registry with provider identifiers, restart and cross-client recovery, seven-day default expiry, artifact identity continuity, and best-effort cancellation unless provider cancellation is observed.",
    )
    architecture = re.sub(
        r"## Recommended Development Order\n.*\Z",
        """## Recommended Development Order

1. Configure and record the dedicated full live baseline using maintainer-supplied Cookie secrets.
2. Add the shared SQLite-backed long-operation service and tool contract.
3. Complete typed results for deep history and account/admin workflows.
4. Finish the mutation read-back audit.
5. Implement SQLite-backed durable cleanup.
6. Add video/music/file/URL/research onboarding and a real client/platform matrix.
7. Revisit UI-parity features only after the preceding workflows are stable.
""",
        architecture,
        flags=re.DOTALL,
    )
    write(architecture_path, architecture)

    validation_path = ".agents/skills/gemini-web-mcp-development/references/validation.md"
    validation = read(validation_path)
    sqlite_tests = """## SQLite Persistence Tests

For long operations, cover schema creation/migration, restart recovery, concurrent status/result calls, cross-client resume, idempotent terminal reads, seven-day expiry/pruning, best-effort versus provider-confirmed cancellation, corrupt/missing records, artifact identity continuity, and proof that prompts, chat text, Cookies, and raw responses are never persisted.

For cleanup, cover restart-safe pending work, retry/backoff, terminal failure, list/retry/cancel, duplicate deletion, direct-ID verification, temporary-chat bypass, and absence of private text in storage.

"""
    if "## SQLite Persistence Tests" not in validation:
        validation = replace_once(
            validation,
            "## Live Evidence Boundary\n",
            sqlite_tests + "## Live Evidence Boundary\n",
            label="SQLite validation insertion",
        )
    validation = validation.replace(
        "The canonical repository version is `0.2.1`. Inspect ClawHub before claiming that the repository version has been published there.",
        "The active repository version is `0.2.1`; preserve the existing `v0.2.0` tag. Inspect ClawHub before claiming that `0.2.1` has been published there.",
    )
    validation = validation.replace(
        "The Python package and both public Skills share one active version, currently `0.2.1`. Verify their frontmatter, the changelog section, runtime metadata, release tag, and generated artifact names together. Rewritten history and release refs use the canonical project version.",
        "The Python package and both public Skills share active version `0.2.1`. Verify frontmatter, changelog, runtime metadata, release tag, and artifacts together while preserving `v0.2.0` as immutable history.",
    )
    write(validation_path, validation)

    tool_path = ".agents/skills/gemini-web-mcp-development/references/tool-design.md"
    tool = read(tool_path)
    job_target = """## Shared Long-Operation Experience Target

After the full live baseline, implement one local SQLite-backed flow:

```text
start -> operation_id
status(operation_id)
result(operation_id)
cancel(operation_id)
```

Users must be able to restart the server and resume from another MCP client. Keep recovery metadata for seven days by default, preserve provider IDs and artifact identity, and report cancellation as best effort unless the provider confirms it. The database never stores prompts, chat text, Cookies, or raw responses.
""
    if "## Shared Long-Operation Experience Target" not in tool:
        tool = replace_once(
            tool,
            "## 8. Cleanup and History Verification\n",
            job_target + "## 8. Cleanup and History Verification\n",
            label="long-operation target insertion",
        )
    write(tool_path, tool)

    roadmap = """# Settled Decisions and Next Development Packages

Routine bug fixes and bounded service migrations should proceed without reopening settled product choices.

## Settled Decisions

- Preserve the existing `v0.2.0` tag and publish the completed audited patch as `v0.2.1`.
- The maintainer supplies account Cookies through a protected GitHub environment or local process environment; never commit or print them.
- Development order is full live baseline first, shared long-operation service second, then typed admin results, mutation verification, durable cleanup, onboarding/client matrix, and selected UI parity.
- Use local-only SQLite for operation recovery and delayed cleanup. Store IDs, state, timestamps, attempts, error/verification codes, and artifact locators onlyð€”never prompts, chat text, Cookies, or raw upstream responses.
- Long operations default to seven-day retention, support restart and cross-client resume, and use best-effort cancellation unless provider cancellation is observed.
- Prefer provider-native temporary chats; only non-temporary cleanup work enters the durable queue.
- Compact remains the low-token discovery product while execution belongs in shared services.

## Package A â€” Dedicated Full Live Baseline

Configure maintainer-provided Cookie secrets, record observable Web build/locale/tier/entitlements, test text, primary/compact sessions, image, video, music, file, URL, Deep Research, disposable Scheduled/Gem/Notebook mutations, and direct-ID cleanup. Produce a sanitized schema-valid report with every capability classified and every resource accounted for.

## Package B â€” Shared SQLite Long-Operation Service

Implement `start/status/result/cancel` for Deep Research, video, and music with stable operation/provider IDs, queued/running/completed/timed-out/cancelled/failed states, local SQLite migrations, restart/cross-client recovery, seven-day retention, pruning, artifact identity continuity, and best-effort cancellation.

## Package C â€” Complete Typed Admin and Deep History

Order: primary deep history; account/compatibility; prompts; Cookie outcomes; doctor; cleanup; remaining scheduled/Notebook presentation. Require stable `DomainResult`, pagination/truncation/retryability/verification, text agreement, and primary/compact parity.

## Package D â€” Mutation Verification Audit

For every remote mutation, define authoritative read-back, positive terminal evidence, ambiguous states, idempotency/retry behavior, and cleanup/rollback guidance.

## Package E â€” DÛÍüÙÈZ®Ëkºwµç