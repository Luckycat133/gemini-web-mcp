# Gemini Web MCP 0.x release ledger

Published tags and Releases are historical records. Do not move, recreate or
replace their refs or assets. The 0.1.0 row is a retrospective source milestone,
not an assertion that a package, tag or GitHub Release ever existed.

| Version | Evidence | Historical milestone |
| --- | --- | --- |
| 0.1.0 | [Source milestone](https://github.com/Luckycat133/gemini-web-mcp/commit/c17eed6569a4190373d114d48907f288cfd77e4b), 2026-05-05 | First package/source/test tree on main; metadata already said 0.2.0. No 0.1.0 tag or Release observed. |
| 0.2.0 | [v0.2.0 tag](https://github.com/Luckycat133/gemini-web-mcp/tree/v0.2.0) → `4341253563870012e465ea86741d295ed311ac94` | Version and distribution contracts aligned. Retained tag; no GitHub Release object observed. |
| 0.2.1 | [v0.2.1 Release](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.2.1) → `d9179952e8a9d3e1b155d24c2ac2010c3d0c035a` | Dedicated Assist surface and documentation. |
| 0.2.2 | [v0.2.2 Release](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.2.2) → `362a368f6c68b01c6eb143586db45743c5222e2c` | Focused workflows, durable state store and downloadable distribution. |
| 0.3.0 | [v0.3.0 Release](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.3.0) → `1116125e3113701f02acf3cc40c52a5290ca9968`, 2026-11-22; [release workflow](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/44978973789) passed | Function and Account-next coexist with Create and Account. Task recovery and exact Skill descriptions were fixed. The wheel, sdist, and two Skill ZIPs were verified. |

## How v0.3.0 was released

The reviewed `release/v0.3.0.publish` marker identified the preparation commit.
The release workflow verified the marker on main and confirmed CI and CodeQL for
the exact release SHA. It built and checked the wheel, sdist, and two Skill ZIPs
before creating the new tag and GitHub Release. The four public assets were also
downloaded and checked against their GitHub SHA-256 digests. This was a GitHub
Release only; it did not publish to PyPI or ClawHub.

## Rollback

For older clients, install the unchanged v0.2.2 assets and use the matching
state store and account rather than overwriting either ledger. See
[the state compatibility guide](source-update-integration.md).
