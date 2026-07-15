#!/usr/bin/env python3
"""Vendor plugins/shared modules into each installable plugin package.

Plugin marketplaces install a single plugin directory (plugins/claude-code/ or
plugins/codex/); nothing outside that tree ships to user machines. Shared hook
helpers therefore cannot be imported from plugins/shared/ at runtime on an
installed plugin — each plugin carries a committed, vendored copy next to its
hooks instead, and the hooks import from their own directory.

plugins/shared/ stays the canonical source. Edit modules there, then run this
script to refresh the vendored copies. `--check` verifies the copies are
byte-identical without writing (wired into `just package-check` so a drifted
copy fails CI).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = REPO_ROOT / "plugins" / "shared"

# Every installable plugin package that vendors the shared hook helpers.
# The copy lands next to the hooks so each hook can import from its own
# directory in both the repo checkout and the installed layout.
VENDOR_DIRS = (
    REPO_ROOT / "plugins" / "claude-code" / "hooks",
    REPO_ROOT / "plugins" / "codex" / "hooks",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify vendored copies match plugins/shared/; write nothing",
    )
    args = parser.parse_args()

    sources = sorted(SHARED_DIR.glob("*.py"))
    if not sources:
        print(f"no shared modules found under {SHARED_DIR}", file=sys.stderr)
        return 1

    stale: list[Path] = []
    for source in sources:
        for vendor_dir in VENDOR_DIRS:
            target = vendor_dir / source.name
            if args.check:
                if not target.is_file() or target.read_bytes() != source.read_bytes():
                    stale.append(target)
            else:
                shutil.copyfile(source, target)
                print(
                    f"vendored {source.relative_to(REPO_ROOT)} -> {target.relative_to(REPO_ROOT)}"
                )

    if stale:
        listing = "\n".join(f"  {path.relative_to(REPO_ROOT)}" for path in stale)
        print(
            "vendored plugin modules are out of sync with plugins/shared/:\n"
            f"{listing}\n"
            "run: python3 scripts/sync_plugin_shared.py",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
