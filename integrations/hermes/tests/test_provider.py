"""Tests for BasicMemoryProvider with the MCP actor mocked out."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from .conftest import ProjectActor, project_listing


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
    assert bm_tool == "bm_search"
    assert bm_args == {"project": "proj", "query": "hi", "page_size": 3}


def test_bm_projects_returns_routing_fields_verbatim(bm, fake_result):
    """bm_projects hands BM's JSON to the agent unchanged, so the fields a
    skill selects a project by (source, external_id, qualified_name) survive."""
    payload = {
        "projects": [
            {
                "name": "getting-started",
                "external_id": "6b1c3f0e-0f5e-4d6b-9a39-2a4f8c1d7e55",
                "source": "cloud",
                "qualified_name": "personal/getting-started",
            }
        ]
    }
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "proj"
    actor = MagicMock()
    actor.call.side_effect = lambda *_a, **_k: bm._extract_mcp_text(
        fake_result([json.dumps(payload)])
    )
    p._actor = actor

    out = json.loads(p.handle_tool_call("bm_projects", {}))

    assert actor.call.call_args[0][:2] == (
        "bm_project_list",
        {"output_format": "json"},
    )
    assert out == payload


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


def test_register_hands_hermes_a_new_tracked_instance_each_load(bm):
    """Hermes v0.22 refuses an instance another profile's manager already bound,
    so every load gets a fresh provider; atexit cleanup still tracks each one."""
    first_ctx, second_ctx = MagicMock(), MagicMock()
    bm.register(first_ctx)
    bm.register(second_ctx)
    first = first_ctx.register_memory_provider.call_args.args[0]
    second = second_ctx.register_memory_provider.call_args.args[0]
    assert first is not second
    assert first in bm._all_providers and second in bm._all_providers


def test_register_also_registers_bundled_skill(bm):
    """Plugin's bundled SKILL.md should auto-register when `hermes plugins install`
    drops the repo into ~/.hermes/plugins/. Avoids the manual symlink step."""
    fake_ctx = MagicMock()
    bm._all_providers.clear()
    bm.register(fake_ctx)
    fake_ctx.register_skill.assert_called_once()
    args, kwargs = fake_ctx.register_skill.call_args
    # First positional arg is the bare skill name
    assert args[0] == "basic-memory"
    # Second positional arg is the SKILL.md path; it should resolve to a real file
    assert args[1].name == "SKILL.md"
    assert args[1].is_file()
    bm._all_providers.clear()


def test_register_tolerates_old_hermes_without_register_skill(bm):
    """Older Hermes versions don't have ctx.register_skill; we shouldn't crash."""

    class _OldCtx:
        def __init__(self):
            self.calls = []

        def register_memory_provider(self, provider):
            self.calls.append(("memory", provider))

    ctx = _OldCtx()
    bm._all_providers.clear()
    bm.register(ctx)  # must not raise
    assert any(c[0] == "memory" for c in ctx.calls)
    bm._all_providers.clear()


def test_register_swallows_register_skill_errors(bm, caplog):
    """If register_skill raises (path validation, ABC mismatch, etc.) we log
    and continue — don't break the memory-provider registration."""
    fake_ctx = MagicMock()
    fake_ctx.register_skill.side_effect = ValueError("invalid skill name")
    bm._all_providers.clear()
    with caplog.at_level("WARNING"):
        bm.register(fake_ctx)
    fake_ctx.register_memory_provider.assert_called_once()
    assert "register_skill failed" in caplog.text
    bm._all_providers.clear()


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
    assert argv[1:] == ["tool", "install", "basic-memory", "--prerelease=allow", "--quiet"]


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


# ---- Project check through bm mcp (#2019) ----


@pytest.fixture
def startable(bm, monkeypatch, tmp_path):
    """Route initialize() to actor start; the test supplies the actor."""
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bm")
    monkeypatch.setattr(bm, "_init_failures", {})
    monkeypatch.setattr(bm, "_live_providers", {})

    def configure(actor, **cfg):
        monkeypatch.setattr(bm, "_BmMcpActor", actor)
        (tmp_path / "basic-memory.json").write_text(json.dumps(cfg))
        return bm.BasicMemoryProvider()

    return configure


def _start(p, tmp_path):
    p.initialize(session_id="s1", hermes_home=str(tmp_path))
    return p


def test_listed_project_initializes_without_creating_it(bm, startable, tmp_path):
    actor = ProjectActor(project_listing("main", "hermes-memory"))
    p = _start(startable(actor), tmp_path)
    assert p._initialized and p._actor is actor
    assert actor.commands() == ["list"]


def test_project_in_bm_but_not_in_legacy_config_json_initializes(
    bm, startable, monkeypatch, tmp_path
):
    """The #2019 failure: bm lists the project, but the file the plugin used to
    read (~/.basic-memory/config.json) does not. The plugin must not care."""
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".basic-memory" / "config.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"projects": {"main": {"path": "/x"}}}))

    actor = ProjectActor(project_listing("main", "hermes-memory"))
    p = _start(startable(actor), tmp_path)

    assert p._initialized
    assert actor.commands() == ["list"]


