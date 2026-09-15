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
        ("[[/a]]", "[/a](/a)"),
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
    (root / "index.md").write_text(
        "---\nbm: {profile: wiki/1}\ngenerated: {by: Basic Memory Wiki Projector}\n---\n[[Live Wiki]]"
    )
    (root / "log.md").write_text(
        "---\nbm: {profile: wiki/1}\ngenerated: {by: Basic Memory Wiki Projector}\n---\n# old log"
    )
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


def test_nul_normalization_preserves_raw_source_and_link_offsets():
    assert convert_wikilinks("before\0 [[A]]", "source.md", {"A": "a.md"}, "p") == (
        "before\0 [A](/a.md)"
    )


def test_unrecognized_parser_normalization_fails_without_rewriting_wrong_span(monkeypatch):
    from markdown_it import MarkdownIt

    original_parse = MarkdownIt.parse

    def altered_parse(parser, source, env=None):
        tokens = original_parse(parser, source, env)
        for token in tokens:
            if token.type == "inline":
                token.content = "unmappable " + token.content
        return tokens

    monkeypatch.setattr(MarkdownIt, "parse", altered_parse)
    with pytest.raises(ValueError, match="cannot locate wikilink source span"):
        convert_wikilinks("[[A]]", "source.md", {"A": "a.md"}, "p")


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


def test_filename_default_title_resolves_nested_note():
    files = render_bundle(
        ExportSnapshot(
            "p",
            (
                ExportFile("guides/Guide.md", b"---\ntype: note\n---\n# Guide"),
                ExportFile("source.md", b"[[Guide]]"),
            ),
        )
    )
    source = next(file.content for file in files if file.path == "source.md")
    assert b"[Guide](/guides/Guide.md)" in source


def test_timestamp_and_fence_whitespace_survive_export():
    content = (
        "--- \t\ntype: note\ntitle: A\npermalink: a\ntags: [tag]\n"
        "stale_after: 2026-06-30T14:00:00Z\ncreated: 2026-06-01\n---\t\n# A\n"
    )
    assert check_document("a.md", content) == []
    expected = parse_document(content)
    assert expected.metadata["stale_after"] == "2026-06-30T14:00:00Z"
    assert expected.metadata["created"] == "2026-06-01"
    files = render_bundle(ExportSnapshot("p", (ExportFile("a.md", content.encode()),)))
    document = parse_document(next(file.content.decode() for file in files if file.path == "a.md"))
    assert document.body == expected.body
    assert {key: document.metadata[key] for key in expected.metadata} == expected.metadata


@pytest.mark.parametrize("name", ["index.md", "log.md"])
def test_unmarked_reserved_notes_are_not_discarded(export_config, name):
    root = Path(export_config.projects["export"].path)
    path = root / name
    path.write_text("# My authored note\nImportant facts")
    with pytest.raises(ValueError, match="rename it first"):
        snapshot_files(root)
    assert path.read_text() == "# My authored note\nImportant facts"


def test_check_rejects_fifo_without_opening_it(tmp_path, monkeypatch):
    import os

    if os.name == "nt":
        pytest.skip("Windows does not support POSIX FIFOs")
    path = tmp_path / "blocked.md"
    os.mkfifo(path)
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("opened FIFO"))
    report = check_bundle(tmp_path)
    assert report.diagnostics[0].path == "blocked.md"
    assert report.diagnostics[0].rule == "filesystem.regular_file"


