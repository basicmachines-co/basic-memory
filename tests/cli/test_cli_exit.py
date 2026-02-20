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


def test_bm_version_does_not_import_heavy_modules():
    """Regression test: 'bm --version' must not import heavy modules.

    The fast-path guard in cli/main.py skips command registration when
    argv is exactly ['--version']. This test verifies that modules like
    basic_memory.mcp (which pull in FastAPI, SQLAlchemy, etc.) are NOT
    loaded during a version-only invocation.
    """
    # Run a Python snippet that imports main.py the same way the entrypoint does,
    # then checks sys.modules for heavy imports
    check_script = (
        "import sys; "
        "sys.argv = ['bm', '--version']; "
        "import basic_memory.cli.main; "
        "heavy = [m for m in sys.modules if m.startswith('basic_memory.mcp')]; "
        "print(','.join(heavy) if heavy else 'CLEAN')"
    )
    result = subprocess.run(
        ["uv", "run", "python", "-c", check_script],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=Path(__file__).parent.parent.parent,
    )
    assert result.returncode == 0
    # The fast path should NOT have loaded any mcp modules
    assert "CLEAN" in result.stdout, (
        f"Heavy modules loaded during --version: {result.stdout.strip()}"
    )
