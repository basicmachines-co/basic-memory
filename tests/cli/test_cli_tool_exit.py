"""Test that CLI tool commands exit cleanly without hanging.

This test ensures that CLI commands properly clean up database connections
on exit, preventing process hangs. See GitHub issue for details.

The issue occurs when:
1. ensure_initialization() calls asyncio.run(initialize_app())
2. initialize_app() creates global database connections via db.get_or_create_db()
3. When asyncio.run() completes, the event loop closes
4. But the global database engine holds async connections that prevent clean exit
5. Process hangs indefinitely

The fix ensures db.shutdown_db() is called before asyncio.run() returns.
"""

import subprocess
import sys

import pytest


class TestCLIToolExit:
    """Test that CLI tool commands exit cleanly."""

    @pytest.mark.parametrize(
        "command",
        [
            ["tool", "--help"],
            ["tool", "write-note", "--help"],
            ["tool", "read-note", "--help"],
            ["tool", "search-notes", "--help"],
            ["tool", "build-context", "--help"],
        ],
    )
    def test_cli_command_exits_cleanly(self, command: list[str]):
        """Test that CLI commands exit without hanging.

        Each command should complete within the timeout without requiring
        manual termination (Ctrl+C).
        """
        full_command = [sys.executable, "-m", "basic_memory.cli.main"] + command

        try:
            result = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                timeout=10.0,  # 10 second timeout - commands should complete in ~2s
            )
            # Command should exit with code 0 for --help
            assert result.returncode == 0, f"Command failed: {result.stderr}"
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"Command '{' '.join(command)}' hung and did not exit within timeout. "
                "This indicates database connections are not being cleaned up properly."
            )

    def test_ensure_initialization_exits_cleanly(self):
        """Test that ensure_initialization doesn't cause process hang.

        This test directly tests the initialization function that's called
        by CLI commands, ensuring it cleans up database connections properly.
        """
        code = """
import asyncio
from basic_memory.config import ConfigManager
from basic_memory.services.initialization import ensure_initialization

app_config = ConfigManager().config
ensure_initialization(app_config)
print("OK")
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            assert "OK" in result.stdout, f"Unexpected output: {result.stdout}"
        except subprocess.TimeoutExpired:
            pytest.fail(
                "ensure_initialization() caused process hang. "
                "Database connections are not being cleaned up before event loop closes."
            )
