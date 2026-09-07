"""Copy canonical memory schemas into self-contained host packages."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = {
    "plugins/claude-code/schemas": ["coding-session.md", "session.md", "task.md", "decision.md"],
    "plugins/codex/schemas": ["coding-session.md", "task.md", "decision.md"],
    "integrations/tau/schemas": ["coding-session.md", "session.md", "task.md", "decision.md"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report drift without writing")
    args = parser.parse_args()
    drift: list[str] = []
    for directory, names in BUNDLES.items():
        for name in names:
            source = ROOT / "integrations/shared/schemas" / name
            target = ROOT / directory / name
            expected = source.read_bytes()
            if args.check:
                if not target.exists() or target.read_bytes() != expected:
                    drift.append(str(target.relative_to(ROOT)))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(expected)
    if drift:
        parser.exit(1, "Schema copies differ: " + ", ".join(drift) + "\n")


if __name__ == "__main__":
    main()
