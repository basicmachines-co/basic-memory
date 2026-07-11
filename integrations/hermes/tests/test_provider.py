"""Tests for BasicMemoryProvider with the MCP actor mocked out."""

from __future__ import annotations

import json
from unittest.mock import MagicMock


def test_is_available_no_mcp(bm, monkeypatch):
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", False)
    p = bm.BasicMemoryProvider()
    assert p.is_available() is False


def test_is_available_no_bm_no_uv(bm, monkeypatch):
    """No bm AND no uv → can't install, can't operate → unavailable."""
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: None)
    p = bm.BasicMemoryProvider()
    assert p.is_available() is False


def test_is_available_bm_present(bm, monkeypatch):
    """bm already installed → available regardless of uv."""
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bm")
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: None)
    p = bm.BasicMemoryProvider()
    assert p.is_available() is True


def test_is_available_bm_missing_but_uv_present(bm, monkeypatch):
    """bm missing but uv available → we can install bm at init time → available."""
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: "/fake/uv")
    p = bm.BasicMemoryProvider()
    assert p.is_available() is True


def test_name(bm):
    assert bm.BasicMemoryProvider().name == "basic-memory"
    assert bm.BasicMemoryProvider().name == bm.PROVIDER_NAME


def test_get_tool_schemas_unconditional(bm):
    """
    Regression: Hermes captures the schema list at *register* time, before
    `initialize()` runs. If get_tool_schemas() returns [] when uninitialized,
    Hermes builds _tool_to_provider with no entries for us and every
    subsequent bm_* invocation returns "Unknown tool: bm_*" forever.
    Schemas are static — return them unconditionally.
    """
    # Fresh provider, never initialized, should still expose all 10 schemas
    p = bm.BasicMemoryProvider()
    assert p._initialized is False
    schemas = p.get_tool_schemas()
    assert len(schemas) == 10
    names = {s["name"] for s in schemas}
    assert names == {
        "bm_search",
        "bm_read",
        "bm_write",
        "bm_edit",
        "bm_context",
        "bm_delete",
        "bm_move",
        "bm_recent",
        "bm_projects",
        "bm_workspaces",
    }

    # Initialized provider also returns 10 (idempotent)
    p._initialized = True
    assert len(p.get_tool_schemas()) == 10


def test_get_tool_schemas_returns_independent_copies(bm):
    """Mutating the returned list shouldn't affect the next call."""
    p = bm.BasicMemoryProvider()
    schemas = p.get_tool_schemas()
    schemas.clear()
    assert len(p.get_tool_schemas()) == 10


def test_handle_tool_call_uninitialized(bm):
    p = bm.BasicMemoryProvider()
    out = json.loads(p.handle_tool_call("bm_search", {"query": "x"}))
    assert "error" in out


