"""Main CLI entry point for basic-memory."""  # pragma: no cover

from basic_memory.cli.app import app  # pragma: no cover

# Register commands
from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
    auth,
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

if __name__ == "__main__":  # pragma: no cover
    # start the app
    app()
