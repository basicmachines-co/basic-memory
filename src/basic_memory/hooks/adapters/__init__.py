"""Per-harness hook stdin adapters.

Each harness speaks its own hook JSON dialect; an adapter normalizes it into
``NormalizedHookEvent`` so everything downstream (envelope, projector, CLI) is
harness-agnostic. Adding a harness means adding one small module here plus its
recorded fixtures — nothing else changes.
"""

from __future__ import annotations

from basic_memory.hooks.adapters import claude, codex
from basic_memory.hooks.adapters.base import HarnessAdapter, HookPayload, NormalizedHookEvent

_ADAPTERS: dict[str, HarnessAdapter] = {
    "claude": claude.ADAPTER,
    "codex": codex.ADAPTER,
}


def for_harness(harness: str) -> HarnessAdapter:
    """Look up the adapter for a harness id, failing fast on unknown values."""
    try:
        return _ADAPTERS[harness]
    except KeyError:
        raise ValueError(f"Unknown harness {harness!r}; supported: {sorted(_ADAPTERS)}") from None


__all__ = [
    "HarnessAdapter",
    "HookPayload",
    "NormalizedHookEvent",
    "for_harness",
]