def test_handle_tool_call_unknown_tool(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._actor = MagicMock()
    out = json.loads(p.handle_tool_call("bm_bogus", {}))
    assert "error" in out


def test_handle_tool_call_dispatches(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "proj"
    actor = MagicMock()
    actor.call.return_value = json.dumps({"results": []})
    p._actor = actor

    p.handle_tool_call("bm_search", {"query": "hi", "limit": 3})
    actor.call.assert_called_once()
    bm_tool, bm_args = actor.call.call_args[0][:2]
    assert bm_tool == "search_notes"
    assert bm_args == {"project": "proj", "query": "hi", "page_size": 3}


def test_handle_tool_call_missing_arg(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._actor = MagicMock()
    out = json.loads(p.handle_tool_call("bm_write", {"title": "x"}))
    assert "error" in out


def test_handle_tool_call_actor_failure(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "proj"
    actor = MagicMock()
    actor.call.side_effect = RuntimeError("boom")
    p._actor = actor
    out = json.loads(p.handle_tool_call("bm_search", {"query": "x"}))
    assert "error" in out
    assert p._failure_count == 1


def test_circuit_breaker_opens_after_5_failures(bm, monkeypatch):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "proj"
    actor = MagicMock()
    actor.call.side_effect = RuntimeError("boom")
    p._actor = actor

    for _ in range(5):
        p.handle_tool_call("bm_search", {"query": "x"})

    assert p._failure_pause_until > 0
    assert p._is_circuit_open() is True


def test_circuit_breaker_resets_after_pause(bm, monkeypatch):
    p = bm.BasicMemoryProvider()
    p._failure_count = 5
    p._failure_pause_until = 1.0  # already in the past
    monkeypatch.setattr(bm.time, "monotonic", lambda: 9999.0)
    assert p._is_circuit_open() is False
    assert p._failure_count == 0


def test_session_note_title_with_session_id(bm):
    from datetime import datetime, timezone

    p = bm.BasicMemoryProvider()
    p._session_started_at = datetime(2026, 5, 10, 13, 5, tzinfo=timezone.utc)
    p._session_id = "20260510_080249_571920"
    title = p._session_note_title()
    # Date appears once; trailing random component is the disambiguator
    assert title == "Hermes Session 2026-05-10 1305 571920"


def test_session_note_title_no_session_id(bm):
    from datetime import datetime, timezone

    p = bm.BasicMemoryProvider()
    p._session_started_at = datetime(2026, 5, 10, 13, 5, 42, tzinfo=timezone.utc)
    p._session_id = ""
    title = p._session_note_title()
    # Falls back to seconds for disambiguation
    assert title == "Hermes Session 2026-05-10 1305 42"


def test_session_note_title_short_session_id(bm):
    from datetime import datetime, timezone

    p = bm.BasicMemoryProvider()
    p._session_started_at = datetime(2026, 5, 10, 13, 5, tzinfo=timezone.utc)
    p._session_id = "abcdef"
    title = p._session_note_title()
    # No `_` in id → use last 6 chars
    assert title == "Hermes Session 2026-05-10 1305 abcdef"


def test_system_prompt_block_uninitialized(bm):
    p = bm.BasicMemoryProvider()
    assert p.system_prompt_block() == ""


def test_system_prompt_block_mentions_tools(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "test-proj"
    p._mode = "local"
    out = p.system_prompt_block()
    assert "bm_search" in out
    assert "test-proj" in out
    assert "local" in out


def test_system_prompt_block_steers_away_from_cli(bm):
    """
    Felix's training data is biased toward `bm tool ...` CLI patterns. The
    system_prompt_block must explicitly direct the model to the bm_* tools
    AND give a reason (latency) so it has a justification for following the
    directive. Without this nudge, agents reach for bash/terminal tools and
    pay 1-2s per call instead of ~0.1s.
    """
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "test-proj"
    p._mode = "local"
    out = p.system_prompt_block().lower()
    # Directive: don't use the bm CLI
    assert "do not shell out" in out or "do not use the" in out or "do not run" in out
    assert "bm" in out and "cli" in out
    # Reason given (latency / capture bypass)
    assert "mcp" in out  # the persistent connection is the mechanism we cite
    assert "spawn" in out or "process" in out  # cold-start cost is mentioned


def test_save_config_writes_json(bm, tmp_path):
    p = bm.BasicMemoryProvider()
    p.save_config({"mode": "local", "project": "x", "capture_per_turn": "true"}, str(tmp_path))
    written = json.loads((tmp_path / "basic-memory.json").read_text())
    assert written["mode"] == "local"
    assert written["project"] == "x"
    assert written["capture_per_turn"] is True  # coerced from "true"


def test_save_config_merges_existing(bm, tmp_path):
    cfg = tmp_path / "basic-memory.json"
    cfg.write_text(json.dumps({"mode": "cloud", "project": "old"}))
    p = bm.BasicMemoryProvider()
    p.save_config({"project": "new"}, str(tmp_path))
    after = json.loads(cfg.read_text())
    assert after["mode"] == "cloud"  # preserved
    assert after["project"] == "new"  # updated


def test_load_config_missing_returns_empty(bm, tmp_path):
    assert bm._load_config(str(tmp_path)) == {}


def test_load_config_corrupt_returns_empty(bm, tmp_path):
    (tmp_path / "basic-memory.json").write_text("{not json")
    assert bm._load_config(str(tmp_path)) == {}


def test_get_config_schema_shape(bm):
    schema = bm.BasicMemoryProvider().get_config_schema()
    keys = {entry["key"] for entry in schema}
    assert {
        "mode",
        "project",
        "project_path",
        "capture_per_turn",
        "capture_session_end",
        "capture_folder",
    }.issubset(keys)


def test_register_reuses_single_provider_per_process(bm):
    """Repeated plugin registration must not create providers that each own a bm mcp child."""
    first_ctx = MagicMock()
    second_ctx = MagicMock()

    bm.register(first_ctx)
    bm.register(second_ctx)

    first_provider = first_ctx.register_memory_provider.call_args.args[0]
    second_provider = second_ctx.register_memory_provider.call_args.args[0]
    assert first_provider is second_provider
    assert bm._provider_singleton is first_provider
    assert bm._active_providers == [first_provider]


def test_initialize_shuts_down_existing_actor_before_reinitializing(bm, monkeypatch, tmp_path):
    """Repeated initialize() calls must not orphan the previous bm mcp actor."""
    actors = []

    class _FakeActor:
        def __init__(self, argv):
            self.argv = argv
            self.shutdown_calls = 0
            actors.append(self)

        def start(self, timeout=25.0):
            return None

        def shutdown(self, timeout=5.0):
            self.shutdown_calls += 1

        def list_tools(self):
            return [{"name": name} for name in bm._HERMES_TO_BM.values()]

    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bm")
    monkeypatch.setattr(bm, "_BmMcpActor", _FakeActor)
    monkeypatch.setattr(bm.BasicMemoryProvider, "_ensure_local_project", lambda self: None)
    monkeypatch.setattr(bm.BasicMemoryProvider, "_verify_project_registered", lambda self: True)
    monkeypatch.setattr(bm.BasicMemoryProvider, "_server_argv", lambda self: ["/fake/bm", "mcp"])

    p = bm.BasicMemoryProvider()
    p.initialize(session_id="first", hermes_home=str(tmp_path))
    first_actor = p._actor
    p.initialize(session_id="second", hermes_home=str(tmp_path))

    assert len(actors) == 2
    assert first_actor is actors[0]
    assert actors[0].shutdown_calls == 1
    assert p._actor is actors[1]
    assert p._initialized is True


def test_register_also_registers_bundled_skill(bm):
    """Plugin's bundled SKILL.md should auto-register when `hermes plugins install`
    drops the repo into ~/.hermes/plugins/. Avoids the manual symlink step."""
    fake_ctx = MagicMock()
    bm._active_providers.clear()
    bm.register(fake_ctx)
    fake_ctx.register_skill.assert_called_once()
    args, kwargs = fake_ctx.register_skill.call_args
    # First positional arg is the bare skill name
    assert args[0] == "basic-memory"
    # Second positional arg is the SKILL.md path; it should resolve to a real file
    assert args[1].name == "SKILL.md"
    assert args[1].is_file()
    bm._active_providers.clear()


def test_register_tolerates_old_hermes_without_register_skill(bm):
    """Older Hermes versions don't have ctx.register_skill; we shouldn't crash."""

    class _OldCtx:
        def __init__(self):
            self.calls = []

        def register_memory_provider(self, provider):
            self.calls.append(("memory", provider))

    ctx = _OldCtx()
    bm._active_providers.clear()
    bm.register(ctx)  # must not raise
    assert any(c[0] == "memory" for c in ctx.calls)
    bm._active_providers.clear()


def test_register_swallows_register_skill_errors(bm, caplog):
    """If register_skill raises (path validation, ABC mismatch, etc.) we log
    and continue — don't break the memory-provider registration."""
    fake_ctx = MagicMock()
    fake_ctx.register_skill.side_effect = ValueError("invalid skill name")
    bm._active_providers.clear()
    with caplog.at_level("WARNING"):
        bm.register(fake_ctx)
    fake_ctx.register_memory_provider.assert_called_once()
    assert "register_skill failed" in caplog.text
    bm._active_providers.clear()


# ---- Edge cases ----


def test_handle_tool_call_with_none_args(bm):
    """args=None must not crash; should be coerced to {} and surface a missing-arg error."""
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._actor = MagicMock()
    out = json.loads(p.handle_tool_call("bm_search", None))  # type: ignore[arg-type]
    assert "error" in out


def test_handle_tool_call_unknown_does_not_invoke_actor(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._actor = MagicMock()
    p.handle_tool_call("bm_does_not_exist", {"x": 1})
    p._actor.call.assert_not_called()


def test_translate_args_unknown_tool_raises(bm):
    """Unknown tool names should raise KeyError so callers handle it explicitly."""
    import pytest

    with pytest.raises(KeyError):
        bm._translate_args("not_a_tool", {}, "proj")


# ---- Version metadata ----


def test_module_version_present(bm):
    import re

    assert hasattr(bm, "__version__")
    assert isinstance(bm.__version__, str)
    # Matches the Python release versions written by scripts/update_versions.py.
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:(?:b|rc)\d+)?", bm.__version__), bm.__version__


def test_module_version_matches_plugin_yaml(bm):
    """plugin.yaml ships to Hermes; __version__ is what tooling reads. Keep them in sync."""
    import os
    import re

    plugin_yaml = os.path.join(
        os.path.dirname(os.path.abspath(bm.__file__)),
        "plugin.yaml",
    )
    text = open(plugin_yaml).read()
    m = re.search(r"^\s*version\s*:\s*(\S+)\s*$", text, re.MULTILINE)
    assert m is not None, "plugin.yaml is missing a version field"
    assert m.group(1) == bm.__version__, (
        f"plugin.yaml version ({m.group(1)}) doesn't match __version__ ({bm.__version__})"
    )


# ---- uv bootstrap ----


def test_install_bm_via_uv_no_uv(bm, monkeypatch):
    """If uv isn't available, install returns None without trying to spawn."""
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: None)
    assert bm._install_bm_via_uv() is None


def test_install_bm_via_uv_runs_uv_tool_install(bm, monkeypatch):
    """Install shells out to `uv tool install basic-memory`."""
    calls: list = []

    class _Result:
        returncode = 0
        stdout = b""
        stderr = b""

    def _fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _Result()

    monkeypatch.setattr(bm, "_uv_binary_path", lambda: "/fake/uv")
    monkeypatch.setattr(bm.subprocess, "run", _fake_run)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bm-after-install")

    result = bm._install_bm_via_uv()
    assert result == "/fake/bm-after-install"
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[0] == "/fake/uv"
    assert argv[1:] == ["tool", "install", "basic-memory", "--quiet"]


def test_install_bm_via_uv_failed_returncode(bm, monkeypatch):
    """Non-zero exit logs and returns None — doesn't pretend success."""

    class _Result:
        returncode = 2
        stdout = b""
        stderr = b"network unreachable"

    monkeypatch.setattr(bm, "_uv_binary_path", lambda: "/fake/uv")
    monkeypatch.setattr(bm.subprocess, "run", lambda *a, **kw: _Result())

    assert bm._install_bm_via_uv() is None


def test_install_bm_via_uv_subprocess_exception(bm, monkeypatch):
    """If subprocess raises (timeout, OSError, etc.) we degrade to None, not crash."""
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: "/fake/uv")

    def _raise(*a, **kw):
        raise OSError("boom")

    monkeypatch.setattr(bm.subprocess, "run", _raise)
    assert bm._install_bm_via_uv() is None


def test_initialize_invokes_uv_install_when_bm_missing(bm, monkeypatch, tmp_path):
    """Cold-start path: bm absent, uv present → initialize triggers install."""
    install_calls: list = []

    def _fake_install():
        install_calls.append(True)
        return None  # install reports failure; initialize logs and returns

    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: "/fake/uv")
    monkeypatch.setattr(bm, "_install_bm_via_uv", _fake_install)

    p = bm.BasicMemoryProvider()
    p.initialize(session_id="test", hermes_home=str(tmp_path))

    assert install_calls == [True], "expected initialize() to attempt the install"
    assert p._initialized is False  # install reported failure → don't start actor


def test_initialize_skips_uv_install_when_bm_present(bm, monkeypatch, tmp_path):
    """Steady-state path: bm already installed → no install attempt."""
    install_calls: list = []

    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bm")

    def _fake_install():
        install_calls.append(True)
        return "/should-not-be-called"

    monkeypatch.setattr(bm, "_install_bm_via_uv", _fake_install)
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", False)  # short-circuit actor start

    p = bm.BasicMemoryProvider()
    p.initialize(session_id="test", hermes_home=str(tmp_path))

    assert install_calls == [], "should not invoke install when bm is already present"


def test_initialize_bails_when_no_bm_no_uv(bm, monkeypatch, tmp_path, caplog):
    """No bm, no uv → log clear error, don't try to install, don't initialize."""
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: None)

    install_calls: list = []
    monkeypatch.setattr(bm, "_install_bm_via_uv", lambda: install_calls.append(True) or None)

    p = bm.BasicMemoryProvider()
    with caplog.at_level("ERROR"):
        p.initialize(session_id="test", hermes_home=str(tmp_path))

    assert install_calls == []  # never attempted — no uv to call
    assert p._initialized is False
    assert "uv is not installed" in caplog.text or "uv" in caplog.text.lower()


# ---- Defaults: stay out of bm's app dir ----


def test_default_project_name_is_hermes_memory(bm):
    """The default project name no longer carries a hostname suffix.
    Each machine has its own isolated local store with this same name."""
    assert bm._default_project() == "hermes-memory"


def test_default_project_path_is_in_user_space(bm):
    """~/.basic-memory/ is reserved for bm's app state. Projects live in user space."""
    p = bm._default_project_path()
    assert ".basic-memory" not in p, (
        f"default project path must not live inside ~/.basic-memory/, got: {p}"
    )
    assert p.rstrip("/").endswith("hermes-memory")


# ---- bm config introspection ----


def test_bm_known_projects_missing_file(bm, monkeypatch, tmp_path):
    """When bm has never been run, return None (callers should treat as 'unknown')."""
    monkeypatch.setattr(bm, "_bm_config_path", lambda: tmp_path / "config.json")
    assert bm._bm_known_projects() is None


def test_bm_known_projects_corrupt_file(bm, monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json")
    monkeypatch.setattr(bm, "_bm_config_path", lambda: cfg)
    assert bm._bm_known_projects() is None


def test_bm_known_projects_returns_dict(bm, monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"projects": {"main": {}, "hermes-memory": {}}}))
    monkeypatch.setattr(bm, "_bm_config_path", lambda: cfg)
    result = bm._bm_known_projects()
    assert isinstance(result, dict)
    assert set(result.keys()) == {"main", "hermes-memory"}


