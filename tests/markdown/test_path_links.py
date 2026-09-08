"""Ordinary Markdown links retain exact project-local path semantics."""

import pytest

from basic_memory.markdown.entity_parser import EntityParser, parse
from basic_memory.markdown.path_links import markdown_link_target


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("../Guide%20One.md#section", "/Guide One.md"),
        ("same.md", "/notes/same.md"),
        ("./nested/../same.md", "/notes/same.md"),
        ("/root.md", "/root.md"),
        ("../../outside.md", None),
        ("https://example.com/note.md", None),
        ("//example.com/note.md", None),
        ("mailto:me@example.com", None),
        ("file:///tmp/note.md", None),
        ("https://[broken", None),
        ("#section", None),
        ("../", None),
        ("bad%00.md", None),
        ("bad%5Cpath.md", None),
    ],
)
def test_markdown_target_is_bounded_to_project(href, expected):
    assert markdown_link_target(href, "notes/source.md") == expected


def test_markdown_parser_uses_real_links_without_rewriting_content():
    content = """See [guide](../Guide%20One.md#section) and [reference][ref].
![image](picture.png) and `[code](code.md)` and [web](https://example.com).
[[Existing Wiki]]

[ref]: next.md
"""
    parsed = parse(content, source_path="notes/source.md")
    assert parsed.content == content
    assert [(relation.type, relation.target) for relation in parsed.relations] == [
        ("links_to", "/Guide One.md"),
        ("links_to", "/notes/next.md"),
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
