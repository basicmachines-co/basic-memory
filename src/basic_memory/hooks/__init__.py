"""Harness hook front door (issue #997, SPEC-55 revision 2026-07-15).

Agent harnesses (Claude Code, Codex) fire lifecycle hooks; this package is the
producer side of the harness WAL. Capture is dumb: hook stdin is normalized by
a per-harness adapter, wrapped in a bounded producer envelope, and appended to
the local inbox. ``bm hook flush`` retires valid trace into a local audit archive;
durable knowledge is written separately by an active agent or explicit workflow.

Modules:
  - ``_uuid7``    time-ordered event ids (inbox filenames sort chronologically)
  - ``envelope``  the SPEC-55 producer envelope contract
  - ``inbox``     append-only WAL under the Basic Memory home dir
  - ``adapters``  per-harness hook stdin normalization
  - ``archive``   idempotent local audit-archive sweep
  - ``project_ref`` project-name / project-id routing helpers
"""
