---
name: basic-machines-review
description: Use when reviewing Basic Machines code for house style, architecture risk, pre-merge hardening, or whether a change fits basic-memory/basic-memory-cloud conventions.
license: MIT
---

# Basic Machines Review

Use this skill for repo-local review passes where ordinary code review needs Basic Machines
house style and architecture judgment. Report findings only; do not edit code unless the user
asks you to fix specific findings.

## Scope

Review the current diff or named files against:

- The repo's `AGENTS.md` / `CLAUDE.md`
- `docs/ENGINEERING_STYLE.md`
- The touched code paths and tests

Apply only the guidance for the active repo. In `basic-memory`, prioritize local-first
file/database/MCP boundaries. In `basic-memory-cloud`, prioritize tenant/workspace isolation,
cloud worker behavior, and web-v2 state/runtime boundaries.

## Review Rubric

Report only concrete, falsifiable risks:

- **Cognitive load:** Is the change harder to understand than the problem requires?
- **Change propagation:** Will one product change force edits across unrelated layers?
- **Knowledge duplication:** Is the same rule encoded in multiple places that can drift?
- **Accidental complexity:** Did the change add abstractions, fallbacks, or state without need?
- **Dependency direction:** Are API/MCP/CLI, services, repositories, and UI stores respecting
  their intended boundaries?
- **Domain model distortion:** Do names and types still match the product concept, or did a
  transport/storage detail leak into the domain?
- **Test oracle quality:** Would the tests fail for the bug or regression the change claims to
  protect against?

## House Rules To Check Explicitly

- No speculative `getattr(obj, "attr", default)` for unknown model shapes.
- No broad exception swallowing, warning-only failure paths, or hidden fallback behavior.
- No casts or `Any` that hide an unclear type relationship.
- Dataclasses for internal value/result objects; Pydantic at validation/serialization
  boundaries.
- Narrow `Protocol`s when only a capability is needed.
- Explicit async/resource ownership, cancellation, and cleanup.
- Meaningful regression tests or verification for risky changes.
- Comments explain why, not what.

## Reporting Format

Lead with findings ordered by severity. Each finding should include:

| Severity | Use for |
| -------- | ------- |
| `high` | A likely correctness, security, data-loss, or tenant/workspace isolation failure |
| `medium` | A concrete maintainability or boundary risk that can cause future defects |
| `low` | A minor consistency issue, ambiguous guidance, or review-only cleanup |

```text
severity | file:line | risk category | claim
Why: concrete behavior or code path that proves the risk.
Fix: smallest practical change, or "none obvious" if the risk needs product input.
```

If there are no findings, say so and note any verification gaps that remain.
