#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "basic-memory @ git+https://github.com/basicmachines-co/basic-memory@5d444f0974476645f904c1446998c0a938a6e7f7",
# ]
# ///
"""PreCompact hook launcher backed by a pinned Basic Memory revision.

Fail-open contract: a hook must never disrupt an agent session, so every
failure path exits 0. Codex has no project-dir env var; project mapping uses
the payload cwd. The hook JSON on stdin passes through untouched.
"""

import sys

VERB = "pre-compact"
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
