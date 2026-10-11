#!/usr/bin/env python3
"""Package Basic Memory SKILL.md sources into distributable archives.

The canonical skill source lives in ``skills/memory-*/`` as pure markdown. This
script stages and zips those skills into ``dist/`` in two flavors:

  dist/skills/<name>.zip                          Agent Skill (SKILL.md + resources)
  dist/skills/basic-memory.zip                    one combined, uploadable skill
  dist/skills/basic-memory-skills.zip             every skill, for unzipping by hand
  dist/skills-openai/<name>-chatgpt.zip           same skill + agents/openai.yaml
  dist/skills-openai/basic-memory-chatgpt.zip
  dist/skills-openai/basic-memory-skills-chatgpt.zip

The plain flavor is the open Agent Skills format (agentskills.io) — the zip
Claude, Cursor, and the `skills` CLI consume. The ChatGPT flavor adds an
``agents/openai.yaml`` interface block so the skill shows up with a name, blurb,
and brand color in ChatGPT/Codex (see https://learn.chatgpt.com/docs/build-skills).
Every asset name is unique across both flavors, because the publish workflow
uploads them all to one GitHub release (``skills-latest``) whose download URLs
the Basic Memory web app links to.

Claude and ChatGPT accept one skill per upload, so ``basic-memory.zip`` folds the
hosted-friendly skills into a single skill: a hand-written router SKILL.md
(``scripts/skill-bundle/basic-memory.md``) plus one generated
``references/<skill>.md`` per skill, read on demand.

Dependency-free (stdlib only) so it runs under bare ``python3`` in CI, matching
scripts/validate_skills.py — which owns the frontmatter parser we reuse here.
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from validate_skills import (
    distributed_skill_names,
    memory_skill_dirs,
    parse_frontmatter,
    skill_body,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Basic Memory brand blue — matches the Codex plugin's authored openai.yaml files.
BRAND_COLOR = "#2563EB"
BUNDLE_NAME = "basic-memory-skills"
COMBINED_NAME = "basic-memory"
COMBINED_DISPLAY_NAME = "Basic Memory"
COMBINED_SHORT_DESCRIPTION = "Find, read, and save notes in your Basic Memory knowledge base."
# Not named SKILL.md so no skill discovery (the `skills` CLI, even with
# --full-depth; plugin loaders; our memory-* globs) can mistake the template for
# a second, competing copy of the individual skills.
COMBINED_TEMPLATE = REPO_ROOT / "scripts" / "skill-bundle" / "basic-memory.md"
ROUTING_TABLE_MARKER = "<!-- routing-table -->"

# Skills left out of the combined skill. They still ship as individual zips.
COMBINED_EXCLUDED = {
    # Reorganizes MEMORY.md and memory/ files on the agent's own disk.
    "memory-defrag",
    # Background consolidation of local daily notes into MEMORY.md, run by cron.
    "memory-reflect",
    # Needs the source text copied into the project directory and `bm` CLI verbs.
    "memory-literary-analysis",
}

# Hand-written routing intents. A skill without one routes by its description,
# so a new skill joins the combined skill with no code change.
ROUTING_INTENTS = {
    "memory-quest": (
        "Finish Memory Quest, verify this assistant is set up, or become a Memory Master"
    ),
}

# evals/ holds test fixtures for skill authors, not instructions for the agent.
EVALS_DIR = "evals"
# agents/ holds OpenAI-only metadata (agents/openai.yaml).
AGENTS_DIR = "agents"
IGNORED_FILES = {".DS_Store"}


@dataclass(frozen=True)
class Flavor:
    dist_subdir: str
    suffix: str
    openai: bool

    def asset(self, base: str) -> str:
        return f"{base}{self.suffix}.zip"


PLAIN = Flavor(dist_subdir="skills", suffix="", openai=False)
CHATGPT = Flavor(dist_subdir="skills-openai", suffix="-chatgpt", openai=True)
FLAVORS = (PLAIN, CHATGPT)


def discover_skills(skills_root: Path) -> list[Path]:
    """Return sorted, distributable memory-* skill directories (internal ones excluded)."""
    distributed = distributed_skill_names(skills_root)
    return [
        skill_dir for skill_dir in memory_skill_dirs(skills_root) if skill_dir.name in distributed
    ]


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


def generate_openai_yaml(source: str, title: str, description: str) -> str:
    """Build an agents/openai.yaml interface block.

    We emit only the ``interface`` section: the MCP dependency that these skills
    need (the basic-memory server) is provided by the host at runtime — the
    plugin's .mcp.json locally, or ChatGPT's connected MCP remotely — not pinned
    in per-skill metadata. Implicit invocation stays at the host default
    (allowed). Hand-tune via a source agents/openai.yaml to override.
    """
    return (
        f"# Generated from {source} by scripts/build_skills_dist.py\n"
        "# ChatGPT / Codex skill metadata — https://learn.chatgpt.com/docs/build-skills\n"
        "interface:\n"
        f"  display_name: {yaml_quote(title)}\n"
        f"  short_description: {yaml_quote(short_description(description))}\n"
        f'  brand_color: "{BRAND_COLOR}"\n'
    )


def write_openai_yaml(skill_out: Path, source: str, title: str, description: str) -> None:
    agents_dir = skill_out / AGENTS_DIR
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "openai.yaml").write_text(generate_openai_yaml(source, title, description))


# --- Staging individual skills ---


def resource_ignore(skill_dir: Path, *, keep_agents: bool) -> Callable[[str, list[str]], set[str]]:
    """copytree ignore hook: drop evals/ (and agents/ unless wanted) at the skill root."""
    top_level = {EVALS_DIR} if keep_agents else {EVALS_DIR, AGENTS_DIR}

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in IGNORED_FILES}
        if Path(directory) == skill_dir:
            ignored |= top_level & set(names)
        return ignored

    return ignore


def stage_skill(skill_dir: Path, dest: Path, flavor: Flavor) -> Path:
    """Copy a skill directory into `dest/<name>`, adding openai.yaml for ChatGPT."""
    out = dest / skill_dir.name
    shutil.copytree(skill_dir, out, ignore=resource_ignore(skill_dir, keep_agents=flavor.openai))

    # Respect a hand-authored source agents/openai.yaml; generate otherwise.
    if flavor.openai and not (out / AGENTS_DIR / "openai.yaml").exists():
        frontmatter = parse_frontmatter(skill_dir / "SKILL.md")
        write_openai_yaml(
            out,
            f"skills/{skill_dir.name}/SKILL.md",
            display_name(skill_dir.name),
            frontmatter.get("description", ""),
        )
    return out


# --- The combined `basic-memory` skill ---


def resource_files(skill_dir: Path) -> list[str]:
    """Posix paths of a skill's bundled resources, relative to the skill folder."""
    files: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        rel = path.relative_to(skill_dir)
        if not path.is_file() or rel.as_posix() == "SKILL.md" or path.name in IGNORED_FILES:
            continue
        if rel.parts[0] in (EVALS_DIR, AGENTS_DIR):
            continue
        files.append(rel.as_posix())
    return files