def test_export_installs_event_loop_policy_before_async_work(export_config, tmp_path, monkeypatch):
    import basic_memory.cli.commands.command_utils as command_utils

    set_container(CliContainer(export_config, RuntimeMode.TEST))
    events = []
    run_with_cleanup = command_utils.run_with_cleanup

    def install(config):
        assert config is export_config
        events.append("policy")

    def run(coroutine):
        assert events == ["policy"]
        return run_with_cleanup(coroutine)

    monkeypatch.setattr("basic_memory.db.maybe_install_uvloop", install)
    monkeypatch.setattr(command_utils, "run_with_cleanup", run)
    result = CliRunner().invoke(
        app, ["okf", "export", str(tmp_path / "bundle"), "--project", "export"]
    )
    assert result.exit_code == 0, result.output
    assert events == ["policy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".markdown", ".MD", ".Markdown"])
async def test_export_rejects_non_okf_markdown_suffix(export_config, tmp_path, suffix):
    source = Path(export_config.projects["export"].path) / ("note" + suffix)
    source.write_text("---\ntype: note\n---\n[[A]]")
    destination = tmp_path / "bundle"
    with pytest.raises(ValueError, match="lowercase .md suffix"):
        await export_project(export_config, "export", destination)
    assert not destination.exists()
    assert source.read_text() == "---\ntype: note\n---\n[[A]]"


def test_index_entry_link_applies_to_whole_item():
    assert check_document("index.md", "# Index\n- [A](a.md)\n\n  A description.") == []
    assert check_document("index.md", "# Index\n- Group\n  - [A](a.md)\n\n    Description.") == []
    diagnostics = check_document("index.md", "# Index\n- [A](a.md)\n  - Missing link")
    assert [item.rule for item in diagnostics] == ["index.link"]


def test_log_heading_ends_date_group():
    text = "## 2026-01-01\n- Recorded\n# Appendix\n- Undated"
    assert [item.rule for item in check_document("log.md", text)] == ["log.group"]


@pytest.mark.parametrize("prefix", ["\ufeff", "\n \t\n", "\ufeff\n\n"])
def test_source_frontmatter_prefix_preserves_metadata(prefix):
    content = prefix + "---\ntitle: A\npermalink: a\ntags: [tag]\ntype: custom\n---\n# A"
    files = render_bundle(ExportSnapshot("p", (ExportFile("a.md", content.encode()),)))
    document = parse_document(next(file.content.decode() for file in files if file.path == "a.md"))
    assert document.metadata["title"] == "A"
    assert document.metadata["permalink"] == "a"
    assert document.metadata["tags"] == ["tag"]
    assert document.metadata["type"] == "custom"
    assert document.body == "# A"
    assert check_document("a.md", content)[0].rule == "concept.frontmatter"


def test_empty_frontmatter_gets_export_defaults():
    files = render_bundle(ExportSnapshot("p", (ExportFile("a.md", b"---\n---\n# A"),)))
    document = parse_document(next(file.content.decode() for file in files if file.path == "a.md"))
    assert document.metadata["type"] == "note"
    assert document.metadata["tags"] == []
    assert document.body == "# A"
    assert check_document("a.md", "---\n---\n# A")[0].rule == "concept.type"


@pytest.mark.parametrize("setting", ['"False"', '"FALSE"', '"fAlSe"'])
def test_mixed_case_semantic_opt_out(setting):
    content = f"---\nbm_parse_semantics: {setting}\n---\n- depends_on [[A]]"
    files = render_bundle(ExportSnapshot("p", (ExportFile("a.md", content.encode()),)))
    document = parse_document(next(file.content.decode() for file in files if file.path == "a.md"))
    assert document.metadata["bm"] == {"okf_export": {"version": 1, "relations": []}}


def test_indented_source_fence_is_body_not_metadata():
    source = "  ---\ntype: custom\ntitle: Authored text\n---\n# Body"
    files = render_bundle(ExportSnapshot("p", (ExportFile("a.md", source.encode()),)))
    document = parse_document(next(file.content.decode() for file in files if file.path == "a.md"))
    assert document.metadata["type"] == "note"
    assert "title" not in document.metadata
    assert document.body == source
    assert check_document("a.md", source) == []
    unmatched = "---\ntype: custom\n  ---\nBody"
    assert parse_document(unmatched, source=True).body == unmatched


def test_unique_filename_alias_follows_exact_identity():
    targets = {"My_Note.md": "My_Note.md"}
    assert convert_wikilinks("[[my-note]]", "source.md", targets, "p") == "[my-note](/My_Note.md)"
    targets["my-note"] = "specific.md"
    assert convert_wikilinks("[[my-note]]", "source.md", targets, "p") == "[my-note](/specific.md)"
    targets.pop("my-note")
    targets["MY-NOTE.md"] = "MY-NOTE.md"
    assert convert_wikilinks("[[my-note]]", "source.md", targets, "p") == "[my-note](/my-note)"
    targets["folder/My_Note.md"] = "folder/My_Note.md"
    assert (
        convert_wikilinks("[[./my-note.md]]", "folder/source.md", targets, "p")
        == "[./my-note.md](/folder/My_Note.md)"
    )


@pytest.mark.parametrize("source", ["---\n# Thematic break"])
def test_non_frontmatter_source_blocks_remain_body(source):
    files = render_bundle(ExportSnapshot("p", (ExportFile("a.md", source.encode()),)))
    exported = next(file.content.decode() for file in files if file.path == "a.md")
    document = parse_document(exported)
    assert document.metadata["type"] == "note"
    assert document.body == source
    assert check_document("a.md", exported) == []
    assert check_document("a.md", source)[0].rule == "frontmatter"


@pytest.mark.parametrize("prefix", ["my-project", "My Project"])
def test_project_permalink_prefix_is_not_source_relative(prefix):
    targets = {
        "my-project/foo": "foo.md",
        "folder/my-project/foo.md": "folder/my-project/foo.md",
    }
    assert (
        convert_wikilinks(
            f"[[{prefix}/foo]]",
            "folder/source.md",
            targets,
            "My Project",
            permalinks={"my-project/foo": "foo.md"},
        )
        == f"[{prefix}/foo](/foo.md)"
    )


def test_cli_export_resolves_configured_display_name(export_config, tmp_path):
    export_config.projects = {"My Project": export_config.projects["export"]}
    set_container(CliContainer(export_config, RuntimeMode.TEST))
    destination = tmp_path / "bundle"
    result = CliRunner().invoke(
        app, ["okf", "export", str(destination), "--project", "my-project", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["concepts"] == 1
    assert "# My Project" in (destination / "index.md").read_text()


@pytest.mark.parametrize(
    "body", ["- item\n\t[[A]]", "> quote\n>\t[[A]]", "- item\n\t[[A]] and [[A]]"]
)
def test_tab_indented_links_keep_source_indentation(body):
    assert convert_wikilinks(body, "source.md", {"A": "a.md"}, "p") == body.replace(
        "[[A]]", "[A](/a.md)"
    )


@pytest.mark.parametrize("path", ["log.md", "nested/index.md"])
def test_okf_version_only_exempts_root_index(export_config, path):
    root = Path(export_config.projects["export"].path)
    target = root / path
    target.parent.mkdir(exist_ok=True)
    target.write_text("---\nokf_version: '0.2'\n---\nAuthored content")
    with pytest.raises(ValueError, match="rename it first"):
        snapshot_files(root)


@pytest.mark.asyncio
async def test_disabled_project_prefix_policy_reaches_export(export_config, tmp_path):
    root = Path(export_config.projects["export"].path)
    (root / "folder/export").mkdir(parents=True)
    (root / "folder/export/foo.md").write_text("---\ntype: note\n---\nRelative")
    (root / "a.md").write_text("---\npermalink: export/foo\n---\nSemantic")
    (root / "folder/source.md").write_text("[[export/foo]]")
    export_config.permalinks_include_project = False
    destination = tmp_path / "bundle"
    assert (await export_project(export_config, "export", destination)).success
    assert "[export/foo](/folder/export/foo.md)" in (destination / "folder/source.md").read_text()


def test_ambiguous_bare_title_does_not_fall_through_to_filename():
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("Same.md", b"---\ntitle: Same\n---\n"),
            ExportFile("other.md", b"---\ntitle: Same\npermalink: same\n---\n"),
            ExportFile("source.md", b"[[Same]] and [[Same.md]] and [[./Same.md]]"),
        ),
    )
    source = next(file.content for file in render_bundle(snapshot) if file.path == "source.md")
    assert b"[Same](/Same) and [Same.md](/other.md) and [./Same.md](/Same.md)" in source


@pytest.mark.parametrize(
    "yaml_title,target",
    [
        ("123", "123"),
        ("[My, Note]", "My, Note"),
        ("false", "False"),
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00+00:00"),
    ],
)
def test_source_title_normalization_preserves_authored_metadata(yaml_title, target):
    authored = f"---\ntitle: {yaml_title}\n---\n# Note"
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("note.md", authored.encode()),
            ExportFile("source.md", f"[[{target}]]".encode()),
        ),
    )
    files = {file.path: file.content.decode() for file in render_bundle(snapshot)}
    assert f"[{target}](/note.md)" in files["source.md"]
    assert (
        parse_document(files["note.md"]).metadata["title"]
        == parse_document(authored).metadata["title"]
    )
    assert f"[{target}](note.md)" in files["index.md"]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\f"])