def test_bm_known_projects_handles_non_dict_root(bm, monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(["not a dict"]))
    monkeypatch.setattr(bm, "_bm_config_path", lambda: cfg)
    assert bm._bm_known_projects() is None


# ---- Project verification ----


def test_verify_project_registered_no_bm_config(bm, monkeypatch):
    """No bm config yet → assume registration is fine; let downstream surface real failures."""
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: None)
    p = bm.BasicMemoryProvider()
    p._project = "anything-goes"
    assert p._verify_project_registered() is True


def test_verify_project_registered_present(bm, monkeypatch):
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: {"hermes-memory": {}, "main": {}})
    p = bm.BasicMemoryProvider()
    p._project = "hermes-memory"
    assert p._verify_project_registered() is True


def test_verify_project_registered_missing(bm, monkeypatch):
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: {"main": {}, "other": {}})
    p = bm.BasicMemoryProvider()
    p._project = "hermes-memory-cloud"
    assert p._verify_project_registered() is False


def test_log_missing_project_local_hint_includes_path(bm, caplog):
    p = bm.BasicMemoryProvider()
    p._mode = "local"
    p._project = "hermes-memory"
    p._project_path = "/tmp/somewhere"
    with caplog.at_level("ERROR"):
        p._log_missing_project()
    msg = caplog.text
    assert "hermes-memory" in msg
    assert "/tmp/somewhere" in msg
    assert "--cloud" not in msg


