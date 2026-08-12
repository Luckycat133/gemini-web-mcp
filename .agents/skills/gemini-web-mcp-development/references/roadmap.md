# Decisions to Discuss With the Owner

These choices materially change product contracts. Do not bury them inside unrelated maintenance work.

## 1. What Is the Next Public Version?

Current source metadata remains on a `1.x` line while historical Git tags/releases include higher `2.x` versions. A future release must not silently publish a numerically lower “latest” version.

Options:

- adopt a new version greater than every historical tag;
- document separate historical/rebuilt lines with one canonical current line;
- postpone binary releases and support reviewed source-at-SHA installs only.

Recommended discussion: keep one canonical project version at `0.2.0` because the current architecture uses MCP SDK v2, structured contracts, and substantially different runtime boundaries.

## 2. What Is the Official Live Compatibility Strategy?

A targeted authorized live run has proven some current behavior, but it is not a complete compatibility baseline.

Decide:

- whether to maintain a dedicated Gemini test account;
- ownership, recovery, region, locale, and entitlement boundaries;
- secret rotation and evidence retention rules;
- which workflows must be proven before releases.

Recommended sequence:

1. read-only capability baseline;
2. temporary text/session/media verification;
3. disposable mutations with read-back and cleanup.

## 3. Security Boundary for Cookie and Account Diagnostics

Recent skill security review highlighted that browser Cookie handling must be treated as authentication material.

Decide:

- whether Cookie access is always explicit opt-in;
- allowed storage and cleanup policy;
- whether diagnostics may access browser-derived authentication state;
- what evidence may appear in logs or reports.

Rules to preserve:

- never print Cookie values;
- never treat browser authorization as arbitrary credential-file access;
- keep account/session cleanup separate from agent memory or instructions.

## 4. Should Long Operations Become First-Class Jobs?

Current calls can expose queued/running/timed-out states, but recovery is workflow-specific.

Recommended contract:

```text
start -> operation_id
status(operation_id)
result(operation_id)
cancel(operation_id)
```

Use one operation model across Deep Research, video, music, and future asynchronous media.

## 5. What Is the Long-Term Role of Compact Server?

Recommended direction:

- keep compact discovery as a low-token product;
- move execution into shared services;
- test semantic parity instead of maintaining duplicate business logic.

## 6. Core Reliability or UI Parity?

Prioritize:

1. multimodal reliability and artifact workflows;
2. typed account/admin results and mutation verification;
3. job recovery and onboarding;
4. only then broad Gemini UI parity.

Potential UI-parity work remains:

- Drive import;
- Canvas;
- richer scheduled recurrence;
- Notebook CRUD/source management;
- sharing/settings workflows;
- Library management.

## 7. Supported Client Matrix

Define official support instead of listing examples only:

- client versions;
- protocol modes;
- supported operating systems;
- artifact rendering expectations;
- timeout guidance.

## 8. Distribution Strategy

Choose the canonical path among:

- reviewed Git SHA;
- immutable GitHub Release wheel;
- PyPI;
- runtime/development Skills;
- MCP directories.

A discovery listing should follow a reproducible installation and version strategy.

## Suggested Owner Conversation Order

1. version/release line;
2. dedicated live compatibility account;
3. security and Cookie boundary policy;
4. next three user workflows;
5. long-operation job model;
6. cleanup durability;
7. compact server commitment;
8. official client/platform matrix;
9. distribution channels.
