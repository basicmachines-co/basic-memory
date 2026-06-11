"""Tests for the basic_memory_diagnostics MCP tool."""

import json
import platform
import sys
from unittest.mock import MagicMock, patch

import basic_memory
from basic_memory.mcp.tools.basic_memory_diagnostics import (
    _redact_config,
    basic_memory_diagnostics,
)


# ---------------------------------------------------------------------------
# Unit tests for _redact_config helper
# ---------------------------------------------------------------------------


def test_redact_config_removes_cloud_api_key():
    raw = {"cloud_api_key": "bmc_secret", "default_project": "main", "projects": {}}
    result = _redact_config(raw)
    assert "cloud_api_key" not in result
    assert result["default_project"] == "main"
    assert "projects" in result


def test_redact_config_passes_through_safe_fields():
    raw = {"default_project": "main", "log_level": "INFO", "env": "dev"}
    result = _redact_config(raw)
    assert result == raw


def test_redact_config_empty_dict():
    assert _redact_config({}) == {}


# ---------------------------------------------------------------------------
# Tests for the basic_memory_diagnostics tool
# ---------------------------------------------------------------------------


def test_diagnostics_returns_string():
    result = basic_memory_diagnostics()
    assert isinstance(result, str)


def test_diagnostics_includes_version():
    result = basic_memory_diagnostics()
    assert basic_memory.__version__ in result
    assert basic_memory.__api_version__ in result


def test_diagnostics_includes_python_version():
    result = basic_memory_diagnostics()
    # sys.version can be multi-line; just check the version tuple prefix
    major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert major_minor in result


def test_diagnostics_includes_platform():
    result = basic_memory_diagnostics()
    assert platform.machine() in result


def test_diagnostics_includes_config_path(tmp_path):
    """Config path section should appear in output."""
    with patch("basic_memory.mcp.tools.basic_memory_diagnostics.ConfigManager") as MockMgr:
        mock_mgr = MagicMock()
        mock_mgr.config_dir = tmp_path
        MockMgr.return_value = mock_mgr

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"default_project": "main", "projects": {}}))

        result = basic_memory_diagnostics()

    assert str(tmp_path) in result
    assert "Config path:" in result


def test_diagnostics_config_exists_with_valid_json(tmp_path):
    """When config file exists, its safe contents should appear as JSON."""
    config_data = {
        "default_project": "research",
        "projects": {"research": {"path": str(tmp_path / "research")}},
    }
    with patch("basic_memory.mcp.tools.basic_memory_diagnostics.ConfigManager") as MockMgr:
        mock_mgr = MagicMock()
        mock_mgr.config_dir = tmp_path
        MockMgr.return_value = mock_mgr

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        result = basic_memory_diagnostics()

    assert "research" in result
    assert "```json" in result


def test_diagnostics_redacts_cloud_api_key(tmp_path):
    """cloud_api_key must never appear in diagnostic output."""
    config_data = {
        "default_project": "main",
        "cloud_api_key": "bmc_super_secret_token",
        "projects": {},
    }
    with patch("basic_memory.mcp.tools.basic_memory_diagnostics.ConfigManager") as MockMgr:
        mock_mgr = MagicMock()
        mock_mgr.config_dir = tmp_path
        MockMgr.return_value = mock_mgr

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        result = basic_memory_diagnostics()

    assert "bmc_super_secret_token" not in result
    assert "cloud_api_key" not in result


def test_diagnostics_config_missing(tmp_path):
    """When config file does not exist, output should say so."""
    with patch("basic_memory.mcp.tools.basic_memory_diagnostics.ConfigManager") as MockMgr:
        mock_mgr = MagicMock()
        mock_mgr.config_dir = tmp_path
        MockMgr.return_value = mock_mgr

        # Ensure no config.json is present
        config_file = tmp_path / "config.json"
        assert not config_file.exists()

        result = basic_memory_diagnostics()

    assert "Config exists: False" in result
    assert "<config file not found>" in result


def test_diagnostics_output_sections():
    """All expected section headers should be present."""
    result = basic_memory_diagnostics()
    assert "# Basic Memory Diagnostics" in result
    assert "## Version" in result
    assert "## System" in result
    assert "## Configuration" in result
