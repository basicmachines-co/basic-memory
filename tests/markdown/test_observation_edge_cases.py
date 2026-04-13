"""Tests for edge cases in observation parsing."""

from markdown_it import MarkdownIt

from basic_memory.markdown.plugins import observation_plugin, parse_observation
from basic_memory.markdown.schemas import Observation


def test_empty_input():
    """Test handling of empty input."""
    md = MarkdownIt().use(observation_plugin)

    tokens = md.parse("")
    assert not any(t.meta and "observation" in t.meta for t in tokens)

    tokens = md.parse("   ")
    assert not any(t.meta and "observation" in t.meta for t in tokens)

    tokens = md.parse("\n")
    assert not any(t.meta and "observation" in t.meta for t in tokens)


def test_invalid_context():
    """Test handling of invalid context format."""
    md = MarkdownIt().use(observation_plugin)

    # Unclosed context
    tokens = md.parse("- [test] Content (unclosed")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert obs["content"] == "Content (unclosed"
    assert obs["context"] is None

    # Multiple parens
    tokens = md.parse("- [test] Content (with) extra) parens)")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert obs["content"] == "Content"
    assert obs["context"] == "with) extra) parens"


def test_complex_format():
    """Test parsing complex observation formats."""
    md = MarkdownIt().use(observation_plugin)

    # Multiple hashtags together
    tokens = md.parse("- [complex test] This is #tag1#tag2 with #tag3 content")
    token = next(t for t in tokens if t.type == "inline")

    obs = parse_observation(token)
    assert obs["category"] == "complex test"
    assert set(obs["tags"]) == {"tag1", "tag2", "tag3"}
    assert obs["content"] == "This is #tag1#tag2 with #tag3 content"

    # Pydantic model validation
    observation = Observation.model_validate(obs)
    assert observation.category == "complex test"
    assert set(observation.tags) == {"tag1", "tag2", "tag3"}
    assert observation.content == "This is #tag1#tag2 with #tag3 content"


def test_malformed_category():
    """Test handling of malformed category brackets."""
    md = MarkdownIt().use(observation_plugin)

    # Empty category
    tokens = md.parse("- [] Empty category")
    token = next(t for t in tokens if t.type == "inline")
    observation = Observation.model_validate(parse_observation(token))
    assert observation.category is None
    assert observation.content == "Empty category"

    # Missing close bracket
    tokens = md.parse("- [test Content")
    token = next(t for t in tokens if t.type == "inline")
    observation = Observation.model_validate(parse_observation(token))
    # Should treat whole thing as content
    assert observation.category is None
    assert "test Content" in observation.content


def test_no_category():
    """Test handling of malformed category brackets."""
    md = MarkdownIt().use(observation_plugin)

    # Empty category
    tokens = md.parse("- No category")
    token = next(t for t in tokens if t.type == "inline")
    observation = Observation.model_validate(parse_observation(token))
    assert observation.category is None
    assert observation.content == "No category"


def test_unicode_content():
    """Test handling of Unicode content."""
    md = MarkdownIt().use(observation_plugin)

    # Emoji
    tokens = md.parse("- [test] Emoji test 👍 #emoji #test (Testing emoji)")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert "👍" in obs["content"]
    assert "emoji" in obs["tags"]

    # Non-Latin scripts
    tokens = md.parse("- [中文] Chinese text 测试 #language (Script test)")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert obs["category"] == "中文"
    assert "测试" in obs["content"]

    # Mixed scripts and emoji
    tokens = md.parse("- [test] Mixed 中文 and 👍 #mixed")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert "中文" in obs["content"]
    assert "👍" in obs["content"]

    # Model validation with Unicode
    observation = Observation.model_validate(obs)
    assert "中文" in observation.content
    assert "👍" in observation.content
