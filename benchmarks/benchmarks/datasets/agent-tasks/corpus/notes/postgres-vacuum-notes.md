---
title: Postgres Vacuum Notes
type: note
tags: [postgres, maintenance]
status: active
priority: low
---

# Postgres Vacuum Notes

The old tracker labelled this "priority: high" in its free-text field, but the
frontmatter above records the real triage priority after we measured the bloat.

## Observations

- [note] Autovacuum stalls on the events table during bulk imports
- [note] A manual VACUUM ANALYZE after imports keeps bloat under 10 percent
