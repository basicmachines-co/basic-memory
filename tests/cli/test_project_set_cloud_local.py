"""Tests for bm project set-cloud and bm project set-local commands."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the commands module registers the project subcommands with the app
import basic_memory.cli.commands.project  # noqa: F401


def _workspace(
    *,
    tenant_id: str,
    workspace_type: str,
    name: str,
    role: str,
    slug: str | None = None,
    is_default: bool = False,
):
    from basic_memory.schemas.cloud import WorkspaceInfo

    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type=workspace_type,
        slug=slug or name.casefold().replace(" ", "-"),
        name=name,
        role=role,
        is_default=is_default,
    )


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Create a mock config with projects for testing set-cloud/set-local."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    config_data = {
        "env": "dev",
        "projects": {
            "main": {"path": str(tmp_path / "main")},
            "research": {"path": str(tmp_path / "research")},
        },
        "default_project": "main",
        "cloud_api_key": "bmc_test_key_123",
    }

    config_file.write_text(json.dumps(config_data, indent=2))

    monkeypatch.setenv("HOME", str(tmp_path))

    yield config_file


class TestSetCloud:
    """Tests for bm project set-cloud command."""

    def test_set_cloud_success(self, runner, mock_config):
        """Test setting a project to cloud mode."""
        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 0
        assert "cloud mode" in result.stdout.lower()

        # Verify config was updated
        config_data = json.loads(mock_config.read_text())
        assert config_data["projects"]["research"]["mode"] == "cloud"

    def test_set_cloud_nonexistent_project(self, runner, mock_config):
        """Test set-cloud with a project that doesn't exist in config."""
        result = runner.invoke(app, ["project", "set-cloud", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_set_cloud_no_credentials(self, runner, tmp_path, monkeypatch):
        """Test set-cloud when neither API key nor OAuth session is available."""
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"

        # Config without cloud_api_key
        config_data = {
            "env": "dev",
            "projects": {"research": {"path": str(tmp_path / "research")}},
            "default_project": "research",
        }
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))

        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 1
        assert "no cloud credentials" in result.stdout.lower()

    def test_set_cloud_with_oauth_session(self, runner, tmp_path, monkeypatch):
        """Test set-cloud succeeds with OAuth token but no API key."""
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"

        # Config without cloud_api_key but with a project
        config_data = {
            "env": "dev",
            "projects": {"research": {"path": str(tmp_path / "research")}},
            "default_project": "research",
        }
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))

        # Write OAuth token file so CLIAuth.load_tokens() returns something
        token_file = config_dir / "basic-memory-cloud.json"
        token_data = {
            "access_token": "oauth-token-789",
            "refresh_token": None,
            "expires_at": 9999999999,
            "token_type": "Bearer",
        }
        token_file.write_text(json.dumps(token_data, indent=2))

        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 0
        assert "cloud mode" in result.stdout.lower()

        # Verify config was updated
        config_data = json.loads(config_file.read_text())
        assert config_data["projects"]["research"]["mode"] == "cloud"


