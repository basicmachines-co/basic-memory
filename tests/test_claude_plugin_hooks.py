"""E2E tests for the plugin hook shims (Claude Code and Codex).

The shims are the entire plugin hook surface: resolve the Basic Memory CLI
(BM_BIN → basic-memory/bm on PATH → uvx at a released floor) and exec
``bm hook <event> --harness <harness>`` with the hook JSON passed through on
stdin. All behavior lives in the package (covered by tests/cli/); these tests
pin the shim contract itself with fake binaries that record argv and stdin.
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The floor the shims pass to uvx must be the released version — read it from
# the package so `scripts/update_versions.py` keeps this suite green.
_version_match = re.search(
    r'^__version__ = "(.+)"$',
    (REPO_ROOT / "src/basic_memory/__init__.py").read_text(encoding="utf-8"),
    re.MULTILINE,
)
assert _version_match is not None, "no __version__ in src/basic_memory/__init__.py"
CURRENT_VERSION = _version_match.group(1)

# (plugin hooks dir, --harness value the shims must pass)
PLUGINS = [
    pytest.param("plugins/claude-code/hooks", "claude", id="claude-code"),
    pytest.param("plugins/codex/hooks", "codex", id="codex"),
]
EVENTS = [
    pytest.param("session-start.sh", "session-start", id="session-start"),
    pytest.param("pre-compact.sh", "pre-compact", id="pre-compact"),
]

# A fake CLI that records its argv (one arg per line) and its stdin, so tests
# can assert which binary the shim resolved and what flowed through. Pure bash
# builtins, and deliberately shebang-less: the tests replace PATH with the
# fake bin dirs, so `#!/usr/bin/env bash` couldn't find bash — the shim's
# `exec` relies on bash's ENOEXEC fallback to run the file as a bash script.
FAKE_CLI = """{{ printf '%s\\n' "$@"; }} > "$BM_SHIM_LOG_DIR/{name}.argv"
IFS= read -r -d '' stdin_data || true
printf '%s' "$stdin_data" > "$BM_SHIM_LOG_DIR/{name}.stdin"
"""

# A pre-hook CLI: it predates the `hook` command group, so the shim's
# `hook --help` probe fails the way Click's "No such command" does (exit 2). Any
# other invocation records argv like the normal fake. Used to prove the shim
# falls back to the uvx floor instead of exec-ing a CLI that cannot serve hooks.
STALE_FAKE_CLI = """if [ "$1" = "hook" ]; then
    echo "Error: No such command 'hook'." >&2
    exit 2
