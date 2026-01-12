"""Tests for markdown plugins."""

from textwrap import dedent
from markdown_it import MarkdownIt
from markdown_it.token import Token

from basic_memory.markdown.plugins import (
    observation_plugin,
    relation_plugin,
    is_observation,
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


def test_observation_excludes_markdown_and_wiki_links():
    """Test that markdown links and wiki links are NOT parsed as observations.

    This test validates the fix for issue #247 where:
    - [text](url) markdown links were incorrectly parsed as observations
    - [[text]] wiki links were incorrectly parsed as observations
    """
    # Test markdown links are NOT observations
    token = Token("inline", "[Click here](https://example.com)", 0)
    assert not is_observation(token), "Markdown links should not be parsed as observations"

    token = Token("inline", "[Documentation](./docs/readme.md)", 0)
    assert not is_observation(token), "Relative markdown links should not be parsed as observations"

    token = Token("inline", "[Empty link]()", 0)
    assert not is_observation(token), "Empty markdown links should not be parsed as observations"

    # Test wiki links are NOT observations
    token = Token("inline", "[[SomeWikiPage]]", 0)
    assert not is_observation(token), "Wiki links should not be parsed as observations"

    token = Token("inline", "[[Multi Word Page]]", 0)
    assert not is_observation(token), "Multi-word wiki links should not be parsed as observations"

    # Test nested brackets are NOT observations
    token = Token("inline", "[[Nested [[Inner]] Link]]", 0)
    assert not is_observation(token), "Nested wiki links should not be parsed as observations"

    # Test valid observations still work (should return True)
    token = Token("inline", "[category] This is a valid observation", 0)
    assert is_observation(token), "Valid observations should still be parsed correctly"

    token = Token("inline", "[design] Valid observation #tag", 0)
    assert is_observation(token), "Valid observations with tags should still work"

    token = Token("inline", "Just some text #tag", 0)
    assert is_observation(token), "Tag-only observations should still work"

    # Test edge cases that should NOT be observations
    token = Token("inline", "[]Empty brackets", 0)
    assert not is_observation(token), "Empty category brackets should not be observations"

    token = Token("inline", "[category]No space after category", 0)
    assert not is_observation(token), "No space after category should not be valid observation"


def test_observation_excludes_html_color_codes():
    """Test that HTML color codes are NOT interpreted as hashtags.

    This test validates the fix for issue #446 where:
    - HTML color codes like #4285F4 in attributes were incorrectly
      causing lines to be parsed as observations.
    """
    # HTML color code in font tag should NOT be an observation
    token = Token("inline", '**<font color="#4285F4">Jane:</font>** Welcome to the show', 0)
    assert not is_observation(token), "HTML color codes should not trigger hashtag detection"

    # Color code in style attribute
    token = Token("inline", '<span style="color:#FF5733">Styled text</span>', 0)
    assert not is_observation(token), "Color codes in style should not be observations"

    # Multiple color codes
    token = Token(
        "inline", '<font color="#4285F4">Blue</font> and <font color="#EA4335">Red</font>', 0
    )
    assert not is_observation(token), "Multiple color codes should not be observations"

    # Hex color without quotes (edge case)
    token = Token("inline", "background-color:#FFFFFF is white", 0)
    assert not is_observation(token), "Inline hex colors should not be observations"

    # But standalone hashtags SHOULD still work
    token = Token("inline", "This has a #realtag in it", 0)
    assert is_observation(token), "Standalone hashtags should still work"

    # Multiple real hashtags
    token = Token("inline", "Tags: #design #feature #important", 0)
    assert is_observation(token), "Multiple standalone hashtags should work"

    # Mix of color code and real tag - should be observation because of real tag
    token = Token("inline", '<font color="#4285F4">Text</font> #actualtag', 0)
    assert is_observation(token), "Real hashtag with color code should still be observation"


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
    assert rels[0]["type"] == "links_to"
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
    assert link["type"] == "links_to"
