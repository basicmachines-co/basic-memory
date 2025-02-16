"""Main CLI entry point for basic-memory."""  # pragma: no cover

from basic_memory.cli.app import app  # pragma: no cover
from basic_memory.utils import setup_logging  # pragma: no cover

# Register commands
from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
    status,
    sync,
    db,
    import_memory_json,
    mcp,
    import_claude_conversations,
    import_claude_projects,
    import_chatgpt,
)


# Set up logging when module is imported
setup_logging(log_file=".basic-memory/basic-memory-cli.log")  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    app()
