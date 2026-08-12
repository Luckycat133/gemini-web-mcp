# Development Status and Next Steps

Updated: 2026-08-12

This page is the human-readable companion to [`.agents/skills/gemini-web-mcp-development`](../.agents/skills/gemini-web-mcp-development/SKILL.md). The Skill is the canonical engineering workflow; this page summarizes the current repository baseline and owner decisions.

## Current Baseline

Latest reviewed pre-rewrite `main` commit at this update:

```text
d3145a3be45745523e7483b18835b4800da80ab5
```

Its hosted CI and CodeQL completed successfully, including Ruff, Mypy, Python 3.11/3.12 tests, targeted architecture contracts, real primary/compact MCP stdio smoke, Agent Skill validation/direct install, and clean-wheel/onboarding checks.

Current version lines are intentionally separate:

| Surface | Current line | Meaning |
| --- | --- | --- |
| Python package source | `0.2.0` | Canonical package version synchronized with rewritten release refs |
| Historical Git tags | through `0.2.0` | Rewritten package-release history on the canonical version line |
| Development Skill | `0.2.0` | Repository-maintenance instructions |
| ClawHub runtime Skill | `0.2.0` | Operating instructions on the same canonical version line as the Python package |

## Implemented Repository Contracts

| Area | State | Current boundary |
| --- | --- | --- |
| MCP SDK v2 and protocol negotiation | Implemented | Primary and compact entrypoints exercise current discovery and legacy negotiation over real stdio. |
| Packaging and onboarding | Implemented | Wheel, source distribution, runtime Skill archive, clean installation, and credential-free `uvx` onboarding are maintained gates. |
| Skill distribution | Implemented | `.agents/skills` is the single repository source; the runtime Skill has a separate ClawHub preview line. |
| Chat, sessions, and lifecycle | Implemented | Primary and compact adapters share core execution, opaque session IDs, explicit missing-session behavior, and typed state. |
| Artifact model | Implemented | Image, video, audio, file, webpage, data, and report outputs use shared artifact state and verification metadata. |
| Typed history | Implemented for list/search/read/export/delete | Primary and compact use one service with mapping/object normalization and evidence-based deletion. The primary deep scan remains prose-first. |
| Gem mutation verification | Implemented | Create/update/delete require positive read-back evidence before success. |
| Browser-Cookie authorization boundary | Implemented | Bounded authorization, sanitized errors, explicit approval, and sensitive local-cache handling are documented and tested. |
| Account/admin typed results | Partial | Account, prompt, Cookie, doctor, cleanup, scheduled, Notebook, and compatibility coverage remains uneven. |
| Mutation verification outside history/Gems | Partial | Several Notebook/scheduled paths preserve evidence, but the complete remote-mutation audit is unfinished. |
| Live Gemini compatibility | Partial bounded observation | A 2026-08-08 authorized run verified Cookie initialization, text, sessions, typed history, and chat cleanup. The dedicated full canary remains unconfigured/skipped. |
| Delayed cleanup | Partial | Process-local cleanup exists; pending delayed work is not durable across restarts. |
| Long-running operations | Partial | Media and Deep Research preserve queued/running/timed-out states and IDs, but no shared `start/status/result/cancel` API exists. |
| Broad Gemini UI parity | Deferred | Drive, Canvas, richer Notebook/scheduled/sharing/settings/Library workflows follow core reliability. |

## Current Live Evidence Boundary

The 2026-08-08 authorized run observed:

- Cookie initialization and reachable read-only history probes;
- temporary and retained text;
- primary multi-turn context and session reset;
- typed primary/compact history list/search;
- four returned remote chat IDs deleted with `verification.status=verified_absent` after complete fresh metadata pagination.

It did not establish:

- a dedicated repository canary account;
- Web build, locale, account tier, or entitlement matrix;
- image, video, music, files, URLs, or Deep Research;
- scheduled, Gem, or Notebook mutation behavior in the current deployment;
- an official multi-client/platform compatibility matrix.

The latest scheduled dedicated-canary workflow remained skipped because the required repository variables/environment were not configured. A skipped run is not live success.

## Settled Directions

The following should not be repeatedly reopened during normal development:

- browser Cookies are sensitive authentication material and follow the explicit-approval/restricted-cache contract;
- session reset changes only MCP/Gemini conversation state;
- the compact eleven-tool facade remains the low-token discovery product while execution moves into shared services;
- core multimodal reliability, artifacts, recovery, and agent task completion precede broad UI parity;
- `.agents/skills` remains the single repository Skill source.

## Recommended Next Development Order

1. Configure the dedicated full live baseline and exercise media, files, URLs, research, disposable account mutations, and cleanup.
2. Complete typed results for the primary deep history scan and account/admin surfaces.
3. Finish the positive-read-back audit for every remote mutation.
4. Implement one shared long-operation `start/status/result/cancel` service.
5. Implement the selected restart-durable cleanup model.
6. Add video, music, file, URL, and research onboarding plus the official client/platform matrix.
7. Select UI-parity features only after the preceding workflows are stable.

## Owner Decisions Still Required

### Package Release Line

Keep the canonical package version at `0.2.0` until the owner explicitly approves a future unified bump.

### Dedicated Live Account

Define ownership, recovery, tier, region/locale, entitlements, secret rotation, run cadence, evidence retention, and whether stale/missing live evidence blocks a package release.

### Long-Operation State

Decide persistence across restarts, provider-ID recovery, cancellation guarantees, expiry, and cross-client resume semantics.

### Cleanup Durability

Decide whether unavoidable delayed deletions use a durable local queue, its retry/terminal-state model, and whether the project may claim automatic cleanup without restart durability.

### Official Support and Distribution

Choose the first supported client/OS matrix and the canonical relationship among reviewed Git SHA, immutable GitHub Release wheel, PyPI, ClawHub runtime Skill, and MCP directory listings.

See the Skill's [architecture reference](../.agents/skills/gemini-web-mcp-development/references/architecture.md), [testing ladder](../.agents/skills/gemini-web-mcp-development/references/validation.md), [experience guide](../.agents/skills/gemini-web-mcp-development/references/tool-design.md), and [owner decision record](../.agents/skills/gemini-web-mcp-development/references/roadmap.md) for issue-sized acceptance criteria.