fi
{{ printf '%s\\n' "$@"; }} > "$BM_SHIM_LOG_DIR/{name}.argv"
"""


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
pytestmark = pytest.mark.skipif(
    BASH_EXECUTABLE is None,
    reason="hook shim tests require bash",
)


@dataclass(frozen=True, slots=True)
class ShimHarness:
    bin_dir: Path
    log_dir: Path

    def add_fake(
        self, name: str, directory: Path | None = None, *, template: str = FAKE_CLI
    ) -> Path:
        """Install a fake recording CLI named `name` (default: on the fake PATH)."""
        target_dir = directory or self.bin_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        fake = target_dir / name
        fake.write_text(template.format(name=name), encoding="utf-8")
        fake.chmod(0o755)
        return fake

    def run(
        self,
        plugin_hooks_dir: str,
        script: str,
        payload: dict[str, str] | None = None,
        *,
        bm_bin: str | None = None,
        claude_project_dir: str | None = None,
        path_dirs: list[Path] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert BASH_EXECUTABLE is not None
        env = os.environ.copy()
        # PATH is replaced (not prepended) so a developer's real basic-memory
        # install can never satisfy the resolver and mask an ordering bug.
        dirs = path_dirs if path_dirs is not None else [self.bin_dir]
        env["PATH"] = os.pathsep.join(str(d) for d in dirs)
        # as_posix() keeps the paths bash-friendly under Git Bash on Windows.
        env["BM_SHIM_LOG_DIR"] = self.log_dir.as_posix()
        env.pop("BM_BIN", None)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if bm_bin is not None:
            env["BM_BIN"] = bm_bin
        if claude_project_dir is not None:
            env["CLAUDE_PROJECT_DIR"] = claude_project_dir
        return subprocess.run(
            [BASH_EXECUTABLE, str(REPO_ROOT / plugin_hooks_dir / script)],
            input=json.dumps(payload if payload is not None else {"cwd": "/work/repo"}),
            capture_output=True,
            check=False,
            env=env,
            text=True,
        )

    def argv(self, name: str) -> list[str] | None:
        record = self.log_dir / f"{name}.argv"
        if not record.exists():
            return None
        return record.read_text(encoding="utf-8").splitlines()

    def stdin(self, name: str) -> str | None:
        record = self.log_dir / f"{name}.stdin"
        return record.read_text(encoding="utf-8") if record.exists() else None


@pytest.fixture
def shim(tmp_path: Path) -> ShimHarness:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "log"
    bin_dir.mkdir()
    log_dir.mkdir()
    return ShimHarness(bin_dir=bin_dir, log_dir=log_dir)


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


# --- Resolver order and exec contract ---


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
@pytest.mark.parametrize(("script", "verb"), EVENTS)
def test_shim_prefers_basic_memory_and_passes_stdin(
    shim: ShimHarness, hooks_dir: str, harness: str, script: str, verb: str
) -> None:
    shim.add_fake("basic-memory")
    shim.add_fake("bm")
    shim.add_fake("uvx")
    payload = {"cwd": "/work/repo", "session_id": "s-1"}

    result = shim.run(hooks_dir, script, payload)

    assert result.returncode == 0, result.stderr
    assert shim.argv("basic-memory") == ["hook", verb, "--harness", harness]
    # Stdin passthrough: the hook JSON arrives at the CLI byte-identical.
    assert shim.stdin("basic-memory") == json.dumps(payload)
    assert shim.argv("bm") is None
    assert shim.argv("uvx") is None


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
def test_shim_prefers_bm_before_uvx(shim: ShimHarness, hooks_dir: str, harness: str) -> None:
    shim.add_fake("bm")
    shim.add_fake("uvx")

    result = shim.run(hooks_dir, "session-start.sh")

    assert result.returncode == 0, result.stderr
    assert shim.argv("bm") == ["hook", "session-start", "--harness", harness]
    assert shim.argv("uvx") is None


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
def test_shim_falls_back_to_uvx_with_released_floor(
    shim: ShimHarness, hooks_dir: str, harness: str
) -> None:
    shim.add_fake("uvx")

    result = shim.run(hooks_dir, "session-start.sh")

    assert result.returncode == 0, result.stderr
    # The floor must be the released version so a cold uvx resolves a
    # basic-memory that ships the `bm hook` verbs.
    assert shim.argv("uvx") == [
        f"basic-memory>={CURRENT_VERSION}",
        "hook",
        "session-start",
        "--harness",
        harness,
    ]


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
def test_shim_falls_back_to_uv_tool_run_when_no_uvx(
    shim: ShimHarness, hooks_dir: str, harness: str
) -> None:
    # Some installs ship `uv` without the `uvx` shim on PATH; `uv tool run` is
    # the same launcher and must be used rather than falling through to no-op.
    shim.add_fake("uv")

    result = shim.run(hooks_dir, "session-start.sh")

    assert result.returncode == 0, result.stderr
    assert shim.argv("uv") == [
        "tool",
        "run",
        f"basic-memory>={CURRENT_VERSION}",
        "hook",
        "session-start",
        "--harness",
        harness,
    ]


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
def test_shim_skips_stale_path_install_without_hook_support(
    shim: ShimHarness, hooks_dir: str, harness: str
) -> None:
    # A pre-hook basic-memory left on PATH must not shadow the uvx floor:
    # exec-ing it would error on `hook`, breaking fail-open. The shim probes for
    # `hook` support and falls back to the released floor when it is missing.
    shim.add_fake("basic-memory", template=STALE_FAKE_CLI)
    shim.add_fake("uvx")

    result = shim.run(hooks_dir, "session-start.sh")

    assert result.returncode == 0, result.stderr
    assert shim.argv("uvx") == [
        f"basic-memory>={CURRENT_VERSION}",
        "hook",
        "session-start",
        "--harness",
        harness,
    ]
    # The stale CLI is probed (hook --help exits 2) but never serves the hook.
    assert shim.argv("basic-memory") is None


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
def test_shim_exits_silently_when_only_stale_install_present(
    shim: ShimHarness, hooks_dir: str, harness: str
) -> None:
    # Stale basic-memory, nothing else resolvable: better silent than exec a CLI
    # that errors on `hook` — the probe failing must still fail open.
    shim.add_fake("basic-memory", template=STALE_FAKE_CLI)

    result = shim.run(hooks_dir, "session-start.sh")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert shim.argv("basic-memory") is None


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
@pytest.mark.parametrize(("script", "verb"), EVENTS)
def test_shim_exits_silently_when_nothing_resolvable(
    shim: ShimHarness, hooks_dir: str, harness: str, script: str, verb: str
) -> None:
    # Fail-open: no BM_BIN, empty PATH — the plugin must be invisible.
    result = shim.run(hooks_dir, script)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
@pytest.mark.parametrize(("script", "verb"), EVENTS)
def test_shim_exits_zero_when_resolved_command_fails_at_runtime(
    shim: ShimHarness, hooks_dir: str, harness: str, script: str, verb: str, tmp_path: Path
) -> None:
    # Fail-open: a launcher that resolves but errors at runtime (a cold uvx that
    # cannot reach PyPI, an unbuildable floor, a bad BM_BIN) must not propagate
    # its non-zero exit — the shim runs the command rather than tail-exec-ing it.
    failing = tmp_path / "failing-bm"
    failing.write_text("exit 17\n", encoding="utf-8")
    failing.chmod(0o755)

    result = shim.run(hooks_dir, script, bm_bin=str(failing))

    assert result.returncode == 0, result.stderr


# --- BM_BIN override ---


@pytest.mark.parametrize(("hooks_dir", "harness"), PLUGINS)
def test_bm_bin_overrides_path_resolution(shim: ShimHarness, hooks_dir: str, harness: str) -> None:
    shim.add_fake("basic-memory")
    custom = shim.add_fake("custom-bm")

    result = shim.run(hooks_dir, "session-start.sh", bm_bin=str(custom))

    assert result.returncode == 0, result.stderr
    assert shim.argv("custom-bm") == ["hook", "session-start", "--harness", harness]
    assert shim.argv("basic-memory") is None


def test_bm_bin_executable_path_with_spaces_stays_one_word(
    shim: ShimHarness, tmp_path: Path
) -> None:
    spaced = shim.add_fake("basic memory", directory=tmp_path / "with space")

    result = shim.run("plugins/claude-code/hooks", "session-start.sh", bm_bin=str(spaced))

    assert result.returncode == 0, result.stderr
    assert shim.argv("basic memory") == ["hook", "session-start", "--harness", "claude"]


def test_bm_bin_multi_token_launcher_word_splits(shim: ShimHarness) -> None:
    shim.add_fake("uvx")

    result = shim.run("plugins/claude-code/hooks", "session-start.sh", bm_bin="uvx basic-memory")

    assert result.returncode == 0, result.stderr
    assert shim.argv("uvx") == ["basic-memory", "hook", "session-start", "--harness", "claude"]


# --- Project-dir plumbing ---


@pytest.mark.parametrize(("script", "verb"), EVENTS)
def test_claude_shim_passes_claude_project_dir(shim: ShimHarness, script: str, verb: str) -> None:
    shim.add_fake("basic-memory")

    result = shim.run("plugins/claude-code/hooks", script, claude_project_dir="/work/repo root")

    assert result.returncode == 0, result.stderr
    assert shim.argv("basic-memory") == [
        "hook",
        verb,
        "--harness",
        "claude",
        "--project-dir",
        "/work/repo root",
    ]


def test_codex_shim_ignores_claude_project_dir(shim: ShimHarness) -> None:
    # Codex has no project-dir env contract; mapping comes from the payload cwd.
    shim.add_fake("basic-memory")

    result = shim.run("plugins/codex/hooks", "session-start.sh", claude_project_dir="/work/repo")

    assert result.returncode == 0, result.stderr
    assert shim.argv("basic-memory") == ["hook", "session-start", "--harness", "codex"]
