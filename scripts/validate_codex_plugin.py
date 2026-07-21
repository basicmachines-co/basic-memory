#!/usr/bin/env python3
"""Validate the Basic Memory Codex plugin layout."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from validate_skills import parse_frontmatter


REQUIRED_SKILLS = (
    "bm-setup",
    "bm-orient",
    "bm-checkpoint",
    "bm-decide",
    "bm-remember",
    "bm-writing",
    "bm-share",
    "bm-status",
)
REQUIRED_SKILL_TEXT: dict[str, tuple[str, ...]] = {
    "bm-setup": (
        "captureEvents",
        "user-level",
        "project-level",
        "codex/<repo-dir>",
        "redactKeys",
        "redactPaths",
        "sessionProfile",
        "coding-session.md",
        "hook status --harness codex",
    ),
    "bm-status": (
        "~/.codex/basic-memory.json",
        "hook status --harness codex",
        "pending envelopes",
        "archived envelopes",
        "Pending checkpoint requests",
        "last flush",
        "type=codex_session",
        "type=coding_session",
    ),
    "bm-orient": (
        "Always query `codex_session`",
        "type=codex_session",
        "type=coding_session",
    ),
    "bm-checkpoint": (
        "Apply the `bm-writing` skill",
        "A checkpoint is a durable handoff, not a status dump",
        "## Privacy Gate",
        "`redactKeys` and `redactPaths` accumulate",
        "[REDACTED_PATH]",
        "skip the checkpoint",
        "username: <current username>",
        "hostname: <current hostname>",
        "type: coding_session",
        "pull_request_number",
        "- `[decision]` for each decision made or preserved",
        "## Relations",
        "- relates_to [[Exact existing note title]]",
        "Never write `[relates_to]`",
    ),
    "bm-writing": (
        "intentionally user-customizable",
        "problem -> approach -> current state and impact",
        "## Anchor The Work",
        "## Preserve The Semantic Layer",
        "- relation_type [[Target Note]]",
        "Do not invent intent, impact, verification, decisions, or drama",
    ),
}
REQUIRED_SCHEMAS = ("codex-session.md", "coding-session.md", "decision.md", "task.md")
REQUIRED_HOOK_EVENTS = ("SessionStart", "PreCompact", "Stop")
# Zero-logic shims: the only hook code the plugin ships. The Python bodies
# moved into the basic-memory package behind `bm hook` (SPEC-55).
REQUIRED_HOOK_SCRIPTS = (
    "hooks/session_start.py",
    "hooks/pre_compact.py",
    "hooks/stop.py",
)
REQUIRED_SKILL_AGENT_FILES = ("agents/openai.yaml", "assets/icon.svg")
REQUIRED_INTERFACE_ASSETS = {
    "composerIcon": "assets/app-icon.png",
    "logo": "assets/logo.png",
}
HOOK_DEPENDENCY_RE = re.compile(
    r'"basic-memory @ git\+https://github\.com/'
    r'basicmachines-co/basic-memory@([^"]+)"'
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"Missing JSON file: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return payload


def require_path(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"Missing {label}: {path}")


def validate_plugin(plugin_dir: Path) -> None:
    plugin_dir = plugin_dir.resolve()

    # --- Manifest ---
    manifest_path = plugin_dir / ".codex-plugin" / "plugin.json"
    manifest = read_json(manifest_path)
    if manifest.get("name") != "codex":
        raise SystemExit(f"{manifest_path}: expected name=codex")
    if manifest.get("skills") != "./skills/":
        raise SystemExit(f"{manifest_path}: expected skills=./skills/")
    if manifest.get("mcpServers") != "./.mcp.json":
        raise SystemExit(f"{manifest_path}: expected mcpServers=./.mcp.json")
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise SystemExit(f"{manifest_path}: missing interface object")
    if interface.get("displayName") != "Basic Memory for Codex":
        raise SystemExit(f"{manifest_path}: unexpected interface.displayName")
    for field, expected_path in REQUIRED_INTERFACE_ASSETS.items():
        if interface.get(field) != f"./{expected_path}":
            raise SystemExit(f"{manifest_path}: expected interface.{field}=./{expected_path}")
        require_path(plugin_dir / expected_path, f"interface.{field} asset")

    # --- MCP ---
    mcp = read_json(plugin_dir / ".mcp.json")
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or "basic-memory" not in servers:
        raise SystemExit(".mcp.json: expected mcpServers.basic-memory")
    basic_memory = servers["basic-memory"]
    if not isinstance(basic_memory, dict):
        raise SystemExit(".mcp.json: basic-memory server must be an object")
    if basic_memory.get("command") not in {"uvx", "basic-memory", "bm"}:
        raise SystemExit(".mcp.json: basic-memory server uses an unexpected command")

    # --- Hooks ---
    hooks_json = read_json(plugin_dir / "hooks" / "hooks.json")
    hooks = hooks_json.get("hooks")
    if not isinstance(hooks, dict):
        raise SystemExit("hooks/hooks.json: expected hooks object")
    for event in REQUIRED_HOOK_EVENTS:
        if event not in hooks:
            raise SystemExit(f"hooks/hooks.json: missing {event}")
    dependency_refs: set[str] = set()
    for rel in REQUIRED_HOOK_SCRIPTS:
        script = plugin_dir / rel
        require_path(script, "hook script")
        if not os.access(script, os.X_OK):
            raise SystemExit(f"Hook script is not executable: {script}")
        text = script.read_text(encoding="utf-8")
        dependency_match = HOOK_DEPENDENCY_RE.search(text)
        if "# /// script" not in text or dependency_match is None:
            raise SystemExit(f"Hook script missing pinned Basic Memory Git dependency: {script}")
        dependency_refs.add(dependency_match.group(1))
    if len(dependency_refs) != 1:
        raise SystemExit("Codex hook scripts must use the same Basic Memory Git ref")

    # --- Skills ---
    skills_root = plugin_dir / "skills"
    require_path(skills_root, "skills directory")
    present = {path.name for path in skills_root.iterdir() if path.is_dir()}
    for skill_name in REQUIRED_SKILLS:
        if skill_name not in present:
            raise SystemExit(f"Missing required skill: skills/{skill_name}/SKILL.md")
    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        require_path(skill_file, "skill file")
        frontmatter = parse_frontmatter(skill_file)
        if frontmatter.get("name") != skill_dir.name:
            raise SystemExit(f"{skill_file}: name must match directory")
        if not frontmatter.get("description"):
            raise SystemExit(f"{skill_file}: missing description")
        skill_text = skill_file.read_text(encoding="utf-8")
        for required_text in REQUIRED_SKILL_TEXT.get(skill_dir.name, ()):
            if required_text not in skill_text:
                raise SystemExit(f"{skill_file}: missing plugin contract text {required_text!r}")
        for rel in REQUIRED_SKILL_AGENT_FILES:
            require_path(skill_dir / rel, f"skill {rel}")

    # --- Schemas ---
    schemas_root = plugin_dir / "schemas"
    require_path(schemas_root, "schemas directory")
    for schema_name in REQUIRED_SCHEMAS:
        schema_file = schemas_root / schema_name
        require_path(schema_file, "schema")
        frontmatter = parse_frontmatter(schema_file)
        if frontmatter.get("type") != "schema":
            raise SystemExit(f"{schema_file}: expected type: schema")
        if not frontmatter.get("entity"):
            raise SystemExit(f"{schema_file}: missing entity")

    readme = (plugin_dir / "README.md").read_text(encoding="utf-8")
    for required_text in (
        "lifecycle trace stays local",
        "Stop hook",
        "agent-authored checkpoint",
    ):
        if required_text not in readme:
            raise SystemExit(f"README.md: missing schema ownership text {required_text!r}")

    print(f"validated Codex plugin in {plugin_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_dir", nargs="?", default="plugins/codex")
    args = parser.parse_args()
    validate_plugin(Path.cwd() / args.plugin_dir)


if __name__ == "__main__":
    main()
