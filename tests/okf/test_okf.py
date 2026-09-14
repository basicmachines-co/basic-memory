"""Structural rules, source-preserving rendering, and staged export failures."""

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app
from basic_memory.cli.container import CliContainer, set_container
from basic_memory.config import BasicMemoryConfig, ProjectEntry
from basic_memory.okf.export import export_project, snapshot_files
from basic_memory.okf.render import (
    ExportFile,
    ExportSnapshot,
    RecordedChange,
    convert_wikilinks,
    render_bundle,
)
from basic_memory.okf.validation import check_bundle, check_document, parse_document
from basic_memory.runtime.mode import RuntimeMode


@pytest.mark.parametrize(
    "path,content,rule",
    [
        ("a.md", "body", "concept.frontmatter"),
        ("a.md", "---\ntype: [\n---\n", "frontmatter"),
        ("a.md", "---\ntype: note\n", "frontmatter"),
        ("a.md", "---\n- note\n---\n", "frontmatter"),
        ("a.md", "---\ntype: ' '\n---\n", "concept.type"),
        ("a.md", "---\ntype: 2\n---\n", "concept.type"),
        ("index.md", "---\ntype: note\n---\n# Index", "reserved.frontmatter"),
        ("sub/index.md", "---\nokf_version: '0.2'\n---\n# Index", "reserved.frontmatter"),
        ("log.md", "---\nokf_version: '0.2'\n---\n# Log", "reserved.frontmatter"),
        ("index.md", "- [[Target]]", "index.link"),
        ("index.md", "[Target](target.md)", "index.heading"),
        ("log.md", "## Yesterday", "log.date"),
        ("log.md", "## 2026-02-31", "log.date"),
        ("log.md", "### 2026-01-01", "log.date"),
        ("log.md", "## 2026-01-01\n## 2026-02-01", "log.order"),
    ],
)
def test_structural_diagnostics(path, content, rule):
    diagnostics = check_document(path, content)
    assert any(item.path == path and item.rule == rule for item in diagnostics)


@pytest.mark.parametrize(
    "path,content",
    [
        (
            "odd.md",
            "---\ntype: Unregistered\nunknown: [1, true]\nverified: {by: human:a}\n---\n[missing](/gone.md)",
        ),
        ("index.md", "---\nokf_version: '9.9'\n---\n# Index\n- [Missing](gone/)"),
        ("sub/index.md", "# Index\n- [Missing](gone.md)\n\nOrdinary prose."),
        ("log.md", "# Log\n## 2026-02-01\n- Added.\n## 2026-01-01\n- Started."),
        ("log.md", "# Log\nNo history recorded."),
    ],
)
def test_soft_guidance_is_not_rejected(path, content):
    assert check_document(path, content) == []


