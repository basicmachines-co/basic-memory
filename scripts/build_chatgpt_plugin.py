#!/usr/bin/env python3
"""Update an exported ChatGPT plugin with the latest combined Basic Memory skill.

Usage: just chatgpt-plugin
Rebuilds the existing ZIP in dist/chatgpt-plugin/ in place.
For initial setup, pass the current plugin export once.
The export must contain a portable plugin.json at its root or in one wrapper
folder. All existing files outside skills/basic-memory/ are preserved verbatim,
including MCP connections, manifests, assets, and other skills.
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
    with zipfile.ZipFile(source) as archive:
        files = {
            entry.filename: archive.read(entry)
            for entry in archive.infolist()
            if not entry.is_dir()
        }
    manifests = [
        name
        for name in files
        if name == "plugin.json" or name.count("/") == 1 and name.endswith("/plugin.json")
    ]
    if len(manifests) != 1:
        raise ValueError("Export must contain exactly one root or wrapped plugin.json")
    prefix = manifests[0].removesuffix("plugin.json")
    if any(not name.startswith(prefix) for name in files):
        raise ValueError("Export contains files outside the plugin folder")
    files = {name.removeprefix(prefix): content for name, content in files.items()}
    if any(".." in PurePosixPath(name).parts or name.startswith("/") for name in files):
        raise ValueError("Export contains unsafe archive paths")
    manifest = json.loads(files["plugin.json"])
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
        help="Initial plugin export (defaults to rebuilding the output ZIP)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "dist/chatgpt-plugin/basic-memory-chatgpt-plugin.zip",
    )
    args = parser.parse_args()
    source = args.source if args.source is not None else args.output
    if not source.exists():
        parser.error(
            f"No plugin ZIP at {source}. Initialize once with "
            "just chatgpt-plugin /path/to/current-plugin-export.zip; "
            "subsequent builds need no arguments."
        )
    build_plugin(source, args.output)


if __name__ == "__main__":
    main()
