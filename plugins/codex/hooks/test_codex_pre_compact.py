"""Tests for the Codex PreCompact uv hook script."""

import os
import re
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("pre_compact.py")
GIT_DEPENDENCY_RE = re.compile(
    r'"basic-memory @ git\+https://github\.com/'
    r'basicmachines-co/basic-memory@([^"]+)"'
)


def test_script_fails_open_with_empty_home(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="{}",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0


def test_dependency_is_pinned_to_a_basic_memory_git_ref() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert len(GIT_DEPENDENCY_RE.findall(text)) == 1


def test_metadata_and_command_shape() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert text.count("# /// script") == 1
    assert re.search(r'^# requires-python = ">=3\.12"$', text, re.MULTILINE)
    assert 'VERB = "pre-compact"' in text
    assert 'HARNESS = "codex"' in text
