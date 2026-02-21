"""Regression tests for bm reset command process exit behavior."""

import os
import platform
import subprocess
from pathlib import Path

import pytest


IS_WINDOWS = platform.system() == "Windows"
skip_on_windows = pytest.mark.skipif(
    IS_WINDOWS, reason="Subprocess cleanup tests are unreliable on Windows CI"
)


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    if os.name == "nt":
        env["USERPROFILE"] = str(tmp_path)
    env["BASIC_MEMORY_HOME"] = str(tmp_path / "basic-memory")
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / ".basic-memory")
    return env


@skip_on_windows
def test_bm_reset_exits_cleanly(tmp_path: Path):
    """bm reset should finish and exit cleanly with non-interactive confirmation."""
    result = subprocess.run(
        ["uv", "run", "bm", "reset"],
        input="y\n",
        capture_output=True,
        text=True,
        timeout=20,
        cwd=Path(__file__).parent.parent.parent,
        env=_isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert "Database reset complete" in result.stdout


@skip_on_windows
def test_bm_reset_reindex_exits_cleanly(tmp_path: Path):
    """bm reset --reindex should finish and exit cleanly with non-interactive confirmation."""
    result = subprocess.run(
        ["uv", "run", "bm", "reset", "--reindex"],
        input="y\n",
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parent.parent.parent,
        env=_isolated_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert "Reindex complete" in result.stdout
