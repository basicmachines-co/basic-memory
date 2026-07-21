#!/usr/bin/env python3
"""Validate the Basic Memory Claude Code plugin layout (v0.4 bridge redesign).

The plugin's surfaces are intentionally minimal: lifecycle hooks (SessionStart,
PreCompact), an opt-in output style, and seed schemas. There is no bundled agent,
and skills are optional in this layout (added by later phases). This validator
mirrors that contract so `package-check-claude-code` passes for what the plugin
actually ships.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from validate_skills import parse_frontmatter


ROOT = Path(__file__).resolve().parents[1]

# Hook events the bridge plugin must register, and the scripts that back them.
REQUIRED_HOOK_EVENTS = ("SessionStart", "PreCompact")
REQUIRED_HOOK_SCRIPTS = ("hooks/session_start.py", "hooks/pre_compact.py")
# Seed schemas the plugin ships for its note types (copied into the user's
# project at bootstrap). Each must be a parseable schema note.
REQUIRED_SCHEMAS = ("session.md", "coding-session.md", "decision.md", "task.md")
# Skills the plugin ships as namespaced slash commands (/basic-memory:<name>).
REQUIRED_SKILLS = (
    "bm-setup",
    "bm-orient",
    "bm-checkpoint",
    "bm-decide",
    "bm-remember",
    "bm-status",
    "bm-share",
    "bm-writing",
)
REQUIRED_SKILL_TEXT: dict[str, tuple[str, ...]] = {
    "bm-setup": (
        "captureEvents",
        "redactKeys",
        "redactPaths",
        "sessionProfile",
        "coding-session.md",
        "hook status --harness claude",
    ),
    "bm-status": (
        "hook status --harness claude",
        "pending envelopes",
        "archived envelopes",
        "Pending checkpoint requests",
        "last flush",
        '"type": "session"',
        '"type": "coding_session"',
    ),
    # Mirrors the Codex validator's bm-checkpoint contract, adapted to the
    # Claude harness (settings-based config, session/coding_session types).
    "bm-checkpoint": (
        "Apply the `bm-writing` skill",
        "A checkpoint is a durable handoff, not a status dump",
        "username: <current username>",
        "hostname: <current hostname>",
        "type: coding_session",
        "pull_request_number",
        "- `[decision]` for each decision made or preserved",
        "## Relations",
        "- relates_to [[Exact existing note title]]",
        "Never write `[relates_to]`",
    ),
    "bm-remember": ("Apply the `bm-writing` skill",),
    "bm-decide": ("Apply the `bm-writing` skill",),
    # Coding-session recall must stay repository-scoped — an unscoped query
    # crosses repos and pollutes orientation.
    "bm-orient": (
        'Always query `"type": "session"`',
        '"type": "coding_session"',
        "Never run an unscoped coding-session query",
    ),
    # Mirrors the Codex validator's bm-writing contract so the shared writing
    # standard stays in sync across host plugins.
    "bm-writing": (
        "intentionally user-customizable",
        "problem -> approach -> current state and impact",
        "## Anchor The Work",
        "## Preserve The Semantic Layer",
        "- relation_type [[Target Note]]",
        "Do not invent intent, impact, verification, decisions, or drama",
    ),
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def validate_claude_plugin(plugin_dir: Path) -> None:
    plugin_dir = plugin_dir.resolve()

    # --- Manifests ---
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

    # --- Hooks ---
    # The bridge runs on lifecycle events, not on tool calls. Require the two
    # event keys and confirm each backing uv script exists, is executable
    # (direct runs via its shebang), and carries the PEP 723 dependency floor
    # that hooks.json's `uv run --script` resolves and the release version
    # updater bumps.
    hooks_json = read_json(plugin_dir / "hooks/hooks.json")
    hooks = hooks_json.get("hooks", {})
    for event in REQUIRED_HOOK_EVENTS:
        if event not in hooks:
            raise SystemExit(f"hooks/hooks.json: missing {event} hook")
    for rel in REQUIRED_HOOK_SCRIPTS:
        script = plugin_dir / rel
        if not script.exists():
            raise SystemExit(f"Missing hook script: {script}")
        if not os.access(script, os.X_OK):
            raise SystemExit(f"Hook script is not executable: {script}")
        text = script.read_text(encoding="utf-8")
        if "# /// script" not in text or not re.search(
            r'^# dependencies = \["basic-memory>=[^"]+"\]$', text, re.MULTILINE
        ):
            raise SystemExit(f"Hook script missing PEP 723 basic-memory floor: {script}")

    # --- Output style ---
    output_style = plugin_dir / "output-styles/basic-memory.md"
    if not output_style.exists():
        raise SystemExit(f"Missing output style: {output_style}")
    if not parse_frontmatter(output_style).get("description"):
        raise SystemExit(f"{output_style}: missing description frontmatter")

    # --- Seed schemas ---
    # Each must declare type: schema and an entity so it resolves once indexed.
    for name in REQUIRED_SCHEMAS:
        schema_file = plugin_dir / "schemas" / name
        if not schema_file.exists():
            raise SystemExit(f"Missing seed schema: {schema_file}")
        fm = parse_frontmatter(schema_file)
        if fm.get("type") != "schema":
            raise SystemExit(f"{schema_file}: expected type: schema")
        if not fm.get("entity"):
            raise SystemExit(f"{schema_file}: missing entity field")

    # --- Skills ---
    # Each skill is a directory with a SKILL.md whose `name` matches the directory;
    # it surfaces as /basic-memory:<dir>. Require the shipped set, and validate the
    # frontmatter of every skill present.
    skills_root = plugin_dir / "skills"
    if not skills_root.exists():
        raise SystemExit(f"Missing skills directory: {skills_root}")
    present_skills = {p.name for p in skills_root.iterdir() if p.is_dir()}
    for required in REQUIRED_SKILLS:
        if required not in present_skills:
            raise SystemExit(f"Missing required skill: skills/{required}/SKILL.md")
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            raise SystemExit(f"{skill_dir}: missing SKILL.md")
        frontmatter = parse_frontmatter(skill_md)
        if frontmatter.get("name") != skill_dir.name:
            raise SystemExit(f"{skill_dir}: skill name must match directory")
        if not frontmatter.get("description"):
            raise SystemExit(f"{skill_md}: missing description frontmatter")
        skill_text = skill_md.read_text(encoding="utf-8")
        for required_text in REQUIRED_SKILL_TEXT.get(skill_dir.name, ()):
            if required_text not in skill_text:
                raise SystemExit(f"{skill_md}: missing plugin contract text {required_text!r}")

    readme = (plugin_dir / "README.md").read_text(encoding="utf-8")
    for required_text in (
        "lifecycle trace never becomes a graph note",
        "archive locally",
        "never creates session or tool-ledger notes",
    ):
        if required_text not in readme:
            raise SystemExit(f"README.md: missing schema ownership text {required_text!r}")

    print(f"validated Claude Code plugin in {plugin_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_dir", nargs="?", default="plugins/claude-code")
    args = parser.parse_args()
    validate_claude_plugin(Path.cwd() / args.plugin_dir)


if __name__ == "__main__":
    main()
