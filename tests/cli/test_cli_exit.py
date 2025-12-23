"""Regression tests for CLI command exit behavior.

These tests verify that CLI commands exit cleanly without hanging,
which was a bug fixed in the database initialization refactor.
"""

import subprocess
from pathlib import Path


def test_bm_version_exits_cleanly():
    """Test that 'bm --version' exits cleanly within timeout."""
    # Use uv run to ensure correct environment
    result = subprocess.run(
        ["uv", "run", "bm", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=Path(__file__).parent.parent.parent,  # Project root
    )
    assert result.returncode == 0
    assert "Basic Memory version:" in result.stdout


def test_bm_help_exits_cleanly():
    """Test that 'bm --help' exits cleanly within timeout."""
    result = subprocess.run(
        ["uv", "run", "bm", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=Path(__file__).parent.parent.parent,
    )
    assert result.returncode == 0
    assert "Basic Memory" in result.stdout


def test_bm_tool_help_exits_cleanly():
    """Test that 'bm tool --help' exits cleanly within timeout."""
    result = subprocess.run(
        ["uv", "run", "bm", "tool", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=Path(__file__).parent.parent.parent,
    )
    assert result.returncode == 0
    assert "tool" in result.stdout.lower()
