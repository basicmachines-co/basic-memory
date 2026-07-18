"""Pure helpers for how Alembic bridges async migrations across event loops.

Extracted from ``alembic/env.py`` so these two decisions are unit-testable:
env.py runs migrations at import time and cannot be imported in a test, but the
loop-detection and error-classification logic is exactly what regresses, so it
lives here where it can be covered directly.
"""

from __future__ import annotations

import asyncio


def running_on_uvloop() -> bool:
    """Return True when the active asyncio policy is uvloop.

    The Postgres backend installs the uvloop policy before the event loop starts
    (#831/#877). nest_asyncio cannot patch a uvloop loop, but depending on the
    nest_asyncio version ``apply()`` may NOT raise the ValueError we expect — it
    can silently mis-patch the stdlib loop instead. The later ``asyncio.run()``
    then fails with "this event loop is already running" rather than the standard
    "cannot be called from a running event loop", which the thread-based fallback
    would not recognize, crashing startup. Detecting uvloop up front lets env.py
    skip nest_asyncio entirely and stay on the standard running-loop error path.
    """
    try:
        import uvloop
    except ImportError:
        return False
    return isinstance(asyncio.get_event_loop_policy(), uvloop.EventLoopPolicy)
