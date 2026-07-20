"""Manifest-level tests for the plugin uv hook scripts (Claude Code and Codex).

The scripts are the entire plugin hook surface: self-contained PEP 723
launchers with uv-resolved Basic Memory dependencies. Co-located tests pin
script behavior; this suite pins manifest wiring, executable bits, and each
plugin's dependency policy.
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
CODEX_GIT_DEPENDENCY_RE = re.compile(
    r'"basic-memory @ git\+https://github\.com/'
    r'basicmachines-co/basic-memory@([^\"]+)"'
)


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


@pytest.mark.parametrize(("event", "script_name"), EVENTS)
def test_claude_script_floor_matches_released_version(event: str, script_name: str) -> None:
    # Release drift between the PEP 723 floor and the package version fails
    # here (and in each script's co-located test) before a release lands.
    text = (REPO_ROOT / "plugins/claude-code/hooks" / script_name).read_text(encoding="utf-8")
    floors = re.findall(r'^# dependencies = \["basic-memory>=([^"]+)"\]$', text, re.MULTILINE)

    assert floors == [CURRENT_VERSION]


def test_codex_scripts_share_git_dependency_ref() -> None:
    refs = {
        ref
        for script_name in ("session_start.py", "pre_compact.py")
        for ref in CODEX_GIT_DEPENDENCY_RE.findall(
            (REPO_ROOT / "plugins/codex/hooks" / script_name).read_text(encoding="utf-8")
        )
    }

    assert len(refs) == 1


def test_no_shell_shims_remain() -> None:
    # The bash shims were replaced by the uv scripts; a stray .sh reappearing
    # would mean a manifest or installer regressed to the old launcher.
    for hooks_dir in ("plugins/claude-code/hooks", "plugins/codex/hooks"):
        assert not list((REPO_ROOT / hooks_dir).glob("*.sh"))