def test_unicode_separators_are_not_markdown_line_boundaries(separator):
    body = f"Prose{separator}[[A]] and [[A]]"
    assert convert_wikilinks(body, "source.md", {"A": "a.md"}, "p") == body.replace(
        "[[A]]", "[A](/a.md)"
    )


@pytest.mark.parametrize(
    "yaml_type,expected",
    [("123", "123"), ("false", "False"), ("[My, Type]", "My, Type"), ("null", "note")],
)
def test_export_uses_canonical_bm_type(yaml_type, expected):
    snapshot = ExportSnapshot(
        "p", (ExportFile("a.md", f"---\ntype: {yaml_type}\n---\nBody".encode()),)
    )
    content = next(file.content.decode() for file in render_bundle(snapshot) if file.path == "a.md")
    assert parse_document(content).metadata["type"] == expected
    assert check_document("a.md", content) == []


@pytest.mark.parametrize("name", ["index.md", "log.md"])
def test_wiki_profile_alone_does_not_establish_ownership(export_config, name):
    root = Path(export_config.projects["export"].path)
    source = root / name
    source.write_text("---\nbm: {profile: wiki/1}\n---\nAuthored body")
    with pytest.raises(ValueError, match="rename it first"):
        snapshot_files(root)
    assert "Authored body" in source.read_text()


