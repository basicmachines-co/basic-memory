"""Tests for cloud sync and bisync command behavior."""

import importlib
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.rclone_commands import TransferPlan
from basic_memory.config import ProjectEntry, ProjectMode
from basic_memory.schemas.cloud import WorkspaceInfo

runner = CliRunner()


@pytest.mark.parametrize(
    "argv",
    [
        ["cloud", "sync", "--name", "research"],
        ["cloud", "bisync", "--name", "research"],
        # --project is an alias for --name (issue #817)
        ["cloud", "sync", "--project", "research"],
        ["cloud", "bisync", "--project", "research"],
    ],
)
def test_cloud_sync_commands_skip_explicit_cloud_project_sync(monkeypatch, argv, config_manager):
    """Cloud sync commands should not trigger an extra explicit cloud project sync."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(config)

    monkeypatch.setattr(project_sync_command, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(
        project_sync_command, "_require_personal_workspace", lambda _name, _config, **_kwargs: None
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: _async_value(SimpleNamespace(bucket_name="tenant-bucket")),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_cloud_project",
        lambda _name: _async_value(
            SimpleNamespace(name="research", external_id="external-project-id", path="research")
        ),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_sync_project",
        lambda _name, _config, _project_data: (SimpleNamespace(name="research"), "/tmp/research"),
    )
    monkeypatch.setattr(project_sync_command, "project_sync", lambda *args, **kwargs: True)
    monkeypatch.setattr(project_sync_command, "project_bisync", lambda *args, **kwargs: True)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    assert "Database sync initiated" not in result.output


def test_cloud_bisync_fails_fast_when_sync_entry_disappears(monkeypatch, config_manager):
    """Bisync should raise a runtime error when validated sync config vanishes before persistence."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.projects.pop("research", None)
    config_manager.save_config(config)

    monkeypatch.setattr(project_sync_command, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(
        project_sync_command, "_require_personal_workspace", lambda _name, _config, **_kwargs: None
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: _async_value(SimpleNamespace(bucket_name="tenant-bucket")),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_cloud_project",
        lambda _name: _async_value(
            SimpleNamespace(name="research", external_id="external-project-id", path="research")
        ),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_sync_project",
        lambda _name, _config, _project_data: (SimpleNamespace(name="research"), "/tmp/research"),
    )
    monkeypatch.setattr(project_sync_command, "project_bisync", lambda *args, **kwargs: True)

    result = runner.invoke(app, ["cloud", "bisync", "--name", "research"])

    assert result.exit_code == 1, result.output
    assert "unexpectedly missing after validation" in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["cloud", "bisync", "--name", "research"],
        ["cloud", "bisync-reset", "research"],
    ],
)
def test_cloud_bisync_commands_block_organization_workspace(monkeypatch, argv, config_manager):
    """Bisync commands should fail before setup/execution for Team workspaces."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test"
    config.projects["research"] = ProjectEntry(
        path="/tmp/research",
        mode=ProjectMode.CLOUD,
        workspace_id="team-tenant",
        local_sync_path="/tmp/research",
    )
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("team-tenant", "organization", "team")]),
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: pytest.fail("workspace guard should run before mount lookup"),
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_project_bisync_state",
        lambda _name: pytest.fail("workspace guard should run before bisync state lookup"),
    )

    result = runner.invoke(app, argv)

    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "The bisync operation is only supported on Personal workspaces" in output
    assert "bm cloud pull --name research" in output
    assert "bm cloud push --name research" in output


def test_cloud_sync_blocks_organization_workspace(monkeypatch, config_manager):
    """The destructive mirror `sync` is now Personal-only and blocks Team workspaces."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test"
    config.projects["research"] = ProjectEntry(
        path="/tmp/research",
        mode=ProjectMode.CLOUD,
        workspace_id="team-tenant",
        local_sync_path="/tmp/research",
    )
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("team-tenant", "organization", "team")]),
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: pytest.fail("workspace guard should run before mount lookup"),
    )

    result = runner.invoke(app, ["cloud", "sync", "--name", "research"])

    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "only supported on Personal workspaces" in output
    assert "bm cloud push --name research" in output
    assert "bm cloud pull --name research" in output


