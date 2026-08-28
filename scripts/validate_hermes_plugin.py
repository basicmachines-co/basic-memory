#!/usr/bin/env python3
"""Validate the Basic Memory Hermes plugin layout."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from validate_skills import parse_frontmatter

HERMES_INSTALL_COMMAND = "hermes plugins install basicmachines-co/basic-memory/integrations/hermes"
# The highest manifest_version the released `hermes plugins install` accepts.
INSTALLER_SUPPORTED_MANIFEST_VERSION = 1
UNSUPPORTED_HERMES_INSTALL_COMMAND = (
    "hermes plugins install basicmachines-co/basic-memory --path integrations/hermes"
)


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
    readme = plugin_dir / "README.md"
    root_readme = plugin_dir.parents[1] / "README.md"
    skill = plugin_dir / "skill/SKILL.md"
    tests = plugin_dir / "tests"

    for path in [plugin_yaml, module, readme, root_readme, skill, tests]:
        if not path.exists():
            raise SystemExit(f"Missing Hermes plugin file: {path}")

    manifest = parse_plugin_yaml(plugin_yaml)
    if manifest.get("name") != "basic-memory":
        raise SystemExit(f"{plugin_yaml}: expected name=basic-memory")
    if not manifest.get("version"):
        raise SystemExit(f"{plugin_yaml}: missing version")
    # Every released Hermes installer refuses manifest_version > 1 while the
    # runtime reads the newer fields regardless (#1339). Fail here so a version
    # bump or manifest cleanup cannot quietly restore the installer rejection.
    if manifest.get("manifest_version") != str(INSTALLER_SUPPORTED_MANIFEST_VERSION):
        raise SystemExit(
            f"{plugin_yaml}: manifest_version must stay {INSTALLER_SUPPORTED_MANIFEST_VERSION} "
            "until a Hermes release ships installer support for a newer manifest "
            "(NousResearch/hermes-agent#85893)"
        )

    module_text = module.read_text()
    if "def register(" not in module_text:
        raise SystemExit(f"{module}: missing register(ctx)")
    if "register_memory_provider" not in module_text and "MemoryProvider" not in module_text:
        raise SystemExit(f"{module}: Hermes memory provider marker missing")

    frontmatter = parse_frontmatter(skill)
    if frontmatter.get("name") != "basic-memory":
        raise SystemExit(f"{skill}: expected name=basic-memory")

    for documentation in [root_readme, readme]:
        documentation_text = documentation.read_text()
        if HERMES_INSTALL_COMMAND not in documentation_text:
            raise SystemExit(f"{documentation}: missing supported Hermes install command")
        if UNSUPPORTED_HERMES_INSTALL_COMMAND in documentation_text:
            raise SystemExit(f"{documentation}: contains unsupported Hermes --path command")

    print(f"validated Hermes plugin in {plugin_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_dir", nargs="?", default="integrations/hermes")
    args = parser.parse_args()
    validate_hermes_plugin(Path.cwd() / args.plugin_dir)


if __name__ == "__main__":
    main()
