"""
Tests for the plugin-owned /bm-* slash commands.

Covers:
- registration through ctx.register_command (forward-compat path)
- PluginManager reach-in (production path with current Hermes collector)
- per-handler behavior: usage text, uninitialized provider, happy path,
  and exception → plain-text error.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from .conftest import FakeSession, make_scripted_actor


# ---------------------------------------------------------------------------
# Helpers for the reach-in tests
# ---------------------------------------------------------------------------


class _ProviderCollectorLike:
    """
    Mirror of Hermes's real `_ProviderCollector` shape: captures
    `register_memory_provider` and no-ops everything else. NOT a MagicMock —
    `hasattr(collector, "register_command")` must return False, matching the
    real collector.
    """

    def __init__(self):
        self.provider = None

    def register_memory_provider(self, provider):
        self.provider = provider


class _FakePluginManager:
    """Stand-in for hermes_cli.plugins.PluginManager — just the bits we touch."""

    def __init__(self):
        self._plugin_commands: dict = {}
        self._plugin_skills: dict = {}


def _install_fake_hermes_cli(monkeypatch, *, resolve_returns=None):
    """
    Insert a fake `hermes_cli.plugins` (with `_ensure_plugins_discovered`) and
    `hermes_cli.commands` (with `resolve_command`) into sys.modules so the
    reach-in's lazy imports resolve. Returns the FakePluginManager instance
    so tests can assert against its registries.

    resolve_returns: optional mapping of command name → truthy/falsy value
    the fake resolve_command should return. Use a truthy value to simulate a
    built-in conflict for that name.
    """
    fake_mgr = _FakePluginManager()

    plugins_mod = types.ModuleType("hermes_cli.plugins")

    def _ensure_plugins_discovered(force: bool = False):
        return fake_mgr

    plugins_mod._ensure_plugins_discovered = _ensure_plugins_discovered  # type: ignore[attr-defined]

    commands_mod = types.ModuleType("hermes_cli.commands")

    def _resolve_command(name: str):
        if resolve_returns and name in resolve_returns:
            return resolve_returns[name]
        return None

    commands_mod.resolve_command = _resolve_command  # type: ignore[attr-defined]

    hermes_cli = types.ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins_mod)
    monkeypatch.setitem(sys.modules, "hermes_cli.commands", commands_mod)

    return fake_mgr


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_EXPECTED_COMMANDS = {
    "bm-search",
    "bm-read",
    "bm-context",
    "bm-recent",
    "bm-status",
    "bm-remember",
    "bm-project",
    "bm-workspace",
}


def test_register_wires_up_all_slash_commands_on_modern_ctx(bm):
    """Forward-compat path: when ctx supports register_command (e.g. after the
    upstream collector patch lands, or for plugins loaded via PluginContext),
    every /bm-* command is registered through that path."""
    ctx = MagicMock()
    bm._active_providers.clear()
    bm.register(ctx)
    names = {call.args[0] for call in ctx.register_command.call_args_list}
    assert names == _EXPECTED_COMMANDS
    bm._active_providers.clear()


def test_register_command_calls_include_description_and_args_hint(bm):
    ctx = MagicMock()
    bm._active_providers.clear()
    bm.register(ctx)
    for call in ctx.register_command.call_args_list:
        name, handler = call.args[0], call.args[1]
        kwargs = call.kwargs
        assert callable(handler)
        assert "description" in kwargs and kwargs["description"]
        assert "args_hint" in kwargs  # may be empty string for no-arg commands
    bm._active_providers.clear()


def test_register_tolerates_old_hermes_without_register_command(bm):
    """Plugins must not crash on Hermes < v0.11.0 (no register_command)."""

    class _OldCtx:
        def __init__(self):
            self.memory_calls = []

        def register_memory_provider(self, provider):
            self.memory_calls.append(provider)

    ctx = _OldCtx()
    bm._active_providers.clear()
    bm.register(ctx)  # must not raise
    assert len(ctx.memory_calls) == 1
    bm._active_providers.clear()


def test_register_swallows_register_command_errors(bm, caplog):
    """If one register_command call fails, the others — and provider
    registration — still proceed."""
    ctx = MagicMock()
    ctx.register_command.side_effect = ValueError("name collision with builtin")
    bm._active_providers.clear()
    with caplog.at_level("WARNING"):
        bm.register(ctx)
    ctx.register_memory_provider.assert_called_once()
    assert ctx.register_command.call_count == len(_EXPECTED_COMMANDS)
    assert "register_command" in caplog.text
    bm._active_providers.clear()


# ---------------------------------------------------------------------------
# Reach-in (production path): ctx is the no-op _ProviderCollector
# ---------------------------------------------------------------------------


def test_reach_in_writes_all_commands_to_plugin_manager(bm, monkeypatch):
    """Regression for Codex P1: with the real collector shape (no
    register_command method), reach into PluginManager and write commands
    directly. The unit suite previously used MagicMock, which masked this
    silent-skip by making every attribute exist."""
    fake_mgr = _install_fake_hermes_cli(monkeypatch)
    ctx = _ProviderCollectorLike()
    assert not hasattr(ctx, "register_command"), (
        "test collector must mirror real _ProviderCollector — no register_command"
    )

    bm._active_providers.clear()
    bm.register(ctx)
    try:
        assert ctx.provider is not None  # memory provider still registered
        assert set(fake_mgr._plugin_commands.keys()) == _EXPECTED_COMMANDS
        for name, entry in fake_mgr._plugin_commands.items():
            assert callable(entry["handler"])
            assert entry["plugin"] == "basic-memory"
            assert "description" in entry
            assert "args_hint" in entry
    finally:
        bm._active_providers.clear()


def test_reach_in_writes_skill_to_plugin_manager(bm, monkeypatch):
    """Same silent-skip applies to register_skill — the bundled skill never
    landed in real installs prior to this fix. Reach-in writes the namespaced
    entry directly."""
    fake_mgr = _install_fake_hermes_cli(monkeypatch)
    ctx = _ProviderCollectorLike()
    bm._active_providers.clear()
    bm.register(ctx)
    try:
        assert "basic-memory:basic-memory" in fake_mgr._plugin_skills
        skill = fake_mgr._plugin_skills["basic-memory:basic-memory"]
        assert skill["plugin"] == "basic-memory"
        assert skill["bare_name"] == "basic-memory"
        assert isinstance(skill["path"], Path)
        assert skill["path"].name == "SKILL.md"
    finally:
        bm._active_providers.clear()


def test_reach_in_skips_command_conflicting_with_builtin(bm, monkeypatch, caplog):
    """Mirror Hermes's PluginContext.register_command guard — when
    resolve_command(name) returns a truthy value, skip that command and
    log a warning rather than overwriting a built-in."""
    # Simulate /bm-search colliding with a built-in.
    fake_mgr = _install_fake_hermes_cli(monkeypatch, resolve_returns={"bm-search": object()})
    ctx = _ProviderCollectorLike()
    bm._active_providers.clear()
    with caplog.at_level("WARNING"):
        bm.register(ctx)
    try:
        assert "bm-search" not in fake_mgr._plugin_commands
        # Other commands still landed
        assert "bm-read" in fake_mgr._plugin_commands
        assert "conflicts with a built-in" in caplog.text
    finally:
        bm._active_providers.clear()


def test_reach_in_degrades_when_hermes_cli_missing(bm, monkeypatch, caplog):
    """If hermes_cli.plugins isn't importable (e.g. running outside a Hermes
    install), the reach-in must log and continue — never crash the plugin's
    memory-provider registration."""
    # Don't install fake modules; force import to fail.
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", None)  # type: ignore[arg-type]
    ctx = _ProviderCollectorLike()
    bm._active_providers.clear()
    with caplog.at_level("DEBUG"):
        bm.register(ctx)  # must not raise
    try:
        assert ctx.provider is not None
        # Either DEBUG message logged or nothing — both acceptable degrade modes.
    finally:
        bm._active_providers.clear()


def test_reach_in_degrades_when_plugin_manager_missing_attrs(bm, monkeypatch):
    """Forward-compat: if Hermes ever refactors _plugin_commands /
    _plugin_skills away, the reach-in must not crash."""
    fake_mgr = _FakePluginManager()
    # Strip the attrs to simulate the rename/refactor
    del fake_mgr._plugin_commands
    del fake_mgr._plugin_skills

    plugins_mod = types.ModuleType("hermes_cli.plugins")
    plugins_mod._ensure_plugins_discovered = lambda force=False: fake_mgr  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins_mod)

    ctx = _ProviderCollectorLike()
    bm._active_providers.clear()
    bm.register(ctx)  # must not raise
    bm._active_providers.clear()


def test_reach_in_normalizes_command_names(bm, monkeypatch):
    """Reach-in must mirror Hermes's name normalization (lowercase, strip,
    leading slash removed, spaces → hyphens). All our names are already
    canonical, so this is a defensive check on the transform itself."""
    fake_mgr = _install_fake_hermes_cli(monkeypatch)
    ctx = _ProviderCollectorLike()
    bm._active_providers.clear()
    bm.register(ctx)
    try:
        for name in fake_mgr._plugin_commands:
            assert name == name.lower()
            assert not name.startswith("/")
            assert " " not in name
    finally:
        bm._active_providers.clear()


def test_reach_in_entries_match_hermes_internal_shape(bm, monkeypatch):
    """The dict shape PluginContext.register_command writes
    (plugins.py:447-452) is the contract Hermes's dispatch reads. Our
    reach-in must produce byte-identical entries."""
    fake_mgr = _install_fake_hermes_cli(monkeypatch)
    ctx = _ProviderCollectorLike()
    bm._active_providers.clear()
    bm.register(ctx)
    try:
        entry = fake_mgr._plugin_commands["bm-search"]
        assert set(entry.keys()) == {"handler", "description", "plugin", "args_hint"}
        assert callable(entry["handler"])
        assert entry["description"]  # non-empty string
        assert entry["plugin"] == "basic-memory"
        assert isinstance(entry["args_hint"], str)
    finally:
        bm._active_providers.clear()


# ---------------------------------------------------------------------------
# Per-handler tests
# ---------------------------------------------------------------------------


def _ready_provider(bm, session: FakeSession | None = None):
    """Build a provider in 'initialized' state with a scripted actor."""
    provider = bm.BasicMemoryProvider()
    actor = make_scripted_actor(bm, session=session)
    actor.start()
    provider._actor = actor
    provider._initialized = True
    provider._project = "test-proj"
    return provider, actor


def _handlers_by_name(bm, provider):
    return {name: handler for name, handler, _, _ in bm._build_slash_commands(provider)}


# ---- Usage strings ----


@pytest.mark.parametrize(
    "name,args",
    [
        ("bm-search", ""),
        ("bm-search", "help"),
        ("bm-read", ""),
        ("bm-read", "-h"),
        ("bm-context", ""),
        ("bm-remember", ""),
        ("bm-remember", "--help"),
        # Commands that take no args use 'help' to surface their usage line
        ("bm-recent", "help"),
        ("bm-status", "help"),
        ("bm-project", "help"),
        ("bm-workspace", "help"),
    ],
)
def test_usage_returned_for_empty_or_help_args(bm, name, args):
    provider = bm.BasicMemoryProvider()  # not initialized — usage path shouldn't need it
    handlers = _handlers_by_name(bm, provider)
    out = handlers[name](args)
    assert isinstance(out, str)
    assert out.lower().startswith("usage:")


# ---- Uninitialized provider ----


@pytest.mark.parametrize(
    "name,args",
    [
        ("bm-search", "hello"),
        ("bm-read", "some/note"),
        ("bm-context", "memory://x"),
        ("bm-recent", ""),
        ("bm-remember", "a thought"),
        ("bm-project", ""),
    ],
)
def test_handler_init_failure_returns_message(bm, monkeypatch, name, args):
    provider = bm.BasicMemoryProvider()
    monkeypatch.setattr(provider, "initialize", lambda *a, **kw: None)
    handlers = _handlers_by_name(bm, provider)
    out = handlers[name](args)
    assert "not initialized" in out
    assert name in out  # message includes command name


def test_handler_lazily_initializes_provider_for_slash_command(bm, monkeypatch):
    provider = bm.BasicMemoryProvider()
    calls = []

    def fake_initialize(*args, **kwargs):
        calls.append((args, kwargs))
        session = FakeSession(default_response={"results": []})
        actor = make_scripted_actor(bm, session=session)
        actor.start()
        provider._actor = actor
        provider._initialized = True

    monkeypatch.setattr(provider, "initialize", fake_initialize)
    out = _handlers_by_name(bm, provider)["bm-search"]("widgets")
    assert "No results" in out
    assert len(calls) == 1
    assert calls[0][1]["session_id"].startswith("slash:bm-search:")


# ---- /bm-status ----


def test_bm_status_renders_provider_state(bm, monkeypatch):
    provider = bm.BasicMemoryProvider()
    provider._mode = "local"
    provider._project = "demo"
    provider._project_path = "/tmp/demo"
    provider._capture_per_turn = True
    provider._capture_session_end = False
    provider._capture_folder = "transcripts"
    provider._remember_folder = "inbox"
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bin/bm")
    out = _handlers_by_name(bm, provider)["bm-status"]("")
    assert "demo" in out
    assert "/tmp/demo" in out
    assert "/fake/bin/bm" in out
    assert "Initialized: no" in out
    assert "transcripts" in out and "inbox" in out


# ---- /bm-search ----


def test_bm_search_happy_path(bm):
    session = FakeSession()
    session.stub(
        "search_notes",
        lambda args: {
            "results": [
                {"title": "Decisions", "permalink": "decisions/foo", "content": "we chose X"},
                {"title": "Plan", "permalink": "plans/p1", "preview": "next quarter"},
            ]
        },
    )
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-search"]("widgets")
        assert "Decisions" in out
        assert "decisions/foo" in out
        assert "we chose X" in out
        # Args sent to BM
        call = session.calls[-1]
        assert call[0] == "search_notes"
        assert call[1]["query"] == "widgets"
        assert call[1]["project"] == "test-proj"
        assert call[1]["output_format"] == "json"
    finally:
        actor.shutdown()


def test_bm_search_empty_results(bm):
    session = FakeSession(default_response={"results": []})
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-search"]("missing")
        assert "No results" in out
        assert "missing" in out
    finally:
        actor.shutdown()


def test_bm_search_actor_exception_returns_plain_string(bm):
    session = FakeSession()

    def _boom(_args):
        raise RuntimeError("MCP transport closed")

    session.stub("search_notes", _boom)
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-search"]("anything")
        assert isinstance(out, str)
        assert out.startswith("bm-search:")
        assert "MCP transport closed" in out
    finally:
        actor.shutdown()


# ---- /bm-read ----


def test_bm_read_returns_text_body(bm):
    """BM's read_note returns markdown wrapped in {"text": "..."} once our
    extractor wraps the non-JSON response. The handler should unwrap and
    return the bare markdown."""
    session = FakeSession()
    session.stub(
        "read_note",
        # FakeSession serializes whatever the handler returns; emit the JSON
        # the wrapper would produce for a markdown response.
        lambda args: {"text": "# Foo\n\nbody text"},
    )
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-read"]("foo")
        assert out == "# Foo\n\nbody text"
        assert session.calls[-1][1]["identifier"] == "foo"
    finally:
        actor.shutdown()


# ---- /bm-recent ----


def test_bm_recent_default_timeframe(bm):
    session = FakeSession(default_response={"results": []})
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-recent"]("")
        assert "7d" in out
        assert session.calls[-1][1]["timeframe"] == "7d"
    finally:
        actor.shutdown()


def test_bm_recent_custom_timeframe(bm):
    session = FakeSession()
    session.stub(
        "recent_activity",
        lambda args: {"results": [{"title": "Recent thing", "permalink": "x/y"}]},
    )
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-recent"]("2 weeks")
        assert "2 weeks" in out
        assert "Recent thing" in out
        assert session.calls[-1][1]["timeframe"] == "2 weeks"
    finally:
        actor.shutdown()


def test_bm_recent_bare_list_shape(bm):
    """Regression: BM's `recent_activity(output_format="json")` returns a bare
    `list[dict]` (signature: `-> str | list[dict]`), not a dict-with-results.
    The handler must surface those rows, not report "no activity"."""
    session = FakeSession()
    session.stub(
        "recent_activity",
        lambda args: [
            {"title": "Edited yesterday", "permalink": "notes/a", "content": "blob"},
            {"title": "Edited 3d ago", "permalink": "notes/b"},
        ],
    )
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-recent"]("")
        assert "Edited yesterday" in out
        assert "Edited 3d ago" in out
        assert "No activity" not in out
    finally:
        actor.shutdown()


# ---- /bm-remember ----


def test_bm_remember_derives_title_from_first_line(bm):
    captured = {}

    def _write(args):
        captured.update(args)
        return {"permalink": "bm-remember/note-perm"}

    session = FakeSession()
    session.stub("write_note", _write)
    provider, actor = _ready_provider(bm, session)
    provider._remember_folder = "bm-remember"
    try:
        out = _handlers_by_name(bm, provider)["bm-remember"](
            "Quarterly OKR review notes\n\nWe agreed to ship X."
        )
        assert "Saved:" in out
        assert "bm-remember/note-perm" in out
        assert captured["title"] == "Quarterly OKR review notes"
        assert captured["directory"] == "bm-remember"
        assert "manual-capture" in captured["tags"]
    finally:
        actor.shutdown()


def test_bm_remember_long_first_line_truncated_to_80(bm):
    session = FakeSession(default_response={"permalink": "x"})
    provider, actor = _ready_provider(bm, session)
    try:
        long_line = "A" * 200
        _handlers_by_name(bm, provider)["bm-remember"](long_line)
        title = session.calls[-1][1]["title"]
        assert len(title) == 80
    finally:
        actor.shutdown()


def test_bm_remember_uses_configured_folder(bm):
    session = FakeSession(default_response={"permalink": "x"})
    provider, actor = _ready_provider(bm, session)
    provider._remember_folder = "scratch"
    try:
        _handlers_by_name(bm, provider)["bm-remember"]("hello")
        assert session.calls[-1][1]["directory"] == "scratch"
    finally:
        actor.shutdown()


# ---- /bm-project ----


def test_bm_project_lists_and_marks_active(bm):
    session = FakeSession()
    session.stub(
        "list_memory_projects",
        lambda args: {
            "projects": [
                {"name": "other-proj"},
                {"name": "test-proj"},
            ]
        },
    )
    provider, actor = _ready_provider(bm, session)
    try:
        out = _handlers_by_name(bm, provider)["bm-project"]("")
        assert "other-proj" in out
        assert "test-proj" in out
        # Active project line includes the marker
        active_line = next(line for line in out.splitlines() if "test-proj" in line)
        assert "active" in active_line
    finally:
        actor.shutdown()


# ---- /bm-workspace ----


def test_bm_workspace_local_mode_message(bm):
    provider, actor = _ready_provider(bm)
    provider._mode = "local"
    try:
        out = _handlers_by_name(bm, provider)["bm-workspace"]("")
        assert "Cloud" in out
        assert "local" in out
    finally:
        actor.shutdown()


def test_bm_workspace_cloud_mode_lists(bm):
    session = FakeSession()
    session.stub(
        "list_workspaces",
        lambda args: {
            "workspaces": [
                {
                    "name": "Personal",
                    "workspace_type": "personal",
                    "role": "owner",
                    "is_default": True,
                },
                {"name": "Acme", "workspace_type": "team", "role": "member"},
            ]
        },
    )
    provider, actor = _ready_provider(bm, session)
    provider._mode = "cloud"
    try:
        out = _handlers_by_name(bm, provider)["bm-workspace"]("")
        assert "Personal" in out
        assert "Acme" in out
        assert "default" in out
    finally:
        actor.shutdown()


def test_bm_workspace_lazily_initializes_before_mode_check(bm, monkeypatch):
    session = FakeSession()
    session.stub(
        "list_workspaces",
        lambda args: {"workspaces": [{"name": "Personal", "workspace_type": "personal"}]},
    )
    provider = bm.BasicMemoryProvider()
    calls = []

    def fake_initialize(*args, **kwargs):
        calls.append((args, kwargs))
        actor = make_scripted_actor(bm, session=session)
        actor.start()
        provider._actor = actor
        provider._initialized = True
        provider._mode = "cloud"

    monkeypatch.setattr(provider, "initialize", fake_initialize)
    try:
        out = _handlers_by_name(bm, provider)["bm-workspace"]("")
        assert "Personal" in out
        assert "no workspaces to list" not in out
        assert calls
        assert session.calls[-1][0] == "list_workspaces"
    finally:
        if provider._actor is not None:
            provider._actor.shutdown()


# ---- _unwrap_json_or_text helper ----


def test_unwrap_passes_through_raw_string(bm):
    assert bm._unwrap_json_or_text("plain text") == "plain text"


def test_unwrap_returns_inner_json_when_text_wraps_json(bm):
    outer = json.dumps({"text": json.dumps({"a": 1})})
    assert bm._unwrap_json_or_text(outer) == {"a": 1}


def test_unwrap_returns_text_value_when_inner_is_markdown(bm):
    outer = json.dumps({"text": "# Heading\n\nbody"})
    assert bm._unwrap_json_or_text(outer) == "# Heading\n\nbody"


def test_unwrap_returns_dict_when_top_level_json(bm):
    outer = json.dumps({"results": [1, 2]})
    assert bm._unwrap_json_or_text(outer) == {"results": [1, 2]}


# ---- _remember_title ----


def test_remember_title_strips_markdown_heading(bm):
    assert bm._remember_title("# Decisions\n\nbody") == "Decisions"


def test_remember_title_skips_blank_lines(bm):
    assert bm._remember_title("\n\nFirst real line\nrest") == "First real line"


def test_remember_title_falls_back_to_timestamp(bm):
    title = bm._remember_title("   \n\n")
    assert title.startswith("Note ")
