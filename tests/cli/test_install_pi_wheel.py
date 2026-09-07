"""Opt-in distribution smoke test: BM_PI_INSTALL_WHEEL=/absolute/path/to.whl."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

WHEEL = os.environ.get("BM_PI_INSTALL_WHEEL")


@pytest.mark.skipif(not WHEEL, reason="Set BM_PI_INSTALL_WHEEL to test a built distribution")
def test_packaged_installer_without_checkout(tmp_path: Path) -> None:
    assert WHEEL is not None
    package = tmp_path / "site-packages"
    home = tmp_path / "home"
    home.mkdir()
    with zipfile.ZipFile(WHEEL) as archive:
        resources = [
            name for name in archive.namelist() if name.startswith("basic_memory/data/pi/")
        ]
        assert resources
        assert not any("/node_modules/" in name or "/test/" in name for name in resources)
        assert "basic_memory/data/pi/package/extensions/index.ts" in resources
        assert "basic_memory/data/pi/package/package.json" in resources
        archive.extractall(package)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pi_log = tmp_path / "pi.log"
    pi = fake_bin / "pi"
    pi.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> {pi_log}\nexit 0\n")
    pi.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(package),
        "BASIC_MEMORY_CONFIG_DIR": str(home / "bm-config"),
    }
    command = [sys.executable, "-m", "basic_memory.cli.main", "install", "pi"]
    for arguments in (["--dry-run"], ["--yes"], ["--yes"]):
        result = subprocess.run(
            command + arguments, cwd=home, env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if arguments == ["--dry-run"]:
            assert not (home / ".pi").exists()
    assert "unchanged:" in result.stdout
    target = home / ".pi/agent/packages/basic-memory"
    assert (target / "extensions/index.ts").exists()
    assert (target / "skills/basic-memory-pi/SKILL.md").exists()
    assert (target / "skills/basic-memory-pi-setup/SKILL.md").exists()
    assert not (target / "node_modules").exists()
    assert not (home / ".pi/basic-memory.json").exists()
    assert not (home / "bm-config").exists()
    assert str(target) in pi_log.read_text()