def test_log_missing_project_cloud_hint_uses_cloud_flag(bm, caplog):
    p = bm.BasicMemoryProvider()
    p._mode = "cloud"
    p._project = "hermes-memory-cloud"
    with caplog.at_level("ERROR"):
        p._log_missing_project()
    msg = caplog.text
    assert "hermes-memory-cloud" in msg
    assert "--cloud" in msg


# ---- initialize() bail-out on missing project ----


def test_initialize_bails_when_project_missing(bm, monkeypatch, tmp_path):
    """If bm config says the project doesn't exist, refuse to initialize."""
    # bm config exists, but our project isn't in it
    bm_cfg = tmp_path / ".basic-memory" / "config.json"
    bm_cfg.parent.mkdir(parents=True)
    bm_cfg.write_text(json.dumps({"projects": {"main": {}}}))
    monkeypatch.setattr(bm, "_bm_config_path", lambda: bm_cfg)

    # Cloud mode so _ensure_local_project doesn't auto-create
    plugin_cfg = tmp_path / "basic-memory.json"
    plugin_cfg.write_text(json.dumps({"mode": "cloud", "project": "not-registered"}))

    p = bm.BasicMemoryProvider()
    p.initialize(session_id="test", hermes_home=str(tmp_path))

    assert p._initialized is False
    assert p._actor is None


def test_initialize_proceeds_when_bm_config_absent(bm, monkeypatch, tmp_path):
    """If bm config doesn't exist (fresh install), don't false-reject — let actor try."""
    monkeypatch.setattr(bm, "_bm_config_path", lambda: tmp_path / "no-such" / "config.json")
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", False)  # shortcut: actor won't actually start

    plugin_cfg = tmp_path / "basic-memory.json"
    plugin_cfg.write_text(json.dumps({"mode": "cloud", "project": "anything"}))

    p = bm.BasicMemoryProvider()
    # We don't fully assert _initialized here because the actor won't start without
    # MCP — but we DO assert _verify_project_registered didn't gate us out before
    # actor-start was attempted.
    p.initialize(session_id="test", hermes_home=str(tmp_path))
    # Initialization fails at actor-start (MCP unavailable), not at verify.
    assert p._initialized is False  # expected — actor couldn't start
    # _project should have been set despite the failure (proves we got past verify)
    assert p._project == "anything"
