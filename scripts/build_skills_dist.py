#!/usr/bin/env python3
"""Package Basic Memory SKILL.md sources into distributable archives.

The canonical skill source lives in ``skills/memory-*/`` as pure markdown. This
script stages and zips those skills into ``dist/`` in two flavors:

  dist/skills/<name>.zip               Agent Skill (SKILL.md + any resources)
  dist/skills/basic-memory-skills.zip  all skills bundled together
  dist/skills-openai/<name>.zip        same skill + agents/openai.yaml metadata

The plain flavor is the open Agent Skills format (agentskills.io) — the zip
Claude Desktop, Cursor, and the `skills` CLI consume. The OpenAI flavor adds an
``agents/openai.yaml`` interface block so the skill shows up with a name, blurb,
and brand color in ChatGPT/Codex (see https://learn.chatgpt.com/docs/build-skills).

Dependency-free (stdlib only) so it runs under bare ``python3`` in CI, matching
scripts/validate_skills.py — which owns the frontmatter parser we reuse here.
"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

from validate_skills import parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parent.parent

# Basic Memory brand blue — matches the Codex plugin's authored openai.yaml files.
BRAND_COLOR = "#2563EB"
BUNDLE_NAME = "basic-memory-skills"


def discover_skills(skills_root: Path) -> list[Path]:
    """Return sorted memory-* skill directories, or fail loudly if none exist."""
    skill_dirs = sorted(p for p in skills_root.glob("memory-*") if p.is_dir())
    if not skill_dirs:
        raise SystemExit(f"No memory-* skill directories found in {skills_root}")
    return skill_dirs


# --- OpenAI / ChatGPT metadata generation ---


def display_name(name: str) -> str:
    """`memory-tasks` -> `Memory Tasks` for the ChatGPT skill card."""
    return " ".join(word.capitalize() for word in name.split("-"))


def short_description(description: str) -> str:
    """First sentence of the frontmatter description, capped for the UI blurb."""
    # Split on the first sentence terminator followed by whitespace so mid-word
    # colons and abbreviations don't truncate early.
    first = description.strip()
    for terminator in (". ", "! ", "? "):
        idx = first.find(terminator)
        if idx != -1:
            first = first[: idx + 1]
            break
    first = first.rstrip()
    # ChatGPT surfaces this inline; keep it short but never cut mid-word.
    if len(first) > 140:
        first = first[:140].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return first


def yaml_quote(value: str) -> str:
    """Double-quote a scalar and escape backslashes/quotes for safe YAML."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def generate_openai_yaml(name: str, frontmatter: dict[str, str]) -> str:
    """Build an agents/openai.yaml interface block from the skill frontmatter.

    We emit only the ``interface`` section: the MCP dependency that these skills
    need (the basic-memory server) is provided by the host at runtime — the
    plugin's .mcp.json locally, or ChatGPT's connected MCP remotely — not pinned
    in per-skill metadata. Hand-tune via a source agents/openai.yaml to override.
    """
    description = frontmatter.get("description", "")
    return (
        f"# Generated from skills/{name}/SKILL.md by scripts/build_skills_dist.py\n"
        "# ChatGPT / Codex skill metadata — https://learn.chatgpt.com/docs/build-skills\n"
        "interface:\n"
        f"  display_name: {yaml_quote(display_name(name))}\n"
        f"  short_description: {yaml_quote(short_description(description))}\n"
        f'  brand_color: "{BRAND_COLOR}"\n'
    )


# --- Staging and archiving ---


def stage_skill(skill_dir: Path, dest: Path, *, openai: bool) -> None:
    """Copy a skill directory into `dest/<name>`, optionally adding openai.yaml."""
    out = dest / skill_dir.name
    shutil.copytree(skill_dir, out)

    if openai:
        # Respect a hand-authored source agents/openai.yaml; generate otherwise.
        if not (out / "agents" / "openai.yaml").exists():
            frontmatter = parse_frontmatter(skill_dir / "SKILL.md")
            agents_dir = out / "agents"
            agents_dir.mkdir(exist_ok=True)
            (agents_dir / "openai.yaml").write_text(
                generate_openai_yaml(skill_dir.name, frontmatter)
            )


def zip_tree(source_root: Path, zip_path: Path, arc_base: Path | None = None) -> None:
    """Zip every file under `source_root`, with arc paths relative to `arc_base`.

    `arc_base` defaults to `source_root`. Passing the staging root as `arc_base`
    keeps the `<name>/` folder prefix inside a single-skill zip, so unzipping it
    (or dropping it into an uploader) lands a `<name>/SKILL.md` skill directory —
    the layout the Agent Skills spec and Claude Desktop / ChatGPT uploaders expect.
    """
    base = arc_base or source_root
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(base).as_posix())


def build_flavor(skill_dirs: list[Path], dist_dir: Path, *, openai: bool) -> int:
    """Stage + zip each skill (and the combined bundle) for one flavor.

    Returns the per-skill zip count. Staging happens in a sibling ``.stage``
    directory so the zips contain a clean ``<name>/SKILL.md`` root.
    """
    stage = dist_dir / ".stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for skill_dir in skill_dirs:
        stage_skill(skill_dir, stage, openai=openai)
        # arc_base=stage keeps the `<name>/` prefix inside the single-skill zip.
        zip_tree(stage / skill_dir.name, dist_dir / f"{skill_dir.name}.zip", arc_base=stage)

    # Combined bundle: all staged skill dirs under one zip root.
    zip_tree(stage, dist_dir / f"{BUNDLE_NAME}.zip")

    shutil.rmtree(stage)
    return len(skill_dirs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=REPO_ROOT / "skills",
        help="Directory holding memory-* skill folders (default: <repo>/skills)",
    )
    parser.add_argument(
        "--dist-root",
        type=Path,
        default=REPO_ROOT / "dist",
        help="Output directory for archives (default: <repo>/dist)",
    )
    args = parser.parse_args()

    skills_root = args.skills_root.resolve()
    if not skills_root.exists():
        raise SystemExit(f"Skills directory not found: {skills_root}")
    skill_dirs = discover_skills(skills_root)

    # Rebuild both flavor directories from scratch so stale skills don't linger.
    plain_dir = args.dist_root / "skills"
    openai_dir = args.dist_root / "skills-openai"
    for directory in (plain_dir, openai_dir):
        if directory.exists():
            shutil.rmtree(directory)

    count = build_flavor(skill_dirs, plain_dir, openai=False)
    build_flavor(skill_dirs, openai_dir, openai=True)

    print(f"packaged {count} skills")
    print(f"  {plain_dir}/  (Agent Skills format)")
    print(f"  {openai_dir}/  (ChatGPT/Codex format, with agents/openai.yaml)")


if __name__ == "__main__":
    main()
