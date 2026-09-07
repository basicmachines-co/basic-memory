"""Pi installation is packaged, repeatable, and safe before BM database setup."""

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
    root = tmp_path / "package"
    mapping = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"][
        "targets"
    ]["wheel"]["force-include"]
    for source, destination in mapping.items():
        if not destination.startswith("basic_memory/data/pi/"):
            continue
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
    return root / "basic_memory/data/pi/package"


def test_dry_run_never_initializes_config_or_database(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", Mock(side_effect=AssertionError))
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    run = Mock(side_effect=AssertionError)
    monkeypatch.setattr(install.subprocess, "run", run)

    result = runner.invoke(app, ["install", "pi", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "extensions/index.ts" in result.output
    assert "basic-memory-pi-setup" in result.output
    assert "Pi registration: /bin/pi install" in result.output
    assert not (Path.home() / ".pi/agent/packages/basic-memory").exists()
    run.assert_not_called()


def test_install_repeat_and_preserve_workspace_config(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stderr="PRIVATE-STDERR"))
    monkeypatch.setattr(install.subprocess, "run", run)
    config = Path.cwd() / ".pi/basic-memory.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("not even valid JSON: private")

    first = runner.invoke(app, ["install", "pi", "--yes"])
    assert first.exit_code == 0, first.output
    entry = Path.home() / ".pi/agent/packages/basic-memory/extensions/index.ts"
    assert entry.exists()
    assert (
        Path.home() / ".pi/agent/packages/basic-memory/skills/basic-memory-pi/SKILL.md"
    ).exists()
    assert config.read_text() == "not even valid JSON: private"
    original_mtime = entry.stat().st_mtime_ns

    second = runner.invoke(app, ["install", "pi", "--yes"])
    assert second.exit_code == 0, second.output
    assert "unchanged:" in second.output
    assert entry.stat().st_mtime_ns == original_mtime
    assert run.call_args.args[0] == [
        "/bin/pi",
        "install",
        str(Path.home() / ".pi/agent/packages/basic-memory"),
    ]
    assert "PRIVATE-STDERR" not in second.output


def test_edited_files_need_explicit_replace_and_confirmation(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        install.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0))
    )
    assert runner.invoke(app, ["install", "pi", "--yes"]).exit_code == 0
    package_json = Path.home() / ".pi/agent/packages/basic-memory/package.json"
    package_json.write_text("user-authored")

    result = runner.invoke(app, ["install", "pi", "--yes"])
    assert result.exit_code == 1
    assert package_json.read_text() == "user-authored"

    declined = runner.invoke(app, ["install", "pi", "--replace"], input="n\n")
    assert declined.exit_code != 0
    assert package_json.read_text() == "user-authored"

    approved = runner.invoke(app, ["install", "pi", "--replace"], input="y\n")
    assert approved.exit_code == 0, approved.output
    assert package_json.read_bytes() == (bundle / "package.json").read_bytes()


def test_local_install_uses_project_settings_scope(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(install.subprocess, "run", run)

    result = runner.invoke(app, ["install", "pi", "--local", "--yes"])

    assert result.exit_code == 0, result.output
    target = Path.cwd() / ".pi/packages/basic-memory"
    assert (target / "extensions/index.ts").exists()
    assert run.call_args.args[0] == ["/bin/pi", "install", str(target), "--local"]


def test_missing_pi_blocks_registration_before_any_write(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: None)

    result = runner.invoke(app, ["install", "pi", "--yes"])

    assert result.exit_code == 1
    assert "pi is required" in result.output
    assert not (Path.home() / ".pi/agent/packages/basic-memory").exists()


def test_registration_failure_does_not_leak_values(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    run = Mock(return_value=subprocess.CompletedProcess([], 1, stderr="PRIVATE-STDERR"))
    monkeypatch.setattr(install.subprocess, "run", run)

    result = runner.invoke(app, ["install", "pi", "--yes"])

    assert result.exit_code == 1
    assert "PRIVATE-STDERR" not in result.output
    assert "registration failed" in result.output
    assert (Path.home() / ".pi/agent/packages/basic-memory/package.json").exists()


def test_symlink_destination_is_not_followed(bundle: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    pi_dir = Path.home() / ".pi"
    pi_dir.mkdir(exist_ok=True)
    (pi_dir / "agent").symlink_to(outside, target_is_directory=True)

    result = runner.invoke(app, ["install", "pi", "--yes"])

    assert result.exit_code == 1
    assert not list(outside.iterdir())


def test_plan_drift_is_rejected(bundle: Path) -> None:
    target = Path.home() / ".pi/agent/packages/basic-memory"
    plan = install.plan_pi_install(bundle, target, Path.home())
    first = plan[0].path
    first.parent.mkdir(parents=True)
    first.write_text("changed after preview")

    with pytest.raises(install.InstallError, match="changed after preview"):
        install.apply_pi_install(plan)

    assert first.read_text() == "changed after preview"
