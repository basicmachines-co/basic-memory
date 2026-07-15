"""Shared fixtures for the hook front-door unit tests."""

from pathlib import Path

import pytest


@pytest.fixture
def bm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the Basic Memory data dir (inbox lives under it)."""
    home = tmp_path / "bm-home"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(home))
    return home
