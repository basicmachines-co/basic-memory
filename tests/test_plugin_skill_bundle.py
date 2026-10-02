"""The Claude Code plugin bundles exact copies of the canonical memory-* skills."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/sync_plugin_skills.py"

_spec = importlib.util.spec_from_file_location("sync_plugin_skills", SCRIPT)
assert _spec is not None and _spec.loader is not None
sync_plugin_skills = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_plugin_skills)


def test_plugin_bundle_matches_canonical_skills() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_bundle_covers_every_canonical_skill_except_exclusions() -> None:
    canonical = {path.name for path in (REPO_ROOT / "skills").glob("memory-*") if path.is_dir()}
    bundled = {
        path.name
        for path in (REPO_ROOT / "plugins/claude-code/skills").glob("memory-*")
        if path.is_dir()
    }
    assert set(sync_plugin_skills.EXCLUDED_SKILLS) <= canonical
    assert bundled == canonical - set(sync_plugin_skills.EXCLUDED_SKILLS)


def test_bundled_names_never_collide_with_plugin_owned_skills() -> None:
    plugin_skills = REPO_ROOT / "plugins/claude-code/skills"
    owned = {path.name for path in plugin_skills.iterdir() if not path.name.startswith("memory-")}
    assert owned.isdisjoint(sync_plugin_skills.bundled_skill_names())


def test_drift_check_reports_edits_missing_extra_and_stale_copies(tmp_path: Path) -> None:
    source = tmp_path / "skills"
    target = tmp_path / "plugin-skills"
    for name in ("memory-alpha", "memory-beta"):
        (source / name / "references").mkdir(parents=True)
        (source / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        (source / name / "references" / "guide.md").write_text("guide\n")
    (target / "bm-owned").mkdir(parents=True)
    (target / "bm-owned" / "SKILL.md").write_text("plugin-owned\n")

    assert sync_plugin_skills.sync_plugin_skills(source, target) == ["memory-alpha", "memory-beta"]
    assert sync_plugin_skills.plugin_skill_drift(source, target) == []

    (target / "memory-alpha" / "SKILL.md").write_text("hand edit\n")
    (target / "memory-alpha" / "extra.md").write_text("extra\n")
    (target / "memory-beta" / "references" / "guide.md").unlink()
    (target / "memory-retired").mkdir()
    shutil.copytree(source / "memory-beta", source / "memory-gamma")

    assert sync_plugin_skills.plugin_skill_drift(source, target) == [
        "memory-retired: stale copy (not a bundled canonical skill)",
        "memory-alpha/SKILL.md: differs from skills/memory-alpha/SKILL.md",
        "memory-alpha/extra.md: not in skills/memory-alpha",
        "memory-beta/references/guide.md: missing from the plugin",
        "memory-gamma: missing from the plugin",
    ]

    sync_plugin_skills.sync_plugin_skills(source, target)
    assert sync_plugin_skills.plugin_skill_drift(source, target) == []
    assert not (target / "memory-retired").exists()
    assert (target / "bm-owned" / "SKILL.md").read_text() == "plugin-owned\n"
