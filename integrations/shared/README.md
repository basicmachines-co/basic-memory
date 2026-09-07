# Shared agent memory schemas

`schemas/` is the canonical source for Coding Session, Session, Task, and Decision
seed notes. Host packages carry copies so installation does not depend on a live
repository or another host package. Existing user schemas are never overwritten
by package generation; setup offers missing schemas with approval.

Run `uv run python scripts/sync_memory_schemas.py` after editing these sources.
Use `--check` for a read-only drift check. The Tau package test suite checks every
copy, so `just package-check` enforces consistency across the three hosts.

Claude Code and Tau bundle all four schemas. Codex bundles Coding Session, Task,
and Decision and retains its host-specific general `codex-session.md` schema.
Lifecycle envelopes and optional transcripts are not knowledge schemas.
