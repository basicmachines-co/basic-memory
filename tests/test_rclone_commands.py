"""Test project-scoped rclone commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from basic_memory.cli.commands.cloud.transfer import (
    TransferPlan,
    conflict_copy_name,
)
from basic_memory.cli.commands.cloud.rclone_commands import (
    RcloneError,
    SyncProject,
    _parse_check_combined,
    check_rclone_installed,
    get_project_remote,
    project_check,
    project_copy,
    project_copy_file,
    project_diff,
    project_ls,
    project_prune,
    project_prune_preview,
    project_transfer,
)
from typing import Any


class _RunResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runner:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    def __call__(self, cmd: list[str], **kwargs):
        self.calls.append((cmd, kwargs))
        return _RunResult(returncode=self._returncode, stdout=self._stdout, stderr=self._stderr)


def _assert_has_consistency_headers(cmd: list[str]) -> None:
    """Assert the rclone command includes Tigris consistency headers.

    Uses --header (global flag) so the header applies to ALL HTTP transactions,
    including S3 list requests that check and lsf issue before any download/upload.
    """
    assert "--header" in cmd
    header_idx = cmd.index("--header")
    assert cmd[header_idx + 1] == "X-Tigris-Consistent: true"


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


def test_get_project_remote_uses_workspace_remote():
    # Non-default/team workspaces route through their own tenant-scoped remote.
    project = SyncProject(name="research", path="/research", remote_name="basic-memory-cloud-acme")
    assert (
        get_project_remote(project, "acme-bucket") == "basic-memory-cloud-acme:acme-bucket/research"
    )


def test_sync_project_remote_name_defaults_to_legacy_remote():
    project = SyncProject(name="research", path="/research")
    assert project.remote_name == "basic-memory-cloud"


def test_get_project_remote_strips_app_data_prefix():
    project = SyncProject(name="research", path="/app/data/research")
    assert get_project_remote(project, "my-bucket") == "basic-memory-cloud:my-bucket/research"


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
    _assert_has_consistency_headers(cmd)
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
    cmd, _ = runner.calls[0]
    _assert_has_consistency_headers(cmd)


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


# --- Directional transfer primitives (push / pull, issue #858) ---


def test_parse_check_combined_classifies_lines():
    output = "= same.md\n+ only-src.md\n- only-dst.md\n* differ.md\n! broken.md\n\n"
    plan = _parse_check_combined(output)
    assert plan.new == ["only-src.md"]
    assert plan.dest_only == ["only-dst.md"]
    assert plan.conflicts == ["differ.md"]
    assert plan.errors == ["broken.md"]


def test_parse_check_combined_handles_paths_with_spaces():
    plan = _parse_check_combined("* notes/my file.md\n")
    assert plan.conflicts == ["notes/my file.md"]


def test_conflict_copy_name_inserts_marker_before_extension():
    assert conflict_copy_name("notes/x.md", "20260608-1030") == "notes/x.conflict-20260608-1030.md"
    assert conflict_copy_name("top.md", "S") == "top.conflict-S.md"


def test_project_diff_pull_uses_remote_as_source(tmp_path):
    runner = _Runner(returncode=1, stdout="+ a.md\n* b.md\n")
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    plan = project_diff(
        project,
        "my-bucket",
        "pull",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, kwargs = runner.calls[0]
    assert cmd[:2] == ["rclone", "check"]
    # pull: cloud is the source so "+" files (only on source) come down to local
    assert cmd[2] == "basic-memory-cloud:my-bucket/research"
    assert Path(cmd[3]) == Path("/tmp/research")
    assert "--combined" in cmd and cmd[cmd.index("--combined") + 1] == "-"
    _assert_has_consistency_headers(cmd)
    assert "--filter-from" in cmd
    assert kwargs["capture_output"] is True
    # rclone exits non-zero when files differ; we parse output rather than trust it
    assert plan.new == ["a.md"]
    assert plan.conflicts == ["b.md"]


def test_project_diff_push_uses_local_as_source(tmp_path):
    runner = _Runner(returncode=0, stdout="")
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    project_diff(
        project,
        "my-bucket",
        "push",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert Path(cmd[2]) == Path("/tmp/research")
    assert cmd[3] == "basic-memory-cloud:my-bucket/research"


def test_project_copy_pull_new_only_adds_ignore_existing(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    result = project_copy(
        project,
        "my-bucket",
        "pull",
        overwrite=False,
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    assert result is True
    cmd, _ = runner.calls[0]
    assert cmd[:2] == ["rclone", "copy"]
    assert cmd[2] == "basic-memory-cloud:my-bucket/research"
    assert Path(cmd[3]) == Path("/tmp/research")
    assert "--ignore-existing" in cmd
    assert "--local-no-preallocate" in cmd
    # new-only skips by existence, so no content comparison is needed
    assert "--checksum" not in cmd


def test_project_copy_pull_overwrite_omits_ignore_existing(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    project_copy(
        project,
        "my-bucket",
        "pull",
        overwrite=True,
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert "--ignore-existing" not in cmd
    # overwrite compares by content so it matches hash-based conflict detection
    assert "--checksum" in cmd


def test_project_copy_push_uses_local_as_source(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    project_copy(
        project,
        "my-bucket",
        "push",
        overwrite=False,
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert Path(cmd[2]) == Path("/tmp/research")
    assert cmd[3] == "basic-memory-cloud:my-bucket/research"


def test_project_copy_file_pull_copyto_renames_on_dest(tmp_path):
    runner = _Runner(returncode=0)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    project_copy_file(
        project,
        "my-bucket",
        "pull",
        "notes/dup.md",
        "notes/dup.conflict-S.md",
        run=runner,
        is_installed=lambda: True,
    )

    cmd, _ = runner.calls[0]
    assert cmd[:2] == ["rclone", "copyto"]
    assert cmd[2] == "basic-memory-cloud:my-bucket/research/notes/dup.md"
    # Compare the local dest via Path so the assertion holds on Windows, where the
    # local root renders with backslashes (rclone accepts the mixed separators).
    assert Path(cmd[3]) == Path("/tmp/research/notes/dup.conflict-S.md")
    # pull writes the conflict copy locally → must guard virtual-FS NUL padding
    assert "--local-no-preallocate" in cmd


def test_project_transfer_keep_both_copies_conflicts_then_additive(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")
    plan = TransferPlan(new=["a.md"], conflicts=["dup.md"], dest_only=[], errors=[])

    result = project_transfer(
        project,
        "my-bucket",
        "pull",
        plan,
        strategy="keep-both",
        conflict_suffix="S",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    assert result is True
    # First a copyto for the conflict file, written beside the local copy...
    first_cmd, _ = runner.calls[0]
    assert first_cmd[:2] == ["rclone", "copyto"]
    # Path comparison so this holds on Windows (backslash local root).
    assert Path(first_cmd[3]) == Path("/tmp/research/dup.conflict-S.md")
    # ...then an additive (new-only) copy that won't overwrite existing local files.
    second_cmd, _ = runner.calls[1]
    assert second_cmd[:2] == ["rclone", "copy"]
    assert "--ignore-existing" in second_cmd


def test_project_transfer_keep_cloud_on_pull_overwrites_local(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")
    plan = TransferPlan(new=[], conflicts=["dup.md"], dest_only=[], errors=[])

    project_transfer(
        project,
        "my-bucket",
        "pull",
        plan,
        strategy="keep-cloud",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    # keep-cloud + pull → cloud (source) overwrites local (dest): no --ignore-existing,
    # and --checksum so the overwrite decision matches the content-based conflict.
    assert len(runner.calls) == 1
    cmd, _ = runner.calls[0]
    assert cmd[:2] == ["rclone", "copy"]
    assert "--ignore-existing" not in cmd
    assert "--checksum" in cmd


def test_project_transfer_keep_local_on_push_overwrites_cloud(tmp_path):
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")
    plan = TransferPlan(new=[], conflicts=["dup.md"], dest_only=[], errors=[])

    project_transfer(
        project,
        "my-bucket",
        "push",
        plan,
        strategy="keep-local",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert "--ignore-existing" not in cmd


def test_project_diff_requires_local_path():
    project = SyncProject(name="research", path="/research")
    with pytest.raises(RcloneError):
        project_diff(project, "my-bucket", "pull", is_installed=lambda: True)


def test_project_diff_raises_on_fatal_check_error(tmp_path):
    """A non-zero exit with no combined listing means the check failed, not 'no diffs'."""
    runner = _Runner(returncode=7, stdout="", stderr="Failed to create file system: AccessDenied")
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    with pytest.raises(RcloneError) as exc_info:
        project_diff(
            project,
            "my-bucket",
            "pull",
            run=runner,
            is_installed=lambda: True,
            filter_path=filter_path,
        )

    assert "AccessDenied" in str(exc_info.value)


def test_project_diff_nonzero_with_differences_does_not_raise(tmp_path):
    """Differences make rclone check exit non-zero, but that is expected — no raise."""
    runner = _Runner(returncode=1, stdout="* changed.md\n")
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    plan = project_diff(
        project,
        "my-bucket",
        "pull",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    assert plan.conflicts == ["changed.md"]


def test_project_transfer_keep_local_on_pull_preserves_local(tmp_path):
    """keep-local + pull → local is the destination and must be preserved (--ignore-existing)."""
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")
    plan = TransferPlan(new=[], conflicts=["dup.md"], dest_only=[], errors=[])

    project_transfer(
        project,
        "my-bucket",
        "pull",
        plan,
        strategy="keep-local",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert "--ignore-existing" in cmd
    assert "--checksum" not in cmd


def test_project_transfer_keep_cloud_on_push_preserves_cloud(tmp_path):
    """keep-cloud + push → cloud is the destination and must be preserved (--ignore-existing)."""
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")
    plan = TransferPlan(new=[], conflicts=["dup.md"], dest_only=[], errors=[])

    project_transfer(
        project,
        "my-bucket",
        "push",
        plan,
        strategy="keep-cloud",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert "--ignore-existing" in cmd


# --- .bmignore deletion semantics (issue #1032) ---


@pytest.mark.parametrize("direction", ["push", "pull"])
def test_project_copy_omits_delete_excluded(tmp_path, direction):
    """push/pull are additive transfers: no filter-based deletion either."""
    runner = _Runner(returncode=0)
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")

    project_copy(
        project,
        "my-bucket",
        direction,
        overwrite=False,
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    cmd, _ = runner.calls[0]
    assert "--delete-excluded" not in cmd


def test_project_prune_preview_lists_matching_files(tmp_path):
    runner = _Runner(returncode=0, stdout="secret.env\nsecrets/leak.md\n\n")
    filter_path = _write_filter_file(tmp_path)
    # No local_sync_path: prune is purely remote and must not require one.
    project = SyncProject(name="research", path="/research")

    files = project_prune_preview(
        project, "my-bucket", run=runner, is_installed=lambda: True, filter_path=filter_path
    )

    assert files == ["secret.env", "secrets/leak.md"]
    cmd, kwargs = runner.calls[0]
    assert cmd[:2] == ["rclone", "lsf"]
    assert cmd[2] == "basic-memory-cloud:my-bucket/research"
    _assert_has_consistency_headers(cmd)
    assert "--recursive" in cmd
    assert "--files-only" in cmd
    assert "--filter-from" in cmd
    assert str(filter_path) in cmd
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_project_prune_preview_raises_on_failure(tmp_path):
    """A failed listing must not read as 'nothing to prune'."""
    runner = _Runner(returncode=3, stderr="Failed to create file system: AccessDenied")
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research")

    with pytest.raises(RcloneError) as exc_info:
        project_prune_preview(
            project, "my-bucket", run=runner, is_installed=lambda: True, filter_path=filter_path
        )

    assert "AccessDenied" in str(exc_info.value)


def test_project_prune_preview_reports_exit_code_when_no_stderr(tmp_path):
    runner = _Runner(returncode=5, stderr="")
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research")

    with pytest.raises(RcloneError) as exc_info:
        project_prune_preview(
            project, "my-bucket", run=runner, is_installed=lambda: True, filter_path=filter_path
        )

    assert "exited with code 5" in str(exc_info.value)


def test_project_prune_preview_checks_rclone_installed():
    project = SyncProject(name="research", path="/research")
    with pytest.raises(RcloneError) as exc_info:
        project_prune_preview(project, "my-bucket", is_installed=lambda: False)
    assert "rclone is not installed" in str(exc_info.value)


def test_project_prune_deletes_only_previewed_files():
    runner = _Runner(returncode=0)
    project = SyncProject(name="research", path="/research")

    result = project_prune(
        project,
        "my-bucket",
        ["secret.env", "secrets/new.md"],
        run=runner,
        is_installed=lambda: True,
    )

    assert result is True
    cmd, kwargs = runner.calls[0]
    assert cmd[:2] == ["rclone", "delete"]
    assert cmd[2] == "basic-memory-cloud:my-bucket/research"
    _assert_has_consistency_headers(cmd)
    assert cmd[cmd.index("--files-from-raw") + 1] == "-"
    assert "--filter-from" not in cmd
    assert "--verbose" not in cmd
    assert kwargs["input"] == "secret.env\nsecrets/new.md\n"
    assert kwargs["text"] is True


def test_project_prune_with_verbose():
    runner = _Runner(returncode=0)
    project = SyncProject(name="research", path="/research")

    project_prune(
        project,
        "my-bucket",
        ["secret.env"],
        verbose=True,
        run=runner,
        is_installed=lambda: True,
    )

    cmd, _ = runner.calls[0]
    assert "--verbose" in cmd


def test_project_prune_returns_false_on_failure():
    runner = _Runner(returncode=1)
    project = SyncProject(name="research", path="/research")

    result = project_prune(
        project, "my-bucket", ["secret.env"], run=runner, is_installed=lambda: True
    )

    assert result is False


def test_project_prune_checks_rclone_installed():
    project = SyncProject(name="research", path="/research")
    with pytest.raises(RcloneError) as exc_info:
        project_prune(project, "my-bucket", ["secret.env"], is_installed=lambda: False)
    assert "rclone is not installed" in str(exc_info.value)


def test_project_transfer_keep_both_returns_false_on_copy_failure(tmp_path):
    """A failed conflict-copy aborts the transfer before the additive pass."""
    runner = _Runner(returncode=1)  # every rclone invocation fails
    filter_path = _write_filter_file(tmp_path)
    project = SyncProject(name="research", path="/research", local_sync_path="/tmp/research")
    plan = TransferPlan(new=["a.md"], conflicts=["dup.md"], dest_only=[], errors=[])

    result = project_transfer(
        project,
        "my-bucket",
        "pull",
        plan,
        strategy="keep-both",
        conflict_suffix="S",
        run=runner,
        is_installed=lambda: True,
        filter_path=filter_path,
    )

    assert result is False
    # Stopped after the first failed copyto — the additive copy never ran.
    assert len(runner.calls) == 1
    assert runner.calls[0][0][:2] == ["rclone", "copyto"]
