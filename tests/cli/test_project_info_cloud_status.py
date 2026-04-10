"""Tests for cloud index status in `bm project info`."""

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import typer
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import CloudAPIError
from basic_memory.schemas.cloud import CloudProjectIndexStatus, WorkspaceInfo
from basic_memory.schemas.project_info import (
    ActivityMetrics,
    EmbeddingStatus,
    ProjectInfoResponse,
    ProjectStatistics,
    SystemStatus,
)

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write config.json under a temporary HOME and return the file path."""
    from basic_memory import config as config_module

    def _write(config_data: dict) -> Path:
        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
        monkeypatch.setenv("HOME", str(tmp_path))
        return config_file

    return _write


def _project_info(project_name: str = "demo") -> ProjectInfoResponse:
    return ProjectInfoResponse(
        project_name=project_name,
        project_path=f"/tmp/{project_name}",
        available_projects={
            project_name: {
                "path": f"/tmp/{project_name}",
                "active": True,
                "id": 1,
                "is_default": True,
                "permalink": project_name,
            }
        },
        default_project=project_name,
        statistics=ProjectStatistics(
            total_entities=10,
            total_observations=20,
            total_relations=5,
            total_unresolved_relations=1,
            note_types={"note": 10},
            observation_categories={"fact": 20},
            relation_types={"relates_to": 5},
            most_connected_entities=[],
            isolated_entities=2,
        ),
        activity=ActivityMetrics(
            recently_created=[],
            recently_updated=[],
            monthly_growth={},
        ),
        system=SystemStatus(
            version="0.0.0-test",
            database_path="/tmp/memory.db",
            database_size="1.00 MB",
            watch_status=None,
            timestamp=datetime(2026, 4, 9, 12, 0, 0),
        ),
        embedding_status=EmbeddingStatus(
            semantic_search_enabled=True,
            embedding_provider="fastembed",
            embedding_model="bge-small-en-v1.5",
            total_indexed_entities=10,
            total_entities_with_chunks=10,
            total_chunks=30,
            total_embeddings=30,
            vector_tables_exist=True,
            reindex_recommended=False,
            reindex_reason=None,
        ),
    )


def _cloud_index_status(
    *,
    project_name: str = "demo",
    reindex_recommended: bool = False,
    reindex_reason: str | None = None,
) -> CloudProjectIndexStatus:
    return CloudProjectIndexStatus(
        project_name=project_name,
        project_id=1,
        last_scan_timestamp=1234.5,
        last_file_count=12,
        current_file_count=12,
        total_entities=12,
        total_note_content_rows=12,
        note_content_synced=11,
        note_content_pending=1,
        note_content_failed=0,
        note_content_external_changes=0,
        total_indexed_entities=10,
        embedding_opt_out_entities=2,
        embeddable_indexed_entities=8,
        total_entities_with_chunks=7,
        total_chunks=21,
        total_embeddings=21,
        orphaned_chunks=0,
        vector_tables_exist=True,
        materialization_current=False,
        search_current=False,
        embeddings_current=False,
        project_current=not reindex_recommended,
        reindex_recommended=reindex_recommended,
        reindex_reason=reindex_reason,
    )


def test_project_info_local_output_is_unchanged(runner: CliRunner, write_config, monkeypatch):
    """Local project info should not attempt cloud augmentation."""
    write_config(
        {
            "env": "dev",
            "projects": {"demo": {"path": "/tmp/demo", "mode": "local"}},
            "default_project": "demo",
        }
    )

    async def fake_get_project_info(_project_name: str) -> ProjectInfoResponse:
        return _project_info()

    def fail_if_called(_project_name: str):
        raise AssertionError("cloud index status should not be fetched for local projects")

    monkeypatch.setattr(project_cmd, "get_project_info", fake_get_project_info)
    monkeypatch.setattr(project_cmd, "_load_cloud_project_index_status", fail_if_called)

    result = runner.invoke(app, ["project", "info", "demo"], env={"COLUMNS": "240"})

    assert result.exit_code == 0
    assert "Knowledge Graph" in result.stdout
    assert "Cloud Index Status" not in result.stdout


def test_project_info_cloud_output_includes_index_status(
    runner: CliRunner, write_config, monkeypatch
):
    """Cloud project info should render the extra Cloud Index Status block."""
    write_config(
        {
            "env": "dev",
            "projects": {
                "demo": {
                    "path": "/tmp/demo",
                    "mode": "cloud",
                    "workspace_id": "11111111-1111-1111-1111-111111111111",
                }
            },
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    async def fake_get_project_info(_project_name: str) -> ProjectInfoResponse:
        return _project_info()

    def fake_load_cloud_project_index_status(_project_name: str):
        return (
            _cloud_index_status(
                reindex_recommended=True,
                reindex_reason="Search index coverage does not match the current file count",
            ),
            None,
        )

    monkeypatch.setattr(project_cmd, "get_project_info", fake_get_project_info)
    monkeypatch.setattr(
        project_cmd, "_load_cloud_project_index_status", fake_load_cloud_project_index_status
    )

    result = runner.invoke(app, ["project", "info", "demo"], env={"COLUMNS": "240"})

    assert result.exit_code == 0
    assert "Cloud Index Status" in result.stdout
    assert "Files" in result.stdout
    assert "12" in result.stdout
    assert "Note content" in result.stdout
    assert "11/12" in result.stdout
    assert "Search" in result.stdout
    assert "10/12" in result.stdout
    assert "Embeddable" in result.stdout
    assert "8" in result.stdout
    assert "Vectorized" in result.stdout
    assert "7/8" in result.stdout
    assert "Reindex recommended" in result.stdout
    assert "Search index coverage does not match the current file count" in result.stdout


def test_project_info_cloud_output_warns_when_index_lookup_fails(
    runner: CliRunner, write_config, monkeypatch
):
    """Cloud project info should keep rendering when the admin lookup fails."""
    write_config(
        {
            "env": "dev",
            "projects": {
                "demo": {
                    "path": "/tmp/demo",
                    "mode": "cloud",
                    "workspace_id": "11111111-1111-1111-1111-111111111111",
                }
            },
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    async def fake_get_project_info(_project_name: str) -> ProjectInfoResponse:
        return _project_info()

    def fake_load_cloud_project_index_status(_project_name: str):
        return None, "HTTP 503: index-status endpoint unavailable"

    monkeypatch.setattr(project_cmd, "get_project_info", fake_get_project_info)
    monkeypatch.setattr(
        project_cmd, "_load_cloud_project_index_status", fake_load_cloud_project_index_status
    )

    result = runner.invoke(app, ["project", "info", "demo"], env={"COLUMNS": "240"})

    assert result.exit_code == 0
    assert "Knowledge Graph" in result.stdout
    assert "Cloud Index Status" in result.stdout
    assert "Warning" in result.stdout
    assert "HTTP 503: index-status endpoint unavailable" in result.stdout


def test_project_info_json_includes_cloud_index_status(
    runner: CliRunner, write_config, monkeypatch
):
    """JSON output should include the matched cloud index status block."""
    write_config(
        {
            "env": "dev",
            "projects": {
                "demo": {
                    "path": "/tmp/demo",
                    "mode": "cloud",
                    "workspace_id": "11111111-1111-1111-1111-111111111111",
                }
            },
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    async def fake_get_project_info(_project_name: str) -> ProjectInfoResponse:
        return _project_info()

    def fake_load_cloud_project_index_status(_project_name: str):
        return _cloud_index_status(), None

    monkeypatch.setattr(project_cmd, "get_project_info", fake_get_project_info)
    monkeypatch.setattr(
        project_cmd, "_load_cloud_project_index_status", fake_load_cloud_project_index_status
    )

    result = runner.invoke(app, ["project", "info", "demo", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["project_name"] == "demo"
    assert data["cloud_index_status"]["project_name"] == "demo"
    assert data["cloud_index_status"]["current_file_count"] == 12
    assert data["cloud_index_status"]["note_content_synced"] == 11
    assert data["cloud_index_status"]["embeddable_indexed_entities"] == 8
    assert data["cloud_index_status"]["total_entities_with_chunks"] == 7
    assert data["cloud_index_status_error"] is None


def test_project_info_json_includes_cloud_error_when_lookup_fails(
    runner: CliRunner, write_config, monkeypatch
):
    """JSON output should preserve project info when the cloud status lookup fails."""
    write_config(
        {
            "env": "dev",
            "projects": {
                "demo": {
                    "path": "/tmp/demo",
                    "mode": "cloud",
                    "workspace_id": "11111111-1111-1111-1111-111111111111",
                }
            },
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    async def fake_get_project_info(_project_name: str) -> ProjectInfoResponse:
        return _project_info()

    def fake_load_cloud_project_index_status(_project_name: str):
        return None, "HTTP 503: index-status endpoint unavailable"

    monkeypatch.setattr(project_cmd, "get_project_info", fake_get_project_info)
    monkeypatch.setattr(
        project_cmd, "_load_cloud_project_index_status", fake_load_cloud_project_index_status
    )

    result = runner.invoke(app, ["project", "info", "demo", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["project_name"] == "demo"
    assert data["cloud_index_status"] is None
    assert data["cloud_index_status_error"] == "HTTP 503: index-status endpoint unavailable"


def test_uses_cloud_project_info_route_respects_flags_and_project_mode(write_config, monkeypatch):
    """Route detection should stay local unless flags or cloud mode require augmentation."""
    write_config(
        {
            "env": "dev",
            "projects": {
                "local-demo": {"path": "/tmp/local-demo", "mode": "local"},
                "cloud-demo": {"path": "/tmp/cloud-demo", "mode": "cloud"},
            },
            "default_project": "local-demo",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    assert (
        project_cmd._uses_cloud_project_info_route("cloud-demo", local=False, cloud=False) is True
    )
    assert (
        project_cmd._uses_cloud_project_info_route("local-demo", local=False, cloud=False) is False
    )
    assert project_cmd._uses_cloud_project_info_route("local-demo", local=False, cloud=True) is True
    assert (
        project_cmd._uses_cloud_project_info_route("cloud-demo", local=True, cloud=False) is False
    )


def test_resolve_cloud_status_workspace_id_prefers_project_workspace(write_config):
    """Cloud status lookup should use the project workspace before any fallback lookup."""
    write_config(
        {
            "env": "dev",
            "projects": {
                "demo": {
                    "path": "/tmp/demo",
                    "mode": "cloud",
                    "workspace_id": "project-workspace",
                }
            },
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
            "default_workspace": "default-workspace",
        }
    )

    assert project_cmd._resolve_cloud_status_workspace_id("demo") == "project-workspace"


def test_resolve_cloud_status_workspace_id_uses_fallback_resolution(write_config, monkeypatch):
    """Cloud status lookup should fall back to workspace discovery when config has no workspace."""
    write_config(
        {
            "env": "dev",
            "projects": {"demo": {"path": "/tmp/demo", "mode": "cloud"}},
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    monkeypatch.setattr(
        project_cmd, "_resolve_workspace_id", lambda _config, _workspace: "resolved"
    )

    assert project_cmd._resolve_cloud_status_workspace_id("demo") == "resolved"


def test_resolve_cloud_status_workspace_id_requires_credentials(write_config):
    """Cloud status lookup should fail fast when no credentials are available."""
    write_config(
        {
            "env": "dev",
            "projects": {"demo": {"path": "/tmp/demo", "mode": "cloud"}},
            "default_project": "demo",
        }
    )

    with pytest.raises(RuntimeError, match="Cloud credentials not found"):
        project_cmd._resolve_cloud_status_workspace_id("demo")


@pytest.mark.asyncio
async def test_resolve_cloud_status_workspace_id_async_auto_discovers_single_workspace(
    write_config, monkeypatch
):
    """Async cloud status lookup should auto-select a single available workspace."""
    write_config(
        {
            "env": "dev",
            "projects": {"demo": {"path": "/tmp/demo", "mode": "cloud"}},
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    async def fake_get_available_workspaces():
        return [
            WorkspaceInfo(
                tenant_id="11111111-1111-1111-1111-111111111111",
                workspace_type="personal",
                name="Personal",
                role="owner",
            )
        ]

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    workspace_id = await project_cmd._resolve_cloud_status_workspace_id_async("demo")

    assert workspace_id == "11111111-1111-1111-1111-111111111111"


def test_match_cloud_index_status_project_prefers_exact_then_permalink():
    """Project matching should use exact names first, then a unique permalink match."""
    exact = _cloud_index_status(project_name="Demo Project")
    permalink_match = _cloud_index_status(project_name="demo-project")
    unrelated = _cloud_index_status(project_name="other")

    assert (
        project_cmd._match_cloud_index_status_project("Demo Project", [exact, unrelated]) is exact
    )
    assert (
        project_cmd._match_cloud_index_status_project("Demo Project", [permalink_match, unrelated])
        is permalink_match
    )
    assert (
        project_cmd._match_cloud_index_status_project(
            "Demo Project",
            [permalink_match, _cloud_index_status(project_name="Demo Project!!")],
        )
        is None
    )


def test_format_cloud_index_status_error_prefers_cloud_api_detail():
    """Cloud API errors should surface the most useful available detail."""
    assert project_cmd._format_cloud_index_status_error(RuntimeError("boom")) == "boom"
    assert (
        project_cmd._format_cloud_index_status_error(
            CloudAPIError("fail", status_code=503, detail={"detail": "down"})
        )
        == "HTTP 503: down"
    )
    assert (
        project_cmd._format_cloud_index_status_error(
            CloudAPIError("fail", status_code=503, detail={"detail": {"message": "nested"}})
        )
        == "HTTP 503: nested"
    )
    assert (
        project_cmd._format_cloud_index_status_error(
            CloudAPIError("fail", status_code=503, detail={"detail": {"detail": "nested-detail"}})
        )
        == "HTTP 503: nested-detail"
    )
    assert (
        project_cmd._format_cloud_index_status_error(CloudAPIError("fail", status_code=503))
        == "HTTP 503"
    )


@pytest.mark.asyncio
async def test_fetch_cloud_project_index_status_returns_matching_project(write_config, monkeypatch):
    """Cloud index status fetch should validate the tenant payload and return the matched project."""
    write_config(
        {
            "env": "dev",
            "projects": {"demo": {"path": "/tmp/demo", "mode": "cloud"}},
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
            "cloud_host": "https://cloud.example.test",
        }
    )

    async def fake_resolve_workspace(_project_name: str) -> str:
        return "11111111-1111-1111-1111-111111111111"

    monkeypatch.setattr(
        project_cmd, "_resolve_cloud_status_workspace_id_async", fake_resolve_workspace
    )

    async def fake_make_api_request(**kwargs):
        assert kwargs["method"] == "GET"
        assert (
            kwargs["url"]
            == "https://cloud.example.test/admin/tenants/11111111-1111-1111-1111-111111111111/index-status"
        )
        return httpx.Response(
            200,
            json={
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "fly_app_name": "demo-app",
                "email": "demo@example.com",
                "projects": [_cloud_index_status().model_dump()],
                "error": None,
            },
        )

    monkeypatch.setattr(project_cmd, "make_api_request", fake_make_api_request)

    status = await project_cmd._fetch_cloud_project_index_status("demo")

    assert status.project_name == "demo"
    assert status.current_file_count == 12


@pytest.mark.asyncio
async def test_fetch_cloud_project_index_status_handles_exit_and_missing_project(
    write_config, monkeypatch
):
    """Cloud fetch should convert auth exits and fail when the project is missing."""
    write_config(
        {
            "env": "dev",
            "projects": {"demo": {"path": "/tmp/demo", "mode": "cloud"}},
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
            "cloud_host": "https://cloud.example.test",
        }
    )

    async def fake_resolve_workspace(_project_name: str) -> str:
        return "tenant-1"

    monkeypatch.setattr(
        project_cmd, "_resolve_cloud_status_workspace_id_async", fake_resolve_workspace
    )

    async def fake_make_api_request_exit(**_kwargs):
        raise typer.Exit(1)

    monkeypatch.setattr(project_cmd, "make_api_request", fake_make_api_request_exit)

    with pytest.raises(RuntimeError, match="Cloud credentials not found"):
        await project_cmd._fetch_cloud_project_index_status("demo")

    async def fake_make_api_request_missing(**_kwargs):
        return httpx.Response(
            200,
            json={
                "tenant_id": "tenant-1",
                "fly_app_name": "demo-app",
                "email": "demo@example.com",
                "projects": [_cloud_index_status(project_name="other").model_dump()],
                "error": None,
            },
        )

    monkeypatch.setattr(project_cmd, "make_api_request", fake_make_api_request_missing)

    with pytest.raises(RuntimeError, match="was not found in workspace index status"):
        await project_cmd._fetch_cloud_project_index_status("demo")


@pytest.mark.asyncio
async def test_fetch_cloud_project_index_status_preserves_successful_exit_and_tenant_error(
    write_config, monkeypatch
):
    """Only non-zero typer exits should be converted; tenant-level errors should bubble clearly."""
    write_config(
        {
            "env": "dev",
            "projects": {"demo": {"path": "/tmp/demo", "mode": "cloud"}},
            "default_project": "demo",
            "cloud_api_key": "bmc_test_key_123",
            "cloud_host": "https://cloud.example.test",
        }
    )

    async def fake_resolve_workspace(_project_name: str) -> str:
        return "tenant-1"

    monkeypatch.setattr(
        project_cmd, "_resolve_cloud_status_workspace_id_async", fake_resolve_workspace
    )

    async def fake_make_api_request_success_exit(**_kwargs):
        raise typer.Exit(0)

    monkeypatch.setattr(project_cmd, "make_api_request", fake_make_api_request_success_exit)

    with pytest.raises(typer.Exit) as exc_info:
        await project_cmd._fetch_cloud_project_index_status("demo")
    assert exc_info.value.exit_code == 0

    async def fake_make_api_request_tenant_error(**_kwargs):
        return httpx.Response(
            200,
            json={
                "tenant_id": "tenant-1",
                "fly_app_name": "demo-app",
                "email": "demo@example.com",
                "projects": [],
                "error": "tenant is unavailable",
            },
        )

    monkeypatch.setattr(project_cmd, "make_api_request", fake_make_api_request_tenant_error)

    with pytest.raises(RuntimeError, match="tenant is unavailable"):
        await project_cmd._fetch_cloud_project_index_status("demo")


def test_build_cloud_index_status_section_handles_missing_status():
    """The renderer should return a safe header-only table if invariants are broken."""
    table = project_cmd._build_cloud_index_status_section(None, None)
    assert table is None

    warning_table = project_cmd._build_cloud_index_status_section(
        None, "HTTP 503: index-status endpoint unavailable"
    )
    assert warning_table is not None