def test_cloud_sync_allows_personal_workspace(monkeypatch, config_manager):
    """Personal workspaces keep the one-way mirror sync available."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test"
    config.projects["research"] = ProjectEntry(
        path="/tmp/research",
        mode=ProjectMode.CLOUD,
        workspace_id="personal-tenant",
        local_sync_path="/tmp/research",
    )
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("personal-tenant", "personal", "personal")]),
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: _async_value(SimpleNamespace(bucket_name="tenant-bucket")),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_cloud_project",
        lambda _name: _async_value(
            SimpleNamespace(name="research", external_id="external-project-id", path="research")
        ),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_sync_project",
        lambda _name, _config, _project_data: (SimpleNamespace(name="research"), "/tmp/research"),
    )
    monkeypatch.setattr(project_sync_command, "project_sync", lambda *args, **kwargs: True)

    result = runner.invoke(app, ["cloud", "sync", "--name", "research"])

    assert result.exit_code == 0, result.output
    assert "research synced successfully" in result.output


def test_require_personal_workspace_allows_personal_workspace(monkeypatch, config_manager):
    """Personal workspaces keep the existing rclone sync path available."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.projects["research"] = ProjectEntry(
        path="/tmp/research",
        mode=ProjectMode.CLOUD,
        workspace_id="personal-tenant",
        local_sync_path="/tmp/research",
    )
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("personal-tenant", "personal", "personal")]),
    )

    workspace = project_sync_command._require_personal_workspace("research", config)

    assert workspace.tenant_id == "personal-tenant"


def test_require_personal_workspace_uses_default_workspace(monkeypatch, config_manager):
    """When no project workspace is set, the single cloud default is used."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.default_workspace = None
    config.projects["research"] = ProjectEntry(path="/tmp/research", mode=ProjectMode.CLOUD)
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value(
            [
                _workspace("team-tenant", "organization", "team"),
                _workspace("personal-tenant", "personal", "personal", is_default=True),
            ]
        ),
    )

    workspace = project_sync_command._require_personal_workspace("research", config)

    assert workspace.tenant_id == "personal-tenant"


def test_require_personal_workspace_uses_single_workspace(monkeypatch, config_manager):
    """A single accessible workspace is unambiguous even when none is marked default."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.default_workspace = None
    config.projects["research"] = ProjectEntry(path="/tmp/research", mode=ProjectMode.CLOUD)
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("personal-tenant", "personal", "personal")]),
    )

    workspace = project_sync_command._require_personal_workspace("research", config)

    assert workspace.tenant_id == "personal-tenant"


def test_require_personal_workspace_reports_no_accessible_workspaces(monkeypatch, config_manager):
    """Workspace resolution exits with a clear error when the account has no workspaces."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.default_workspace = None
    config.projects["research"] = ProjectEntry(path="/tmp/research", mode=ProjectMode.CLOUD)
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([]),
    )

    with pytest.raises(typer.Exit) as exc_info:
        project_sync_command._require_personal_workspace("research", config)

    assert exc_info.value.exit_code == 1


def test_require_personal_workspace_reports_inaccessible_configured_workspace(
    monkeypatch, config_manager
):
    """A configured workspace id must be present in the accessible workspace list."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.projects["research"] = ProjectEntry(
        path="/tmp/research",
        mode=ProjectMode.CLOUD,
        workspace_id="missing-tenant",
    )
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("personal-tenant", "personal", "personal")]),
    )

    with pytest.raises(typer.Exit) as exc_info:
        project_sync_command._require_personal_workspace("research", config)

    assert exc_info.value.exit_code == 1


def test_require_personal_workspace_reports_ambiguous_workspace(monkeypatch, config_manager):
    """Multiple accessible workspaces need an explicit project or account default."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.default_workspace = None
    config.projects["research"] = ProjectEntry(path="/tmp/research", mode=ProjectMode.CLOUD)
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value(
            [
                _workspace("personal-tenant-a", "personal", "personal-a"),
                _workspace("personal-tenant-b", "personal", "personal-b"),
            ]
        ),
    )

    with pytest.raises(typer.Exit) as exc_info:
        project_sync_command._require_personal_workspace("research", config)

    assert exc_info.value.exit_code == 1


def test_bisync_reset_skips_workspace_check_without_credentials(monkeypatch, tmp_path):
    """Resetting local bisync state stays harmless when no cloud credentials exist."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: pytest.fail("workspace lookup requires cloud credentials"),
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_project_bisync_state",
        lambda _name: tmp_path / "missing-state",
    )

    result = runner.invoke(app, ["cloud", "bisync-reset", "research"])

    assert result.exit_code == 0, result.output
    assert "No bisync state found for project 'research'" in result.output


