"""Tests for the basic_memory_diagnostics MCP tool."""

import json
import platform
import sys
from pathlib import Path

import basic_memory
import pytest
from basic_memory.mcp.tools.basic_memory_diagnostics import (
    _redact_config,
    _redact_url,
    basic_memory_diagnostics,
)


@pytest.fixture(autouse=True)
def isolate_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every diagnostics test isolated from the developer's real config."""
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path))


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
    assert basic_memory.__api_version__ == "v2"
    assert f"API: {basic_memory.__api_version__}" in result


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
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))

    result = basic_memory_diagnostics()

    assert "bmc_super_secret_token" not in result
    assert "cloud_api_key" not in result


def test_diagnostics_config_missing(tmp_path):
    """When config file does not exist, output should say so."""
    config_file = tmp_path / "config.json"
    assert not config_file.exists()

    result = basic_memory_diagnostics()

    assert "Config exists: False" in result
    assert "<config file not found>" in result


def test_diagnostics_does_not_create_config_directory(monkeypatch, tmp_path):
    """Resolving diagnostics must remain read-only when no config exists."""
    missing_config_dir = tmp_path / "does-not-exist"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(missing_config_dir))

    result = basic_memory_diagnostics()

    assert "Config exists: False" in result
    assert not missing_config_dir.exists()


def test_diagnostics_reports_invalid_json(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{not valid json", encoding="utf-8")

    result = basic_memory_diagnostics()

    assert "<error reading config:" in result


def test_diagnostics_reports_invalid_utf8(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_bytes(b'{"default_project": "main\xff"}')

    result = basic_memory_diagnostics()

    assert "<error reading config:" in result
    assert "invalid start byte" in result


def test_diagnostics_rejects_non_object_config(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("[]", encoding="utf-8")

    result = basic_memory_diagnostics()

    assert "<error reading config: expected a JSON object>" in result


def test_diagnostics_reports_config_read_error(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{}", encoding="utf-8")

    def fail_read_text(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    result = basic_memory_diagnostics()

    assert "<error reading config: permission denied>" in result


def test_diagnostics_output_sections():
    """All expected section headers should be present."""
    result = basic_memory_diagnostics()
    assert "# Basic Memory Diagnostics" in result
    assert "## Version" in result
    assert "## System" in result
    assert "## Configuration" in result


# ---------------------------------------------------------------------------
# Unit tests for _redact_url helper
# ---------------------------------------------------------------------------


def test_redact_url_strips_password():
    url = "postgresql://user:secret@localhost/mydb"
    result = _redact_url(url)
    assert "secret" not in result
    assert "user" not in result
    assert "localhost" in result
    assert "mydb" in result
    assert "***" in result


def test_redact_url_strips_only_password_when_no_username():
    # password-only userinfo (unusual but valid per RFC)
    url = "postgresql://:secret@db.example.com/app"
    assert _redact_url(url) == "postgresql://***@db.example.com/app"


def test_redact_url_preserves_port():
    url = "postgresql://admin:pw@db.internal:5432/prod"
    assert _redact_url(url) == "postgresql://***@db.internal:5432/prod"


def test_redact_url_preserves_ipv6_brackets():
    url = "postgresql://admin:pw@[::1]:5432/prod"
    assert _redact_url(url) == "postgresql://***@[::1]:5432/prod"


def test_redact_url_scrubs_credentials_from_malformed_url():
    url = "postgresql://admin:pw@[::1"
    assert _redact_url(url) == "postgresql://***@[::1"


def test_redact_url_scrubs_query_credentials_from_malformed_url():
    url = "postgresql://[::1?sslpassword=query-secret"
    assert _redact_url(url) == "postgresql://[::1?sslpassword=%2A%2A%2A"


def test_redact_url_leaves_malformed_url_without_credentials_unchanged():
    url = "postgresql://[::1"
    assert _redact_url(url) == url


def test_redact_url_no_credentials_unchanged():
    url = "postgresql://db.internal:5432/prod"
    assert _redact_url(url) == url


def test_redact_url_masks_query_password_and_preserves_safe_options():
    url = "postgresql://db.internal/prod?sslmode=require&sslpassword=query-secret"
    result = _redact_url(url)

    assert "query-secret" not in result
    assert result == "postgresql://db.internal/prod?sslmode=require&sslpassword=%2A%2A%2A"


def test_redact_url_masks_userinfo_and_query_secrets_together():
    url = "postgresql://dbuser:user-secret@db.internal/prod?password=query-secret"
    result = _redact_url(url)

    assert "dbuser" not in result
    assert "user-secret" not in result
    assert "query-secret" not in result
    assert result == "postgresql://***@db.internal/prod?password=%2A%2A%2A"


def test_redact_url_non_url_string_unchanged():
    # Bare file paths / non-URL values must not be mangled.
    path = "/home/user/.local/share/basic-memory/main.db"
    assert _redact_url(path) == path


# ---------------------------------------------------------------------------
# _redact_config tests for database_url
# ---------------------------------------------------------------------------


def test_redact_config_scrubs_database_url_credentials():
    raw = {
        "default_project": "main",
        "database_url": "postgresql://dbuser:dbpass@host.example.com:5432/bm",
        "projects": {},
    }
    result = _redact_config(raw)
    # Exact match: credentials replaced, host/port/db preserved for diagnostics.
    assert result["database_url"] == "postgresql://***@host.example.com:5432/bm"


def test_redact_config_leaves_database_url_without_credentials():
    raw = {"database_url": "sqlite:////tmp/basic-memory/main.db"}
    result = _redact_config(raw)
    assert result["database_url"] == "sqlite:////tmp/basic-memory/main.db"


def test_redact_config_drops_secret_fields_independently():
    raw = {
        "cloud_api_key": "bmc_top_secret",
        "database_url": "postgresql://dbuser:dbpassword@host/db",
        "default_project": "main",
    }
    result = _redact_config(raw)
    assert "cloud_api_key" not in result
    assert "dbpassword" not in result["database_url"]
    assert "dbuser" not in result["database_url"]
    assert "main" == result["default_project"]


# ---------------------------------------------------------------------------
# Integration: database_url redaction surfaces in diagnostic output
# ---------------------------------------------------------------------------


def test_diagnostics_redacts_database_url_password(tmp_path):
    """Postgres password in database_url must not appear in diagnostic output."""
    config_data = {
        "default_project": "main",
        "database_url": "postgresql://pguser:supersecret@db.internal:5432/basicmemory",
        "projects": {},
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))

    result = basic_memory_diagnostics()

    assert "supersecret" not in result
    assert "pguser" not in result
    # Host and port remain visible for diagnostics.
    assert "db.internal" in result
    assert "5432" in result


def test_diagnostics_redacts_database_url_query_password(tmp_path):
    """Query-string credentials must not escape through diagnostic output."""
    config_data = {
        "default_project": "main",
        "database_url": (
            "postgresql://db.internal:5432/basicmemory"
            "?sslmode=require&sslpassword=query-supersecret"
        ),
        "projects": {},
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data))

    result = basic_memory_diagnostics()

    assert "query-supersecret" not in result
    assert "sslmode=require" in result
    assert "sslpassword=%2A%2A%2A" in result