class TestSetLocal:
    """Tests for bm project set-local command."""

    def test_set_local_success(self, runner, mock_config, tmp_path):
        """Test reverting a project to local mode."""
        # First set to cloud (clears the local path as part of the cutover)
        runner.invoke(app, ["project", "set-cloud", "research"])
        config_data = json.loads(mock_config.read_text())
        assert config_data["projects"]["research"]["mode"] == "cloud"

        # Now set back to local — must supply a path since set-cloud blanked it
        new_path = tmp_path / "research"
        result = runner.invoke(
            app, ["project", "set-local", "research", "--local-path", str(new_path)]
        )
        assert result.exit_code == 0
        assert "local mode" in result.stdout.lower()

        # Verify config was updated — mode reset to local, path restored.
        # set-local normalizes the path via Path.as_posix(), matching the
        # convention used by `bm project add`. On Windows that means
        # backslashes are converted to forward slashes.
        config_data = json.loads(mock_config.read_text())
        assert config_data["projects"]["research"]["mode"] == "local"
        assert config_data["projects"]["research"]["path"] == new_path.as_posix()

    def test_set_local_nonexistent_project(self, runner, mock_config):
        """Test set-local with a project that doesn't exist in config."""
        result = runner.invoke(app, ["project", "set-local", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_set_local_already_local(self, runner, mock_config):
        """Test set-local on a project that's already local (reuses existing path)."""
        result = runner.invoke(app, ["project", "set-local", "main"])
        assert result.exit_code == 0
        assert "local mode" in result.stdout.lower()

    def test_set_local_requires_path_after_set_cloud(self, runner, mock_config):
        """Regression for #680: after set-cloud blanks the path, set-local must
        refuse to silently default — the user has to specify where the project
        lives now."""
        runner.invoke(app, ["project", "set-cloud", "research"])

        # No --local-path; config no longer has a path either.
        result = runner.invoke(app, ["project", "set-local", "research"])
        assert result.exit_code == 1
        assert "--local-path" in result.stdout

    def test_set_local_clears_workspace_id(self, runner, mock_config, tmp_path):
        """Test that set-local clears workspace_id from the project entry."""
        from basic_memory import config as config_module

        # Manually set workspace_id on the project
        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None
        config_data = json.loads(mock_config.read_text())
        config_data["projects"]["research"]["mode"] = "cloud"
        config_data["projects"]["research"]["workspace_id"] = "11111111-1111-1111-1111-111111111111"
        mock_config.write_text(json.dumps(config_data, indent=2))
        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        # Set back to local — supply --local-path; existing config path is preserved
        # in this test setup, but new behavior recommends explicit path passing.
        new_path = tmp_path / "research"
        result = runner.invoke(
            app, ["project", "set-local", "research", "--local-path", str(new_path)]
        )
        assert result.exit_code == 0

        # Verify workspace_id was cleared
        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None
        updated_data = json.loads(mock_config.read_text())
        assert updated_data["projects"]["research"]["workspace_id"] is None
        assert updated_data["projects"]["research"]["mode"] == "local"

    def test_set_local_update_path_when_row_exists(self, runner, mock_config, tmp_path):
        """Re-running set-local with a different --local-path must update the
        existing DB row (covers the repo.update_path() branch in
        _attach_local_project_row)."""
        path_a = tmp_path / "research_v1"
        path_b = tmp_path / "research_v2"

        # First call seeds the DB row at path_a.
        result_a = runner.invoke(
            app, ["project", "set-local", "research", "--local-path", str(path_a)]
        )
        assert result_a.exit_code == 0

        # Second call must update the existing row's path to path_b.
        result_b = runner.invoke(
            app, ["project", "set-local", "research", "--local-path", str(path_b)]
        )
        assert result_b.exit_code == 0

        updated = json.loads(mock_config.read_text())
        assert updated["projects"]["research"]["path"] == path_b.as_posix()


class TestSetCloudCutover:
    """Regression tests for #680 — set-cloud as a one-way cutover."""

    def test_set_cloud_clears_path(self, runner, mock_config):
        """After set-cloud, config.projects[name].path must be blanked so the
        merged project list reports source: cloud (not local+cloud)."""
        runner.invoke(app, ["project", "set-cloud", "research"])
        updated = json.loads(mock_config.read_text())
        assert updated["projects"]["research"]["mode"] == "cloud"
        assert updated["projects"]["research"]["path"] == ""

    def test_set_cloud_removes_existing_db_row(self, runner, mock_config, tmp_path):
        """When set-cloud runs against a project with a DB row, the row must
        be removed and the user-facing message must mention the cleanup
        (covers the repo.delete() → return True branch in
        _detach_local_project_row)."""
        # Seed a DB row first by going through set-local.
        research_path = tmp_path / "research"
        seed = runner.invoke(
            app, ["project", "set-local", "research", "--local-path", str(research_path)]
        )
        assert seed.exit_code == 0

        # Now flip to cloud — _detach_local_project_row should find and drop the row.
        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 0
        assert "local index entry removed" in result.stdout.lower()


class TestSetCloudWithWorkspace:
    """Tests for 'bm project set-cloud --workspace' option."""

    def test_set_cloud_with_workspace_stores_workspace_id(self, runner, mock_config, monkeypatch):
        """Test that --workspace resolves to tenant_id and stores it."""
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        async def fake_get_available_workspaces():
            return [
                _workspace(
                    tenant_id="11111111-1111-1111-1111-111111111111",
                    workspace_type="personal",
                    slug="personal",
                    name="Personal",
                    role="owner",
                    is_default=True,
                ),
            ]

        monkeypatch.setattr(
            "basic_memory.mcp.project_context.get_available_workspaces",
            fake_get_available_workspaces,
        )

        result = runner.invoke(app, ["project", "set-cloud", "research", "--workspace", "Personal"])
        assert result.exit_code == 0
        assert "cloud mode" in result.stdout.lower()
        assert "11111111-1111-1111-1111-111111111111" in result.stdout

        # Verify workspace_id was persisted
        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None
        updated_data = json.loads(mock_config.read_text())
        assert (
            updated_data["projects"]["research"]["workspace_id"]
            == "11111111-1111-1111-1111-111111111111"
        )

    def test_set_cloud_with_workspace_not_found(self, runner, mock_config, monkeypatch):
        """Test --workspace with unknown workspace name."""
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        async def fake_get_available_workspaces():
            return [
                _workspace(
                    tenant_id="11111111-1111-1111-1111-111111111111",
                    workspace_type="personal",
                    slug="personal",
                    name="Personal",
                    role="owner",
                    is_default=True,
                ),
            ]

        monkeypatch.setattr(
            "basic_memory.mcp.project_context.get_available_workspaces",
            fake_get_available_workspaces,
        )

        result = runner.invoke(
            app, ["project", "set-cloud", "research", "--workspace", "Nonexistent"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_set_cloud_uses_default_workspace_when_no_flag(self, runner, mock_config, monkeypatch):
        """Test that set-cloud uses default_workspace when --workspace is not passed."""
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        # Set default_workspace in config
        config_data = json.loads(mock_config.read_text())
        config_data["default_workspace"] = "global-default-tenant-id"
        mock_config.write_text(json.dumps(config_data, indent=2))
        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 0

        # Verify workspace_id was set from default
        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None
        updated_data = json.loads(mock_config.read_text())
        assert updated_data["projects"]["research"]["workspace_id"] == "global-default-tenant-id"
