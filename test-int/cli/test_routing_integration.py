"""Integration tests for CLI routing flags (--local/--cloud).

These tests verify that the --local and --cloud flags work correctly
across CLI commands, and that the MCP command forces local routing.

Note: Environment variable behavior during command execution is tested
in unit tests (tests/cli/test_routing.py) which can properly monkeypatch
the modules before they are imported. These integration tests focus on
CLI behavior: flag acceptance and error handling.
"""

import os

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


runner = CliRunner()


class TestRoutingFlagsValidation:
    """Tests for --local/--cloud flag validation.

    These tests verify that using both --local and --cloud together
    produces an appropriate error message.
    """

    def test_status_both_flags_error(self):
        """Using both --local and --cloud should produce an error."""
        result = runner.invoke(cli_app, ["status", "--local", "--cloud"])
        # Exit code can be 1 or 2 depending on how typer handles the exception
        assert result.exit_code != 0
        assert "Cannot specify both --local and --cloud" in result.output

    def test_project_list_both_flags_error(self):
        """Using both --local and --cloud should produce an error."""
        result = runner.invoke(cli_app, ["project", "list", "--local", "--cloud"])
        assert result.exit_code != 0
        assert "Cannot specify both --local and --cloud" in result.output

    def test_tool_search_both_flags_error(self):
        """Using both --local and --cloud should produce an error."""
        result = runner.invoke(cli_app, ["tool", "search-notes", "test", "--local", "--cloud"])
        assert result.exit_code != 0
        assert "Cannot specify both --local and --cloud" in result.output

    def test_tool_read_note_both_flags_error(self):
        """Using both --local and --cloud should produce an error."""
        result = runner.invoke(cli_app, ["tool", "read-note", "test", "--local", "--cloud"])
        assert result.exit_code != 0
        assert "Cannot specify both --local and --cloud" in result.output

    def test_tool_build_context_both_flags_error(self):
        """Using both --local and --cloud should produce an error."""
        result = runner.invoke(
            cli_app, ["tool", "build-context", "memory://test", "--local", "--cloud"]
        )
        assert result.exit_code != 0
        assert "Cannot specify both --local and --cloud" in result.output


class TestMcpCommandForcesLocal:
    """Tests that the MCP command forces local routing."""

    def test_mcp_sets_force_local_env(self, monkeypatch):
        """MCP command should set BASIC_MEMORY_FORCE_LOCAL before server starts."""
        # Track what environment variable was set
        env_set_value = []

        # Mock the MCP server run to capture env state without actually starting server
        import basic_memory.cli.commands.mcp as mcp_mod

        def mock_run(*args, **kwargs):
            env_set_value.append(os.environ.get("BASIC_MEMORY_FORCE_LOCAL"))
            # Don't actually start the server
            raise SystemExit(0)

        # Get the actual mcp_server from the module
        monkeypatch.setattr(mcp_mod.mcp_server, "run", mock_run)

        # Also mock init_mcp_logging to avoid file operations
        monkeypatch.setattr(mcp_mod, "init_mcp_logging", lambda: None)

        runner.invoke(cli_app, ["mcp"])

        # Environment variable should have been set to "true"
        assert len(env_set_value) == 1
        assert env_set_value[0] == "true"


class TestToolCommandsAcceptFlags:
    """Tests that tool commands accept routing flags without parsing errors."""

    @pytest.mark.parametrize(
        "command,args",
        [
            ("search-notes", ["test query"]),
            ("recent-activity", []),
            ("read-note", ["test"]),
            ("build-context", ["memory://test"]),
            ("continue-conversation", []),
        ],
    )
    def test_tool_commands_accept_local_flag(self, command, args, app_config):
        """Tool commands should accept --local flag without parsing error."""
        full_args = ["tool", command] + args + ["--local"]
        result = runner.invoke(cli_app, full_args)
        # Should not fail due to flag parsing (No such option error)
        assert "No such option: --local" not in result.output

    @pytest.mark.parametrize(
        "command,args",
        [
            ("search-notes", ["test query"]),
            ("recent-activity", []),
            ("read-note", ["test"]),
            ("build-context", ["memory://test"]),
            ("continue-conversation", []),
        ],
    )
    def test_tool_commands_accept_cloud_flag(self, command, args, app_config):
        """Tool commands should accept --cloud flag without parsing error."""
        full_args = ["tool", command] + args + ["--cloud"]
        result = runner.invoke(cli_app, full_args)
        # Should not fail due to flag parsing (No such option error)
        assert "No such option: --cloud" not in result.output


class TestProjectCommandsAcceptFlags:
    """Tests that project commands accept routing flags without parsing errors."""

    def test_project_list_accepts_local_flag(self, app_config):
        """project list should accept --local flag."""
        result = runner.invoke(cli_app, ["project", "list", "--local"])
        assert "No such option: --local" not in result.output

    def test_project_list_accepts_cloud_flag(self, app_config):
        """project list should accept --cloud flag."""
        result = runner.invoke(cli_app, ["project", "list", "--cloud"])
        assert "No such option: --cloud" not in result.output

    def test_project_info_accepts_local_flag(self, app_config):
        """project info should accept --local flag."""
        result = runner.invoke(cli_app, ["project", "info", "--local"])
        assert "No such option: --local" not in result.output

    def test_project_info_accepts_cloud_flag(self, app_config):
        """project info should accept --cloud flag."""
        result = runner.invoke(cli_app, ["project", "info", "--cloud"])
        assert "No such option: --cloud" not in result.output

    def test_project_default_accepts_local_flag(self, app_config):
        """project default should accept --local flag."""
        result = runner.invoke(cli_app, ["project", "default", "test", "--local"])
        assert "No such option: --local" not in result.output

    def test_project_sync_config_accepts_local_flag(self, app_config):
        """project sync-config should accept --local flag."""
        result = runner.invoke(cli_app, ["project", "sync-config", "test", "--local"])
        assert "No such option: --local" not in result.output

    def test_project_move_accepts_local_flag(self, app_config):
        """project move should accept --local flag."""
        result = runner.invoke(cli_app, ["project", "move", "test", "/tmp/dest", "--local"])
        assert "No such option: --local" not in result.output


class TestStatusCommandAcceptsFlags:
    """Tests that status command accepts routing flags."""

    def test_status_accepts_local_flag(self, app_config):
        """status should accept --local flag."""
        result = runner.invoke(cli_app, ["status", "--local"])
        assert "No such option: --local" not in result.output

    def test_status_accepts_cloud_flag(self, app_config):
        """status should accept --cloud flag."""
        result = runner.invoke(cli_app, ["status", "--cloud"])
        assert "No such option: --cloud" not in result.output