def test_bare_filename_alias_does_not_prefer_source_directory():
    targets = {"folder/My_Note.md": "folder/My_Note.md", "other/My_Note.md": "other/My_Note.md"}
    assert (
        convert_wikilinks("[[my-note]]", "folder/source.md", targets, "p") == "[my-note](/my-note)"
    )
    assert (
        convert_wikilinks("[[./my-note]]", "folder/source.md", targets, "p")
        == "[./my-note](/folder/My_Note.md)"
    )


@pytest.mark.parametrize(
    "label",
    ["![alt [[A]]](img)", "[caption [[A]]][ref]", "![alt [[A]]][ref]"],
)
def test_image_and_reference_labels_keep_literal_wikilinks(label):
    body = label + " and [[A]]\n\n[ref]: /existing.md"
    expected = label + " and [A](/a.md)\n\n[ref]: /existing.md"
    assert convert_wikilinks(body, "source.md", {"A": "a.md"}, "p") == expected


def test_relative_wikilink_percent_sequences_are_literal():
    targets = {path: path for path in ("folder/sub/A%20B.md", "folder/sub/A B.md")}
    assert (
        convert_wikilinks("[[sub/A%20B.md]]", "folder/source.md", targets, "p")
        == "[sub/A%20B.md](/folder/sub/A%2520B.md)"
    )


@pytest.mark.parametrize("yaml_permalink,target", [("123", "123"), ("false", "False")])
def test_scalar_permalink_aliases_preserve_authored_metadata(yaml_permalink, target):
    authored = f"---\npermalink: {yaml_permalink}\n---\n# Note"
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("note.md", authored.encode()),
            ExportFile("source.md", f"[[{target}]]".encode()),
        ),
    )
    files = {file.path: file.content.decode() for file in render_bundle(snapshot)}
    assert f"[{target}](/note.md)" in files["source.md"]
    assert (
        parse_document(files["note.md"]).metadata["permalink"]
        == parse_document(authored).metadata["permalink"]
    )


@pytest.mark.parametrize(
    "body,target,expected",
    [
        (r"[[A\]]B]]", r"A\]]B", r"[A\\\]\]B](/note.md)"),
        (r"[[A\[[B]]", r"A\[[B", r"[A\\\[\[B](/note.md)"),
        (r"[[A\\]] tail", r"A\\", r"[A\\\\](/note.md) tail"),
    ],
)
def test_wikilink_delimiters_use_canonical_escape_rules(body, target, expected):
    assert convert_wikilinks(body, "source.md", {target: "note.md"}, "p") == expected


