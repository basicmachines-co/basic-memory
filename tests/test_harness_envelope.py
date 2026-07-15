"""Unit tests for the shared harness envelope module (plugins/shared/).

The module is stdlib-only and lives outside the basic_memory package (it ships
vendored inside each plugin), so it is loaded straight from its file path.
"""

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_MODULE = REPO_ROOT / "plugins/shared/harness_envelope.py"
VENDORED_MODULES = (
    REPO_ROOT / "plugins/claude-code/hooks/harness_envelope.py",
    REPO_ROOT / "plugins/codex/hooks/harness_envelope.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("harness_envelope", CANONICAL_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass field resolution looks the module up in sys.modules, so the
    # standard importlib recipe registers it before executing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


he = _load_module()


def _envelope(**overrides):
    kwargs = {
        "event_type": he.COMPACTION_IMMINENT,
        "source": "claude-code",
        "session_id": "session-1",
        "cwd": "/tmp/workdir",
        "project_hint": "test-project",
        "hook_name": "PreCompact",
        "timestamp": "2026-07-15T10:00:00+00:00",
    }
    kwargs.update(overrides)
    return he.create_envelope(**kwargs)


# --- Vendored copies stay in sync (mirrors `just package-check` vendor-check) ---


def test_vendored_copies_match_canonical_module() -> None:
    canonical = CANONICAL_MODULE.read_bytes()
    for vendored in VENDORED_MODULES:
        assert vendored.is_file(), f"missing vendored copy: {vendored}"
        assert vendored.read_bytes() == canonical, (
            f"{vendored} is out of sync; run: python3 scripts/sync_plugin_shared.py"
        )


# --- Redaction (recursive) ---


def test_redact_payload_redacts_nested_dict_secrets() -> None:
    payload = {"config": {"api_key": "sk-" + "a" * 30, "region": "us-east-1"}}

    redacted = he.redact_payload(payload)

    assert redacted["config"]["api_key"] == "[REDACTED]"
    assert redacted["config"]["region"] == "us-east-1"


def test_redact_payload_redacts_secrets_inside_lists() -> None:
    payload = {
        "env_dump": ["PATH=/usr/bin", "AWS_SECRET_ACCESS_KEY=" + "s" * 30],
        "steps": [{"auth_token": "t" * 30}, {"note": "safe"}],
    }

    redacted = he.redact_payload(payload)

    assert redacted["env_dump"][0] == "PATH=/usr/bin"
    assert redacted["env_dump"][1] == "[REDACTED]"
    assert redacted["steps"][0]["auth_token"] == "[REDACTED]"
    assert redacted["steps"][1]["note"] == "safe"


def test_redact_payload_denied_key_redacts_whole_subtree() -> None:
    payload = {"auth": {"user": "alice", "nested": {"deep": "value"}}}

    redacted = he.redact_payload(payload)

    assert redacted["auth"] == "[REDACTED]"


def test_redact_payload_applies_paths_and_truncation_at_depth() -> None:
    home_ssh = str(Path("~/.ssh/id_rsa").expanduser())
    payload = {"files": [{"path": home_ssh, "preview": "y" * 600}]}

    redacted = he.redact_payload(payload)

    entry = redacted["files"][0]
    assert entry["path"] == "[REDACTED_PATH]"
    assert entry["preview"].endswith("…[truncated]")
    assert len(entry["preview"]) < 600


def test_redact_payload_extra_keys_apply_at_depth() -> None:
    payload = {"outer": {"internal_id": "abc"}}

    redacted = he.redact_payload(payload, extra_redact_keys=["internal_id"])

    assert redacted["outer"]["internal_id"] == "[REDACTED]"


def test_create_envelope_redacts_payload_recursively() -> None:
    envelope = _envelope(payload_summary={"nested": {"password": "p" * 30}})

    assert envelope.payload_summary["nested"]["password"] == "[REDACTED]"


# --- Idempotency ---


def test_idempotency_key_is_stable_within_the_same_minute() -> None:
    key_a = he.idempotency_key("codex", "s-1", "PreCompact", "2026-07-15T10:00:01+00:00")
    key_b = he.idempotency_key("codex", "s-1", "PreCompact", "2026-07-15T10:00:59+00:00")

    assert key_a == key_b
    assert len(key_a) == 16
    assert int(key_a, 16) is not None  # 16 hex chars


def test_idempotency_key_differs_across_minutes_and_inputs() -> None:
    base = he.idempotency_key("codex", "s-1", "PreCompact", "2026-07-15T10:00:00+00:00")

    assert base != he.idempotency_key("codex", "s-1", "PreCompact", "2026-07-15T10:01:00+00:00")
    assert base != he.idempotency_key(
        "claude-code", "s-1", "PreCompact", "2026-07-15T10:00:00+00:00"
    )
    assert base != he.idempotency_key("codex", "s-2", "PreCompact", "2026-07-15T10:00:00+00:00")


def test_create_envelope_rejects_unknown_event_type() -> None:
    with pytest.raises(ValueError):
        _envelope(event_type="tool_called")


# --- Event log location, retention, and rotation ---


def test_event_log_honors_basic_memory_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "bm-config"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))
    envelope = _envelope()

    assert he.append_to_event_log(envelope) is True

    log_path = he.event_log_path(envelope)
    assert log_path.is_file()
    assert config_dir / "events" in log_path.parents
    # The slug derives from the project hint, so the same project shares a log
    # across checkouts.
    assert log_path.parent.name.startswith("test-project-")
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["idempotency_key"] == envelope.idempotency_key


