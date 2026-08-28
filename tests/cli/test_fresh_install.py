"""Regression tests for a fresh, CLI-only install (#1334, #974)."""

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _pristine_env(home: Path) -> dict[str, str]:
    """A profile with no Basic Memory state, and none inherited from the developer or CI."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("BASIC_MEMORY_") and key != "PYTEST_CURRENT_TEST"
    }
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        BASIC_MEMORY_HOME=str(home / "basic-memory"),
        BASIC_MEMORY_CONFIG_DIR=str(home / ".basic-memory"),
        BASIC_MEMORY_NO_PROMOS="1",
    )
    return env


def _bm(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "bm", *args],
        capture_output=True,
        text=True,
        env=env,
        # Guards a wedged install, not a performance budget: first run creates
        # the database and runs migrations.
        timeout=90,
        cwd=PROJECT_ROOT,
    )


def test_fresh_cli_install_can_use_the_bootstrapped_default_project(tmp_path):
    """The auto-created `main` project must exist in the database, not just config.json.

    A fresh install seeds `main` into config.json. Only the API/MCP server lifespan
    used to reconcile that into the projects table, so a CLI-only flow hit
    "Project not found: 'main'" on every default-project command while
    `project add main` refused with "already exists" (#1334).
    """
    home = tmp_path / "home"
    home.mkdir()
    env = _pristine_env(home)

    status = _bm(["status"], env)

    assert status.returncode == 0, status.stderr
    assert "Project not found" not in status.stdout + status.stderr
    assert "main" in status.stdout
