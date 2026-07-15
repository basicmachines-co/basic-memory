import json
import os
import shlex
import shutil
import subprocess
import sys
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
    note_log: Path
    config_dir: Path

    def write_settings(self, directory: Path, name: str, basic_memory: dict[str, object]) -> None:
        settings_dir = directory / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / name).write_text(
            json.dumps({"basicMemory": basic_memory}),
            encoding="utf-8",
        )

    def run_hook(
        self,
        hook_name: str,
        payload: dict[str, str],
        *,
        basic_memory_command: str | None = None,
        use_default_cli_discovery: bool = False,
        hooks_dir: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert BASH_EXECUTABLE is not None
        env = os.environ.copy()
        env.update(
            {
                # Isolate the harness event log (and any future data-dir use)
                # from the developer's real ~/.basic-memory and any
                # XDG_CONFIG_HOME leaking in from the host environment.
                "BASIC_MEMORY_CONFIG_DIR": str(self.config_dir),
                "BM_TEST_COMMAND_LOG": str(self.command_log),
                "BM_TEST_NOTE_LOG": str(self.note_log),
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}{os.pathsep}{env['PATH']}",
                "USERPROFILE": str(self.home),
            }
        )
        if use_default_cli_discovery:
            env.pop("BM_BIN", None)
        else:
            env["BM_BIN"] = basic_memory_command or shlex.join(
                [sys.executable, str(self.bin_dir / "basic memory")]
            )
        hooks_root = hooks_dir or (self.repo_root / "plugins/claude-code/hooks")
        return subprocess.run(
            [BASH_EXECUTABLE, str(hooks_root / hook_name)],
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

    def written_notes(self) -> list[str]:
        if not self.note_log.exists():
            return []
        content = self.note_log.read_text(encoding="utf-8")
        return [note for note in content.split("\n===NOTE-END===\n") if note.strip()]

    def event_log_files(self) -> list[Path]:
        events_root = self.config_dir / "events"
        if not events_root.exists():
            return []
        return sorted(events_root.rglob("events.jsonl"))


@pytest.fixture
def hook_harness(tmp_path: Path) -> HookHarness:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    command_log = tmp_path / "basic-memory-commands.jsonl"
    note_log = tmp_path / "basic-memory-notes.log"
    config_dir = tmp_path / "bm-config"

    fake_script = """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["BM_TEST_COMMAND_LOG"], "a", encoding="utf-8") as command_log:
    command_log.write(json.dumps(sys.argv[1:]) + "\\n")

if sys.argv[1:3] == ["tool", "search-notes"]:
    print(json.dumps({"results": []}))

if sys.argv[1:3] == ["tool", "write-note"]:
    note_log_path = os.environ.get("BM_TEST_NOTE_LOG")
    if note_log_path:
        with open(note_log_path, "a", encoding="utf-8") as note_log:
            note_log.write(sys.stdin.read())
            note_log.write("\\n===NOTE-END===\\n")
"""
    for command_name in ("basic memory", "basic-memory"):
        fake_basic_memory = bin_dir / command_name
        fake_basic_memory.write_text(fake_script, encoding="utf-8")
        fake_basic_memory.chmod(0o755)

    return HookHarness(
        repo_root=repo_root,
        home=home,
        bin_dir=bin_dir,
        command_log=command_log,
        note_log=note_log,
        config_dir=config_dir,
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


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows cannot execute the fixture's extensionless shebang script directly",
)
def test_session_start_preserves_raw_cli_path_with_spaces(hook_harness: HookHarness) -> None:
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "global-project"},
    )
    cwd = hook_harness.home / "work/repo"
    cwd.mkdir(parents=True)

    result = hook_harness.run_hook(
        "session-start.sh",
        {"cwd": str(cwd)},
        basic_memory_command=str(hook_harness.bin_dir / "basic memory"),
    )

    assert result.returncode == 0, result.stderr
    assert "**Project:** global-project" in result.stdout


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows cannot execute the fixture's extensionless shebang script directly",
)
def test_session_start_discovers_basic_memory_from_path(hook_harness: HookHarness) -> None:
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "global-project"},
    )
    cwd = hook_harness.home / "work/repo"
    cwd.mkdir(parents=True)

    result = hook_harness.run_hook(
        "session-start.sh",
        {"cwd": str(cwd)},
        use_default_cli_discovery=True,
    )

    assert result.returncode == 0, result.stderr
    assert "**Project:** global-project" in result.stdout
    assert hook_harness.logged_commands()


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


def _write_transcript(path: Path, opening: str) -> None:
    path.write_text(
        json.dumps({"message": {"role": "user", "content": opening}}) + "\n",
        encoding="utf-8",
    )


