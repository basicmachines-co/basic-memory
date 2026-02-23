"""Integration tests for CLI routing flags (--local/--cloud).

These tests verify that the --local and --cloud flags work correctly
across CLI commands, and that MCP routing varies by transport.

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

    def test_project_ls_both_flags_error(self):
        """Using both --local and --cloud on project ls should produce an error."""
        result = runner.invoke(
            cli_app,
            ["project", "ls", "--name", "test", "--local", "--cloud"],
        )
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

    def test_tool_edit_note_both_flags_error(self):
        """Using both --local and --cloud should produce an error."""
        result = runner.invoke(
            cli_app,
            [
                "tool",
                "edit-note",
                "test",
                "--operation",
                "append",
                "--content",
                "test",
                "--local",
                "--cloud",
            ],
        )
        assert result.exit_code != 0
        assert "Cannot specify both --local and --cloud" in result.output


class TestMcpCommandRouting:
    """Tests that MCP routing varies by transport."""

    def test_mcp_stdio_does_not_force_local(self, monkeypatch):
        """Stdio transport should not inject explicit local routing env vars."""
        # Ensure env is clean before test
        monkeypatch.delenv("BASIC_MEMORY_FORCE_LOCAL", raising=False)
        monkeypatch.delenv("BASIC_MEMORY_EXPLICIT_ROUTING", raising=False)

        env_at_run = {}

        import basic_memory.cli.commands.mcp as mcp_mod

        def mock_run(*args, **kwargs):
            env_at_run["FORCE_LOCAL"] = os.environ.get("BASIC_MEMORY_FORCE_LOCAL")
            env_at_run["EXPLICIT"] = os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING")
            raise SystemExit(0)

        monkeypatch.setattr(mcp_mod.mcp_server, "run", mock_run)
        monkeypatch.setattr(mcp_mod, "init_mcp_logging", lambda: None)

        runner.invoke(cli_app, ["mcp"])  # default transport is stdio

        # Command should not have set these vars
        assert env_at_run["FORCE_LOCAL"] is None
        assert env_at_run["EXPLICIT"] is None

    def test_mcp_stdio_honors_external_env_override(self, monkeypatch):
        """Stdio transport should pass through externally-set routing env vars."""
        monkeypatch.setenv("BASIC_MEMORY_FORCE_CLOUD", "true")
        monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")

        env_at_run = {}

        import basic_memory.cli.commands.mcp as mcp_mod

        def mock_run(*args, **kwargs):
            env_at_run["FORCE_CLOUD"] = os.environ.get("BASIC_MEMORY_FORCE_CLOUD")
            env_at_run["EXPLICIT"] = os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING")
            raise SystemExit(0)

        monkeypatch.setattr(mcp_mod.mcp_server, "run", mock_run)
        monkeypatch.setattr(mcp_mod, "init_mcp_logging", lambda: None)

        runner.invoke(cli_app, ["mcp"])

        # Externally-set vars should be preserved
        assert env_at_run["FORCE_CLOUD"] == "true"
        assert env_at_run["EXPLICIT"] == "true"

    def test_mcp_streamable_http_forces_local(self, monkeypatch):
        """Streamable-HTTP transport should force local routing."""
        env_at_run = {}

        import basic_memory.cli.commands.mcp as mcp_mod

        def mock_run(*args, **kwargs):
            env_at_run["FORCE_LOCAL"] = os.environ.get("BASIC_MEMORY_FORCE_LOCAL")
            env_at_run["EXPLICIT"] = os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING")
            raise SystemExit(0)

        monkeypatch.setattr(mcp_mod.mcp_server, "run", mock_run)
        monkeypatch.setattr(mcp_mod, "init_mcp_logging", lambda: None)

        runner.invoke(cli_app, ["mcp", "--transport", "streamable-http"])

        assert env_at_run["FORCE_LOCAL"] == "true"
        assert env_at_run["EXPLICIT"] == "true"

    def test_mcp_sse_forces_local(self, monkeypatch):
        """SSE transport should force local routing."""
        env_at_run = {}

        import basic_memory.cli.commands.mcp as mcp_mod

        def mock_run(*args, **kwargs):
            env_at_run["FORCE_LOCAL"] = os.environ.get("BASIC_MEMORY_FORCE_LOCAL")
            env_at_run["EXPLICIT"] = os.environ.get("BASIC_MEMORY_EXPLICIT_ROUTING")
            raise SystemExit(0)

        monkeypatch.setattr(mcp_mod.mcp_server, "run", mock_run)
        monkeypatch.setattr(mcp_mod, "init_mcp_logging", lambda: None)

        runner.invoke(cli_app, ["mcp", "--transport", "sse"])

        assert env_at_run["FORCE_LOCAL"] == "true"
        assert env_at_run["EXPLICIT"] == "true"


class TestToolCommandsAcceptFlags:
    """Tests that tool commands accept routing flags without parsing errors."""

    @pytest.mark.parametrize(
        "command,args",
        [
            ("search-notes", ["test query"]),
            ("recent-activity", []),
            ("read-note", ["test"]),
            ("edit-note", ["test", "--operation", "append", "--content", "test"]),
            ("build-context", ["memory://test"]),
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
            ("edit-note", ["test", "--operation", "append", "--content", "test"]),
            ("build-context", ["memory://test"]),
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

    def test_project_move_local_only(self, app_config):
        """project move should reject --cloud flag."""
        result = runner.invoke(cli_app, ["project", "move", "test", "/tmp/dest", "--cloud"])
        assert result.exit_code == 2

    def test_project_ls_accepts_local_flag(self, app_config):
        """project ls should accept --local flag."""
        result = runner.invoke(cli_app, ["project", "ls", "--name", "test", "--local"])
        assert "No such option: --local" not in result.output

    def test_project_ls_accepts_cloud_flag(self, app_config):
        """project ls should accept --cloud flag."""
        result = runner.invoke(cli_app, ["project", "ls", "--name", "test", "--cloud"])
        assert "No such option: --cloud" not in result.output


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
