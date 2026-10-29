"""The cloud mirror commands removed in 1.0 only print migration guidance (#1845)."""

import json
import re
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app

runner = CliRunner()

REMOVED_INVOCATIONS = [
    ["cloud", "sync", "--name", "research"],
    ["cloud", "sync", "--project", "research", "--dry-run", "--verbose"],
    ["cloud", "bisync", "--name", "research", "--resync"],
    ["cloud", "bisync", "--name=research", "--dry-run"],
    ["cloud", "bisync-reset", "research"],
]


def _plain(text: str) -> str:
    """Strip console styling and wrapping so assertions read against the words."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def _tree(root: Path) -> dict[str, bytes]:
    """Every user file under root with its bytes, to prove a command changed nothing.

    The CLI callback opens the local index database before any `bm` subcommand
    runs, so the database files are not evidence about the removed commands.
    """
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith("memory.db")
    }


@pytest.fixture
def mirror_user(tmp_path, monkeypatch) -> Path:
    """A logged-in user with a synced project and old bisync state on disk."""
    home = tmp_path / "home"
    config_dir = home / ".basic-memory"
    notes = home / "notes"
    state = config_dir / "bisync-state" / "research"
    for directory in (config_dir, notes, state):
        directory.mkdir(parents=True)

    (notes / "only-local.md").write_text("# Only local\n")
    (state / "listing.lst").write_text("old bisync listing\n")
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "cloud_api_key": "bmc_test",
                "projects": {
                    "research": {
                        "path": str(notes),
                        "mode": "cloud",
                        "local_sync_path": str(notes),
                        "bisync_initialized": True,
                    }
                },
                "default_project": "research",
            }
        )
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))

    # Any cloud call (mount info, credential minting, project lookup, WebDAV)
    # goes through httpx; a removed command must never reach the network.
    async def _no_network(*args, **kwargs):
        pytest.fail("a removed mirror command made an HTTP request")

    monkeypatch.setattr(httpx.AsyncClient, "send", _no_network)
    return home


@pytest.mark.parametrize("argv", REMOVED_INVOCATIONS)
def test_removed_mirror_command_exits_nonzero_and_changes_nothing(mirror_user, argv):
    """Old invocations fail with guidance, and no file, credential, or state changes."""
    before = _tree(mirror_user)

    result = runner.invoke(app, argv)

    assert result.exit_code == 1, result.output
    output = _plain(result.output)
    assert f"`bm cloud {argv[1]}` was removed in Basic Memory 1.0" in output
    assert "Nothing was transferred or deleted." in output
    assert "bm cloud pull --name research --dry-run" in output
    assert "bm cloud push --name research --dry-run" in output
    assert "--on-conflict keep-local, keep-cloud, or keep-both" in output
    assert "keeps files that exist only in Cloud" in output
    assert _tree(mirror_user) == before


def test_bisync_reset_points_at_old_state_without_deleting_it(mirror_user):
    """The old state directory is named for manual cleanup and left in place."""
    state_dir = mirror_user / ".basic-memory" / "bisync-state"

    # A wide console keeps Rich from wrapping the long temp path mid-word.
    result = runner.invoke(app, ["cloud", "bisync-reset", "research"], env={"COLUMNS": "400"})

    assert result.exit_code == 1, result.output
    assert f"Old bisync state in {state_dir} is no longer used" in _plain(result.output)
    assert (state_dir / "research" / "listing.lst").exists()


def test_removed_mirror_command_without_project_uses_placeholder(mirror_user):
    """With no project to reuse, the printed commands show where the name goes."""
    result = runner.invoke(app, ["cloud", "sync"])

    assert result.exit_code == 1, result.output
    assert "bm cloud push --name <project>" in _plain(result.output)


@pytest.mark.parametrize("name", ["My Notes", "[bold]x[/bold]"])
def test_removed_mirror_command_quotes_project_names_verbatim(mirror_user, name):
    """Printed commands survive copy-paste: shell-quoted, and Rich markup kept literal."""
    result = runner.invoke(app, ["cloud", "bisync", "--name", name])

    assert result.exit_code == 1, result.output
    assert f"bm cloud pull --name '{name}'" in _plain(result.output)


def test_removed_mirror_commands_are_hidden_from_cloud_help():
    """Help lists push/pull, not the removed mirror commands."""
    result = runner.invoke(app, ["cloud", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    commands = {
        line.strip("│ ").split(" ")[0]
        for line in re.sub(r"\x1b\[[0-9;]*m", "", result.output).splitlines()
    }
    assert {"push", "pull"} <= commands
    assert not {"sync", "bisync", "bisync-reset"} & commands
