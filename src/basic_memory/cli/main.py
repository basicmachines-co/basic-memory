"""Main CLI entry point for basic-memory."""  # pragma: no cover

import io
import sys
import warnings

from basic_memory.cli.app import app  # pragma: no cover


def use_utf8_output(*streams: object) -> None:
    """Write text streams as UTF-8, whatever the platform's locale encoding.

    Windows encodes piped or redirected output with the ANSI code page (cp1252)
    unless UTF-8 mode is on. A note containing "←" then crashed `bm tool read`,
    and the Claude Code hooks, which run this CLI in-process, sent mojibake to a
    consumer that reads UTF-8 (#2003). Streams that are not text wrappers, such as
    a test runner's capture buffer, are left alone.
    """
    for stream in streams:
        if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8")


use_utf8_output(sys.stdout, sys.stderr)


def _version_only_invocation(argv: list[str]) -> bool:
    # Trigger: invocation is exactly `bm --version` or `bm -v`
    # Why: avoid importing command modules on the hot version path
    # Outcome: eager version callback exits quickly with minimal startup work
    return len(argv) == 1 and argv[0] in {"--version", "-v"}


if not _version_only_invocation(sys.argv[1:]):
    # Register commands only when not short-circuiting for --version
    from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
        cache,
        ci,
        cloud,
        db,
        doctor,
        hook,
        import_chatgpt,
        import_claude_conversations,
        import_claude_projects,
        import_document,
        import_memory_json,
        inspect,
        install,
        man,
        mcp,
        okf,
        orphans,
        posix,
        project,
        schema,
        status,
        tool,
        update,
        wiki,
        workspace,
    )

warnings.filterwarnings("ignore")  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    # start the app
    app()
