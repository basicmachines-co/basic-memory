#!/usr/bin/env python3
"""Publish the built skill archives to the rolling `skills-latest` release.

The Basic Memory web app links to these assets by name, so a failed publish must
never leave a download URL empty. `gh release upload --clobber` deletes each old
asset before uploading its replacement, which is exactly that failure. Instead:

1. Clear staging and retired assets left by an earlier run that died midway.
2. Upload every new archive under a staging name. The live assets are untouched,
   so a failure here changes nothing users can see.
3. Only once every upload has succeeded, swap each asset into place: rename the
   live asset aside, rename the staged one to the live name, delete the old one.
4. Delete live assets for skills that are no longer built.

Dependency-free (stdlib only) like the other skills scripts, so it runs under
bare ``python3`` in CI. Usage: ``publish_skills_release.py <archive>...``
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

RELEASE_TAG = "skills-latest"
STAGING_PREFIX = "staging--"
RETIRED_PREFIX = "retired--"
# The web app and docs link to these by name; publishing without one is a bug.
CONTRACT_ASSETS = ("basic-memory.zip", "basic-memory-chatgpt.zip", "memory-onboarding.zip")


class Release(Protocol):
    """The few release operations publishing needs, so tests can fake GitHub."""

    def asset_names(self) -> list[str]: ...
    def upload(self, path: Path, name: str) -> None: ...
    def rename(self, old: str, new: str) -> None: ...
    def delete(self, name: str) -> None: ...


def archive_names(archives: Sequence[Path]) -> dict[str, Path]:
    """Map each archive's asset name to its file, failing on any broken contract."""
    if not archives:
        raise SystemExit("No archives to publish")
    names: dict[str, Path] = {}
    for archive in archives:
        if archive.name in names:
            raise SystemExit(
                f"Duplicate asset name {archive.name}: {names[archive.name]}, {archive}"
            )
        if archive.name.startswith((STAGING_PREFIX, RETIRED_PREFIX)):
            raise SystemExit(f"Asset name {archive.name} uses a reserved prefix")
        names[archive.name] = archive
    missing = [name for name in CONTRACT_ASSETS if name not in names]
    if missing:
        raise SystemExit(f"Missing contract assets: {', '.join(missing)}")
    return names


def publish(release: Release, archives: dict[str, Path]) -> None:
    # --- Leftovers from an interrupted run ---
    for name in release.asset_names():
        if name.startswith((STAGING_PREFIX, RETIRED_PREFIX)):
            release.delete(name)

    # --- Stage: upload everything before changing anything live ---
    for name, path in sorted(archives.items()):
        release.upload(path, STAGING_PREFIX + name)

    # --- Swap: each live name is missing only between two renames ---
    live = set(release.asset_names())
    for name in sorted(archives):
        if name in live:
            release.rename(name, RETIRED_PREFIX + name)
        release.rename(STAGING_PREFIX + name, name)
        if name in live:
            release.delete(RETIRED_PREFIX + name)

    # --- Prune skills that are no longer built ---
    for name in sorted(live - set(archives)):
        if not name.startswith((STAGING_PREFIX, RETIRED_PREFIX)):
            print(f"Deleting stale asset {name}")
            release.delete(name)


class GhRelease:
    """`Release` backed by the gh CLI and the GitHub REST API."""

    def __init__(self, repo: str, tag: str) -> None:
        self.repo = repo
        self.tag = tag
        self._staging_dir = Path(tempfile.mkdtemp(prefix="skills-release-"))

    def _gh(self, *args: str) -> str:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout

    def _assets(self) -> dict[str, int]:
        release = json.loads(self._gh("api", f"repos/{self.repo}/releases/tags/{self.tag}"))
        return {asset["name"]: asset["id"] for asset in release["assets"]}

    def asset_names(self) -> list[str]:
        return sorted(self._assets())

    def upload(self, path: Path, name: str) -> None:
        # gh names an asset after its file, so stage a copy under the new name.
        staged = self._staging_dir / name
        shutil.copyfile(path, staged)
        self._gh("release", "upload", self.tag, str(staged))

    def rename(self, old: str, new: str) -> None:
        asset_id = self._assets()[old]
        self._gh(
            "api",
            "-X",
            "PATCH",
            f"repos/{self.repo}/releases/assets/{asset_id}",
            "-f",
            f"name={new}",
        )

    def delete(self, name: str) -> None:
        self._gh("release", "delete-asset", self.tag, name, "--yes")


def main() -> None:
    archives = archive_names([Path(arg) for arg in sys.argv[1:]])
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    notes = f"Rolling skill archives built from {sha}."

    exists = (
        subprocess.run(["gh", "release", "view", RELEASE_TAG], capture_output=True).returncode == 0
    )
    if not exists:
        # A brand-new release has no live assets to protect.
        create = ["gh", "release", "create", RELEASE_TAG, *map(str, archives.values())]
        create += ["--target", sha, "--title", "Latest Basic Memory skills"]
        create += ["--notes", notes, "--latest=false"]
        subprocess.run(create, check=True)
        return

    publish(GhRelease(repo, RELEASE_TAG), archives)
    subprocess.run(
        ["gh", "release", "edit", RELEASE_TAG, "--notes", notes, "--latest=false"], check=True
    )


if __name__ == "__main__":
    main()
