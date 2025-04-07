"""Main CLI entry point for basic-memory."""  # pragma: no cover

from basic_memory.cli.app import app  # pragma: no cover

# Register commands
from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
    db,
    import_chatgpt,
    import_claude_conversations,
    import_claude_projects,
    import_memory_json,
    mcp,
    project,
    status,
    sync,
    tool,
)
from basic_memory.config import config
from basic_memory.services.initialization import ensure_initialization

if __name__ == "__main__":  # pragma: no cover
    # Run initialization if we are starting as a module
    ensure_initialization(config)

    # start the app
    app()