def _stub_transfer_env(
    monkeypatch, module, *, plan, transfer_result=True, recorder=None, workspace=None
):
    """Stub the push/pull dependency chain so only diff/transfer logic is exercised."""
    monkeypatch.setattr(module, "_require_cloud_credentials", lambda _config: None)
    ws = workspace or _workspace("personal-tenant", "personal", "personal", is_default=True)
    monkeypatch.setattr(
        module,
        "_get_workspace_for_project",
        lambda _name, _config, **_kwargs: _async_value(ws),
    )
    monkeypatch.setattr(module, "rclone_remote_exists", lambda _remote: True)
    monkeypatch.setattr(
        module,
        "get_mount_info",
        lambda **_kwargs: _async_value(
            SimpleNamespace(bucket_name="tenant-bucket", tenant_id=ws.tenant_id)
        ),
    )
    monkeypatch.setattr(
        module,
        "_get_cloud_project",
        lambda _name, **_kwargs: _async_value(SimpleNamespace(name="research", path="research")),
    )
    monkeypatch.setattr(
        module,
        "_get_sync_project",
        lambda _name, _config, _project_data, **kwargs: (
            SimpleNamespace(name="research", remote_name=kwargs.get("remote_name")),
            "/tmp/research",
        ),
    )
    monkeypatch.setattr(module, "project_diff", lambda *args, **kwargs: plan)

    def _fake_transfer(*args, **kwargs):
        if recorder is not None:
            recorder["args"] = args
            recorder["kwargs"] = kwargs
        return transfer_result

    monkeypatch.setattr(module, "project_transfer", _fake_transfer)


def test_cloud_pull_aborts_on_conflict_by_default(monkeypatch, config_manager):
    """Pull refuses to clobber: it lists conflicts and exits without transferring."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    plan = TransferPlan(new=["new.md"], conflicts=["notes/dup.md"], dest_only=[], errors=[])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder)

    result = runner.invoke(app, ["cloud", "pull", "--name", "research"])

    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "notes/dup.md" in output
    assert "--on-conflict keep-cloud" in output
    assert "args" not in recorder  # transfer never ran


def test_cloud_pull_clean_transfers(monkeypatch, config_manager):
    """With no conflicts, pull proceeds and reports success."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    plan = TransferPlan(new=["new.md"], conflicts=[], dest_only=["local-only.md"], errors=[])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder)

    result = runner.invoke(app, ["cloud", "pull", "--name", "research"])

    assert result.exit_code == 0, result.output
    output = " ".join(result.output.lower().split())
    assert "research pull completed successfully" in output
    # Deletions are surfaced, not propagated
    assert "deletions are not propagated" in output
    assert recorder["kwargs"]["strategy"] == "fail"
    assert recorder["args"][2] == "pull"


def test_cloud_pull_keep_cloud_resolves_conflict(monkeypatch, config_manager):
    """An explicit --on-conflict strategy lets pull proceed through conflicts."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    plan = TransferPlan(new=[], conflicts=["notes/dup.md"], dest_only=[], errors=[])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder)

    result = runner.invoke(
        app, ["cloud", "pull", "--name", "research", "--on-conflict", "keep-cloud"]
    )

    assert result.exit_code == 0, result.output
    assert recorder["kwargs"]["strategy"] == "keep-cloud"


def test_cloud_pull_aborts_on_compare_errors(monkeypatch, config_manager):
    """If rclone cannot read/hash files, pull aborts before transferring."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    plan = TransferPlan(new=[], conflicts=[], dest_only=[], errors=["bad.md"])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder)

    result = runner.invoke(app, ["cloud", "pull", "--name", "research"])

    assert result.exit_code == 1, result.output
    assert "could not compare" in result.output
    assert "args" not in recorder  # transfer never ran


