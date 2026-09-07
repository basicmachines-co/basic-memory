"""Host installation is file-only, repeatable, and safe before BM database setup."""

import shutil
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from basic_memory.cli.commands import install
from basic_memory.cli.main import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Exercise exactly the resource mapping used by the wheel, without copying a venv.
    root = tmp_path / "package"
    mapping = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"][
        "targets"
    ]["wheel"]["force-include"]
    for source, destination in mapping.items():
        target = root / destination
        if (ROOT / source).is_dir():
            shutil.copytree(ROOT / source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, target)
    monkeypatch.setattr(
        install, "distribution", lambda package: Mock(locate_file=lambda path: root / path)
    )
    monkeypatch.chdir(tmp_path)
    return root / "basic_memory/data/tau"


def test_dry_run_never_initializes_config_or_database(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", Mock(side_effect=AssertionError))
    before = list((Path.home() / ".tau").glob("**/*"))
    result = runner.invoke(app, ["install", "tau", "--dry-run", "--sync"])
    assert result.exit_code == 0, result.output
    assert "Basic Memory.py" in result.output
    assert "bm-wrap-up.md" in result.output
    assert list((Path.home() / ".tau").glob("**/*")) == before


def test_install_repeat_and_preserve_config(bundle: Path) -> None:
    config = Path.home() / ".tau/basic-memory.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("not even valid JSON: private")
    first = runner.invoke(app, ["install", "tau", "--yes"])
    assert first.exit_code == 0, first.output
    entry = Path.home() / ".tau/extensions/basic-memory/Basic Memory.py"
    assert entry.exists()
    assert (Path.home() / ".tau/skills/basic-memory-setup/SKILL.md").exists()
    assert config.read_text() == "not even valid JSON: private"
    original_mtime = entry.stat().st_mtime_ns
    second = runner.invoke(app, ["install", "tau", "--yes"])
    assert second.exit_code == 0, second.output
    assert "unchanged:" in second.output
    assert entry.stat().st_mtime_ns == original_mtime
    assert not (entry.parent / ".venv").exists()


def test_edited_files_need_explicit_replace_and_confirmation(bundle: Path) -> None:
    assert runner.invoke(app, ["install", "tau", "--yes"]).exit_code == 0
    prompt = Path.home() / ".tau/prompts/bm-plan.md"
    prompt.write_text("user-authored")
    result = runner.invoke(app, ["install", "tau", "--yes"])
    assert result.exit_code == 1
    assert prompt.read_text() == "user-authored"
    declined = runner.invoke(app, ["install", "tau", "--replace"], input="n\n")
    assert declined.exit_code != 0
    assert prompt.read_text() == "user-authored"
    approved = runner.invoke(app, ["install", "tau", "--replace"], input="y\n")
    assert approved.exit_code == 0, approved.output
    assert prompt.read_bytes() == (bundle / "prompts/bm-plan.md").read_bytes()


def test_existing_shared_skills_and_prompts_are_not_duplicated(bundle: Path) -> None:
    existing = Path.home() / ".agents/skills/memory-tasks/SKILL.md"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("user's shared skill")
    prompt = Path.cwd() / ".agents/prompts/bm-plan.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("project plan")
    result = runner.invoke(app, ["install", "tau", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Skip skill memory-tasks" in result.output
    assert not (Path.home() / ".tau/skills/memory-tasks").exists()
    assert not (Path.home() / ".tau/prompts/bm-plan.md").exists()
    assert existing.read_text() == "user's shared skill"


def test_another_extension_copy_blocks_install(bundle: Path) -> None:
    other = Path.home() / ".tau/extensions/tau/pyproject.toml"
    other.parent.mkdir(parents=True)
    other.write_text('[project]\nname = "basic-memory-tau"\n')
    result = runner.invoke(app, ["install", "tau", "--yes"])
    assert result.exit_code == 1
    assert "Another Basic Memory extension copy" in result.output
    assert not (Path.home() / ".tau/extensions/basic-memory").exists()


def test_symlink_destination_is_not_followed(bundle: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    tau = Path.home() / ".tau"
    tau.mkdir(exist_ok=True)
    (tau / "extensions").symlink_to(outside, target_is_directory=True)
    result = runner.invoke(app, ["install", "tau", "--yes"])
    assert result.exit_code == 1
    assert not list(outside.iterdir())


def test_plan_drift_is_rejected(bundle: Path) -> None:
    plan, _ = install.plan_tau_install(bundle, Path.home(), Path.cwd())
    first = plan[0].path
    first.parent.mkdir(parents=True)
    first.write_text("changed after preview")
    with pytest.raises(install.InstallError, match="changed after preview"):
        install.apply_tau_install(plan)
    assert first.read_text() == "changed after preview"


@pytest.mark.parametrize("exit_code", [0, 1])
def test_sync_is_explicit_and_failure_is_not_success(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    run = Mock(return_value=subprocess.CompletedProcess([], exit_code, stderr="PRIVATE-STDERR"))
    monkeypatch.setattr(install.subprocess, "run", run)
    result = runner.invoke(app, ["install", "tau", "--sync", "--yes"])
    assert result.exit_code == exit_code, result.output
    assert run.call_args.args[0] == [
        "/bin/uv",
        "sync",
        "--project",
        str(Path.home() / ".tau/extensions/basic-memory"),
        "--no-dev",
        "--frozen",
    ]
    assert "PRIVATE-STDERR" not in result.output
    assert (
        "Connection, recall, and continuity are unverified" in result.output
        if exit_code == 0
        else "dependency installation failed" in result.output
    )


def test_missing_uv_blocks_sync_before_any_write(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["install", "tau", "--sync", "--yes"])
    assert result.exit_code == 1
    assert not (Path.home() / ".tau/extensions/basic-memory").exists()


def test_malformed_extension_manifest_does_not_leak_values(bundle: Path) -> None:
    manifest = Path.home() / ".tau/extensions/other/pyproject.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("PRIVATE-BROKEN-VALUE")
    result = runner.invoke(app, ["install", "tau", "--yes"])
    assert result.exit_code == 1
    assert "PRIVATE-BROKEN-VALUE" not in result.output
    assert "manifest is malformed" in result.output
