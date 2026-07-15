"""
Process-leak regression tests (issue #1017).

Two leak paths, two guards:
1. initialize() called twice on one provider must not allocate a second
   _BmMcpActor (each actor owns a `bm mcp` child process).
2. SIGTERM must run the same cleanup that atexit runs — Python's default
   SIGTERM action skips atexit, orphaning every `bm mcp` child on gateway
   restarts.
"""

from __future__ import annotations

import json
import signal
import threading


class TrackingActor:
    """Stand-in for _BmMcpActor that records lifecycle calls, no threads."""

    instances: list["TrackingActor"] = []

    def __init__(self, argv, env=None):
        self.argv = list(argv)
        self.started = False
        self.alive = False
        self.shutdown_calls: list[float] = []
        self.shutdown_raises = False
        TrackingActor.instances.append(self)

    def start(self, timeout: float = 25.0) -> None:
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def list_tools(self):
        return [{"name": n, "description": ""} for n in ("search_notes", "read_note")]

    def shutdown(self, timeout: float = 5.0) -> None:
        self.shutdown_calls.append(timeout)
        self.alive = False
        if self.shutdown_raises:
            raise RuntimeError("shutdown boom")

    def call(self, tool_name, arguments, timeout: float = 30.0) -> str:
        return json.dumps({"ok": True})


def _make_initializable_provider(bm, monkeypatch, tmp_path):
    """Route initialize() past the environment checks to actor allocation."""
    TrackingActor.instances = []
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bm")
    monkeypatch.setattr(bm, "_bm_known_projects", lambda: None)
    monkeypatch.setattr(bm, "_BmMcpActor", TrackingActor)
    # Cloud mode skips _ensure_local_project's `bm project add` subprocess.
    (tmp_path / "basic-memory.json").write_text(
        json.dumps({"mode": "cloud", "project": "test-proj"})
    )
    return bm.BasicMemoryProvider()


# ---- Duplicate initialize() ----


def test_double_initialize_reuses_running_actor(bm, monkeypatch, tmp_path):
    """Regression #1017: a second initialize() must not spawn a second bm mcp."""
    p = _make_initializable_provider(bm, monkeypatch, tmp_path)

    p.initialize(session_id="one", hermes_home=str(tmp_path))
    assert p._initialized is True
    assert len(TrackingActor.instances) == 1
    first = TrackingActor.instances[0]
    assert p._actor is first
    p._session_note_id = "hermes-sessions/session-one"
    p._first_user_msg = "first session opener"

    p.initialize(session_id="two", hermes_home=str(tmp_path))
    assert p._initialized is True
    assert len(TrackingActor.instances) == 1, "second initialize() allocated a new actor"
    assert p._actor is first
    assert first.shutdown_calls == [], "healthy actor must be reused, not restarted"
    assert p._session_id == "two"
    assert p._session_note_id is None
    assert p._first_user_msg is None


def test_initialize_replaces_dead_actor_after_shutdown(bm, monkeypatch, tmp_path):
    """A crashed actor is shut down (child reaped) before a replacement starts."""
    p = _make_initializable_provider(bm, monkeypatch, tmp_path)

    p.initialize(session_id="one", hermes_home=str(tmp_path))
    first = TrackingActor.instances[0]
    first.alive = False  # simulate actor loop death (bm mcp crashed)
    p._session_note_id = "hermes-sessions/session-one"
    p._first_user_msg = "session opener"

    p.initialize(session_id="one", hermes_home=str(tmp_path))
    assert first.shutdown_calls, "dead actor was replaced without shutdown"
    assert len(TrackingActor.instances) == 2
    assert p._actor is TrackingActor.instances[1]
    assert p._initialized is True
    assert p._session_note_id == "hermes-sessions/session-one"
    assert p._first_user_msg == "session opener"


def test_initialize_replaces_dead_actor_even_if_shutdown_raises(bm, monkeypatch, tmp_path):
    """Shutdown failures on the stale actor must not block re-initialization."""
    p = _make_initializable_provider(bm, monkeypatch, tmp_path)

    p.initialize(session_id="one", hermes_home=str(tmp_path))
    first = TrackingActor.instances[0]
    first.alive = False
    first.shutdown_raises = True

    p.initialize(session_id="two", hermes_home=str(tmp_path))
    assert first.shutdown_calls
    assert len(TrackingActor.instances) == 2
    assert p._actor is TrackingActor.instances[1]


def test_initialize_exposes_actor_to_cleanup_before_start(bm, monkeypatch, tmp_path):
    """Regression (PR #1059 review): SIGTERM during actor.start() — which can
    block up to 25s — must be able to reach the starting actor. Cleanup only
    sees provider._actor, so it has to be assigned before start() blocks."""
    p = _make_initializable_provider(bm, monkeypatch, tmp_path)
    actor_visible_at_start: list[bool] = []

    class ObservingActor(TrackingActor):
        def start(self, timeout: float = 25.0) -> None:
            actor_visible_at_start.append(p._actor is self)
            super().start(timeout)

    monkeypatch.setattr(bm, "_BmMcpActor", ObservingActor)
    p.initialize(session_id="one", hermes_home=str(tmp_path))
    assert actor_visible_at_start == [True], "actor must be reachable by cleanup during start()"
    assert p._initialized is True
    assert p._actor is TrackingActor.instances[0]