def test_cloud_push_aborts_on_conflict_by_default(monkeypatch, config_manager):
    """Push aborts on conflicts like a rejected git push (pull first)."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    plan = TransferPlan(new=["new.md"], conflicts=["notes/dup.md"], dest_only=[], errors=[])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder)

    result = runner.invoke(app, ["cloud", "push", "--name", "research"])

    assert result.exit_code == 1, result.output
    assert "notes/dup.md" in result.output
    assert "args" not in recorder


def test_cloud_push_keep_local_resolves_conflict(monkeypatch, config_manager):
    """Push with --on-conflict keep-local overwrites cloud and reports the direction."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    plan = TransferPlan(new=[], conflicts=["notes/dup.md"], dest_only=[], errors=[])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder)

    result = runner.invoke(
        app, ["cloud", "push", "--name", "research", "--on-conflict", "keep-local"]
    )

    assert result.exit_code == 0, result.output
    assert recorder["kwargs"]["strategy"] == "keep-local"
    assert recorder["args"][2] == "push"


def test_cloud_push_allows_organization_workspace(monkeypatch, config_manager):
    """push is additive and Team-safe — an organization workspace is allowed (no Personal gate)."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    org_ws = _workspace("team-tenant", "organization", "acme", is_default=False)
    plan = TransferPlan(new=["new.md"], conflicts=[], dest_only=[], errors=[])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder, workspace=org_ws)

    result = runner.invoke(app, ["cloud", "push", "--name", "research"])

    assert result.exit_code == 0, result.output
    assert "research push completed successfully" in result.output
    # Routed through the team workspace's own remote, against its tenant's bucket.
    assert recorder["args"][0].remote_name == "basic-memory-cloud-acme"


def test_cloud_pull_workspace_override_routes_through_workspace_remote(monkeypatch, config_manager):
    """pull --workspace routes through the named workspace's own remote and bucket."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    org_ws = _workspace("team-tenant", "organization", "acme", is_default=False)
    plan = TransferPlan(new=["new.md"], conflicts=[], dest_only=[], errors=[])
    recorder: dict = {}

    # _get_workspace_for_project must receive the override and return the org workspace.
    def _resolve(_name, _config, *, workspace_override=None):
        assert workspace_override == "acme"
        return _async_value(org_ws)

    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder, workspace=org_ws)
    monkeypatch.setattr(module, "_get_workspace_for_project", _resolve)

    result = runner.invoke(app, ["cloud", "pull", "--name", "research", "--workspace", "acme"])

    assert result.exit_code == 0, result.output
    assert recorder["args"][0].remote_name == "basic-memory-cloud-acme"
    assert recorder["args"][2] == "pull"


def test_cloud_push_errors_when_workspace_remote_not_set_up(monkeypatch, config_manager):
    """If the workspace's remote isn't configured, push stops with the setup command."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    org_ws = _workspace("team-tenant", "organization", "acme", is_default=False)
    plan = TransferPlan(new=["new.md"], conflicts=[], dest_only=[], errors=[])
    recorder: dict = {}
    _stub_transfer_env(monkeypatch, module, plan=plan, recorder=recorder, workspace=org_ws)
    # Override: this workspace has not been set up yet.
    monkeypatch.setattr(module, "rclone_remote_exists", lambda _remote: False)

    result = runner.invoke(app, ["cloud", "push", "--name", "research"])

    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "not set up for sync" in output
    assert "bm cloud setup --workspace acme" in output
    assert "args" not in recorder  # never transferred


def test_get_workspace_for_project_override_resolves(monkeypatch, config_manager):
    """An explicit --workspace override selects that workspace regardless of config."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    config = config_manager.load_config()
    monkeypatch.setattr(
        module,
        "get_available_workspaces",
        lambda: _async_value(
            [
                _workspace("personal-tenant", "personal", "personal", is_default=True),
                _workspace("team-tenant", "organization", "acme"),
            ]
        ),
    )

    ws = module.run_with_cleanup(
        module._get_workspace_for_project("research", config, workspace_override="acme")
    )

    assert ws.tenant_id == "team-tenant"


