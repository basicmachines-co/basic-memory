"""Main CLI entry point for basic-memory."""  # pragma: no cover

import sys
import warnings

from basic_memory.cli.app import app  # pragma: no cover


def _version_only_invocation(argv: list[str]) -> bool:
    # Trigger: invocation is exactly `bm --version` or `bm -v`
    # Why: avoid importing command modules on the hot version path
    # Outcome: eager version callback exits quickly with minimal startup work
    return len(argv) == 1 and argv[0] in {"--version", "-v"}


if not _version_only_invocation(sys.argv[1:]):
    # Register commands only when not short-circuiting for --version
    from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
        cloud,
        db,
        doctor,
        import_chatgpt,
        import_claude_conversations,
        import_claude_projects,
        import_memory_json,
        mcp,
        project,
        schema,
        status,
        tool,
        workspace,
    )

warnings.filterwarnings("ignore")  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    # start the app
    app()
