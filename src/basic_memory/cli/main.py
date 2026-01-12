"""Main CLI entry point for basic-memory."""  # pragma: no cover

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
    telemetry,
    tool,
)

# Re-apply warning filter AFTER all imports
# (authlib adds a DeprecationWarning filter that overrides ours)
import warnings  # pragma: no cover

warnings.filterwarnings("ignore")  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    # start the app
    app()