def test_filesystem_check_includes_hidden_files_and_assets(tmp_path):
    (tmp_path / "a.md").write_text("---\ntype: note\n---\n")
    (tmp_path / "asset.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / ".hidden.md").write_bytes(b"\xff")
    (tmp_path / "link.md").symlink_to(tmp_path / "a.md")
    report = check_bundle(tmp_path)
    assert report.concepts == 2
    assert {item.rule for item in report.diagnostics} == {"filesystem.read", "filesystem.symlink"}
    assert not check_bundle(tmp_path / "absent").success


@pytest.mark.parametrize(
    "source,expected",
    [
        ("See [[A|alias]] and [[missing]].", "See [alias](/a.md) and [missing](/missing)."),
        ("[[../a#heading]]", "[../a](/a.md#heading)"),
        ("[[/a]]", "[/a](/a.md)"),
        ("[[#here]]", "[here](/folder/source.md#here)"),
        ("[[broken", "[[broken"),
        (r"\[[A]]", r"\[[A]]"),
        ("`[[A]]`\n\n```md\n[[A]]\n```\n\n[[A]]", "`[[A]]`\n\n```md\n[[A]]\n```\n\n[A](/a.md)"),
        ("    [[A]]\n\n[[A]]", "    [[A]]\n\n[A](/a.md)"),
        ("[already](a.md)", "[already](a.md)"),
        ("> [[A]]\n- [[A]]", "> [A](/a.md)\n- [A](/a.md)"),
    ],
)
def test_link_conversion(source, expected):
    assert (
        convert_wikilinks(source, "folder/source.md", {"A": "a.md", "a.md": "a.md"}, "test")
        == expected
    )


def test_render_preserves_frontmatter_and_semantics():
    source = "---\ntitle: A\npermalink: a\ntype: custom\ntags: [one]\nbm: {other: true}\nsources: [{resource: /paper.pdf}]\n---\n# A\n- [fact] Categorized claim #tag (why)\n- depends_on [[B]] (because)\n"
    snapshot = ExportSnapshot(
        "project",
        (
            ExportFile("a.md", source.encode()),
            ExportFile("b.md", b"---\ntitle: B\n---\n# B\n"),
            ExportFile("paper.pdf", b"%PDF-1.4"),
        ),
        (RecordedChange(1, "a.md", "create", datetime(2026, 9, 14, tzinfo=UTC)),),
    )
    output = {file.path: file.content for file in render_bundle(snapshot)}
    doc = parse_document(output["a.md"].decode())
    assert doc.metadata["type"] == "custom"
    assert doc.metadata["tags"] == ["one"]
    assert doc.metadata["sources"] == [{"resource": "/paper.pdf"}]
    assert doc.metadata["bm"] == {
        "other": True,
        "okf_export": {
            "version": 1,
            "relations": [{"type": "depends_on", "target": "B", "context": "because"}],
        },
    }
    assert "- [fact] Categorized claim #tag (why)" in doc.body
    assert "- depends_on [B](/b.md) (because)" in doc.body
    assert output["paper.pdf"] == b"%PDF-1.4"
    assert "## 2026-09-14" in output["log.md"].decode()
    assert not output["log.md"].startswith(b"---")
    assert "[[" not in output["index.md"].decode()
    assert render_bundle(snapshot) == render_bundle(snapshot)


@pytest.fixture
def export_config(config_home):
    root = config_home / "source"
    root.mkdir()
    (root / "a.md").write_text("---\ntype: note\n---\n# A\n")
    return BasicMemoryConfig(projects={"export": ProjectEntry(path=str(root))})


@pytest.mark.asyncio
async def test_export_replace_and_source_safety(export_config, tmp_path):
    root = Path(export_config.projects["export"].path)
    original = (root / "a.md").read_bytes()
    destination = tmp_path / "bundle"
    assert (await export_project(export_config, "export", destination)).success
    with pytest.raises(ValueError, match="Destination exists"):
        await export_project(export_config, "export", destination)
    (destination / "old.pdf").write_bytes(b"old")
    assert (await export_project(export_config, "export", destination, replace=True)).success
    assert not (destination / "old.pdf").exists()
    assert (root / "a.md").read_bytes() == original
    for unsafe in (root, root / "bundle", root.parent):
        with pytest.raises(ValueError, match="outside"):
            await export_project(export_config, "export", unsafe, replace=True)
    with pytest.raises(ValueError, match="configured local"):
        await export_project(export_config, "absent", destination)


@pytest.mark.asyncio
async def test_failed_validation_leaves_destination_intact(export_config, tmp_path):
    root = Path(export_config.projects["export"].path)
    (root / "a.md").write_text("---\ntype: ''\n---\n")
    destination = tmp_path / "bundle"
    destination.mkdir()
    (destination / "keep").write_bytes(b"keep")
    report = await export_project(export_config, "export", destination, replace=True)
    assert not report.success
    assert (destination / "keep").read_bytes() == b"keep"
    assert not list(tmp_path.glob(".bm-okf-*"))


def test_reserved_source_and_ignore_rules(export_config):
    root = Path(export_config.projects["export"].path)
    (root / "index.md").write_text("---\nbm: {profile: wiki/1}\n---\n[[Live Wiki]]")
    (root / "log.md").write_text("# old log")
    (root / ".secret").write_text("secret")
    (root / "paper.pdf").write_bytes(b"pdf")
    assert {file.path for file in snapshot_files(root)} == {"a.md", "paper.pdf"}
    (root / "index.md").write_text("---\ntype: note\n---\nUser concept")
    with pytest.raises(ValueError, match="rename it first"):
        snapshot_files(root)


def test_cli_check_without_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "basic_memory.cli.app.CliContainer.create", lambda: pytest.fail("config read")
    )
    (tmp_path / "a.md").write_text("---\ntype: note\n---\n")
    runner = CliRunner()
    result = runner.invoke(app, ["okf", "check", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["concepts"] == 1
    (tmp_path / "a.md").write_text("broken")
    result = runner.invoke(app, ["okf", "check", str(tmp_path)])
    assert result.exit_code == 1
    assert "a.md: concept.frontmatter" in result.stdout


def test_cli_export(export_config, tmp_path):
    set_container(CliContainer(export_config, RuntimeMode.TEST))
    runner = CliRunner()
    result = runner.invoke(
        app, ["okf", "export", str(tmp_path / "bundle"), "--project", "export", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["concepts"] == 1
    result = runner.invoke(
        app, ["okf", "export", str(tmp_path / "bundle"), "--project", "export", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["diagnostics"][0]["rule"] == "export"


@pytest.mark.parametrize(
    "body,expected",
    [
        ("[outer [[A]]](url)", "[outer [[A]]](url)"),
        ("`open\n\n[[A]]\n\nclose`", "`open\n\n[A](/a.md)\n\nclose`"),
        ("[[A]]\r\n[[A]]", "[A](/a.md)\n[A](/a.md)"),
        ("[[outer [[inner]]]]", r"[outer \[\[inner\]\]](/outer%20%5B%5Binner%5D%5D)"),
        ("- continuation\n  [[A]]", "- continuation\n  [A](/a.md)"),
    ],
)
def test_conversion_respects_inline_block_boundaries(body, expected):
    assert convert_wikilinks(body, "source.md", {"A": "a.md"}, "p") == expected


def test_unlocatable_normalized_source_fails_instead_of_rewriting_wrong_span():
    with pytest.raises(ValueError, match="cannot locate wikilink source span"):
        convert_wikilinks("\0 [[A]]", "source.md", {}, "p")


def test_asset_links_and_reserved_casing(export_config):
    root = Path(export_config.projects["export"].path)
    (root / "Index.md").write_text("---\ntype: note\n---\n")
    with pytest.raises(ValueError, match="casing collides"):
        snapshot_files(root)
    files = render_bundle(
        ExportSnapshot(
            "p",
            (
                ExportFile("refs/paper.pdf", b"pdf"),
                ExportFile("notes/source.md", b"---\ntype: note\n---\n[[../refs/paper.pdf]]"),
            ),
        )
    )
    assert b"[../refs/paper.pdf](/refs/paper.pdf)" in next(
        file.content for file in files if file.path == "notes/source.md"
    )


def test_log_entries_require_date_group():
    assert check_document("log.md", "# Log\n- Undated entry")[0].rule == "log.group"


def test_source_relative_path_precedes_root_path():
    targets = {"nested/a.md": "nested/a.md", "folder/nested/a.md": "folder/nested/a.md"}
    assert (
        convert_wikilinks("[[nested/a]]", "folder/source.md", targets, "p")
        == "[nested/a](/folder/nested/a.md)"
    )