@pytest.mark.parametrize("permalink", ["foo.md", "foo"])
def test_permalink_precedes_file_alias_but_explicit_relative_path_stays_a_path(
    permalink, test_project
):
    from basic_memory.models import Entity
    from basic_memory.services.bulk_link_resolver import ProjectEntityIdentityIndex

    owner = Entity(title="Owner", file_path="owner.md", permalink=permalink)
    index = ProjectEntityIdentityIndex.from_entities(
        test_project, [Entity(title="foo", file_path="foo.md"), owner]
    )
    assert (
        index.resolve_strict(
            "foo.md", include_project_permalinks=True, workspace_permalink=None
        ).entity
        is owner
    )
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("foo.md", b"# File"),
            ExportFile("owner.md", f"---\npermalink: {permalink}\n---\n# Permalink owner".encode()),
            ExportFile("source.md", b"[[foo.md]] and [[./foo.md]]"),
        ),
    )
    source = next(file.content for file in render_bundle(snapshot) if file.path == "source.md")
    assert b"[foo.md](/owner.md) and [./foo.md](/foo.md)" in source


@pytest.mark.asyncio
async def test_duplicate_offline_permalinks_fail_before_replacing_bundle(export_config, tmp_path):
    root = Path(export_config.projects["export"].path)
    for name in ("a.md", "b.md"):
        (root / name).write_text("---\npermalink: same\n---\n[[same]]")
    destination = tmp_path / "bundle"
    destination.mkdir()
    (destination / "keep").write_bytes(b"previous bundle")
    with pytest.raises(ValueError, match="b.md: duplicate permalink 'same' also declared by a.md"):
        await export_project(export_config, "export", destination, replace=True)
    assert (destination / "keep").read_bytes() == b"previous bundle"


def test_unique_title_precedes_filename_and_rooted_links_remain_literal(test_project):
    from basic_memory.models import Entity
    from basic_memory.services.bulk_link_resolver import ProjectEntityIdentityIndex

    owner = Entity(title="foo.md", file_path="owner.md")
    index = ProjectEntityIdentityIndex.from_entities(
        test_project, [Entity(title="foo", file_path="foo.md", permalink="custom"), owner]
    )
    assert (
        index.resolve_strict(
            "foo.md", include_project_permalinks=True, workspace_permalink=None
        ).entity
        is owner
    )
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("foo.md", b"---\npermalink: custom\n---\n# File"),
            ExportFile("owner.md", b"---\ntitle: foo.md\n---\n# Title owner"),
            ExportFile("source.md", b"[[foo.md]] [[/foo]] [[/foo.md]]"),
        ),
    )
    source = next(file.content for file in render_bundle(snapshot) if file.path == "source.md")
    assert b"[foo.md](/owner.md) [/foo](/foo) [/foo.md](/foo.md)" in source


def test_permalink_compatibility_candidates_are_not_filename_aliases(test_project):
    from basic_memory.models import Entity
    from basic_memory.services.bulk_link_resolver import ProjectEntityIdentityIndex

    test_project.name = "p"
    index = ProjectEntityIdentityIndex.from_entities(
        test_project, [Entity(title="foo", file_path="foo.md", permalink="custom")]
    )
    assert (
        index.resolve_strict(
            "p/foo", include_project_permalinks=True, workspace_permalink=None
        ).entity
        is None
    )
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("foo.md", b"---\npermalink: custom\n---\n# File"),
            ExportFile("source.md", b"[[p/foo]] and [[foo]]"),
        ),
    )
    source = next(file.content for file in render_bundle(snapshot) if file.path == "source.md")
    assert b"[p/foo](/p/foo) and [foo](/foo.md)" in source


def test_escaping_wikilink_stays_literal_instead_of_normalizing_to_a_real_note():
    body = "[[../../a.md]] and [[../a.md]]"
    assert convert_wikilinks(body, "folder/source.md", {"a.md": "a.md"}, "p") == (
        "[[../../a.md]] and [../a.md](/a.md)"
    )


def test_ambiguous_slash_title_still_allows_exact_filename_inference(test_project):
    from basic_memory.models import Entity
    from basic_memory.services.bulk_link_resolver import ProjectEntityIdentityIndex

    owner = Entity(title="p/foo", file_path="p/foo.md")
    index = ProjectEntityIdentityIndex.from_entities(
        test_project, [owner, Entity(title="p/foo", file_path="other.md")]
    )
    assert (
        index.resolve_strict(
            "p/foo", include_project_permalinks=True, workspace_permalink=None
        ).entity
        is owner
    )
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("p/foo.md", b"---\ntitle: p/foo\n---\n"),
            ExportFile("other.md", b"---\ntitle: p/foo\n---\n"),
            ExportFile("source.md", b"[[p/foo]]"),
        ),
    )
    source = next(file.content for file in render_bundle(snapshot) if file.path == "source.md")
    assert b"[p/foo](/p/foo.md)" in source


