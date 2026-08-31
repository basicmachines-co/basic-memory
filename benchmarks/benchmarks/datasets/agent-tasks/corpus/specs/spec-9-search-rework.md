---
title: SPEC-9 Search Rework
type: note
tags: [search, spec]
status: active
priority: critical
confidence: 0.92
---

# SPEC-9 Search Rework

Rework the search pipeline so ranking quality stops depending on FTS5 quirks.
This is the active spec; session worklogs carry the running state.

## Observations

- [goal] Replace the legacy FTS query planner with a two-stage ranking pass
- [decision] Keep SQLite FTS5 as the first stage; rescoring happens in Python
- [status] Session 2 captured the current open items and the next step

## Relations

- has_session [[2026-08-20 SPEC-9 Session 1]]
- has_session [[2026-08-27 SPEC-9 Session 2]]
