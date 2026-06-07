"""Unit tests for the uvloop event-loop policy installer.

Covers the structural fix for issue #831 / #877: the asyncpg engine-dispose
race ("IndexError: pop from an empty deque") is avoided by running the Postgres
backend on uvloop, whose C scheduler has no self._ready.popleft() codepath.

These tests verify the gating logic of ``maybe_install_uvloop`` without touching
a real database. The global event-loop policy is saved and restored around each
test so installing uvloop here cannot leak into the rest of the suite.
"""

import asyncio
import sys

import pytest

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.db import maybe_install_uvloop


@pytest.fixture
def restore_event_loop_policy():
    """Save/restore the global event-loop policy around a test."""
    original = asyncio.get_event_loop_policy()
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(original)


def _postgres_config() -> BasicMemoryConfig:
    return BasicMemoryConfig(
        env="test",
        database_backend=DatabaseBackend.POSTGRES,
        database_url="postgresql+asyncpg://user:pass@localhost/db",
    )


def _sqlite_config() -> BasicMemoryConfig:
    return BasicMemoryConfig(env="test", database_backend=DatabaseBackend.SQLITE)


@pytest.mark.skipif(sys.platform == "win32", reason="uvloop is not available on Windows")
def test_installs_uvloop_for_postgres_backend(restore_event_loop_policy):
    """uvloop policy is installed when backend is Postgres and uvloop is available."""
    import uvloop

    installed = maybe_install_uvloop(_postgres_config())

    assert installed is True
    assert isinstance(asyncio.get_event_loop_policy(), uvloop.EventLoopPolicy)


def test_no_uvloop_for_sqlite_backend(restore_event_loop_policy):
    """SQLite users keep the default loop - the helper is a no-op."""
    before = asyncio.get_event_loop_policy()

    installed = maybe_install_uvloop(_sqlite_config())

    assert installed is False
    # Policy must be unchanged for the default (SQLite) path.
    assert asyncio.get_event_loop_policy() is before


@pytest.mark.skipif(sys.platform == "win32", reason="uvloop is not available on Windows")
def test_uvloop_unavailable_is_a_safe_noop(restore_event_loop_policy, monkeypatch):
    """When uvloop cannot be imported the helper returns False without raising."""
    import builtins

    real_import = builtins.__import__

    def _fail_uvloop_import(name, *args, **kwargs):
        if name == "uvloop":
            raise ImportError("simulated missing uvloop")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_uvloop_import)

    before = asyncio.get_event_loop_policy()
    installed = maybe_install_uvloop(_postgres_config())

    assert installed is False
    assert asyncio.get_event_loop_policy() is before
