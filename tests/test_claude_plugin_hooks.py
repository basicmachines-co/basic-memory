import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


def _resolve_bash_executable(*, platform_name: str = os.name) -> str | None:
    """Prefer Git Bash over Windows' WSL launcher for hook execution."""
    if platform_name == "nt":
        git_executable = shutil.which("git")
        if git_executable:
            git_bash = Path(git_executable).resolve().parent.parent / "bin" / "bash.exe"
            if git_bash.is_file():
                return str(git_bash)
    return shutil.which("bash")


BASH_EXECUTABLE = _resolve_bash_executable()
HOOK_RUNTIME_AVAILABLE = BASH_EXECUTABLE is not None and shutil.which("python3") is not None
pytestmark = pytest.mark.skipif(
    not HOOK_RUNTIME_AVAILABLE,
    reason="Claude Code hook tests require bash and python3",
)


@dataclass(frozen=True, slots=True)
class HookHarness:
    repo_root: Path
    home: Path
    bin_dir: Path
    command_log: Path

    def write_settings(self, directory: Path, name: str, basic_memory: dict[str, object]) -> None:
        settings_dir = directory / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / name).write_text(
            json.dumps({"basicMemory": basic_memory}),
            encoding="utf-8",
        )

    def run_hook(self, hook_name: str, payload: dict[str, str]) -> subprocess.CompletedProcess[str]:
        assert BASH_EXECUTABLE is not None
        env = os.environ.copy()
        env.update(
            {
                "BM_TEST_COMMAND_LOG": str(self.command_log),
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}{os.pathsep}{env['PATH']}",
            }
        )
        return subprocess.run(
            [BASH_EXECUTABLE, str(self.repo_root / "plugins/claude-code/hooks" / hook_name)],
            input=json.dumps(payload),
            capture_output=True,
            check=False,
            env=env,
            text=True,
        )

    def logged_commands(self) -> list[list[str]]:
        if not self.command_log.exists():
            return []
        return [json.loads(line) for line in self.command_log.read_text().splitlines()]


@pytest.fixture
def hook_harness(tmp_path: Path) -> HookHarness:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    command_log = tmp_path / "basic-memory-commands.jsonl"

    fake_basic_memory = bin_dir / "basic-memory"
    fake_basic_memory.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["BM_TEST_COMMAND_LOG"], "a", encoding="utf-8") as command_log:
    command_log.write(json.dumps(sys.argv[1:]) + "\\n")

if sys.argv[1:3] == ["tool", "search-notes"]:
    print(json.dumps({"results": []}))
""",
        encoding="utf-8",
    )
    fake_basic_memory.chmod(0o755)

    return HookHarness(
        repo_root=repo_root,
        home=home,
        bin_dir=bin_dir,
        command_log=command_log,
    )


def test_resolve_bash_executable_prefers_git_bash_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    git_root = tmp_path / "Git"
    git_executable = git_root / "cmd/git.exe"
    git_bash = git_root / "bin/bash.exe"
    git_executable.parent.mkdir(parents=True)
    git_executable.touch()
    git_bash.parent.mkdir(parents=True)
    git_bash.touch()

    def fake_which(command: str) -> str | None:
        if command == "git":
            return str(git_executable)
        if command == "bash":
            return "C:/Windows/System32/bash.exe"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    assert _resolve_bash_executable(platform_name="nt") == str(git_bash)


def test_session_start_uses_user_settings_without_project_config(
    hook_harness: HookHarness,
) -> None:
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "global-project", "captureFolder": "global-sessions"},
    )
    # Claude Code does not treat this as a user-level settings source. A stale
    # file must not silently reroute hooks away from the visible global config.
    hook_harness.write_settings(
        hook_harness.home,
        "settings.local.json",
        {"primaryProject": "stale-project"},
    )
    cwd = hook_harness.home / "work/repo/src"
    cwd.mkdir(parents=True)

    result = hook_harness.run_hook("session-start.sh", {"cwd": str(cwd)})

    assert result.returncode == 0, result.stderr
    assert "**Project:** global-project" in result.stdout
    assert "`global-sessions/`" in result.stdout
    assert "stale-project" not in result.stdout


def test_session_start_merges_nearest_ancestor_project_settings_over_user_settings(
    hook_harness: HookHarness,
) -> None:
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "global-project", "captureFolder": "global-sessions"},
    )
    project_root = hook_harness.home / "work/repo"
    hook_harness.write_settings(
        project_root,
        "settings.json",
        {"primaryProject": "project-override"},
    )
    hook_harness.write_settings(
        project_root,
        "settings.local.json",
        {"captureFolder": "local-sessions"},
    )
    cwd = project_root / "packages/client/src"
    cwd.mkdir(parents=True)

    result = hook_harness.run_hook("session-start.sh", {"cwd": str(cwd)})

    assert result.returncode == 0, result.stderr
    assert "**Project:** project-override" in result.stdout
    assert "`local-sessions/`" in result.stdout
    search_commands = [
        command
        for command in hook_harness.logged_commands()
        if command[:2] == ["tool", "search-notes"]
    ]
    assert len(search_commands) == 3
    assert all(
        command[command.index("--project") + 1] == "project-override" for command in search_commands
    )


def test_pre_compact_uses_merged_project_and_capture_folder(
    hook_harness: HookHarness,
    tmp_path: Path,
) -> None:
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "global-project", "captureFolder": "global-sessions"},
    )
    project_root = hook_harness.home / "work/repo"
    hook_harness.write_settings(
        project_root,
        "settings.json",
        {"primaryProject": "project-override"},
    )
    hook_harness.write_settings(
        project_root,
        "settings.local.json",
        {"captureFolder": "local-sessions"},
    )
    cwd = project_root / "src"
    cwd.mkdir(parents=True)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"message": {"role": "user", "content": "Ship the settings fallback"}}) + "\n",
        encoding="utf-8",
    )

    result = hook_harness.run_hook(
        "pre-compact.sh",
        {
            "cwd": str(cwd),
            "session_id": "session-123",
            "transcript_path": str(transcript),
        },
    )

    assert result.returncode == 0, result.stderr
    write_commands = [
        command
        for command in hook_harness.logged_commands()
        if command[:2] == ["tool", "write-note"]
    ]
    assert len(write_commands) == 1
    write_command = write_commands[0]
    assert write_command[write_command.index("--project") + 1] == "project-override"
    assert write_command[write_command.index("--folder") + 1] == "local-sessions"
