#!/usr/bin/env python3
"""Update an exported ChatGPT plugin with the latest combined Basic Memory skill.

Usage: just chatgpt-plugin
Builds from the checked-in configuration in plugins/chatgpt/, even on a clean
checkout. An optional source directory or export ZIP can override that base.
Files outside skills/basic-memory/ are preserved verbatim.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from build_skills_dist import CHATGPT, REPO_ROOT, discover_skills, stage_combined_skill


def build_plugin(source: Path, output: Path) -> None:
    """Replace the combined skill while preserving the exported plugin configuration."""
    if source.is_dir():
        files = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file()
        }
    else:
        with zipfile.ZipFile(source) as archive:
            files = {
                entry.filename: archive.read(entry)
                for entry in archive.infolist()
                if not entry.is_dir()
            }
    # Keep the exported manifest format intact so its MCP declaration remains
    # effective; a new portable manifest would shadow the legacy configuration.
    manifest_paths = ("plugin.json", ".codex-plugin/plugin.json")
    candidates = [
        (name, suffix)
        for name in files
        for suffix in manifest_paths
        if name == suffix
        or (
            name != ".codex-plugin/plugin.json"
            and name.endswith("/" + suffix)
            and name.removesuffix("/" + suffix).count("/") == 0
        )
    ]
    if len(candidates) != 1:
        raise ValueError("Export must contain exactly one root or wrapped plugin.json")
    manifest_path, suffix = candidates[0]
    prefix = manifest_path.removesuffix(suffix)
    if any(not name.startswith(prefix) for name in files):
        raise ValueError("Export contains files outside the plugin folder")
    files = {name.removeprefix(prefix): content for name, content in files.items()}
    if any(".." in PurePosixPath(name).parts or name.startswith("/") for name in files):
        raise ValueError("Export contains unsafe archive paths")
    manifest = json.loads(files[suffix])
    if not isinstance(manifest, dict) or not isinstance(manifest.get("name"), str):
        raise ValueError("plugin.json must contain the existing plugin name")

    # Preserve the complete export: replacing its manifest could silently drop
    # the existing MCP declaration or app binding, which ChatGPT rejects.
    with tempfile.TemporaryDirectory() as temporary:
        staged = stage_combined_skill(
            discover_skills(REPO_ROOT / "skills"), Path(temporary), CHATGPT
        )
        files = {
            name: content
            for name, content in files.items()
            if not name.startswith("skills/basic-memory/")
        }
        for path in staged.rglob("*"):
            if path.is_file():
                files[f"skills/basic-memory/{path.relative_to(staged).as_posix()}"] = (
                    path.read_bytes()
                )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    print(f"Packaged {output} for existing plugin {manifest['name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        default=REPO_ROOT / "plugins/chatgpt",
        help="Plugin directory or export ZIP (default: plugins/chatgpt)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "dist/chatgpt-plugin/basic-memory-chatgpt-plugin.zip",
    )
    args = parser.parse_args()
    build_plugin(args.source, args.output)


if __name__ == "__main__":
    main()
