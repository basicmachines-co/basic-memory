"""Unit tests for the Alembic migration loop helpers.

These guard the exact decisions that broke local Postgres startup: nest_asyncio
mis-patching a uvloop loop, and the fallback failing to recognize the resulting
error. env.py itself can't be imported in a test (it runs migrations at import),
so the logic lives in basic_memory.migration_loop where it can be covered.
"""

import asyncio
import sys

import pytest

from basic_memory import migration_loop


@pytest.mark.skipif(sys.platform == "win32", reason="uvloop is not available on Windows")
def test_running_on_uvloop_true_when_policy_is_uvloop(monkeypatch):
    import uvloop

    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: uvloop.EventLoopPolicy())
    assert migration_loop.running_on_uvloop() is True


def test_running_on_uvloop_false_for_default_policy(monkeypatch):
    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: asyncio.DefaultEventLoopPolicy())
    assert migration_loop.running_on_uvloop() is False


def test_running_on_uvloop_false_when_uvloop_unimportable(monkeypatch):
    # Simulate uvloop not installed (e.g. Windows): import raises -> False.
    monkeypatch.setitem(sys.modules, "uvloop", None)
    assert migration_loop.running_on_uvloop() is False
