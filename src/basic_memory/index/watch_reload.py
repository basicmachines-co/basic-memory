"""Ask the running file watcher to re-read its project set now.

The watcher re-selects projects every watch_project_reload_interval seconds (300 by
default). A project added or moved through this process's API would otherwise wait
for that timer before external edits to it were indexed (#1975). Each running watch
cycle registers an event, and a request wakes all of them; with no watcher running
(Cloud, CLI-only processes) a request does nothing. The timer still covers config
edits made by other processes, such as `bm project add` while a server runs.
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

# One event per waiting cycle. An event binds to the loop that first waits on it, so
# each waiter brings its own instead of sharing a module-level one across loops.
_waiting: set[asyncio.Event] = set()


@contextmanager
def watch_reload_requests() -> Iterator[asyncio.Event]:
    """Register an event that request_watch_reload() sets while the block runs."""
    event = asyncio.Event()
    _waiting.add(event)
    try:
        yield event
    finally:
        _waiting.discard(event)


def request_watch_reload() -> None:
    """Wake every running watch cycle to re-read its project set."""
    for event in tuple(_waiting):
        event.set()


async def wait_for_reload(event: asyncio.Event, timeout: float) -> None:
    """Return after timeout seconds, or sooner when a reload is requested."""
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        pass
    event.clear()
