"""Main CLI entry point for basic-memory."""  # pragma: no cover

import sys

from basic_memory.cli.app import app  # pragma: no cover


def _version_flag_present(argv: list[str]) -> bool:
    return any(flag in argv for flag in ("--version", "-v"))


if not _version_flag_present(sys.argv[1:]):
    # Register commands only when not short-circuiting for --version
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

# Re-apply warning filter AFTER all imports
# (authlib adds a DeprecationWarning filter that overrides ours)
import warnings  # pragma: no cover

warnings.filterwarnings("ignore")  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    # start the app
    app()
