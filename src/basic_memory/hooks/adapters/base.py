"""Shared types for per-harness hook adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# Raw hook stdin after JSON parsing; adapters normalize it.
type HookPayload = dict[str, Any]


@dataclass(frozen=True)
class NormalizedHookEvent:
    """One harness lifecycle event, normalized across Claude Code and Codex.

    Field values come straight from the harness payload; missing fields
    normalize to "" / None rather than failing — hooks are fail-open, and a
    partially-populated event is still worth capturing.
    """

    source: str  # SPEC-55 source id: "claude-code" | "codex"
    event: str  # envelope event name (see basic_memory.hooks.envelope)
    session_id: str
    turn_id: str | None
    cwd: str
    transcript_path: str
    trigger: str | None  # SessionStart: startup|resume|...; PreCompact: manual|auto
    model: str | None


@dataclass(frozen=True)
class HarnessAdapter:
    """A harness's stdin dialect: its SPEC-55 source id plus a normalizer."""

    source: str
    normalize: Callable[[str, HookPayload], NormalizedHookEvent]