def rewrite_resource_links(body: str, skill_name: str, resources: list[str]) -> str:
    """Point a skill's references to its own resources at their new home.

    A skill's resources move from `<skill>/<path>` to `references/<skill>/<path>`,
    and its SKILL.md becomes `references/<skill>.md`. Markdown links resolve
    relative to the file, so they gain a `<skill>/` prefix. Backticked paths are
    read by the agent from the skill root, so they gain `references/<skill>/`.
    """
    for resource in resources:
        escaped = re.escape(resource)
        body = re.sub(
            rf"\]\((?:\./)?{escaped}(#[^)]*)?\)",
            lambda match, target=f"{skill_name}/{resource}": f"]({target}{match.group(1) or ''})",
            body,
        )
        root_relative = f"references/{skill_name}/{resource}"
        body = re.sub(rf"`(?:\./)?{escaped}`", f"`{root_relative}`", body)
        # Link text that spells out the path is read like a backticked path.
        body = re.sub(rf"\[(?:\./)?{escaped}\]\(", f"[{root_relative}](", body)
    return body


def reference_document(skill_dir: Path) -> str:
    """A skill's SKILL.md as a reference: frontmatter swapped for a short header."""
    skill_file = skill_dir / "SKILL.md"
    description = parse_frontmatter(skill_file)["description"]
    body = rewrite_resource_links(
        skill_body(skill_file).lstrip("\n"), skill_dir.name, resource_files(skill_dir)
    )
    header = (
        f"<!-- Generated from skills/{skill_dir.name}/SKILL.md by "
        "scripts/build_skills_dist.py. Edit the source, not this file. -->\n\n"
        f"> **{skill_dir.name}** — {description}\n\n"
    )
    return header + body


def routing_table(skill_dirs: list[Path]) -> str:
    rows = ["| Request | Read in full first |", "|---|---|"]
    for skill_dir in skill_dirs:
        intent = (
            ROUTING_INTENTS.get(skill_dir.name)
            or parse_frontmatter(skill_dir / "SKILL.md")["description"]
        )
        cell = intent.replace("|", "\\|")
        rows.append(f"| {cell} | `references/{skill_dir.name}.md` |")
    return "\n".join(rows)


def combined_skill_dirs(skill_dirs: list[Path]) -> list[Path]:
    return [skill_dir for skill_dir in skill_dirs if skill_dir.name not in COMBINED_EXCLUDED]


