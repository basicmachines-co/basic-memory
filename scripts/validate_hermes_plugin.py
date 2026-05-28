#!/usr/bin/env python3
"""Validate the Basic Memory Hermes plugin layout."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from validate_skills import parse_frontmatter


def parse_plugin_yaml(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Za-z_]+):\s*(.+?)\s*$", line)
        if match:
            data[match.group(1)] = match.group(2).strip('"')
    return data


def validate_hermes_plugin(plugin_dir: Path) -> None:
    plugin_dir = plugin_dir.resolve()
    plugin_yaml = plugin_dir / "plugin.yaml"
    module = plugin_dir / "__init__.py"
    skill = plugin_dir / "skill/SKILL.md"
    tests = plugin_dir / "tests"

    for path in [plugin_yaml, module, skill, tests]:
        if not path.exists():
            raise SystemExit(f"Missing Hermes plugin file: {path}")

    manifest = parse_plugin_yaml(plugin_yaml)
    if manifest.get("name") != "basic-memory":
        raise SystemExit(f"{plugin_yaml}: expected name=basic-memory")
    if not manifest.get("version"):
        raise SystemExit(f"{plugin_yaml}: missing version")

    module_text = module.read_text()
    if "def register(" not in module_text:
        raise SystemExit(f"{module}: missing register(ctx)")
    if "register_memory_provider" not in module_text and "MemoryProvider" not in module_text:
        raise SystemExit(f"{module}: Hermes memory provider marker missing")

    frontmatter = parse_frontmatter(skill)
    if frontmatter.get("name") != "basic-memory":
        raise SystemExit(f"{skill}: expected name=basic-memory")

    print(f"validated Hermes plugin in {plugin_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_dir", nargs="?", default="integrations/hermes")
    args = parser.parse_args()
    validate_hermes_plugin(Path.cwd() / args.plugin_dir)


if __name__ == "__main__":
    main()