def test_yaml_sets_are_deterministic_across_processes():
    import os
    import subprocess
    import sys

    source = b"---\ntype: note\ncustom: !!set {alpha: null, beta: null, gamma: null}\n---\n"
    expected = next(
        file.content.decode()
        for file in render_bundle(ExportSnapshot("p", (ExportFile("a.md", source),)))
        if file.path == "a.md"
    )
    script = (
        "from basic_memory.okf.render import ExportFile, ExportSnapshot, render_bundle\n"
        f"source = {source!r}\n"
        "print(next(f.content.decode() for f in render_bundle(ExportSnapshot('p', "
        "(ExportFile('a.md', source),))) if f.path == 'a.md'))\n"
    )
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed}, text=True
        )
        for seed in ("1", "2")
    ]
    assert outputs[0] == outputs[1] == expected + "\n"
    assert parse_document(outputs[0]).metadata["custom"] == {"alpha", "beta", "gamma"}
    assert outputs[0].index("type: note") < outputs[0].index("custom:")


def test_explicit_project_qualifiers_cannot_bind_to_foreign_local_aliases():
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("trap.md", b"---\npermalink: q/foo\n---\n"),
            ExportFile("target.md", b"---\npermalink: foo\n---\n"),
            ExportFile("folder/target.md", b"---\npermalink: local\n---\n"),
            ExportFile("folder/source.md", b"[[q::foo]] [[p::foo]] [[p::target.md]]"),
        ),
    )
    source = next(
        file.content for file in render_bundle(snapshot) if file.path == "folder/source.md"
    )
    assert b"[[q::foo]] [p::foo](/target.md) [p::target.md](/target.md)" in source


@pytest.mark.parametrize("field", ["title", "type"])
@pytest.mark.parametrize(
    "value",
    ["!!set {alpha: null, beta: null}", "[!!set {alpha: null}]", "{nested: !!set {a: null}}"],
)
def test_unordered_identity_metadata_fails_explicitly(field, value):
    snapshot = ExportSnapshot("p", (ExportFile("a.md", f"---\n{field}: {value}\n---\n".encode()),))
    with pytest.raises(ValueError, match=f"a.md: {field} cannot contain an unordered YAML set"):
        render_bundle(snapshot)


@pytest.mark.parametrize("include_project", [False, True])
def test_missing_authored_permalink_uses_canonical_generated_address(include_project):
    snapshot = ExportSnapshot(
        "p",
        (
            ExportFile("folder/foo.md", b"# Foo"),
            ExportFile("source.md", b"[[p/folder/foo]]"),
        ),
        permalinks_include_project=include_project,
    )
    source = next(file.content for file in render_bundle(snapshot) if file.path == "source.md")
    assert b"[p/folder/foo](/folder/foo.md)" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["---\n- scalar\n---\nBody", "---\nbad: [\n---\nBody"])
async def test_malformed_note_identity_fails_before_replacing_bundle(
    export_config, tmp_path, source
):
    root = Path(export_config.projects["export"].path)
    note = root / "new.md"
    note.write_text(source, encoding="utf-8")
    (root / "a.md").write_text("[[export/old]] and [[export/new]]", encoding="utf-8")
    destination = tmp_path / "bundle"
    destination.mkdir()
    (destination / "keep").write_bytes(b"previous bundle")
    with pytest.raises(ValueError, match="new.md: repair malformed frontmatter before export"):
        await export_project(export_config, "export", destination, replace=True)
    assert (destination / "keep").read_bytes() == b"previous bundle"
    assert note.read_text(encoding="utf-8") == source


def test_network_path_wikilinks_remain_literal():
    body = "[[//example.com/a]] [[//example.com/a|alias]] [[/a.md]]"
    assert convert_wikilinks(body, "source.md", {"a.md": "a.md"}, "p") == (
        "[[//example.com/a]] [[//example.com/a|alias]] [/a.md](/a.md)"
    )
