#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["basic-memory>=0.22.1"]
# ///
"""SessionStart hook — the entire hook. All logic (settings resolution, the
context brief, opt-in envelope capture) lives in the released basic-memory
package behind `basic-memory hook session-start`; this script is only the
launcher, and uv resolves the dependency floor declared above.
scripts/update_versions.py bumps that floor at release time.

BM_BIN overrides the uv-managed environment for development: either a path
to a bm binary, or a POSIX-quoted launcher string like `uvx "basic-memory"`.

Fail-open contract: a hook must never disrupt an agent session, so every
failure path — unresolvable dependencies, a broken BM_BIN, a crash inside
the CLI — exits 0. The hook JSON on stdin passes through untouched.
"""

import os
import shlex
import subprocess
import sys
from pathlib import Path

VERB = "session-start"
HARNESS = "claude"


def hook_args() -> list[str]:
    args = ["hook", VERB, "--harness", HARNESS]
    # CLAUDE_PROJECT_DIR pins project mapping to the session's project root
    # instead of trusting cwd.
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir:
        args += ["--project-dir", project_dir]
    return args


def main() -> None:
    bm_bin = os.environ.get("BM_BIN", "").strip()
    if bm_bin:
        # An existing path (may contain spaces) stays one word; any other
        # value is a multi-token launcher, split with shell quoting rules.
        launcher = [bm_bin] if Path(bm_bin).exists() else shlex.split(bm_bin)
        subprocess.run([*launcher, *hook_args()], check=False)
        return
    # In-process: uv already resolved basic-memory into this script's
    # environment, so importing the CLI skips a second process spawn.
    from basic_memory.cli.main import app

    sys.argv = ["basic-memory", *hook_args()]
    app()


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - the documented fail-open boundary
        pass
    sys.exit(0)
