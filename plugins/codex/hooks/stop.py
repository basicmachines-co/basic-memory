#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "basic-memory @ git+https://github.com/basicmachines-co/basic-memory@0a52e326c9559b7d0c7448f75c32c822f42ae2f0",
# ]
# ///
"""Stop hook launcher backed by a pinned Basic Memory revision.

Stop must always return valid JSON. Running Typer without Click's standalone
mode avoids treating its normal completion as an exception; real failures emit
a fail-open response so Basic Memory can never strand a Codex turn.
"""

import sys

VERB = "stop"
HARNESS = "codex"


def hook_args() -> list[str]:
    return ["hook", VERB, "--harness", HARNESS]


def main() -> None:
    from basic_memory.cli.main import app

    sys.argv = ["basic-memory", *hook_args()]
    app(standalone_mode=False)


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - the documented fail-open boundary
        print('{"continue":true}')
    sys.exit(0)
