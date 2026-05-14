"""Tests for edge cases in relation parsing."""

from markdown_it import MarkdownIt

from basic_memory.markdown.plugins import relation_plugin, parse_relation, parse_inline_relations
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


def test_quoted_multi_word_relation_type_parses_as_explicit_relation():
    """Quoted relation labels allow explicit multi-word relation types."""
    md = MarkdownIt().use(relation_plugin)

    tokens = md.parse('- "some type" [[Target]] (context)')
    token = next(t for t in tokens if t.type == "inline")

    assert token.meta["relations"] == [
        {"type": "some type", "target": "Target", "context": "context"}
    ]
    assert parse_relation(token) == {"type": "some type", "target": "Target", "context": "context"}


def test_single_quoted_multi_word_relation_type_parses_as_explicit_relation():
    """Single-quoted relation labels also allow explicit multi-word relation types."""
    md = MarkdownIt().use(relation_plugin)

    tokens = md.parse("- 'some type' [[Target]] (context)")
    token = next(t for t in tokens if t.type == "inline")

    assert token.meta["relations"] == [
        {"type": "some type", "target": "Target", "context": "context"}
    ]
    assert parse_relation(token) == {"type": "some type", "target": "Target", "context": "context"}


def test_unquoted_multi_word_prefix_is_inline_link_not_relation_type():
    """Unquoted prose before a wikilink is an inline link, not a relation type."""
    md = MarkdownIt().use(relation_plugin)

    tokens = md.parse("- some other thing [[Target]]")
    token = next(t for t in tokens if t.type == "inline")

    assert token.meta["relations"] == [{"type": "links_to", "target": "Target", "context": None}]
    assert parse_relation(token) is None


def test_bare_list_wikilink_is_inline_link_not_default_explicit_relation():
    """A list item containing only a wikilink is still a generic inline link."""
    md = MarkdownIt().use(relation_plugin)

    tokens = md.parse("- [[Target]]")
    token = next(t for t in tokens if t.type == "inline")

    assert token.meta["relations"] == [{"type": "links_to", "target": "Target", "context": None}]
    assert parse_relation(token) is None
