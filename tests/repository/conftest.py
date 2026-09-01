"""Shared fixtures for repository-layer tests."""

import os
from collections.abc import Callable

import pytest


@pytest.fixture
def pin_cpu_budget(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Return a helper that pins the CPU budget the embedding factory observes.

    ``_available_cpu_count`` prefers ``os.process_cpu_count`` and falls back to
    ``os.cpu_count``. Both are pinned to the same value, so callers assert on the
    resolved budget rather than on which API reported it.

    Constraint: ``os.process_cpu_count`` is new in Python 3.13 and
    ``monkeypatch.setattr`` refuses to set an attribute that does not exist, so
    3.12 needs ``raising=False``. Creating it there is deliberate - it keeps the
    preferred branch under test on every supported interpreter, and monkeypatch
    deletes an attribute it created during teardown, so ``os`` is restored either
    way. The genuine 3.12 fallback is covered separately by the tests that delete
    ``os.process_cpu_count``.
    """

    def pin(cpu_count: int) -> None:
        monkeypatch.setattr(os, "process_cpu_count", lambda: cpu_count, raising=False)
        monkeypatch.setattr(os, "cpu_count", lambda: cpu_count)

    return pin
