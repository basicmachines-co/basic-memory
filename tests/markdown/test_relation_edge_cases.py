"""Tests for edge cases in relation parsing."""

from markdown_it import MarkdownIt

from basic_memory.markdown.plugins import (
    MAX_RELATION_TYPE_LENGTH,
    relation_plugin,
    parse_relation,
    parse_inline_relations,
)
from basic_memory.markdown.schemas import Relation


def test_empty_targets():
    """Test handling of empty targets."""
    md = MarkdownIt().use(relation_plugin)

    # Empty brackets
    tokens = md.parse("- type [[]]")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) is None

    # Only spaces
    tokens = md.parse("- type [[ ]]")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) is None

    # Whitespace in brackets
    tokens = md.parse("- type [[   ]]")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) is None


def test_malformed_links():
    """Test handling of malformed wiki links."""
    md = MarkdownIt().use(relation_plugin)

    # Missing close brackets
    tokens = md.parse("- type [[Target")
    assert not any(t.meta and "relations" in t.meta for t in tokens)

    # Missing open brackets
    tokens = md.parse("- type Target]]")
    assert not any(t.meta and "relations" in t.meta for t in tokens)

    # Backwards brackets
    tokens = md.parse("- type ]]Target[[")
    assert not any(t.meta and "relations" in t.meta for t in tokens)

    # Nested brackets
    tokens = md.parse("- type [[Outer [[Inner]] ]]")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert "Outer" in rel["target"]


def test_context_handling():
    """Test handling of contexts."""
    md = MarkdownIt().use(relation_plugin)

    # Unclosed context
    tokens = md.parse("- type [[Target]] (unclosed")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert rel["context"] is None

    # Multiple parens
    tokens = md.parse("- type [[Target]] (with (nested) parens)")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert rel["context"] == "with (nested) parens"

    # Empty context
    tokens = md.parse("- type [[Target]] ()")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert rel["context"] is None


def test_inline_relations():
    """Test inline relation detection."""
    md = MarkdownIt().use(relation_plugin)

    # Multiple links in text
    text = "Text with [[Link1]] and [[Link2]] and [[Link3]]"
    rels = parse_inline_relations(text)
    assert len(rels) == 3
    assert {r["target"] for r in rels} == {"Link1", "Link2", "Link3"}

    # Links with surrounding text
    text = "Before [[Target]] After"
    rels = parse_inline_relations(text)
    assert len(rels) == 1
    assert rels[0]["target"] == "Target"

    # Multiple links on same line
    tokens = md.parse("[[One]] [[Two]] [[Three]]")
    token = next(t for t in tokens if t.type == "inline")
    assert len(token.meta["relations"]) == 3


def test_unicode_targets():
    """Test handling of Unicode in targets."""
    md = MarkdownIt().use(relation_plugin)

    # Unicode in target
    tokens = md.parse("- type [[测试]]")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert rel["target"] == "测试"

    # Unicode in type
    tokens = md.parse("- 使用 [[Target]]")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert rel["type"] == "使用"

    # Unicode in context
    tokens = md.parse("- type [[Target]] (测试)")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert rel["context"] == "测试"

    # Model validation with Unicode
    relation = Relation.model_validate(rel)
    assert relation.type == "type"
    assert relation.target == "Target"
    assert relation.context == "测试"


def test_prose_prefix_not_captured_as_relation_type():
    """A long prose prefix before a [[wikilink]] is prose, not a relation type.

    A list item can legitimately mention a [[wikilink]] inside ordinary prose.
    Without a length bound, the whole sentence before the [[ was captured as
    the relation type -- a meaningless edge that (while RelationType carried a
    MaxLen) failed the entire note write. Such a list item must instead fall
    through to inline handling and be recorded as a generic "links_to".
    """
    md = MarkdownIt().use(relation_plugin)

    prose = (
        "This is a long bullet describing something in detail, going on well "
        "past a hundred characters so the text before the wikilink is clearly "
        "prose and not a relation type, and only at the very end does it cite "
    )
    assert len(prose) > MAX_RELATION_TYPE_LENGTH  # guard: prefix exceeds the bound

    tokens = md.parse(f"- {prose}[[Some Target Note]]\n")
    token = next(t for t in tokens if t.type == "inline")

    # relation_rule routes it to parse_inline_relations: generic "links_to",
    # target preserved, prose not captured as the type.
    rels = token.meta["relations"]
    assert len(rels) == 1
    assert rels[0]["type"] == "links_to"
    assert rels[0]["target"] == "Some Target Note"

    # parse_relation itself also refuses to mint a prose-length type.
    assert parse_relation(token) is None


def test_short_relation_type_still_parses():
    """A genuine short relation type is unaffected by the prose-prefix guard."""
    md = MarkdownIt().use(relation_plugin)

    tokens = md.parse("- relates_to [[Some Target Note]]")
    token = next(t for t in tokens if t.type == "inline")
    rel = parse_relation(token)
    assert rel is not None
    assert rel["type"] == "relates_to"
    assert rel["target"] == "Some Target Note"
