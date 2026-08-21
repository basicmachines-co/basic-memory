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

    # Nested brackets: the tail after the first ]] is not a (context), so the
    # line is not an explicit relation; inline handling depth-matches the link.
    tokens = md.parse("- type [[Outer [[Inner]] ]]")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) is None
    assert all(r["type"] == "links_to" for r in token.meta["relations"])


def test_context_handling():
    """Test handling of contexts."""
    md = MarkdownIt().use(relation_plugin)

    # Unclosed context is a prose tail, not a context: the line falls back to
    # an inline link instead of minting a typed edge with the tail dropped.
    tokens = md.parse("- type [[Target]] (unclosed")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) is None
    assert token.meta["relations"] == [{"type": "links_to", "target": "Target", "context": None}]

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


def test_prose_tail_falls_back_to_inline_link():
    """A sentence containing a wikilink must not mint a typed relation (#1260).

    An explicit relation line ends at its target or its (context); trailing
    prose means the line is ordinary writing, and the old behavior both minted
    a junk type from the word before the link and silently dropped the tail.
    """
    md = MarkdownIt().use(relation_plugin)

    for src in [
        "- Added [[Target Note]] to the roster",
        "- Calls [[Target Note]] every Sunday",
        '- "multi word type" [[Target Note]] trailing prose',
        "- type [[Target Note]] (context) and more",
    ]:
        tokens = md.parse(src)
        token = next(t for t in tokens if t.type == "inline")
        assert parse_relation(token) is None, src
        assert token.meta["relations"] == [
            {"type": "links_to", "target": "Target Note", "context": None}
        ], src


def test_prose_tail_keeps_every_wikilink_in_the_tail():
    """Falling back to inline handling preserves links the old path dropped."""
    md = MarkdownIt().use(relation_plugin)

    # The old explicit path minted `relates_to -> A` and lost B's edge entirely.
    tokens = md.parse("- relates_to [[Alpha]] and [[Beta]]")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) is None
    assert {r["target"] for r in token.meta["relations"]} == {"Alpha", "Beta"}
    assert all(r["type"] == "links_to" for r in token.meta["relations"])

    tokens = md.parse("- Links: [[Alpha]], [[Beta]]")
    token = next(t for t in tokens if t.type == "inline")
    assert {r["target"] for r in token.meta["relations"]} == {"Alpha", "Beta"}

    # A context-looking tail whose opening paren closes before the end is prose:
    # accepting `(primary) and [[Beta]] (secondary)` as one context would drop
    # the Beta link — the corruption class this rule exists to prevent.
    tokens = md.parse("- relates_to [[Alpha]] (primary) and [[Beta]] (secondary)")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) is None
    assert {r["target"] for r in token.meta["relations"]} == {"Alpha", "Beta"}
    assert all(r["type"] == "links_to" for r in token.meta["relations"])


def test_explicit_relation_forms_still_parse():
    """Hand-authored relation shapes keep their types after the #1260 fix."""
    md = MarkdownIt().use(relation_plugin)

    expected = {
        "- spouse_of [[Target Note]]": ("spouse_of", None),
        "- requires [[Target Note]] (because reasons)": ("requires", "because reasons"),
        '- "multi word type" [[Target Note]] (context)': ("multi word type", "context"),
    }
    for src, (rel_type, context) in expected.items():
        tokens = md.parse(src)
        token = next(t for t in tokens if t.type == "inline")
        rel = parse_relation(token)
        assert rel == {"type": rel_type, "target": "Target Note", "context": context}, src

    # Known limitation, pinned deliberately: a single capitalized word with no
    # tail is indistinguishable by shape from a hand-authored type ("Requires"),
    # so it still mints. The grammar policy for this case is tracked in #1260.
    tokens = md.parse("- Mother [[Target Note]]")
    token = next(t for t in tokens if t.type == "inline")
    assert parse_relation(token) == {"type": "Mother", "target": "Target Note", "context": None}


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
