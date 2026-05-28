#!/usr/bin/env python3
"""Update all Basic Memory release manifests to the same product version."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^v?([0-9]+\.[0-9]+\.[0-9]+(?:b[0-9]+|rc[0-9]+)?)$")
PYTHON_PRERELEASE_RE = re.compile(
    r"^(?P<base>[0-9]+\.[0-9]+\.[0-9]+)(?:(?P<label>b|rc)(?P<number>[0-9]+))?$"
)


def parse_version(raw_version: str) -> str:
    match = VERSION_RE.match(raw_version)
    if not match:
        raise SystemExit(f"Invalid version format: {raw_version}")
    return match.group(1)


def npm_package_version(version: str) -> str:
    match = PYTHON_PRERELEASE_RE.match(version)
    if not match:
        raise SystemExit(f"Invalid normalized version format: {version}")

    label = match.group("label")
    if label is None:
        return version

    npm_label = "beta" if label == "b" else label
    return f"{match.group('base')}-{npm_label}.{match.group('number')}"


def write_if_changed(path: Path, old: str, new: str, dry_run: bool) -> bool:
    if old == new:
        print(f"unchanged {path.relative_to(ROOT)}")
        return False

    print(f"update    {path.relative_to(ROOT)}")
    if not dry_run:
        path.write_text(new)
    return True


def update_text(
    path: str,
    pattern: str,
    replacement: str,
    *,
    dry_run: bool,
) -> bool:
    file_path = ROOT / path
    old = file_path.read_text()
    new, count = re.subn(pattern, replacement, old, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Expected one version match in {path}, found {count}")
    return write_if_changed(file_path, old, new, dry_run)


def update_json(
    path: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    dry_run: bool,
) -> bool:
    file_path = ROOT / path
    old = file_path.read_text()
    data = json.loads(old)
    mutate(data)
    new = json.dumps(data, indent=2) + "\n"
    return write_if_changed(file_path, old, new, dry_run)


def set_server_version(data: dict[str, Any], version: str) -> None:
    data["version"] = version
    for package in data.get("packages", []):
        if package.get("identifier") == "basic-memory":
            package["version"] = version


def set_claude_marketplace_version(data: dict[str, Any], version: str) -> None:
    metadata = data.setdefault("metadata", {})
    metadata["version"] = version
    for plugin in data.get("plugins", []):
        if plugin.get("name") == "basic-memory":
            plugin["version"] = version


def set_package_version(data: dict[str, Any], version: str) -> None:
    data["version"] = version


def update_versions(raw_version: str, *, dry_run: bool) -> None:
    version = parse_version(raw_version)
    print(f"{'preview' if dry_run else 'writing'} Basic Memory version {version}")

    update_text(
        "src/basic_memory/__init__.py",
        r'^__version__ = ".*"$',
        f'__version__ = "{version}"',
        dry_run=dry_run,
    )
    update_json(
        "server.json",
        lambda data: set_server_version(data, version),
        dry_run=dry_run,
    )
    update_json(
        ".claude-plugin/marketplace.json",
        lambda data: set_claude_marketplace_version(data, version),
        dry_run=dry_run,
    )
    update_json(
        "plugins/claude-code/.claude-plugin/plugin.json",
        lambda data: set_package_version(data, version),
        dry_run=dry_run,
    )
    update_json(
        "plugins/claude-code/.claude-plugin/marketplace.json",
        lambda data: set_claude_marketplace_version(data, version),
        dry_run=dry_run,
    )
    update_text(
        "integrations/hermes/plugin.yaml",
        r"^version:\s*.*$",
        f"version: {version}",
        dry_run=dry_run,
    )
    update_text(
        "integrations/hermes/__init__.py",
        r'^__version__ = ".*"$',
        f'__version__ = "{version}"',
        dry_run=dry_run,
    )
    update_json(
        "integrations/openclaw/package.json",
        lambda data: set_package_version(data, npm_package_version(version)),
        dry_run=dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", help="Release version, with or without the leading v")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()
    update_versions(args.version, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
