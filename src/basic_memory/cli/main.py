"""Main CLI entry point for basic-memory."""  # pragma: no cover

# Set Windows event loop policy BEFORE any imports that might use asyncio.
# The ProactorEventLoop (Windows default) can crash with "IndexError: pop from
# an empty deque" during cleanup. SelectorEventLoop is more stable for CLI use.
import sys  # pragma: no cover

if sys.platform == "win32":  # pragma: no cover
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from basic_memory.cli.app import app  # pragma: no cover

# Register commands
from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
    cloud,
    db,
    import_chatgpt,
    import_claude_conversations,
    import_claude_projects,
    import_memory_json,
    mcp,
    project,
    status,
    tool,
)

if __name__ == "__main__":  # pragma: no cover
    # start the app
    app()