def test_missing_local_project_is_created_once_at_project_path(bm, startable, tmp_path):
    actor = ProjectActor(project_listing("main"))
    path = tmp_path / "notes"
    p = _start(startable(actor, project="fresh", project_path=str(path)), tmp_path)

    assert p._initialized
    assert actor.commands() == ["list", "create"]
    create_args = actor.calls[-1][1]
    assert create_args["name"] == "fresh" and create_args["path"] == str(path)
    assert path.is_dir()


def test_reinitialize_with_a_running_actor_rechecks_but_does_not_recreate(bm, startable, tmp_path):
    actor = ProjectActor(project_listing("hermes-memory"))
    p = _start(startable(actor), tmp_path)
    p.initialize(session_id="s2", hermes_home=str(tmp_path))
    assert p._initialized
    assert actor.commands() == ["list", "list"]


def test_missing_cloud_project_fails_with_the_command_to_register_it(bm, startable, tmp_path):
    actor = ProjectActor(project_listing("main"))
    p = _start(startable(actor, mode="cloud", project="team-cloud"), tmp_path)

    assert not p._initialized and p._actor is None
    assert actor.shutdowns == 1, "a provider that cannot serve keeps no bm mcp child"
    assert actor.commands() == ["list"], "cloud projects are never created implicitly"
    reason = bm._init_failures[bm._home_key(tmp_path)]
    assert "'team-cloud' is not in Basic Memory's project list" in reason
    assert "bm project add team-cloud --cloud" in reason


@pytest.mark.parametrize(
    ("actor_kwargs", "expected"),
    [
        (
            {"listing": json.dumps({"error": "database is locked"})},
            "could not list Basic Memory projects: database is locked",
        ),
        (
            {"listing": project_listing(), "create": json.dumps({"error": "path not allowed"})},
            "could not create project 'hermes-memory' at",
        ),
        (
            {"listing": "{}", "raises": TimeoutError("no answer in 60s")},
            "could not check project 'hermes-memory': no answer in 60s",
        ),
        ({"listing": json.dumps({"unexpected": []})}, "could not check project"),
    ],
)
def test_project_check_failures_are_recorded_and_release_bm_mcp(
    bm, startable, tmp_path, actor_kwargs, expected
):
    actor = ProjectActor(**actor_kwargs)
    p = _start(startable(actor, project_path=str(tmp_path / "notes")), tmp_path)

    assert not p._initialized and p._actor is None
    assert actor.shutdowns == 1
    assert expected in bm._init_failures[bm._home_key(tmp_path)]


def test_failed_recheck_on_a_reused_actor_uninitializes_the_provider(bm, startable, tmp_path):
    actor = ProjectActor(project_listing("hermes-memory"))
    p = _start(startable(actor), tmp_path)
    assert p._initialized

    actor.listing = json.dumps({"error": "gone"})
    p.initialize(session_id="s2", hermes_home=str(tmp_path))

    assert not p._initialized and p._actor is None


def test_failed_project_check_survives_a_shutdown_that_raises(bm, startable, tmp_path):
    actor = ProjectActor(project_listing("main"))
    actor.shutdown_raises = True
    p = _start(startable(actor, mode="cloud", project="team-cloud"), tmp_path)
    assert not p._initialized and p._actor is None
    assert "team-cloud" in bm._init_failures[bm._home_key(tmp_path)]


@pytest.mark.parametrize(
    "spelling",
    [
        "hermes-memory",
        "HERMES-MEMORY",
        "personal/hermes-memory",
        "00000000-0000-4000-8000-000000000001",
    ],
)
def test_project_listed_matches_every_routable_spelling(bm, spelling):
    rows = json.loads(project_listing("main", "hermes-memory"))["projects"]
    assert bm._project_listed(rows, spelling, local=True)


def test_project_listed_rejects_an_absent_project(bm):
    rows = json.loads(project_listing("main"))["projects"]
    assert not bm._project_listed(rows, "hermes-memory", local=False)


@pytest.mark.parametrize(
    ("source", "local", "listed"),
    [
        ("cloud", True, False),  # a cloud-only twin is not a local registration
        ("cloud", False, True),
        ("local+cloud", True, True),  # a local project attached to a cloud one
        ("local", True, True),
    ],
)
def test_project_listed_counts_only_local_registrations_in_local_mode(bm, source, local, listed):
    rows = json.loads(project_listing("hermes-memory", sources={"hermes-memory": source}))
    assert bm._project_listed(rows["projects"], "hermes-memory", local=local) is listed


def test_local_mode_creates_the_project_when_only_a_cloud_twin_exists(bm, startable, tmp_path):
    """A cloud-only project with the configured name must not satisfy local
    mode: Basic Memory would route the name to the cloud and upload local-mode
    transcripts there."""
    actor = ProjectActor(project_listing("hermes-memory", sources={"hermes-memory": "cloud"}))
    p = _start(startable(actor, project_path=str(tmp_path / "notes")), tmp_path)
    assert p._initialized
    assert actor.commands() == ["list", "create"]


def test_cloud_mode_accepts_a_cloud_only_project(bm, startable, tmp_path):
    actor = ProjectActor(project_listing("team-cloud", sources={"team-cloud": "cloud"}))
    p = _start(startable(actor, mode="cloud", project="team-cloud"), tmp_path)
    assert p._initialized
    assert actor.commands() == ["list"]