def test_get_workspace_for_project_override_no_match_raises(monkeypatch, config_manager):
    """An override that matches no accessible workspace is a clear error."""
    module = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")
    config = config_manager.load_config()
    monkeypatch.setattr(
        module,
        "get_available_workspaces",
        lambda: _async_value(
            [_workspace("personal-tenant", "personal", "personal", is_default=True)]
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        module.run_with_cleanup(
            module._get_workspace_for_project("research", config, workspace_override="acme")
        )

    assert "No accessible workspace matches 'acme'" in str(exc_info.value)


def _stub_setup_env(monkeypatch, core, *, remote_exists=False, recorder=None):
    """Stub the `bm cloud setup` dependency chain for the acme workspace."""
    monkeypatch.setattr(core, "install_rclone", lambda: None)
    monkeypatch.setattr(
        core,
        "get_available_workspaces",
        lambda: _async_value([_workspace("team-tenant", "organization", "acme")]),
    )
    monkeypatch.setattr(core, "rclone_remote_exists", lambda _remote: remote_exists)

    def _mint(_tenant_id):
        if recorder is not None:
            recorder["minted"] = True
        return _async_value(SimpleNamespace(access_key="ak", secret_key="sk"))

    monkeypatch.setattr(
        core,
        "get_mount_info",
        lambda **_kwargs: _async_value(
            SimpleNamespace(tenant_id="team-tenant", bucket_name="acme-bucket")
        ),
    )
    monkeypatch.setattr(core, "generate_mount_credentials", _mint)

    def _fake_configure(**kwargs):
        if recorder is not None:
            recorder.update(kwargs)
        return kwargs.get("remote_name")

    monkeypatch.setattr(core, "configure_rclone_remote", _fake_configure)


def test_cloud_setup_workspace_configures_named_remote(monkeypatch):
    """`bm cloud setup --workspace acme` provisions the acme tenant's own remote."""
    core = importlib.import_module("basic_memory.cli.commands.cloud.core_commands")
    recorder: dict = {}
    _stub_setup_env(monkeypatch, core, remote_exists=False, recorder=recorder)

    result = runner.invoke(app, ["cloud", "setup", "--workspace", "acme"])

    assert result.exit_code == 0, result.output
    assert recorder["remote_name"] == "basic-memory-cloud-acme"


def test_cloud_setup_aborts_when_remote_exists_without_force(monkeypatch):
    """Setup refuses to overwrite an existing remote, and mints nothing (the #922 footgun)."""
    core = importlib.import_module("basic_memory.cli.commands.cloud.core_commands")
    recorder: dict = {}
    _stub_setup_env(monkeypatch, core, remote_exists=True, recorder=recorder)

    result = runner.invoke(app, ["cloud", "setup", "--workspace", "acme"])

    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "basic-memory-cloud-acme' is already configured" in output
    assert "--force" in output
    # Aborted before minting credentials or touching the remote.
    assert "minted" not in recorder
    assert "remote_name" not in recorder


def test_cloud_setup_force_overwrites_existing_remote(monkeypatch):
    """--force reconfigures an existing remote."""
    core = importlib.import_module("basic_memory.cli.commands.cloud.core_commands")
    recorder: dict = {}
    _stub_setup_env(monkeypatch, core, remote_exists=True, recorder=recorder)

    result = runner.invoke(app, ["cloud", "setup", "--workspace", "acme", "--force"])

    assert result.exit_code == 0, result.output
    assert recorder["remote_name"] == "basic-memory-cloud-acme"
    assert recorder.get("minted") is True


def test_cloud_setup_default_workspace_aborts_when_remote_exists(monkeypatch):
    """The original footgun: `bm cloud setup` (no --workspace) must not clobber
    the shared basic-memory-cloud remote without --force."""
    core = importlib.import_module("basic_memory.cli.commands.cloud.core_commands")
    recorder: dict = {}
    _stub_setup_env(monkeypatch, core, remote_exists=True, recorder=recorder)

    result = runner.invoke(app, ["cloud", "setup"])  # no --workspace → basic-memory-cloud

    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "'basic-memory-cloud' is already configured" in output
    assert "minted" not in recorder  # nothing minted on abort
    assert "remote_name" not in recorder


async def _async_value(value):
    return value


def _workspace(
    tenant_id: str, workspace_type: str, slug: str, *, is_default: bool = False
) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type=workspace_type,
        slug=slug,
        name=slug.title(),
        role="owner",
        is_default=is_default,
        has_active_subscription=True,
    )
