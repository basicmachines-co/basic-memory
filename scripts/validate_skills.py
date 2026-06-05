#!/usr/bin/env python3
"""Validate Basic Memory SKILL.md source directories."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PLAIN_SCALAR_MAPPING_VALUE = re.compile(r":(?:\s|$)")


def strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def validate_plain_scalar(path: Path, line_number: int, key: str, value: str) -> None:
    """Catch invalid plain-scalar YAML that Codex rejects while loading skills."""
    stripped = value.strip()
    if not stripped or stripped[0] in {'"', "'"} or stripped in {"|", ">", "|-", ">-", "|+", ">+"}:
        return
    if PLAIN_SCALAR_MAPPING_VALUE.search(stripped):
        raise SystemExit(
            f"{path}:{line_number}: invalid YAML frontmatter for {key!r}: "
            "unquoted ':' followed by whitespace; quote the value"
        )


def parse_frontmatter(path: Path) -> dict[str, str]:
    """Extract top-level frontmatter keys from a Markdown file.

    A deliberately minimal parser (no PyYAML — this runs under bare `python3` in
    CI). It only captures **top-level** `key: value` lines. Indented lines are
    skipped, so nested blocks (a schema note's `schema:`/`settings:` children) can't
    overwrite a top-level key like `type` or `entity` via last-write-wins. It does
    not interpret block scalars or multi-line values; callers rely on single-line
    top-level fields (name, description, type, entity). Keep the Codex-facing YAML
    guard here dependency-free so package checks work under bare `python3`.
    """
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        raise SystemExit(f"{path}: missing YAML frontmatter")

    frontmatter: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:], start=2):
        if line == "---":
            break
        if line[:1] in (" ", "\t"):  # nested key — not a top-level field
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        validate_plain_scalar(path, line_number, key, value)
        frontmatter[key] = strip_matching_quotes(value)
    else:
        raise SystemExit(f"{path}: unclosed YAML frontmatter")

    return frontmatter


def validate_skills(skills_root: Path) -> None:
    if not skills_root.exists():
        raise SystemExit(f"Skills directory not found: {skills_root}")

    skill_dirs = sorted(path for path in skills_root.glob("memory-*") if path.is_dir())
    if not skill_dirs:
        raise SystemExit(f"No memory-* skill directories found in {skills_root}")

    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            raise SystemExit(f"{skill_dir}: missing SKILL.md")

        frontmatter = parse_frontmatter(skill_file)
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if name != skill_dir.name:
            raise SystemExit(f"{skill_file}: name {name!r} does not match directory")
        if not description:
            raise SystemExit(f"{skill_file}: missing description")

    print(f"validated {len(skill_dirs)} skills in {skills_root}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("skills_root", nargs="?", default="skills")
    args = parser.parse_args()
    validate_skills((Path.cwd() / args.skills_root).resolve())


if __name__ == "__main__":
    main()
