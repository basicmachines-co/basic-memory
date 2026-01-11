"""Test project-scoped rclone commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from basic_memory.cli.commands.cloud.rclone_commands import (
    MIN_RCLONE_VERSION_EMPTY_DIRS,
    RcloneError,
    SyncProject,
    bisync_initialized,
    check_rclone_installed,
    get_project_bisync_state,
    get_project_remote,
    get_rclone_version,
    project_bisync,
    project_check,
    project_ls,
    project_sync,
    supports_create_empty_src_dirs,
)


class _RunResult:
    def __init__(self, returncode: int = 0, stdout: str = ""):
        self.returncode = returncode
        self.stdout = stdout


class _Runner:
    def __init__(self, *, returncode: int = 0, stdout: str = ""):
        self.calls: list[tuple[list[str], dict]] = []
        self._returncode = returncode
        self._stdout = stdout

    def __call__(self, cmd: list[str], **kwargs):
        self.calls.append((cmd, kwargs))
        return _RunResult(returncode=self._returncode, stdout=self._stdout)


def _write_filter_file(tmp_path: Path) -> Path:
    p = tmp_path / "filters.txt"
    p.write_text("- .git/**\n", encoding="utf-8")
    return p


def test_sync_project_dataclass():
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/Users/test/research"
    )
    assert project.name == "research"
    assert project.path == "app/data/research"
    assert project.local_sync_path == "/Users/test/research"


def test_sync_project_optional_local_path():
    project = SyncProject(name="research", path="app/data/research")
    assert project.name == "research"
    assert project.path == "app/data/research"
    assert project.local_sync_path is None


def test_get_project_remote():
    project = SyncProject(name="research", path="/research")
    assert get_project_remote(project, "my-bucket") == "basic-memory-cloud:my-bucket/research"


def test_get_project_remote_strips_app_data_prefix():
    project = SyncProject(name="research", path="/app/data/research")
    assert get_project_remote(project, "my-bucket") == "basic-memory-cloud:my-bucket/research"


def test_get_project_bisync_state():
    state_path = get_project_bisync_state("research")
    expected = Path.home() / ".basic-memory" / "bisync-state" / "research"
    assert state_path == expected


def test_bisync_initialized_false_when_not_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.rclone_commands.get_project_bisync_state",
        lambda project_name: tmp_path / project_name,
    )
    assert bisync_initialized("research") is False


def test_bisync_initialized_false_when_empty(tmp_path, monkeypatch):
    state_dir = tmp_path / "research"
    state_dir.mkdir()
    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.rclone_commands.get_project_bisync_state",
        lambda project_name: tmp_path / project_name,
    )
    assert bisync_initialized("research") is False


def test_bisync_initialized_true_when_has_files(tmp_path, monkeypatch):
    state_dir = tmp_path / "research"
    state_dir.mkdir()
    (state_dir / "state.lst").touch()
    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.rclone_commands.get_project_bisync_state",
        lambda project_name: tmp_path / project_name,
    )
    assert bisync_initialized("research") is True


def test_project_sync_success(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    result = project_sync(
        project,
        "my-bucket",
        dry_run=True,
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    assert result is True
    assert len(runner.calls) == 1
    cmd, kwargs = runner.calls[0]
    assert cmd[:2] == ["rclone", "sync"]
    assert Path(cmd[2]) == Path("/tmp/research")
    assert cmd[3] == "basic-memory-cloud:my-bucket/research"
    assert "--filter-from" in cmd
    assert str(filter_path) in cmd
    assert "--dry-run" in cmd
    assert kwargs["text"] is True


def test_project_sync_with_verbose(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    project_sync(
        project,
        "my-bucket",
        verbose=True,
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert "--verbose" in cmd
    assert "--progress" not in cmd


def test_project_sync_with_progress(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    project_sync(
        project, "my-bucket", run=runner, is_installed=lambda: True, filter_path=filter_path
    )

    cmd, _ = runner.calls[0]
    assert "--progress" in cmd
    assert "--verbose" not in cmd


def test_project_sync_no_local_path():
    project = SyncProject(name="research", path="app/data/research")
    with pytest.raises(RcloneError) as exc_info:
        project_sync(project, "my-bucket", is_installed=lambda: True)
    assert "no local_sync_path configured" in str(exc_info.value)


def test_project_sync_checks_rclone_installed():
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )
    with pytest.raises(RcloneError) as exc_info:
        project_sync(project, "my-bucket", is_installed=lambda: False)
    assert "rclone is not installed" in str(exc_info.value)


def test_project_bisync_success(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    state_path = tmp_path / "state"
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    result = project_bisync(
        project,
        "my-bucket",
        run=runner,
        is_installed=lambda: True,
        version=(1, 64, 2),
        filter_path=filter_path,
        state_path=state_path,
        is_initialized=lambda _name: True,
    )

    assert result is True
    cmd, _ = runner.calls[0]
    assert cmd[:2] == ["rclone", "bisync"]
    assert "--resilient" in cmd
    assert "--conflict-resolve=newer" in cmd
    assert "--max-delete=25" in cmd
    assert "--compare=modtime" in cmd
    assert "--workdir" in cmd
    assert str(state_path) in cmd


def test_project_bisync_requires_resync_first_time(tmp_path):
    filter_path = _write_filter_file(tmp_path)
    state_path = tmp_path / "state"
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    with pytest.raises(RcloneError) as exc_info:
        project_bisync(
            project,
            "my-bucket",
            is_installed=lambda: True,
            version=(1, 64, 2),
            filter_path=filter_path,
            state_path=state_path,
            is_initialized=lambda _name: False,
        )

    assert "requires --resync" in str(exc_info.value)


def test_project_bisync_with_resync_flag(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    state_path = tmp_path / "state"
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    result = project_bisync(
        project,
        "my-bucket",
        resync=True,
        run=runner,
        is_installed=lambda: True,
        version=(1, 64, 2),
        filter_path=filter_path,
        state_path=state_path,
        is_initialized=lambda _name: False,
    )

    assert result is True
    cmd, _ = runner.calls[0]
    assert "--resync" in cmd


def test_project_bisync_dry_run_skips_init_check(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    state_path = tmp_path / "state"
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    result = project_bisync(
        project,
        "my-bucket",
        dry_run=True,
        run=runner,
        is_installed=lambda: True,
        version=(1, 64, 2),
        filter_path=filter_path,
        state_path=state_path,
        is_initialized=lambda _name: False,
    )

    assert result is True
    cmd, _ = runner.calls[0]
    assert "--dry-run" in cmd


def test_project_bisync_no_local_path():
    project = SyncProject(name="research", path="app/data/research")
    with pytest.raises(RcloneError) as exc_info:
        project_bisync(project, "my-bucket", is_installed=lambda: True)
    assert "no local_sync_path configured" in str(exc_info.value)


def test_project_bisync_checks_rclone_installed(tmp_path):
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )
    with pytest.raises(RcloneError) as exc_info:
        project_bisync(
            project,
            "my-bucket",
            is_installed=lambda: False,
            filter_path=_write_filter_file(tmp_path),
            state_path=tmp_path / "state",
            is_initialized=lambda _name: True,
        )
    assert "rclone is not installed" in str(exc_info.value)


def test_project_bisync_includes_empty_dirs_flag_when_supported(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    state_path = tmp_path / "state"
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    project_bisync(
        project,
        "my-bucket",
        run=runner,
        is_installed=lambda: True,
        version=(1, 64, 2),
        filter_path=filter_path,
        state_path=state_path,
        is_initialized=lambda _name: True,
    )

    cmd, _ = runner.calls[0]
    assert "--create-empty-src-dirs" in cmd


def test_project_bisync_excludes_empty_dirs_flag_when_not_supported(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    state_path = tmp_path / "state"
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    project_bisync(
        project,
        "my-bucket",
        run=runner,
        is_installed=lambda: True,
        version=(1, 60, 1),
        filter_path=filter_path,
        state_path=state_path,
        is_initialized=lambda _name: True,
    )

    cmd, _ = runner.calls[0]
    assert "--create-empty-src-dirs" not in cmd


def test_project_check_success(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    result = project_check(
        project, "my-bucket", run=runner, is_installed=lambda: True, filter_path=filter_path
    )
    assert result is True
    cmd, kwargs = runner.calls[0]
    assert cmd[:2] == ["rclone", "check"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_project_check_with_one_way(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )

    project_check(
        project,
        "my-bucket",
        one_way=True,
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert "--one-way" in cmd


def test_project_check_checks_rclone_installed():
    project = SyncProject(
        name="research", path="app/data/research", local_sync_path="/tmp/research"
    )
    with pytest.raises(RcloneError) as exc_info:
        project_check(project, "my-bucket", is_installed=lambda: False)
    assert "rclone is not installed" in str(exc_info.value)


def test_project_ls_success():
    runner = _Runner(returncode=0, stdout="file1.md\nfile2.md\nsubdir/file3.md\n")
    project = SyncProject(name="research", path="app/data/research")
    files = project_ls(project, "my-bucket", run=runner, is_installed=lambda: True)
    assert files == ["file1.md", "file2.md", "subdir/file3.md"]


def test_project_ls_with_subpath():
    runner = _Runner(returncode=0, stdout="")
    project = SyncProject(name="research", path="/research")
    project_ls(project, "my-bucket", path="subdir", run=runner, is_installed=lambda: True)

    cmd, kwargs = runner.calls[0]
    assert cmd[-1] == "basic-memory-cloud:my-bucket/research/subdir"
    assert kwargs["check"] is True


def test_project_ls_checks_rclone_installed():
    project = SyncProject(name="research", path="app/data/research")
    with pytest.raises(RcloneError) as exc_info:
        project_ls(project, "my-bucket", is_installed=lambda: False)
    assert "rclone is not installed" in str(exc_info.value)


def test_check_rclone_installed_success():
    check_rclone_installed(is_installed=lambda: True)


def test_check_rclone_installed_not_found():
    with pytest.raises(RcloneError) as exc_info:
        check_rclone_installed(is_installed=lambda: False)

    error_msg = str(exc_info.value)
    assert "rclone is not installed" in error_msg
    assert "bm cloud setup" in error_msg
    assert "https://rclone.org/downloads/" in error_msg


def test_get_rclone_version_parses_standard_version():
    get_rclone_version.cache_clear()
    runner = _Runner(stdout="rclone v1.64.2\n- os/version: darwin 23.0.0\n- os/arch: arm64\n")
    assert get_rclone_version(run=runner) == (1, 64, 2)


def test_get_rclone_version_parses_dev_version():
    get_rclone_version.cache_clear()
    runner = _Runner(stdout="rclone v1.60.1-DEV\n- os/version: linux 5.15.0\n")
    assert get_rclone_version(run=runner) == (1, 60, 1)


def test_get_rclone_version_handles_invalid_output():
    get_rclone_version.cache_clear()
    runner = _Runner(stdout="not a valid version string")
    assert get_rclone_version(run=runner) is None


def test_get_rclone_version_handles_exception():
    get_rclone_version.cache_clear()

    def bad_run(_cmd, **_kwargs):
        raise Exception("Command failed")

    assert get_rclone_version(run=bad_run) is None


def test_get_rclone_version_handles_timeout():
    get_rclone_version.cache_clear()
    from subprocess import TimeoutExpired

    def bad_run(_cmd, **_kwargs):
        raise TimeoutExpired(cmd="rclone version", timeout=10)

    assert get_rclone_version(run=bad_run) is None


def test_supports_create_empty_src_dirs_true_for_new_version():
    assert supports_create_empty_src_dirs((1, 64, 2)) is True


def test_supports_create_empty_src_dirs_true_for_exact_min_version():
    assert supports_create_empty_src_dirs((1, 64, 0)) is True


def test_supports_create_empty_src_dirs_false_for_old_version():
    assert supports_create_empty_src_dirs((1, 60, 1)) is False


def test_supports_create_empty_src_dirs_false_for_unknown_version():
    assert supports_create_empty_src_dirs(None) is False


def test_min_rclone_version_constant():
    assert MIN_RCLONE_VERSION_EMPTY_DIRS == (1, 64, 0)
