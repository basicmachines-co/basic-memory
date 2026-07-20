"""Manifest-level tests for the plugin uv hook scripts (Claude Code and Codex).

The scripts are the entire plugin hook surface: self-contained PEP 723
launchers that uv resolves at a released dependency floor, invoking
``bm hook <event> --harness <name>`` in-process. Script behavior (BM_BIN
override, argument plumbing, fail-open) is pinned by the co-located tests
next to each script under plugins/*/hooks/; this suite pins the wiring —
hooks.json commands, executable bits, and the release-floor cross-check
against the package version.
"""

import json
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The floor the scripts declare must be the released version — read it from
# the package so `scripts/update_versions.py` keeps this suite green.
_version_match = re.search(
    r'^__version__ = "(.+)"$',
    (REPO_ROOT / "src/basic_memory/__init__.py").read_text(encoding="utf-8"),
    re.MULTILINE,
)
assert _version_match is not None, "no __version__ in src/basic_memory/__init__.py"
CURRENT_VERSION = _version_match.group(1)

# (plugin hooks dir, plugin-root template variable the harness substitutes)
PLUGINS = [
    pytest.param("plugins/claude-code/hooks", "${CLAUDE_PLUGIN_ROOT}", id="claude-code"),
    pytest.param("plugins/codex/hooks", "${PLUGIN_ROOT}", id="codex"),
]
EVENTS = [
    pytest.param("SessionStart", "session_start.py", id="session-start"),
    pytest.param("PreCompact", "pre_compact.py", id="pre-compact"),
]


def _hook_commands(hooks_dir: Path, event: str) -> list[str]:
    manifest = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
    groups = manifest["hooks"][event]
    return [hook["command"] for group in groups for hook in group["hooks"]]


@pytest.mark.parametrize(("hooks_dir", "root_var"), PLUGINS)
@pytest.mark.parametrize(("event", "script_name"), EVENTS)
def test_hooks_json_wires_uv_run_script(
    hooks_dir: str, root_var: str, event: str, script_name: str
) -> None:
    # The command is multi-word and the path is quoted: it runs through the
    # harness shell on both POSIX and Windows, and plugin roots can contain
    # spaces. --script forces script mode so a project venv at the session
    # cwd can never shadow the pinned dependency floor.
    commands = _hook_commands(REPO_ROOT / hooks_dir, event)

    assert commands == [f'uv run --quiet --script "{root_var}/hooks/{script_name}"']


@pytest.mark.parametrize(("hooks_dir", "root_var"), PLUGINS)
@pytest.mark.parametrize(("event", "script_name"), EVENTS)
def test_hook_scripts_exist_and_are_executable(
    hooks_dir: str, root_var: str, event: str, script_name: str
) -> None:
    script = REPO_ROOT / hooks_dir / script_name

    assert script.exists()
    # hooks.json invokes them via `uv run`, but the shebang supports direct
    # runs; the exec bit keeps both paths working from a fresh clone.
    assert os.access(script, os.X_OK)
    first_line = script.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env -S uv run --quiet --script"


@pytest.mark.parametrize(("hooks_dir", "root_var"), PLUGINS)
@pytest.mark.parametrize(("event", "script_name"), EVENTS)
def test_script_floor_matches_released_version(
    hooks_dir: str, root_var: str, event: str, script_name: str
) -> None:
    # Release drift between the PEP 723 floor and the package version fails
    # here (and in each script's co-located test) before a release lands.
    text = (REPO_ROOT / hooks_dir / script_name).read_text(encoding="utf-8")
    floors = re.findall(r'^# dependencies = \["basic-memory>=([^"]+)"\]$', text, re.MULTILINE)

    assert floors == [CURRENT_VERSION]


def test_no_shell_shims_remain() -> None:
    # The bash shims were replaced by the uv scripts; a stray .sh reappearing
    # would mean a manifest or installer regressed to the old launcher.
    for hooks_dir in ("plugins/claude-code/hooks", "plugins/codex/hooks"):
        assert not list((REPO_ROOT / hooks_dir).glob("*.sh"))
