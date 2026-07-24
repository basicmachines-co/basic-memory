#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "basic-memory @ git+https://github.com/basicmachines-co/basic-memory@2f58b81b5e00cf4cecf12ce595ec6b500d6575a8",
# ]
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
