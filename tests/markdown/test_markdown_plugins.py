"""Tests for markdown plugins."""

from textwrap import dedent
from markdown_it import MarkdownIt
from markdown_it.token import Token

from basic_memory.markdown.plugins import (
    observation_plugin,
    relation_plugin,
    is_observation,
    parse_observation,
    is_explicit_relation,
    parse_relation,
    parse_inline_relations,
)


def test_observation_plugin():
    """Test observation plugin."""
    # Set up markdown-it instance
    md = MarkdownIt().use(observation_plugin)

    # Test basic observation with all features
    content = dedent("""
        - [design] Basic observation #tag1 #tag2 (with context)
        """)

    tokens = md.parse(content)
    token = [t for t in tokens if t.type == "inline"][0]
    assert "observation" in token.meta
    obs = token.meta["observation"]
    assert obs["category"] == "design"
    assert obs["content"] == "Basic observation #tag1 #tag2"
    assert set(obs["tags"]) == {"tag1", "tag2"}
    assert obs["context"] == "with context"

    # Test without category
    content = "- Basic observation #tag1 (context)"
    token = [t for t in md.parse(content) if t.type == "inline"][0]
    obs = token.meta["observation"]
    assert obs["category"] is None
    assert obs["content"] == "Basic observation #tag1"
    assert obs["tags"] == ["tag1"]
    assert obs["context"] == "context"

    # Test without tags
    content = "- [note] Basic observation (context)"
    token = [t for t in md.parse(content) if t.type == "inline"][0]
    obs = token.meta["observation"]
    assert obs["category"] == "note"
    assert obs["content"] == "Basic observation"
    assert obs["tags"] is None
    assert obs["context"] == "context"


def test_observation_edge_cases():
    """Test observation parsing edge cases."""
    # Test non-inline token
    token = Token("paragraph", "", 0)
    assert not is_observation(token)

    # Test empty content
    token = Token("inline", "", 0)
    assert not is_observation(token)

    # Test markdown task
    token = Token("inline", "[ ] Task item", 0)
    assert not is_observation(token)

    # Test completed task
    token = Token("inline", "[x] Done task", 0)
    assert not is_observation(token)

    # Test in-progress task
    token = Token("inline", "[-] Ongoing task", 0)
    assert not is_observation(token)


def test_relation_plugin():
    """Test relation plugin."""
    md = MarkdownIt().use(relation_plugin)

    # Test explicit relation with all features
    content = dedent("""
        - implements [[Component]] (with context)
        """)

    tokens = md.parse(content)
    token = [t for t in tokens if t.type == "inline"][0]
    assert "relations" in token.meta
    rel = token.meta["relations"][0]
    assert rel["type"] == "implements"
    assert rel["target"] == "Component"
    assert rel["context"] == "with context"

    # Test implicit relations in text
    content = "Some text with a [[Link]] and [[Another Link]]"
    token = [t for t in md.parse(content) if t.type == "inline"][0]
    rels = token.meta["relations"]
    assert len(rels) == 2
    assert rels[0]["type"] == "links to"
    assert rels[0]["target"] == "Link"
    assert rels[1]["target"] == "Another Link"


def test_relation_edge_cases():
    """Test relation parsing edge cases."""
    # Test non-inline token
    token = Token("paragraph", "", 0)
    assert not is_explicit_relation(token)

    # Test empty content
    token = Token("inline", "", 0)
    assert not is_explicit_relation(token)

    # Test incomplete relation (missing target)
    token = Token("inline", "relates_to [[]]", 0)
    result = parse_relation(token)
    assert result is None

    # Test non-relation content
    token = Token("inline", "Just some text", 0)
    result = parse_relation(token)
    assert result is None

    # Test invalid inline link (empty target)
    assert not parse_inline_relations("Text with [[]] empty link")

    # Test nested links (avoid duplicates)
    result = parse_inline_relations("Text with [[Outer [[Inner]] Link]]")
    assert len(result) == 1
    assert result[0]["target"] == "Outer [[Inner]] Link"


def test_combined_plugins():
    """Test both plugins working together."""
    md = MarkdownIt().use(observation_plugin).use(relation_plugin)

    content = dedent("""
        # Section
        - [design] Observation with [[Link]] #tag (context)
        - implements [[Component]] (details)
        - Just a [[Reference]] in text
        
        Some text with a [[Link]] reference.
        """)

    tokens = md.parse(content)
    inline_tokens = [t for t in tokens if t.type == "inline"]

    # First token has both observation and relation
    obs_token = inline_tokens[1]
    assert "observation" in obs_token.meta
    assert "relations" in obs_token.meta

    # Second token has explicit relation
    rel_token = inline_tokens[2]
    assert "relations" in rel_token.meta
    rel = rel_token.meta["relations"][0]
    assert rel["type"] == "implements"

    # Third token has implicit relation
    text_token = inline_tokens[4]
    assert "relations" in text_token.meta
    link = text_token.meta["relations"][0]
    assert link["type"] == "links to"
