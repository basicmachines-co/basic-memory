"""Regression coverage for updating ChatGPT plugin exports without losing MCP."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_chatgpt_plugin.py"


@pytest.mark.parametrize("prefix", ["", "exported-plugin/"])
def test_preserves_plugin_configuration_and_replaces_skill(tmp_path: Path, prefix: str) -> None:
    source = tmp_path / "existing.zip"
    output = tmp_path / "updated.zip"
    preserved = {
        "plugin.json": json.dumps({"name": "app-existing", "version": "1.0.0"}).encode(),
        "mcp.json": b'{"mcpServers":{"memory":{"url":"https://example.com/mcp"}}}',
        ".app.json": b'{"apps":{}}',
        "assets/icon.png": b"existing binary asset",
        "skills/other/SKILL.md": b"existing other skill",
    }
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(prefix + "assets/", b"")
        for name, content in preserved.items():
            archive.writestr(prefix + name, content)
        archive.writestr(prefix + "skills/basic-memory/SKILL.md", "old router")
        archive.writestr(prefix + "skills/basic-memory/references/stale.md", "stale")
    subprocess.run([sys.executable, str(SCRIPT), str(source), "--output", str(output)], check=True)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name, content in preserved.items():
            assert archive.read(name) == content
        assert "skills/basic-memory/references/stale.md" not in archive.namelist()
        assert b"name: basic-memory" in archive.read("skills/basic-memory/SKILL.md")
        assert "skills/basic-memory/agents/openai.yaml" in archive.namelist()
        assert "skills/basic-memory/references/memory-notes.md" in archive.namelist()


@pytest.mark.parametrize(
    "files, message",
    [
        ({"basic-memory/SKILL.md": "skill"}, "exactly one"),
        ({"plugin.json": "{}"}, "existing plugin name"),
        ({"plugin.json": '{"name":"existing"}', "../bad": "bad"}, "unsafe"),
        ({"wrapped/plugin.json": "{}", "unrelated": "bad"}, "outside"),
    ],
)
def test_rejects_invalid_export(tmp_path: Path, files: dict[str, str], message: str) -> None:
    source = tmp_path / "existing.zip"
    output = tmp_path / "updated.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert not output.exists()


def test_rebuilds_output_without_source_argument(tmp_path: Path) -> None:
    output = tmp_path / "plugin.zip"
    manifest = b'{"name":"existing","version":"1.0.0"}'
    connection = b'{"mcpServers":{"memory":{"url":"https://example.com/mcp"}}}'
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("plugin.json", manifest)
        archive.writestr("mcp.json", connection)
        archive.writestr("skills/basic-memory/SKILL.md", "old skill")
    subprocess.run([sys.executable, str(SCRIPT), "--output", str(output)], check=True)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("plugin.json") == manifest
        assert archive.read("mcp.json") == connection
        assert b"name: basic-memory" in archive.read("skills/basic-memory/SKILL.md")