def _pre_compact_payload(harness: HookHarness, tmp_path: Path, opening: str) -> dict[str, str]:
    cwd = harness.home / "work/repo"
    cwd.mkdir(parents=True, exist_ok=True)
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, opening)
    return {
        "cwd": str(cwd),
        "session_id": "session-envelope-1",
        "transcript_path": str(transcript),
    }


def test_pre_compact_stamps_envelope_provenance_on_checkpoint(
    hook_harness: HookHarness,
    tmp_path: Path,
) -> None:
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "envelope-project"},
    )
    payload = _pre_compact_payload(hook_harness, tmp_path, "Trace envelope provenance")

    result = hook_harness.run_hook("pre-compact.sh", payload)

    assert result.returncode == 0, result.stderr
    notes = hook_harness.written_notes()
    assert len(notes) == 1
    note = notes[0]
    # Frontmatter fields make the checkpoint queryable by source and dedup-safe.
    assert "envelope_source: claude-code" in note
    assert "envelope_event: compaction_imminent" in note
    assert "envelope_hook: PreCompact" in note
    assert "idempotency_key: " in note
    # Provenance observations trace the note back to its producing session.
    assert "- [source] claude-code/session-envelope-1" in note
    assert "- [hook] PreCompact" in note


def test_pre_compact_event_log_is_opt_in_and_lives_in_data_dir(
    hook_harness: HookHarness,
    tmp_path: Path,
) -> None:
    # Without captureEvents the hook must not write any local event log.
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "envelope-project"},
    )
    payload = _pre_compact_payload(hook_harness, tmp_path, "Check the event log location")

    result = hook_harness.run_hook("pre-compact.sh", payload)

    assert result.returncode == 0, result.stderr
    assert hook_harness.event_log_files() == []

    # Opting in writes the log under the Basic Memory data dir, never the repo cwd.
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "envelope-project", "captureEvents": True},
    )
    result = hook_harness.run_hook("pre-compact.sh", payload)

    assert result.returncode == 0, result.stderr
    event_logs = hook_harness.event_log_files()
    assert len(event_logs) == 1
    assert not (Path(payload["cwd"]) / ".basic-memory").exists()
    events = [json.loads(line) for line in event_logs[0].read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "compaction_imminent"
    assert event["source"] == "claude-code"
    assert event["session_id"] == "session-envelope-1"
    assert event["idempotency_key"]


def test_pre_compact_event_retention_caps_local_event_log(
    hook_harness: HookHarness,
    tmp_path: Path,
) -> None:
    # A long opening keeps every event line above the rotation byte estimate,
    # so eventRetention=1 forces rotation as soon as a second line lands.
    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {
            "primaryProject": "envelope-project",
            "captureEvents": True,
            "eventRetention": 1,
        },
    )
    payload = _pre_compact_payload(hook_harness, tmp_path, "Retention check " + "x" * 400)

    runs = 4
    for _ in range(runs):
        result = hook_harness.run_hook("pre-compact.sh", payload)
        assert result.returncode == 0, result.stderr

    event_logs = hook_harness.event_log_files()
    assert len(event_logs) == 1
    lines = event_logs[0].read_text(encoding="utf-8").splitlines()
    # The default cap (1000) would keep all lines; rotation proves the
    # configured eventRetention reached the writer.
    assert 1 <= len(lines) < runs
    assert json.loads(lines[-1])["event_type"] == "compaction_imminent"


def test_installed_plugin_layout_imports_vendored_envelope_module(
    hook_harness: HookHarness,
    tmp_path: Path,
) -> None:
    # A marketplace install copies only the plugin directory — plugins/shared/
    # never ships. Running the hook from a bare copy of plugins/claude-code
    # proves the vendored harness_envelope module resolves without the repo
    # layout around it (regression: the import used to silently fail and
    # installed users got no envelope capture at all).
    installed_plugin = tmp_path / "installed/claude-code"
    shutil.copytree(hook_harness.repo_root / "plugins/claude-code", installed_plugin)
    assert not (tmp_path / "installed/shared").exists()

    hook_harness.write_settings(
        hook_harness.home,
        "settings.json",
        {"primaryProject": "envelope-project", "captureEvents": True},
    )
    payload = _pre_compact_payload(hook_harness, tmp_path, "Installed layout import")

    result = hook_harness.run_hook(
        "pre-compact.sh",
        payload,
        hooks_dir=installed_plugin / "hooks",
    )

    assert result.returncode == 0, result.stderr
    notes = hook_harness.written_notes()
    assert len(notes) == 1
    assert "envelope_source: claude-code" in notes[0]
    assert len(hook_harness.event_log_files()) == 1
