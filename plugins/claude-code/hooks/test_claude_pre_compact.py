"""Tests for the co-located pre_compact.py uv hook script.

The script is executed via ``sys.executable`` (the PEP 723 metadata is inert
comments to a plain interpreter), so these tests exercise the real launcher
logic — BM_BIN resolution, argument plumbing, fail-open — without a network
resolve. The metadata block itself is pinned against the package version so
release drift fails here rather than at a user's cold uvx cache.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from basic_memory import __version__

SCRIPT = Path(__file__).with_name("pre_compact.py")
VERB = "pre-compact"
HARNESS = "claude"


def run_script(
    *,
    stdin: str = "{}",
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **(env_overrides or {})}
    # Isolate from the developer's shell: these vars change the script's path.
    for var in ("BM_BIN", "CLAUDE_PROJECT_DIR"):
        if var not in (env_overrides or {}):
            env.pop(var, None)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def recorder(tmp_path: Path) -> tuple[str, Path]:
    """A BM_BIN launcher that records its argv and stdin to a file."""
    record_file = tmp_path / "record.json"
    script = tmp_path / "recorder.py"
    script.write_text(
        "import json, os, sys\n"
        'payload = {"argv": sys.argv[1:], "stdin": sys.stdin.read()}\n'
        'with open(os.environ["RECORD_FILE"], "w", encoding="utf-8") as fh:\n'
        "    json.dump(payload, fh)\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"', record_file


def test_bm_bin_launcher_receives_verb_and_stdin(
    recorder: tuple[str, Path],
) -> None:
    bm_bin, record_file = recorder
    hook_json = '{"session_id": "s-1", "cwd": "/tmp/work"}'

    result = run_script(
        stdin=hook_json,
        env_overrides={"BM_BIN": bm_bin, "RECORD_FILE": str(record_file)},
    )

    assert result.returncode == 0
    recorded = json.loads(record_file.read_text(encoding="utf-8"))
    assert recorded["argv"] == ["hook", VERB, "--harness", HARNESS]
    assert recorded["stdin"] == hook_json


def test_project_dir_env_plumbs_through(recorder: tuple[str, Path]) -> None:
    bm_bin, record_file = recorder
    project_dir = "/Users/someone/My Projects/demo"

    result = run_script(
        env_overrides={
            "BM_BIN": bm_bin,
            "RECORD_FILE": str(record_file),
            "CLAUDE_PROJECT_DIR": project_dir,
        },
    )

    assert result.returncode == 0
    recorded = json.loads(record_file.read_text(encoding="utf-8"))
    assert recorded["argv"] == [
        "hook",
        VERB,
        "--harness",
        HARNESS,
        "--project-dir",
        project_dir,
    ]


def test_unresolvable_bm_bin_fails_open(tmp_path: Path) -> None:
    result = run_script(env_overrides={"BM_BIN": str(tmp_path / "missing" / "bm")})

    assert result.returncode == 0


def test_in_process_cli_fails_open_with_empty_home(tmp_path: Path) -> None:
    # No BM_BIN: the script imports the CLI from its own environment (in tests,
    # the dev install). An empty HOME exercises the real fail-open verb path.
    result = run_script(
        env_overrides={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
    )

    assert result.returncode == 0


def test_dependency_floor_matches_package_version() -> None:
    # scripts/update_versions.py bumps this line at release; drift between the
    # script floor and the package version fails here, before a release lands.
    text = SCRIPT.read_text(encoding="utf-8")
    floors = re.findall(r'^# dependencies = \["basic-memory>=([^"]+)"\]$', text, re.MULTILINE)

    assert floors == [__version__]


def test_metadata_block_shape() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert text.count("# /// script") == 1
    assert re.search(r'^# requires-python = ">=3\.12"$', text, re.MULTILINE)
    # The version updater's anchored pattern assumes exactly one basic-memory
    # spec in the whole file.
    assert len(re.findall(r"basic-memory>=", text)) == 1
