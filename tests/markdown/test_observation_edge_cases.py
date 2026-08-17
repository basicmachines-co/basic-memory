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
    assert obs is not None
    assert obs["content"] == "Content (unclosed"
    assert obs["context"] is None

    # Multiple parens
    tokens = md.parse("- [test] Content (with) extra) parens)")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert obs is not None
    assert obs["content"] == "Content"
    assert obs["context"] == "with) extra) parens"


def test_complex_format():
    """Test parsing complex observation formats."""
    md = MarkdownIt().use(observation_plugin)

    # Multiple hashtags together
    tokens = md.parse("- [complex test] This is #tag1#tag2 with #tag3 content")
    token = next(t for t in tokens if t.type == "inline")

    obs = parse_observation(token)
    assert obs is not None
    assert obs["category"] == "complex test"
    assert set(obs["tags"]) == {"tag1", "tag2", "tag3"}
    assert obs["content"] == "This is #tag1#tag2 with #tag3 content"

    # Pydantic model validation
    observation = Observation.model_validate(obs)
    assert observation.category == "complex test"
    assert observation.tags is not None
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
    assert obs is not None
    assert "👍" in obs["content"]
    assert "emoji" in obs["tags"]

    # Non-Latin scripts
    tokens = md.parse("- [中文] Chinese text 测试 #language (Script test)")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert obs is not None
    assert obs["category"] == "中文"
    assert "测试" in obs["content"]

    # Mixed scripts and emoji
    tokens = md.parse("- [test] Mixed 中文 and 👍 #mixed")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert obs is not None
    assert "中文" in obs["content"]
    assert "👍" in obs["content"]

    # Model validation with Unicode
    observation = Observation.model_validate(obs)
    assert "中文" in observation.content
    assert "👍" in observation.content


def test_timestamp_prefixes_are_not_observation_categories():
    """Transcript timecodes must not mint observations (issue #1219)."""
    md = MarkdownIt().use(observation_plugin)

    # The issue's repro: list-item and bare transcript lines plus one real observation.
    tokens = md.parse(
        "[00:00:11] Speaker: We chose the safer option.\n"
        "- [00:01:42] Speaker: Follow up next week.\n"
        "- [decision] Use the safer option.\n"
    )
    observations = [t.meta["observation"] for t in tokens if t.meta and "observation" in t.meta]
    assert len(observations) == 1
    assert observations[0]["category"] == "decision"
    assert observations[0]["content"] == "Use the safer option."


def test_timestamp_shapes_rejected_across_formats():
    """MM:SS, HH:MM:SS, and fractional-second timecodes all stay ordinary content."""
    md = MarkdownIt().use(observation_plugin)

    for line in (
        "- [1:02] short timecode",
        "- [00:00:11] plain timecode",
        "- [1:02:03.500] fractional seconds",
        "- [100:02:11] long recording hours",
        "- [12:03,250] comma milliseconds",
    ):
        tokens = md.parse(line)
        assert not any(t.meta and "observation" in t.meta for t in tokens), line


def test_timestamp_ranges_are_not_observation_categories():
    """Spaced transcript time ranges stay ordinary content (issue #1270)."""
    md = MarkdownIt().use(observation_plugin)

    for line in (
        "- [24:33.098 - 24:41.260] fractional range",
        "- [1:02 - 2:03] minute range",
        "- [00:01:02 - 100:02:11] long recording range",
        "- [12:03,250 - 13:04,500] comma-millisecond range",
    ):
        tokens = md.parse(line)
        assert not any(t.meta and "observation" in t.meta for t in tokens), line


def test_hashtag_promoted_timestamp_line_keeps_timecode_in_content():
    """A tagged transcript line is an observation via its hashtag, never via the timecode."""
    md = MarkdownIt().use(observation_plugin)

    tokens = md.parse("- [00:00:11] Speaker: decision recorded #meeting")
    token = next(t for t in tokens if t.type == "inline")
    obs = parse_observation(token)
    assert obs["category"] is None
    assert obs["content"].startswith("[00:00:11] Speaker:")
    assert obs["tags"] == ["meeting"]


def test_numeric_but_non_timestamp_categories_still_parse():
    """Only pure clock values are rejected; other numeric categories keep working."""
    md = MarkdownIt().use(observation_plugin)

    for line, category in (
        ("- [2024] year in review", "2024"),
        ("- [v1:2] odd but not a clock", "v1:2"),
        ("- [10:30am] time-of-day words", "10:30am"),
        ("- [10:30am - 11:30am] named time range", "10:30am - 11:30am"),
    ):
        tokens = md.parse(line)
        token = next(t for t in tokens if t.type == "inline")
        obs = token.meta.get("observation") if token.meta else None
        assert obs is not None, line
        assert obs["category"] == category


def test_extended_checkbox_markers_are_not_observation_categories():
    """Obsidian's extended task markers must not mint observations (issue #1241)."""
    md = MarkdownIt().use(observation_plugin)

    for line in (
        "- [/] in progress task",
        "- [>] deferred task",
        "- [?] maybe task",
        "- [!] important task",
        "- [X] uppercase done task",
    ):
        tokens = md.parse(line)
        assert not any(t.meta and "observation" in t.meta for t in tokens), line


def test_single_character_alphanumeric_categories_still_parse():
    """Only marker shapes are rejected; short real categories keep working."""
    md = MarkdownIt().use(observation_plugin)

    for line, category in (
        ("- [a] annotation shorthand", "a"),
        ("- [1] first point", "1"),
        ("- [q] question shorthand", "q"),
    ):
        tokens = md.parse(line)
        token = next(t for t in tokens if t.type == "inline")
        obs = token.meta.get("observation") if token.meta else None
        assert obs is not None, line
        assert obs["category"] == category
