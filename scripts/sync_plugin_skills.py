"""Copy the canonical memory-* skills into the Claude Code plugin.

`skills/` is the single source of truth. The Claude Code plugin ships its own copy
under `plugins/claude-code/skills/` because a marketplace install copies only the
plugin directory into Claude Code's plugin cache: a path or symlink that reaches
`../../skills` is not part of the installed plugin on every install path (a
plugin-local marketplace, `--plugin-dir`, or a Windows checkout without symlinks).

Run without arguments after editing `skills/`. `--check` reports drift without
writing, and `validate_claude_plugin.py` runs that check in CI.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "skills"
PLUGIN_SKILLS_ROOT = ROOT / "plugins/claude-code/skills"

# Canonical skills the plugin deliberately does not bundle, with the reason.
EXCLUDED_SKILLS: dict[str, str] = {
    # A machine prompt for the `bm ci` GitHub Actions flow: it returns only
    # AgentSynthesis JSON, so offering it in an interactive session is a trap.
    "memory-ci-capture": "CI-only prompt for `bm ci publish`; not an interactive skill",
}


def bundled_skill_names(source_root: Path = SOURCE_ROOT) -> list[str]:
    """Every canonical memory-* skill except the deliberate exclusions."""
    return sorted(
        path.name
        for path in source_root.glob("memory-*")
        if path.is_dir() and path.name not in EXCLUDED_SKILLS
    )


def relative_files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def plugin_skill_drift(
    source_root: Path = SOURCE_ROOT, plugin_skills_root: Path = PLUGIN_SKILLS_ROOT
) -> list[str]:
    """Describe every way the plugin's memory-* copies differ from `skills/`."""
    expected = bundled_skill_names(source_root)
    present = sorted(path.name for path in plugin_skills_root.glob("memory-*") if path.is_dir())
    drift = [
        f"{name}: stale copy (not a bundled canonical skill)"
        for name in present
        if name not in expected
    ]
    for name in expected:
        target = plugin_skills_root / name
        if not target.is_dir():
            drift.append(f"{name}: missing from the plugin")
            continue
        source_files = relative_files(source_root / name)
        target_files = relative_files(target)
        for rel in sorted(source_files.keys() | target_files.keys()):
            if rel not in target_files:
                drift.append(f"{name}/{rel}: missing from the plugin")
            elif rel not in source_files:
                drift.append(f"{name}/{rel}: not in skills/{name}")
            elif source_files[rel] != target_files[rel]:
                drift.append(f"{name}/{rel}: differs from skills/{name}/{rel}")
    return drift


def sync_plugin_skills(
    source_root: Path = SOURCE_ROOT, plugin_skills_root: Path = PLUGIN_SKILLS_ROOT
) -> list[str]:
    """Replace the plugin's memory-* copies with the canonical set. bm-* is untouched."""
    expected = bundled_skill_names(source_root)
    for stale in plugin_skills_root.glob("memory-*"):
        if stale.is_dir() and stale.name not in expected:
            shutil.rmtree(stale)
    for name in expected:
        target = plugin_skills_root / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source_root / name, target)
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report drift without writing")
    args = parser.parse_args()
    if args.check:
        drift = plugin_skill_drift()
        if drift:
            parser.exit(
                1,
                "Claude Code plugin skill copies differ from skills/:\n  "
                + "\n  ".join(drift)
                + "\nRun `python3 scripts/sync_plugin_skills.py` and commit the result.\n",
            )
        print("plugin memory-* skills match skills/")
        return
    names = sync_plugin_skills()
    print(f"copied {len(names)} memory-* skills into {PLUGIN_SKILLS_ROOT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
