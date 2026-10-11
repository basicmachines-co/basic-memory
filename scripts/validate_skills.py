#!/usr/bin/env python3
"""Validate Basic Memory SKILL.md source directories.

Checks every ``skills/memory-*/SKILL.md`` against the rules the hosts that load
or upload our skills enforce, and keeps the hand-maintained skill lists in the
repo in sync with the skill directories themselves.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


PLAIN_SCALAR_MAPPING_VALUE = re.compile(r":(?:\s|$)")
BLOCK_SCALAR_INDICATORS = {"|", ">", "|-", ">-", "|+", ">+"}

# Agent Skills spec: lowercase letters, digits, and single hyphens, at most 64
# characters. Claude's uploader also rejects names containing reserved words.
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
RESERVED_NAME_WORDS = ("anthropic", "claude")
# The spec allows 1024 characters, but Claude's skill upload (help center) caps
# descriptions at 200, and we ship the same SKILL.md everywhere.
MAX_DESCRIPTION_LENGTH = 200

MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
# Backticked paths into a skill's own resource folders, e.g. `references/x.md`.
CODE_RESOURCE_PATH = re.compile(r"`((?:references|assets|scripts)/[^`\s]+)`")
METADATA_INTERNAL = re.compile(r"^\s+internal:\s*true\s*$")


def strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def validate_plain_scalar(path: Path, line_number: int, key: str, value: str) -> None:
    """Catch invalid plain-scalar YAML that Codex rejects while loading skills."""
    stripped = value.strip()
    if not stripped or stripped[0] in {'"', "'"} or stripped in BLOCK_SCALAR_INDICATORS:
        return
    if PLAIN_SCALAR_MAPPING_VALUE.search(stripped):
        raise SystemExit(
            f"{path}:{line_number}: invalid YAML frontmatter for {key!r}: "
            "unquoted ':' followed by whitespace; quote the value"
        )


def frontmatter_lines(path: Path) -> list[str]:
    """Return the raw lines between the opening and closing `---` fences."""
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        raise SystemExit(f"{path}: missing YAML frontmatter")
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            return lines[1:index]
    raise SystemExit(f"{path}: unclosed YAML frontmatter")


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
    frontmatter: dict[str, str] = {}
    for line_number, line in enumerate(frontmatter_lines(path), start=2):
        if line[:1] in (" ", "\t"):  # nested key — not a top-level field
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        validate_plain_scalar(path, line_number, key, value)
        frontmatter[key] = strip_matching_quotes(value)
    return frontmatter


def is_internal_skill(skill_file: Path) -> bool:
    """True when frontmatter carries `metadata:` with a nested `internal: true`.

    This is the marker the `skills` CLI (vercel-labs/skills) uses to hide a skill
    from discovery unless INSTALL_INTERNAL_SKILLS=1, so one flag keeps a skill out
    of both `npx skills add` and our release archives.
    """
    in_metadata = False
    for line in frontmatter_lines(skill_file):
        if line[:1] not in (" ", "\t"):
            in_metadata = line.split(":", 1)[0].strip() == "metadata"
            continue
        if in_metadata and METADATA_INTERNAL.match(line):
            return True
    return False


def skill_body(skill_file: Path) -> str:
    """SKILL.md content after the frontmatter block."""
    text = skill_file.read_text()
    _, _, body = text.partition("\n---\n")
    return body


def relative_link_targets(markdown: str) -> list[str]:
    """Relative file paths a skill points at: Markdown links and backticked resources.

    External URLs, in-page anchors, and site-absolute paths are not files in the
    skill folder, so they are skipped. Anchors on file links are dropped.
    """
    targets: list[str] = []
    for target in MARKDOWN_LINK.findall(markdown):
        if target.startswith(("#", "/")) or re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue
        targets.append(target.split("#", 1)[0])
    targets.extend(CODE_RESOURCE_PATH.findall(markdown))
    return targets


def memory_skill_dirs(skills_root: Path) -> list[Path]:
    if not skills_root.exists():
        raise SystemExit(f"Skills directory not found: {skills_root}")
    skill_dirs = sorted(path for path in skills_root.glob("memory-*") if path.is_dir())
    if not skill_dirs:
        raise SystemExit(f"No memory-* skill directories found in {skills_root}")
    return skill_dirs


def distributed_skill_names(skills_root: Path) -> set[str]:
    """Skills we publish: every memory-* directory not marked internal."""
    return {
        skill_dir.name
        for skill_dir in memory_skill_dirs(skills_root)
        if not is_internal_skill(skill_dir / "SKILL.md")
    }


def validate_name(skill_file: Path, name: str) -> None:
    if len(name) > MAX_NAME_LENGTH or not SKILL_NAME_PATTERN.match(name):
        raise SystemExit(
            f"{skill_file}: name {name!r} must be lowercase letters, digits, and single "
            f"hyphens, at most {MAX_NAME_LENGTH} characters"
        )
    for word in RESERVED_NAME_WORDS:
        if word in name:
            raise SystemExit(f"{skill_file}: name {name!r} must not contain {word!r}")


def validate_description(skill_file: Path, description: str | None) -> None:
    if not description:
        raise SystemExit(f"{skill_file}: missing description")
    if description in BLOCK_SCALAR_INDICATORS:
        raise SystemExit(
            f"{skill_file}: description must be a single-line string, not a block scalar"
        )
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise SystemExit(
            f"{skill_file}: description is {len(description)} characters; "
            f"the limit is {MAX_DESCRIPTION_LENGTH}"
        )
    if "<" in description or ">" in description:
        raise SystemExit(f"{skill_file}: description must not contain '<' or '>'")


def validate_skill_file(skill_file: Path, expected_name: str) -> None:
    """Validate one SKILL.md: frontmatter rules plus links into its own folder."""
    frontmatter = parse_frontmatter(skill_file)
    name = frontmatter.get("name")
    if name != expected_name:
        raise SystemExit(f"{skill_file}: name {name!r} does not match directory")
    validate_name(skill_file, name)
    validate_description(skill_file, frontmatter.get("description"))

    for target in relative_link_targets(skill_body(skill_file)):
        if not (skill_file.parent / target).is_file():
            raise SystemExit(f"{skill_file}: links to missing file {target!r}")


def validate_skills(skills_root: Path) -> None:
    skill_dirs = memory_skill_dirs(skills_root)
    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            raise SystemExit(f"{skill_dir}: missing SKILL.md")
        validate_skill_file(skill_file, skill_dir.name)

    print(f"validated {len(skill_dirs)} skills in {skills_root}")


# --- Skill lists kept by hand elsewhere in the repo ---


def _pattern_extractor(pattern: str) -> Callable[[str], set[str]]:
    compiled = re.compile(pattern, re.MULTILINE)
    return lambda text: set(compiled.findall(text))


def _openclaw_manifest_skills(text: str) -> set[str]:
    return {Path(entry).name for entry in json.loads(text)["skills"]}


@dataclass(frozen=True)
class SkillList:
    """A hand-maintained list of skills that must match the skill directories."""

    path: str  # relative to the repo root
    extract: Callable[[str], set[str]]
    # Skills this list may omit. Every entry needs a comment saying why.
    may_omit: dict[str, str] = field(default_factory=dict)
    # Developer docs may also list internal skills; user-facing lists may not.
    allows_internal: bool = False


SKILL_LISTS = (
    SkillList("skills/README.md", _pattern_extractor(r"^\| \*\*(memory-[a-z0-9-]+)\*\* \|")),
    SkillList(
        "skills/CLAUDE.md",
        _pattern_extractor(r"^(memory-[a-z0-9-]+)/SKILL\.md"),
        allows_internal=True,
    ),
    SkillList("integrations/openclaw/openclaw.plugin.json", _openclaw_manifest_skills),
    SkillList(
        "integrations/openclaw/README.md",
        _pattern_extractor(r"^- \*\*(memory-[a-z0-9-]+)\*\* —"),
        may_omit={
            # PR #1677 adds memory-quest to the other lists but not this README.
            # Drop this entry once the README lists it.
            "memory-quest": "added by PR #1677 without an OpenClaw README entry",
        },
    ),
)


def validate_skill_lists(repo_root: Path, skill_lists: tuple[SkillList, ...] = SKILL_LISTS) -> None:
    skills_root = repo_root / "skills"
    all_skills = {skill_dir.name for skill_dir in memory_skill_dirs(skills_root)}
    distributed = distributed_skill_names(skills_root)
    internal = all_skills - distributed

    for skill_list in skill_lists:
        listed = skill_list.extract((repo_root / skill_list.path).read_text())
        missing = distributed - listed - set(skill_list.may_omit)
        allowed_extra = internal if skill_list.allows_internal else set()
        unexpected = listed - distributed - allowed_extra
        if missing:
            raise SystemExit(f"{skill_list.path}: missing skills {sorted(missing)}")
        if unexpected:
            raise SystemExit(
                f"{skill_list.path}: lists unknown or internal skills {sorted(unexpected)}"
            )

    print(f"skill lists match {len(distributed)} distributed skills")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("skills_root", nargs="?", default="skills")
    args = parser.parse_args()
    skills_root = (Path.cwd() / args.skills_root).resolve()
    validate_skills(skills_root)
    validate_skill_lists(skills_root.parent)


if __name__ == "__main__":
    main()
