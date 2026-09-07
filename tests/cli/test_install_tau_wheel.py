"""Opt-in distribution smoke test: BM_TAU_INSTALL_WHEEL=/absolute/path/to.whl."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

WHEEL = os.environ.get("BM_TAU_INSTALL_WHEEL")


@pytest.mark.skipif(not WHEEL, reason="Set BM_TAU_INSTALL_WHEEL to test a built distribution")
def test_packaged_installer_without_checkout(tmp_path: Path) -> None:
    assert WHEEL is not None
    package = tmp_path / "site-packages"
    home = tmp_path / "home"
    home.mkdir()
    with zipfile.ZipFile(WHEEL) as archive:
        resources = [
            name for name in archive.namelist() if name.startswith("basic_memory/data/tau/")
        ]
        assert resources
        assert not any("/.venv/" in name or "/__pycache__/" in name for name in resources)
        assert "basic_memory/data/tau/extension/Basic Memory.py" in resources
        archive.extractall(package)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONPATH": str(package),
        "BASIC_MEMORY_CONFIG_DIR": str(home / "bm-config"),
    }
    command = [sys.executable, "-m", "basic_memory.cli.main", "install", "tau"]
    for arguments in (["--dry-run"], ["--yes"], ["--yes"]):
        result = subprocess.run(
            command + arguments, cwd=home, env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if arguments == ["--dry-run"]:
            assert not (home / ".tau").exists()
    assert "unchanged:" in result.stdout
    extension = home / ".tau/extensions/basic-memory"
    assert (extension / "pyproject.toml").exists()
    assert (extension / "schemas/coding-session.md").exists()
    assert len(list((home / ".tau/prompts").glob("*.md"))) == 4
    assert (home / ".tau/skills/basic-memory-setup/SKILL.md").exists()
    assert not (home / ".tau/basic-memory.json").exists()
    assert not (home / "bm-config").exists()
