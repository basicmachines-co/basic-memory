"""
/bm-status readiness (#2016).

The plugin starts `bm mcp` lazily, and /bm-status must never start it. These
tests pin the four states the command distinguishes: ready and idle, ready and
running, unavailable (a missing dependency or registration), and a failed start.
"""

from __future__ import annotations

import json
import weakref

import pytest

from .conftest import FakeSession, make_scripted_actor


@pytest.fixture
def profile(bm, monkeypatch, tmp_path):
    """A Hermes profile with every dependency present and nothing started."""
    monkeypatch.setattr(bm, "_current_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(bm, "_live_providers", {})
    monkeypatch.setattr(bm, "_init_failures", {})
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bin/bm")
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: None)
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: {"hermes-memory": {}})
    monkeypatch.setattr(
        bm.BasicMemoryProvider, "initialize", lambda *a, **k: pytest.fail("status started bm")
    )
    return tmp_path


def _status(bm) -> str:
    return {n: h for n, h, _, _ in bm._build_slash_commands()}["bm-status"]("")


# ---- Ready ----


def test_healthy_profile_with_nothing_started_is_ready_and_idle(bm, profile):
    out = _status(bm)
    assert "Status:      ready" in out
    assert "MCP process: idle (starts on the next /bm-* command or agent session)" in out
    assert "Reason:" not in out and "Note:" not in out


def test_idle_status_reports_the_profiles_configured_project(bm, profile, monkeypatch):
    """Without a live provider, status used to describe built-in defaults."""
    (profile / "basic-memory.json").write_text(
        json.dumps({"project": "team-notes", "project_path": "/srv/team-notes"})
    )
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: {"team-notes": {}})
    out = _status(bm)
    assert "Project:     team-notes" in out
    assert "Path:        /srv/team-notes" in out
    assert "Status:      ready" in out


def test_running_provider_is_ready_and_running(bm, profile, monkeypatch):
    actor = make_scripted_actor(bm, session=FakeSession())
    actor.start()
    try:
        live = bm.BasicMemoryProvider()
        live._actor, live._initialized, live._hermes_home = actor, True, str(profile)
        monkeypatch.setitem(bm._live_providers, bm._home_key(profile), weakref.ref(live))
        out = _status(bm)
        assert "Status:      ready" in out
        assert "MCP process: running" in out
    finally:
        actor.shutdown()


def test_provider_whose_bm_mcp_exited_reports_stopped(bm, profile, monkeypatch):
    actor = make_scripted_actor(bm, session=FakeSession())
    actor.start()
    actor.shutdown()
    live = bm.BasicMemoryProvider()
    live._actor, live._initialized, live._hermes_home = actor, True, str(profile)
    monkeypatch.setitem(bm._live_providers, bm._home_key(profile), weakref.ref(live))
    out = _status(bm)
    assert "Status:      ready" in out
    assert "MCP process: stopped (restarts on the next call)" in out


def test_setup_the_first_start_will_do_is_noted(bm, profile, monkeypatch):
    """A missing bm with uv present, or an unregistered local project, is not a
    failure: initialize() installs and registers them."""
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: "/fake/bin/uv")
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: {})
    out = _status(bm)
    assert "Status:      ready" in out
    assert "Note:        bm CLI is installed with uv on first use" in out
    assert "Note:        project 'hermes-memory' is registered at" in out


# ---- Unavailable ----


def test_missing_mcp_package_is_unavailable(bm, profile, monkeypatch):
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", False)
    out = _status(bm)
    assert "Status:      unavailable" in out
    assert "Reason:      the mcp Python package is not installed" in out
    assert "MCP process: not running" in out


def test_missing_bm_and_uv_is_unavailable(bm, profile, monkeypatch):
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    out = _status(bm)
    assert "Status:      unavailable" in out
    assert "Reason:      bm CLI not found, and uv is not installed to add it" in out


def test_unregistered_cloud_project_is_unavailable(bm, profile, monkeypatch):
    (profile / "basic-memory.json").write_text(
        json.dumps({"mode": "cloud", "project": "hermes-memory-cloud"})
    )
    out = _status(bm)
    assert "Status:      unavailable" in out
    assert "Reason:      project 'hermes-memory-cloud' is not registered with bm." in out
    assert "bm project add hermes-memory-cloud --cloud" in out


# ---- Failed start ----


class _ActorThatFailsToStart:
    def __init__(self, argv):
        self.argv = argv

    def start(self, timeout):
        raise RuntimeError("boom")

    def shutdown(self, timeout):
        pass


def test_failed_start_is_reported_until_a_start_succeeds(bm, monkeypatch, tmp_path):
    monkeypatch.setattr(bm, "_live_providers", {})
    monkeypatch.setattr(bm, "_init_failures", {})
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bin/bm")
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: {"hermes-memory": {}})
    monkeypatch.setattr(bm, "_current_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(bm, "_BmMcpActor", _ActorThatFailsToStart)

    # An agent's provider fails to start. It never registers as live.
    failed = bm.BasicMemoryProvider()
    failed.initialize("s1", hermes_home=str(tmp_path))
    assert not failed._initialized

    out = _status(bm)
    assert "Status:      unavailable" in out
    assert "Reason:      last start failed: `bm mcp` failed to start: boom" in out
    assert "MCP process: not running (retried on next use)" in out

    # A later successful start for the same profile clears the failure.
    failed._hermes_home = str(tmp_path)
    failed._mark_session_ready("s2")
    assert bm._home_key(tmp_path) not in bm._init_failures


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda bm, mp: mp.setattr(bm, "_MCP_AVAILABLE", False), "the mcp Python package"),
        (
            lambda bm, mp: (
                mp.setattr(bm, "_bm_binary_path", lambda: None),
                mp.setattr(bm, "_uv_binary_path", lambda: None),
            ),
            "bm CLI not found",
        ),
        (
            lambda bm, mp: (
                mp.setattr(bm, "_bm_binary_path", lambda: None),
                mp.setattr(bm, "_uv_binary_path", lambda: "/fake/bin/uv"),
                mp.setattr(bm, "_install_bm_via_uv", lambda: None),
            ),
            "uv tool install basic-memory",
        ),
        (lambda bm, mp: mp.setattr(bm, "_bm_known_projects", lambda: {}), "is not registered"),
        (
            lambda bm, mp: mp.setattr(
                bm.BasicMemoryProvider,
                "_server_argv",
                lambda self: (_ for _ in ()).throw(RuntimeError("no argv")),
            ),
            "cannot build the `bm mcp` command: no argv",
        ),
    ],
)
def test_every_initialize_failure_path_records_its_reason(bm, monkeypatch, tmp_path, setup, reason):
    monkeypatch.setattr(bm, "_init_failures", {})
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bin/bm")
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: {"hermes-memory": {}})
    # Local mode would register a missing project; cloud mode refuses it.
    (tmp_path / "basic-memory.json").write_text(json.dumps({"mode": "cloud"}))
    setup(bm, monkeypatch)

    p = bm.BasicMemoryProvider()
    p.initialize("s1", hermes_home=str(tmp_path))

    assert not p._initialized
    assert reason in bm._init_failures[bm._home_key(tmp_path)]


def test_status_help_explains_lazy_startup(bm):
    out = bm._SLASH_USAGE["bm-status"]
    assert out.startswith("Usage: /bm-status")
    assert "never starts bm" in out and "'idle' is healthy" in out
