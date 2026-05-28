#!/usr/bin/env python3
"""Validate Basic Memory SKILL.md source directories."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        raise SystemExit(f"{path}: missing YAML frontmatter")

    frontmatter: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"')
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
