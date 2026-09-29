"""Ordinary Markdown links are recorded as authored paths and resolved against their note."""

import pytest

from basic_memory.markdown.entity_parser import EntityParser, parse
from basic_memory.markdown.path_links import (
    is_path_target,
    markdown_link_path,
    resolve_project_path,
)


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("../Guide%20One.md#section", "../Guide One.md"),
        ("same.md", "./same.md"),
        ("./nested/../same.md", "./nested/../same.md"),
        ("/root.md", "/root.md"),
        ("../../outside.md", "../../outside.md"),
        ("https://example.com/note.md", None),
        ("//example.com/note.md", None),
        ("mailto:me@example.com", None),
        ("file:///tmp/note.md", None),
        ("https://[broken", None),
        ("#section", None),
        ("bad%00.md", None),
        ("bad%5Cpath.md", None),
        ("./", None),
        ("../", None),
        ("docs/", None),
    ],
)
def test_markdown_link_keeps_the_authored_path(href, expected):
    """The parser records where the author pointed; resolution happens later."""
    assert markdown_link_path(href) == expected


@pytest.mark.parametrize(
    ("target", "source_path", "expected"),
    [
        ("../Guide One.md", "notes/source.md", "/Guide One.md"),
        ("./same.md", "notes/source.md", "/notes/same.md"),
        ("./nested/../same.md", "notes/source.md", "/notes/same.md"),
        ("/root.md", "notes/source.md", "/root.md"),
        ("/root.md", None, "/root.md"),
        ("./same.md", None, "/same.md"),
        ("./same.md", "source.md", "/same.md"),
        ("../../outside.md", "notes/source.md", None),
        ("../same.md", None, None),
        ("../", "notes/source.md", None),
        ("Guide One", "notes/source.md", None),
    ],
)
def test_path_targets_resolve_against_the_note_that_carries_them(target, source_path, expected):
    assert resolve_project_path(target, source_path) == expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [("/root.md", True), ("./same.md", True), ("../up.md", True), ("Guide", False), ("a/b", False)],
)
def test_path_targets_are_rooted_or_explicitly_relative(target, expected):
    assert is_path_target(target) is expected


@pytest.mark.parametrize(
    ("href", "source_path", "expected"),
    [
        ("../Guide%20One.md#section", "notes/source.md", "/Guide One.md"),
        ("https://example.com/note.md", "notes/source.md", None),
        ("../../outside.md", "notes/source.md", None),
    ],
)
def test_authoring_then_resolution_names_one_project_file(href, source_path, expected):
    path = markdown_link_path(href)
    resolved = resolve_project_path(path, source_path) if path is not None else None
    assert resolved == expected


def test_markdown_parser_uses_real_links_without_rewriting_content():
    content = """See [guide](../Guide%20One.md#section) and [reference][ref].
![image](picture.png) and `[code](code.md)` and [web](https://example.com).
[[Existing Wiki]]

[ref]: next.md
"""
    parsed = parse(content)
    assert parsed.content == content
    assert [(relation.type, relation.target) for relation in parsed.relations] == [
        ("links_to", "../Guide One.md"),
        ("links_to", "./next.md"),
        ("links_to", "Existing Wiki"),
    ]


@pytest.mark.asyncio
async def test_remote_content_parsing_respects_semantic_opt_out(tmp_path):
    parser = EntityParser(tmp_path)
    body = "[not indexed](target.md)"
    content = "---\nbm_parse_semantics: false\n---\n" + body
    parsed = await parser.parse_markdown_content(tmp_path / "absent.md", content)
    assert parsed.content == body
    assert parsed.relations == []


@pytest.mark.asyncio
async def test_parsing_needs_no_filesystem_location_for_the_note(tmp_path):
    """Content read from remote storage parses under any path, inside the root or not."""
    parser = EntityParser(tmp_path / "project")
    content = "[guide](../guides/Guide.md) and [[../guides/Other.md]]"
    for file_path in (tmp_path / "elsewhere" / "tmp.md", tmp_path / "project" / "notes" / "a.md"):
        parsed = await parser.parse_markdown_content(file_path, content)
        assert [relation.target for relation in parsed.relations] == [
            "../guides/Guide.md",
            "../guides/Other.md",
        ]