def test_event_log_falls_back_to_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    envelope = _envelope()

    log_path = he.event_log_path(envelope)

    assert tmp_path / "xdg" / "basic-memory" / "events" in log_path.parents


def test_event_log_slug_falls_back_to_cwd_without_project_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "bm-config"))
    envelope = _envelope(project_hint="", cwd="/tmp/some/workdir")

    log_path = he.event_log_path(envelope)

    assert log_path.parent.name.startswith("tmp-some-workdir-")


def test_normalize_cap_falls_back_on_junk_values() -> None:
    assert he._normalize_cap(None) == he.DEFAULT_EVENT_LOG_CAP
    assert he._normalize_cap("500") == he.DEFAULT_EVENT_LOG_CAP
    assert he._normalize_cap(True) == he.DEFAULT_EVENT_LOG_CAP
    assert he._normalize_cap(0) == he.DEFAULT_EVENT_LOG_CAP
    assert he._normalize_cap(-5) == he.DEFAULT_EVENT_LOG_CAP
    assert he._normalize_cap(250) == 250


def test_event_log_rotation_keeps_newest_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "bm-config"))
    # Each payload keeps the serialized line above APPROX_BYTES_PER_EVENT, so a
    # cap of 1 trips the size threshold on every append after the first.
    filler = "z" * he.APPROX_BYTES_PER_EVENT

    last_envelope = None
    for index in range(5):
        last_envelope = _envelope(
            session_id=f"session-{index}",
            payload_summary={"opening": filler},
        )
        assert he.append_to_event_log(last_envelope, cap=1) is True

    assert last_envelope is not None
    lines = he.event_log_path(last_envelope).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["session_id"] == "session-4"


# --- Installed Codex plugin layout ---


def test_installed_codex_plugin_stamps_envelope_from_vendored_module(
    tmp_path: Path,
) -> None:
    # Codex installs copy only plugins/codex/ — run the pre-compact hook from a
    # bare copy (no plugins/shared sibling) and prove the vendored module loads
    # and stamps the checkpoint.
    installed_plugin = tmp_path / "installed/codex"
    shutil.copytree(REPO_ROOT / "plugins/codex", installed_plugin)
    assert not (tmp_path / "installed/shared").exists()

    workdir = tmp_path / "workdir"
    (workdir / ".codex").mkdir(parents=True)
    (workdir / ".codex/basic-memory.json").write_text(
        json.dumps(
            {
                "basicMemory": {
                    "primaryProject": "codex-project",
                    "captureEvents": True,
                    "eventRetention": 100,
                }
            }
        ),
        encoding="utf-8",
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"message": {"role": "user", "content": "Vendored codex import"}}) + "\n",
        encoding="utf-8",
    )

    note_log = tmp_path / "notes.log"
    fake_cli = tmp_path / "fake_basic_memory.py"
    fake_cli.write_text(
        "import os, sys\n"
        "with open(os.environ['BM_TEST_NOTE_LOG'], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(sys.stdin.read())\n",
        encoding="utf-8",
    )

    config_dir = tmp_path / "bm-config"
    env = os.environ.copy()
    env.update(
        {
            # Isolate the event log from the developer's real data dir; the
            # explicit config dir also wins over any host XDG_CONFIG_HOME.
            "BASIC_MEMORY_CONFIG_DIR": str(config_dir),
            "BM_BIN": shlex.join([sys.executable, str(fake_cli)]),
            "BM_TEST_NOTE_LOG": str(note_log),
        }
    )
    result = subprocess.run(
        [sys.executable, str(installed_plugin / "hooks/pre-compact.py")],
        input=json.dumps(
            {
                "cwd": str(workdir),
                "session_id": "codex-session-1",
                "transcript_path": str(transcript),
            }
        ),
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    note = note_log.read_text(encoding="utf-8")
    assert "envelope_source: codex" in note
    assert "- [source] codex/codex-session-1" in note
    event_logs = sorted((config_dir / "events").rglob("events.jsonl"))
    assert len(event_logs) == 1
    event = json.loads(event_logs[0].read_text(encoding="utf-8").splitlines()[0])
    assert event["source"] == "codex"
    assert event["project_hint"] == "codex-project"