def test_initialize_reaps_actor_when_start_fails(bm, monkeypatch, tmp_path):
    """A failed start (e.g. the 25s ready timeout) can still have spawned the
    `bm mcp` child — the actor must be shut down, not dropped on the floor."""
    p = _make_initializable_provider(bm, monkeypatch, tmp_path)

    class FailingActor(TrackingActor):
        def start(self, timeout: float = 25.0) -> None:
            self.started = True
            self.alive = True  # child spawned before the ready wait timed out
            raise TimeoutError("didn't initialize within 25s")

    monkeypatch.setattr(bm, "_BmMcpActor", FailingActor)
    p.initialize(session_id="one", hermes_home=str(tmp_path))

    assert p._initialized is False
    assert p._actor is None, "failed-start actor must not remain attached"
    failed = TrackingActor.instances[0]
    assert failed.shutdown_calls, "failed-start actor must be shut down to reap its child"


def test_initialize_does_not_resurrect_actor_detached_during_start(bm, monkeypatch, tmp_path):
    """SIGTERM cleanup must own and detach an actor even while start() is waiting."""
    p = _make_initializable_provider(bm, monkeypatch, tmp_path)

    class CleanupDuringStartActor(TrackingActor):
        def start(self, timeout: float = 25.0) -> None:
            self.started = True
            self.alive = True
            monkeypatch.setattr(bm, "_active_providers", [p])
            bm._atexit_cleanup()

    monkeypatch.setattr(bm, "_BmMcpActor", CleanupDuringStartActor)

    p.initialize(session_id="one", hermes_home=str(tmp_path))

    actor = TrackingActor.instances[0]
    assert actor.shutdown_calls
    assert p._actor is None
    assert p._initialized is False


# ---- SIGTERM cleanup chain ----


class _FakeProviderActor:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self, timeout: float = 5.0) -> None:
        self.shutdown_calls += 1


def _provider_with_fake_actor(bm):
    p = bm.BasicMemoryProvider()
    p._actor = _FakeProviderActor()
    p._initialized = True
    return p


def test_sigterm_handler_chains_to_previous_python_handler(bm, monkeypatch):
    """Host-installed Python handler keeps running after our cleanup."""
    chained: list[int] = []

    def host_handler(signum, frame):
        chained.append(signum)

    original = signal.getsignal(signal.SIGTERM)
    provider = _provider_with_fake_actor(bm)
    monkeypatch.setattr(bm, "_sigterm_installed", False)
    monkeypatch.setattr(bm, "_active_providers", [provider])
    try:
        signal.signal(signal.SIGTERM, host_handler)
        bm._install_sigterm_cleanup()
        assert signal.getsignal(signal.SIGTERM) is not host_handler

        signal.raise_signal(signal.SIGTERM)

        assert provider._actor is None, "SIGTERM did not run provider cleanup"
        assert chained == [signal.SIGTERM], "previous handler was not chained"
    finally:
        signal.signal(signal.SIGTERM, original)


def test_sigterm_handler_with_sig_dfl_restores_and_redelivers(bm, monkeypatch):
    """With SIG_DFL, cleanup runs and the signal is re-delivered to die-by-SIGTERM."""
    kills: list[tuple[int, int]] = []
    original = signal.getsignal(signal.SIGTERM)
    provider = _provider_with_fake_actor(bm)
    monkeypatch.setattr(bm, "_sigterm_installed", False)
    monkeypatch.setattr(bm, "_active_providers", [provider])
    monkeypatch.setattr(bm.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        bm._install_sigterm_cleanup()

        signal.raise_signal(signal.SIGTERM)

        assert provider._actor is None
        # Handler restored SIG_DFL and re-sent SIGTERM so termination
        # semantics match a process without this plugin loaded.
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        assert kills == [(bm.os.getpid(), signal.SIGTERM)]
    finally:
        signal.signal(signal.SIGTERM, original)


def test_sigterm_install_respects_sig_ign(bm, monkeypatch):
    """Host deliberately ignores SIGTERM — the plugin must not resurrect it."""
    original = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(bm, "_sigterm_installed", False)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        bm._install_sigterm_cleanup()
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
        assert bm._sigterm_installed is False
    finally:
        signal.signal(signal.SIGTERM, original)


def test_sigterm_install_noops_off_main_thread(bm, monkeypatch):
    """signal.signal only works from the main thread; installer must not blow up."""
    original = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(bm, "_sigterm_installed", False)
    try:
        t = threading.Thread(target=bm._install_sigterm_cleanup)
        t.start()
        t.join(timeout=5.0)
        assert not t.is_alive()
        assert bm._sigterm_installed is False
        assert signal.getsignal(signal.SIGTERM) is original
    finally:
        signal.signal(signal.SIGTERM, original)


def test_sigterm_install_is_idempotent(bm, monkeypatch):
    """A second install call must not re-wrap the handler."""
    original = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(bm, "_sigterm_installed", False)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        bm._install_sigterm_cleanup()
        assert bm._sigterm_installed is True
        installed = signal.getsignal(signal.SIGTERM)

        bm._install_sigterm_cleanup()
        assert signal.getsignal(signal.SIGTERM) is installed
    finally:
        signal.signal(signal.SIGTERM, original)


def test_module_import_installed_sigterm_cleanup(bm):
    """The safety net is armed at plugin load, alongside atexit registration.

    If the surrounding environment ignores SIGTERM (SIG_IGN inherited across
    exec) or owns it at the C level, install is skipped by design — assert the
    invariant rather than unconditional installation.
    """
    if bm._sigterm_installed:
        assert callable(signal.getsignal(signal.SIGTERM))
    else:  # pragma: no cover - only in environments that pre-own SIGTERM
        assert signal.getsignal(signal.SIGTERM) in (signal.SIG_IGN, None)
