"""Test-only stand-in for Hermes's memory provider interface."""

from dataclasses import dataclass


class MemoryProvider:
    """Minimal base class used by the plugin during unit tests."""


@dataclass(frozen=True)
class RecallStatus:
    """What the last prefetch injected, for Hermes's recall indicator."""

    provider_label: str
    count: int
    glyph: str = "🧠"
