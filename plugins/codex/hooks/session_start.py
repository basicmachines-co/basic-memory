#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "basic-memory @ git+https://github.com/basicmachines-co/basic-memory@v1.0.0",
#     "fastmcp==4.1.3",
# ]
# [tool.uv]
# # Compile the hook env at install. Hosts such as the Claude desktop app set
# # PYTHONDONTWRITEBYTECODE=1, so Python never caches bytecode at import and every
# # hook run would compile the whole import graph from source (#2101).
# compile-bytecode = true
# # The released Basic Memory runtime still pins the beta. Use the stable
# # version tested by core until the next runtime release carries this pin.
# override-dependencies = ["fastmcp==4.1.3"]
# ///
"""SessionStart hook launcher backed by a pinned Basic Memory revision.

Fail-open contract: a hook must never disrupt an agent session, so every
failure path exits 0. Codex has no project-dir env var; project mapping uses
the payload cwd. The hook JSON on stdin passes through untouched.
"""

import sys

VERB = "session-start"
HARNESS = "codex"


def hook_args() -> list[str]:
    return ["hook", VERB, "--harness", HARNESS]


def main() -> None:
    from basic_memory.cli.main import app

    sys.argv = ["basic-memory", *hook_args()]
    app()


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - the documented fail-open boundary
        pass
    sys.exit(0)
