from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_versions.py"
SPEC = importlib.util.spec_from_file_location("update_versions", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
update_versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_versions)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.21.3", "0.21.3"),
        ("0.21.3b1", "0.21.3-beta.1"),
        ("0.21.3rc1", "0.21.3-rc.1"),
    ],
)
def test_npm_package_version_maps_python_prereleases(version: str, expected: str) -> None:
    assert update_versions.npm_package_version(version) == expected


def test_parse_version_preserves_python_prerelease_for_non_npm_manifests() -> None:
    assert update_versions.parse_version("v0.21.3b1") == "0.21.3b1"


def test_update_versions_writes_npm_semver_prerelease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def write(path: str, content: str) -> None:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    package_manifest = {"version": "0.0.0"}
    marketplace_manifest = {
        "metadata": {"version": "0.0.0"},
        "plugins": [{"name": "basic-memory", "version": "0.0.0"}],
    }

    monkeypatch.setattr(update_versions, "ROOT", tmp_path)
    write("src/basic_memory/__init__.py", '__version__ = "0.0.0"\n')
    write(
        "server.json",
        json.dumps(
            {
                "version": "0.0.0",
                "packages": [{"identifier": "basic-memory", "version": "0.0.0"}],
            }
        )
        + "\n",
    )
    write(".claude-plugin/marketplace.json", json.dumps(marketplace_manifest) + "\n")
    write("plugins/claude-code/.claude-plugin/plugin.json", json.dumps(package_manifest) + "\n")
    write(
        "plugins/claude-code/.claude-plugin/marketplace.json",
        json.dumps(marketplace_manifest) + "\n",
    )
    write("integrations/hermes/plugin.yaml", "version: 0.0.0\n")
    write("integrations/hermes/__init__.py", '__version__ = "0.0.0"\n')
    write("integrations/openclaw/package.json", json.dumps(package_manifest) + "\n")

    update_versions.update_versions("v0.21.3b1", dry_run=False)

    assert (tmp_path / "src/basic_memory/__init__.py").read_text() == ('__version__ = "0.21.3b1"\n')
    assert (tmp_path / "integrations/hermes/__init__.py").read_text() == (
        '__version__ = "0.21.3b1"\n'
    )
    openclaw_package = json.loads((tmp_path / "integrations/openclaw/package.json").read_text())
    assert openclaw_package["version"] == "0.21.3-beta.1"


def _seed_repo(tmp_path: Path) -> None:
    """Write all version-bearing manifests at 0.0.0 under a fake repo root."""

    def write(path: str, content: str) -> None:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    package_manifest = {"version": "0.0.0"}
    marketplace_manifest = {
        "metadata": {"version": "0.0.0"},
        "plugins": [{"name": "basic-memory", "version": "0.0.0"}],
    }
    write("src/basic_memory/__init__.py", '__version__ = "0.0.0"\n')
    write(
        "server.json",
        json.dumps(
            {"version": "0.0.0", "packages": [{"identifier": "basic-memory", "version": "0.0.0"}]}
        )
        + "\n",
    )
    write(".claude-plugin/marketplace.json", json.dumps(marketplace_manifest) + "\n")
    write("plugins/claude-code/.claude-plugin/plugin.json", json.dumps(package_manifest) + "\n")
    write(
        "plugins/claude-code/.claude-plugin/marketplace.json",
        json.dumps(marketplace_manifest) + "\n",
    )
    write("integrations/hermes/plugin.yaml", "version: 0.0.0\n")
    write("integrations/hermes/__init__.py", '__version__ = "0.0.0"\n')
    write("integrations/openclaw/package.json", json.dumps(package_manifest) + "\n")


def _plugin_version(tmp_path: Path) -> str:
    path = tmp_path / "plugins/claude-code/.claude-plugin/plugin.json"
    return json.loads(path.read_text())["version"]


def test_scope_packages_leaves_core_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(update_versions, "ROOT", tmp_path)
    _seed_repo(tmp_path)

    update_versions.update_versions("v0.21.6", scope="packages", dry_run=False)

    # Core stays put; only the agent/plugin artifacts move.
    assert (tmp_path / "src/basic_memory/__init__.py").read_text() == '__version__ = "0.0.0"\n'
    assert json.loads((tmp_path / "server.json").read_text())["version"] == "0.0.0"
    assert _plugin_version(tmp_path) == "0.21.6"


def test_scope_core_leaves_packages_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(update_versions, "ROOT", tmp_path)
    _seed_repo(tmp_path)

    update_versions.update_versions("v0.21.6", scope="core", dry_run=False)

    assert (tmp_path / "src/basic_memory/__init__.py").read_text() == '__version__ = "0.21.6"\n'
    assert _plugin_version(tmp_path) == "0.0.0"


def test_invalid_scope_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_versions, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        update_versions.update_versions("v0.21.6", scope="nonsense", dry_run=True)
