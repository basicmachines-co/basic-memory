"""Tests for --json output across CLI commands.

Each test verifies:
- Exit code 0 (or 1 for strict mode)
- Output is valid json.loads()-able
- Expected keys present in the parsed data
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.project_info import ProjectList
from basic_memory.schemas.sync_report import SkippedFileResponse, SyncReportResponse

# Importing registers subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401

runner = CliRunner()


def _parse_json_output(output: str) -> dict:
    """Extract and parse the JSON object from CLI output.

    The CliRunner may capture log lines before the JSON payload.
    We find the first '{' and parse from there.
    """
    start = output.index("{")
    return json.loads(output[start:])


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _mock_config_manager():
    """Create a mock ConfigManager that avoids reading real config."""
    mock_cm = MagicMock()
    mock_cm.config = MagicMock()
    mock_cm.default_project = "test-project"
    mock_cm.get_project.return_value = ("test-project", "/tmp/test")
    return mock_cm


SYNC_REPORT_WITH_CHANGES = SyncReportResponse(
    new={"notes/new-file.md"},
    modified={"notes/existing.md"},
    deleted={"notes/old.md"},
    moves={"notes/moved-from.md": "notes/moved-to.md"},
    checksums={"notes/new-file.md": "abc12345", "notes/existing.md": "def67890"},
    skipped_files=[],
    total=4,
)

SYNC_REPORT_EMPTY = SyncReportResponse(
    new=set(),
    modified=set(),
    deleted=set(),
    moves={},
    checksums={},
    skipped_files=[],
    total=0,
)

SYNC_REPORT_WITH_SKIPPED = SyncReportResponse(
    new=set(),
    modified=set(),
    deleted=set(),
    moves={},
    checksums={},
    skipped_files=[
        SkippedFileResponse(
            path="bad/file.md",
            reason="parse error",
            failure_count=3,
            first_failed=datetime(2025, 6, 15, 12, 0, 0),
        )
    ],
    total=0,
)

VALIDATE_REPORT = {
    "note_type": "person",
    "total_notes": 2,
    "total_entities": 2,
    "valid_count": 1,
    "warning_count": 1,
    "error_count": 1,
    "results": [
        {
            "note_identifier": "people/alice",
            "schema_entity": "person",
            "passed": True,
            "warnings": [],
            "errors": [],
        },
        {
            "note_identifier": "people/bob",
            "schema_entity": "person",
            "passed": False,
            "warnings": ["Missing optional field: role"],
            "errors": ["Missing required field: name"],
        },
    ],
}

INFER_REPORT = {
    "note_type": "person",
    "notes_analyzed": 5,
    "field_frequencies": [
        {"name": "name", "source": "observation", "count": 5, "total": 5, "percentage": 1.0},
        {"name": "role", "source": "observation", "count": 3, "total": 5, "percentage": 0.6},
    ],
    "suggested_schema": {"name": "string, full name", "role?": "string, job title"},
    "suggested_required": ["name"],
    "suggested_optional": ["role"],
    "excluded": [],
}

DIFF_REPORT_WITH_DRIFT = {
    "note_type": "person",
    "schema_found": True,
    "new_fields": [
        {"name": "email", "source": "observation", "count": 3, "total": 5, "percentage": 0.6}
    ],
    "dropped_fields": [
        {"name": "phone", "source": "observation", "count": 0, "total": 5, "percentage": 0.0}
    ],
    "cardinality_changes": ["role: single -> array"],
}


# ---------------------------------------------------------------------------
# Status --json
# ---------------------------------------------------------------------------

_MOCK_PROJECT_ITEM = MagicMock()
_MOCK_PROJECT_ITEM.name = "test-project"
_MOCK_PROJECT_ITEM.external_id = "11111111-1111-1111-1111-111111111111"


@patch("basic_memory.cli.commands.status.ConfigManager")
@patch("basic_memory.cli.commands.status.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.get_client")
def test_status_json_outputs_sync_report(mock_get_client, mock_get_active, mock_config_cls):
    """bm status --json outputs a valid JSON sync report with changes."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM

    mock_project_client = AsyncMock()
    mock_project_client.get_status.return_value = SYNC_REPORT_WITH_CHANGES

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield MagicMock()

    mock_get_client.side_effect = fake_get_client

    with patch.object(ProjectClient, "get_status", mock_project_client.get_status):
        result = runner.invoke(cli_app, ["status", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert data["total"] == 4
    assert "new" in data
    assert "modified" in data
    assert "deleted" in data
    assert "moves" in data


@patch("basic_memory.cli.commands.status.ConfigManager")
@patch("basic_memory.cli.commands.status.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.get_client")
def test_status_json_no_changes(mock_get_client, mock_get_active, mock_config_cls):
    """bm status --json with empty report outputs total: 0."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM

    mock_project_client = AsyncMock()
    mock_project_client.get_status.return_value = SYNC_REPORT_EMPTY

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield MagicMock()

    mock_get_client.side_effect = fake_get_client

    with patch.object(ProjectClient, "get_status", mock_project_client.get_status):
        result = runner.invoke(cli_app, ["status", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert data["total"] == 0
    assert data["new"] == []
    assert data["modified"] == []


@patch("basic_memory.cli.commands.status.ConfigManager")
@patch("basic_memory.cli.commands.status.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.get_client")
def test_status_json_with_skipped_files(mock_get_client, mock_get_active, mock_config_cls):
    """bm status --json serializes skipped_files with datetime fields."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM

    mock_project_client = AsyncMock()
    mock_project_client.get_status.return_value = SYNC_REPORT_WITH_SKIPPED

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield MagicMock()

    mock_get_client.side_effect = fake_get_client

    with patch.object(ProjectClient, "get_status", mock_project_client.get_status):
        result = runner.invoke(cli_app, ["status", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert len(data["skipped_files"]) == 1
    assert data["skipped_files"][0]["path"] == "bad/file.md"
    # datetime should be serialized as ISO string via mode="json"
    assert "2025-06-15" in data["skipped_files"][0]["first_failed"]


# ---------------------------------------------------------------------------
# Status --wait
#
# Real watch/sync timing is nondeterministic (filesystem events + background
# indexing), so these tests mock ProjectClient.get_status to drive deterministic
# poll sequences and patch asyncio.sleep to a no-op to avoid wall-clock waits.
# ---------------------------------------------------------------------------


@patch("basic_memory.cli.commands.status.asyncio.sleep", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.ConfigManager")
@patch("basic_memory.cli.commands.status.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.get_client")
def test_status_wait_succeeds_after_polling(
    mock_get_client, mock_get_active, mock_config_cls, mock_sleep
):
    """bm status --wait polls until total == 0, then exits 0."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM

    # First poll reports pending changes, second reports a synced project.
    get_status = AsyncMock(side_effect=[SYNC_REPORT_WITH_CHANGES, SYNC_REPORT_EMPTY])

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield MagicMock()

    mock_get_client.side_effect = fake_get_client

    with patch.object(ProjectClient, "get_status", get_status):
        result = runner.invoke(cli_app, ["status", "--wait"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Polled twice: pending -> empty.
    assert get_status.await_count == 2
    # Slept once between the two polls.
    assert mock_sleep.await_count == 1


@patch("basic_memory.cli.commands.status.asyncio.sleep", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.ConfigManager")
@patch("basic_memory.cli.commands.status.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.get_client")
def test_status_wait_times_out(mock_get_client, mock_get_active, mock_config_cls, mock_sleep):
    """bm status --wait exits 1 with a timeout message when never synced."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM

    # Always pending: --wait should hit the deadline and fail.
    get_status = AsyncMock(return_value=SYNC_REPORT_WITH_CHANGES)

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield MagicMock()

    mock_get_client.side_effect = fake_get_client

    # timeout=0 makes the deadline immediate: poll once, then time out.
    with patch.object(ProjectClient, "get_status", get_status):
        result = runner.invoke(cli_app, ["status", "--wait", "--timeout", "0"])

    assert result.exit_code == 1
    assert "Timed out" in result.output


def test_status_wait_negative_timeout_is_rejected():
    """A negative --timeout fails fast with a usage error instead of a confusing
    'Timed out after -5s' message. The guard runs before any client I/O, no mocks needed."""
    result = runner.invoke(cli_app, ["status", "--wait", "--timeout", "-5"])

    assert result.exit_code != 0
    # Typer colorizes the flag name with ANSI codes (so the literal "--timeout" is split),
    # but the message body renders clean — assert on that.
    assert "must be >= 0" in result.output


@patch("basic_memory.cli.commands.status.asyncio.sleep", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.ConfigManager")
@patch("basic_memory.cli.commands.status.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.get_client")
def test_status_wait_json_reports_total_zero(
    mock_get_client, mock_get_active, mock_config_cls, mock_sleep
):
    """bm status --wait --json emits total: 0 once indexing completes."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM

    get_status = AsyncMock(side_effect=[SYNC_REPORT_WITH_CHANGES, SYNC_REPORT_EMPTY])

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield MagicMock()

    mock_get_client.side_effect = fake_get_client

    with patch.object(ProjectClient, "get_status", get_status):
        result = runner.invoke(cli_app, ["status", "--wait", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert data["total"] == 0


@patch("basic_memory.cli.commands.status.asyncio.sleep", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.ConfigManager")
@patch("basic_memory.cli.commands.status.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.status.get_client")
def test_status_wait_json_timeout_emits_error(
    mock_get_client, mock_get_active, mock_config_cls, mock_sleep
):
    """bm status --wait --json on timeout emits a JSON error and exits 1."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_get_active.return_value = _MOCK_PROJECT_ITEM

    get_status = AsyncMock(return_value=SYNC_REPORT_WITH_CHANGES)

    @asynccontextmanager
    async def fake_get_client(project_name=None):
        yield MagicMock()

    mock_get_client.side_effect = fake_get_client

    with patch.object(ProjectClient, "get_status", get_status):
        result = runner.invoke(cli_app, ["status", "--wait", "--timeout", "0", "--json"])

    assert result.exit_code == 1
    data = _parse_json_output(result.output)
    assert "error" in data
    assert "Timed out" in data["error"]


# ---------------------------------------------------------------------------
# Schema validate --json
# ---------------------------------------------------------------------------


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_schema_validate_json(mock_mcp, mock_config_cls):
    """bm schema validate person --json outputs the validation report as JSON."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "validate", "person", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert data["note_type"] == "person"
    assert data["total_notes"] == 2
    assert len(data["results"]) == 2


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value={"error": "No schema found for type 'person'"},
)
def test_schema_validate_json_error(mock_mcp, mock_config_cls):
    """bm schema validate --json with error dict outputs the error as JSON."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "validate", "person", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert "error" in data


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_schema_validate_json_strict_exit(mock_mcp, mock_config_cls):
    """bm schema validate --json --strict exits 1 when errors present."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "validate", "person", "--json", "--strict"])

    assert result.exit_code == 1
    # JSON should still be valid in stdout
    data = _parse_json_output(result.output)
    assert data["error_count"] == 1


# ---------------------------------------------------------------------------
# Schema infer --json
# ---------------------------------------------------------------------------


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.mcp.tools.schema_infer",
    new_callable=AsyncMock,
    return_value=INFER_REPORT,
)
def test_schema_infer_json(mock_mcp, mock_config_cls):
    """bm schema infer person --json outputs the inference report as JSON."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "infer", "person", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert data["note_type"] == "person"
    assert data["notes_analyzed"] == 5
    assert "suggested_schema" in data


# ---------------------------------------------------------------------------
# Schema diff --json
# ---------------------------------------------------------------------------


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.mcp.tools.schema_diff",
    new_callable=AsyncMock,
    return_value=DIFF_REPORT_WITH_DRIFT,
)
def test_schema_diff_json(mock_mcp, mock_config_cls):
    """bm schema diff person --json outputs the drift report as JSON."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "diff", "person", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert data["note_type"] == "person"
    assert len(data["new_fields"]) == 1
    assert len(data["dropped_fields"]) == 1


# ---------------------------------------------------------------------------
# Project list --json
# ---------------------------------------------------------------------------


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write config.json under a temporary HOME and return the file path."""

    def _write(config_data: dict):
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))
        return config_file

    return _write


@pytest.fixture
def mock_client(monkeypatch):
    """Mock get_client with a no-op async context manager."""

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield object()

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)


def test_project_list_json_outputs_projects(write_config, mock_client, tmp_path, monkeypatch):
    """project list --json --local outputs structured JSON with project data."""
    alpha_local = (tmp_path / "alpha-local").as_posix()

    write_config(
        {
            "env": "dev",
            "projects": {
                "alpha": {"path": alpha_local, "mode": "local"},
            },
            "default_project": "alpha",
        }
    )

    local_payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "alpha",
                "path": alpha_local,
                "is_default": True,
            }
        ],
        "default_project": "alpha",
    }

    async def fake_list_projects(self):
        return ProjectList.model_validate(local_payload)

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(cli_app, ["project", "list", "--json", "--local"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = _parse_json_output(result.output)
    assert "projects" in data
    assert len(data["projects"]) == 1
    proj = data["projects"][0]
    assert proj["name"] == "alpha"
    assert proj["is_default"] is True
    assert "local_path" in proj
    assert "cli_route" in proj
    assert "mcp_stdio" in proj
