#!/usr/bin/env python3
"""Validate the Basic Memory Claude Code plugin layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_skills import parse_frontmatter


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def validate_claude_plugin(plugin_dir: Path) -> None:
    plugin_dir = plugin_dir.resolve()
    plugin_json = plugin_dir / ".claude-plugin/plugin.json"
    local_marketplace_json = plugin_dir / ".claude-plugin/marketplace.json"
    root_marketplace_json = ROOT / ".claude-plugin/marketplace.json"

    for path in [plugin_json, local_marketplace_json, root_marketplace_json]:
        if not path.exists():
            raise SystemExit(f"Missing Claude plugin manifest: {path}")

    plugin = read_json(plugin_json)
    if plugin.get("name") != "basic-memory":
        raise SystemExit(f"{plugin_json}: expected name=basic-memory")
    if not str(plugin.get("repository", "")).endswith(
        "/basic-memory/tree/main/plugins/claude-code"
    ):
        raise SystemExit(f"{plugin_json}: repository must point at the monorepo subdirectory")

    root_marketplace = read_json(root_marketplace_json)
    root_plugin = root_marketplace["plugins"][0]
    expected_source = "./plugins/claude-code"
    if root_plugin.get("name") != "basic-memory" or root_plugin.get("source") != expected_source:
        raise SystemExit(f"{root_marketplace_json}: expected basic-memory source {expected_source}")

    local_marketplace = read_json(local_marketplace_json)
    local_plugin = local_marketplace["plugins"][0]
    if local_plugin.get("name") != "basic-memory" or local_plugin.get("source") != "./":
        raise SystemExit(f"{local_marketplace_json}: expected plugin-local source ./")

    hooks = plugin_dir / "hooks/hooks.json"
    hooks_json = read_json(hooks)
    if "PreToolUse" not in hooks_json.get("hooks", {}):
        raise SystemExit(f"{hooks}: missing PreToolUse hook")

    agent = plugin_dir / "agents/basic-memory-manager.md"
    agent_frontmatter = parse_frontmatter(agent)
    if agent_frontmatter.get("name") != "basic-memory-manager":
        raise SystemExit(f"{agent}: missing basic-memory-manager frontmatter")

    skill_dirs = sorted(path for path in (plugin_dir / "skills").iterdir() if path.is_dir())
    if not skill_dirs:
        raise SystemExit(f"{plugin_dir / 'skills'}: no bundled Claude Code skills")
    for skill_dir in skill_dirs:
        frontmatter = parse_frontmatter(skill_dir / "SKILL.md")
        if frontmatter.get("name") != skill_dir.name:
            raise SystemExit(f"{skill_dir}: skill name must match directory")

    print(f"validated Claude Code plugin in {plugin_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_dir", nargs="?", default="plugins/claude-code")
    args = parser.parse_args()
    validate_claude_plugin(Path.cwd() / args.plugin_dir)


if __name__ == "__main__":
    main()