def stage_combined_skill(
    skill_dirs: list[Path], dest: Path, flavor: Flavor, template: Path = COMBINED_TEMPLATE
) -> Path:
    """Stage `dest/basic-memory`: router SKILL.md plus generated references."""
    included = combined_skill_dirs(skill_dirs)
    out = dest / COMBINED_NAME
    references = out / "references"
    references.mkdir(parents=True)

    template_text = template.read_text()
    if ROUTING_TABLE_MARKER not in template_text:
        raise SystemExit(f"{template}: missing {ROUTING_TABLE_MARKER} marker")
    (out / "SKILL.md").write_text(
        template_text.replace(ROUTING_TABLE_MARKER, routing_table(included))
    )

    for skill_dir in included:
        (references / f"{skill_dir.name}.md").write_text(reference_document(skill_dir))
        for resource in resource_files(skill_dir):
            target = references / skill_dir.name / resource
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_dir / resource, target)

    if flavor.openai:
        write_openai_yaml(
            out,
            "scripts/skill-bundle/basic-memory.md",
            COMBINED_DISPLAY_NAME,
            COMBINED_SHORT_DESCRIPTION,
        )
    return out


# --- Archiving ---


def zip_tree(source_root: Path, zip_path: Path, arc_base: Path | None = None) -> None:
    """Zip every file under `source_root`, with arc paths relative to `arc_base`.

    `arc_base` defaults to `source_root`. Passing the staging root as `arc_base`
    keeps the `<name>/` folder prefix inside a single-skill zip, so unzipping it
    (or dropping it into an uploader) lands a `<name>/SKILL.md` skill directory —
    the layout the Agent Skills spec and Claude / ChatGPT uploaders expect.
    """
    base = arc_base or source_root
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(base).as_posix())


def verify_layout(archive: Path, skill_names: set[str]) -> None:
    """Fail the build unless `archive` holds exactly `<name>/SKILL.md` skill folders.

    Claude's uploader requires the skill folder as the zip root; a zip that
    unpacks loose files or test fixtures would upload broken or not at all.
    """
    with zipfile.ZipFile(archive) as zf:
        members = zf.namelist()
    roots = {member.split("/", 1)[0] for member in members}
    missing = sorted(name for name in skill_names if f"{name}/SKILL.md" not in members)
    if roots != skill_names or missing:
        raise SystemExit(
            f"{archive.name}: expected skill folders {sorted(skill_names)} with SKILL.md, "
            f"found roots {sorted(roots)}"
        )
    if any(f"/{EVALS_DIR}/" in member for member in members):
        raise SystemExit(f"{archive.name}: contains {EVALS_DIR}/ test fixtures")


def build_flavor(
    skill_dirs: list[Path], dist_dir: Path, flavor: Flavor, template: Path = COMBINED_TEMPLATE
) -> list[Path]:
    """Stage + zip each skill, the combined skill, and the bundle for one flavor.

    Returns the archives written. Staging happens in a sibling ``.stage``
    directory so the zips contain a clean ``<name>/SKILL.md`` root.
    """
    stage = dist_dir / ".stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    archives: list[Path] = []

    for skill_dir in skill_dirs:
        staged = stage_skill(skill_dir, stage, flavor)
        archive = dist_dir / flavor.asset(skill_dir.name)
        # arc_base=stage keeps the `<name>/` prefix inside the single-skill zip.
        zip_tree(staged, archive, arc_base=stage)
        verify_layout(archive, {skill_dir.name})
        archives.append(archive)

    # Bundle: every individual skill under one zip root, for unzipping by hand.
    bundle = dist_dir / flavor.asset(BUNDLE_NAME)
    zip_tree(stage, bundle)
    verify_layout(bundle, {skill_dir.name for skill_dir in skill_dirs})
    archives.append(bundle)

    # The combined skill is staged after the bundle so the bundle never holds it.
    combined_stage = dist_dir / ".stage-combined"
    if combined_stage.exists():
        shutil.rmtree(combined_stage)
    staged = stage_combined_skill(skill_dirs, combined_stage, flavor, template)
    combined = dist_dir / flavor.asset(COMBINED_NAME)
    zip_tree(staged, combined, arc_base=combined_stage)
    verify_layout(combined, {COMBINED_NAME})
    archives.append(combined)

    shutil.rmtree(stage)
    shutil.rmtree(combined_stage)
    return archives


def build_all(skills_root: Path, dist_root: Path, template: Path = COMBINED_TEMPLATE) -> list[Path]:
    """Rebuild every flavor from scratch and return all archives written."""
    skill_dirs = discover_skills(skills_root)
    archives: list[Path] = []
    for flavor in FLAVORS:
        flavor_dir = dist_root / flavor.dist_subdir
        # Rebuild from scratch so stale skills don't linger.
        if flavor_dir.exists():
            shutil.rmtree(flavor_dir)
        archives.extend(build_flavor(skill_dirs, flavor_dir, flavor, template))

    # Every asset lands in one GitHub release, so basenames must be unique.
    names = [archive.name for archive in archives]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SystemExit(f"duplicate archive names across flavors: {duplicates}")
    return archives


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

    archives = build_all(args.skills_root.resolve(), args.dist_root)
    print(f"packaged {len(archives)} archives")
    for archive in archives:
        print(f"  {archive}")


if __name__ == "__main__":
    main()
