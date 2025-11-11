"""Tests for Obsidian-compatible YAML frontmatter formatting."""

import frontmatter

from basic_memory.file_utils import dump_frontmatter


def test_tags_formatted_as_yaml_list():
    """Test that tags are formatted as YAML list instead of JSON array."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["type"] = "note"
    post.metadata["tags"] = ["system", "overview", "reference"]

    result = dump_frontmatter(post)

    # Should use YAML list format
    assert "tags:" in result
    assert "- system" in result
    assert "- overview" in result
    assert "- reference" in result

    # Should NOT use JSON array format
    assert '["system"' not in result
    assert '"overview"' not in result
    assert '"reference"]' not in result


def test_empty_tags_list():
    """Test that empty tags list is handled correctly."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["tags"] = []

    result = dump_frontmatter(post)

    # Should have empty list representation
    assert "tags: []" in result


def test_single_tag():
    """Test that single tag is still formatted as list."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["tags"] = ["single-tag"]

    result = dump_frontmatter(post)

    assert "tags:" in result
    assert "- single-tag" in result


def test_no_tags_metadata():
    """Test that posts without tags work normally."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["type"] = "note"

    result = dump_frontmatter(post)

    assert "title: Test Note" in result
    assert "type: note" in result
    assert "tags:" not in result


def test_no_frontmatter():
    """Test that posts with no frontmatter just return content."""
    post = frontmatter.Post("Test content only")

    result = dump_frontmatter(post)

    assert result == "Test content only"


def test_complex_tags_with_special_characters():
    """Test tags with hyphens, underscores, and other valid characters."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["tags"] = ["python-test", "api_integration", "v2.0", "nested/tag"]

    result = dump_frontmatter(post)

    assert "- python-test" in result
    assert "- api_integration" in result
    assert "- v2.0" in result
    assert "- nested/tag" in result


def test_tags_order_preserved():
    """Test that tag order is preserved in output."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["tags"] = ["zebra", "apple", "banana"]

    result = dump_frontmatter(post)

    # Find the positions of each tag in the output
    zebra_pos = result.find("- zebra")
    apple_pos = result.find("- apple")
    banana_pos = result.find("- banana")

    # They should appear in the same order as input
    assert zebra_pos < apple_pos < banana_pos


def test_non_tags_lists_also_formatted():
    """Test that other lists in metadata are also formatted properly."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["authors"] = ["John Doe", "Jane Smith"]
    post.metadata["keywords"] = ["AI", "machine learning"]

    result = dump_frontmatter(post)

    # Authors should be formatted as YAML list
    assert "authors:" in result
    assert "- John Doe" in result
    assert "- Jane Smith" in result

    # Keywords should be formatted as YAML list
    assert "keywords:" in result
    assert "- AI" in result
    assert "- machine learning" in result


def test_mixed_metadata_types():
    """Test that mixed metadata types are handled correctly."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test Note"
    post.metadata["tags"] = ["tag1", "tag2"]
    post.metadata["created"] = "2024-01-01"
    post.metadata["priority"] = 5
    post.metadata["draft"] = True

    result = dump_frontmatter(post)

    # Lists should use YAML format
    assert "tags:" in result
    assert "- tag1" in result
    assert "- tag2" in result

    # Other types should be normal
    assert "title: Test Note" in result
    assert "created: '2024-01-01'" in result or "created: 2024-01-01" in result
    assert "priority: 5" in result
    assert "draft: true" in result or "draft: True" in result


def test_empty_content():
    """Test posts with empty content but with frontmatter."""
    post = frontmatter.Post("")
    post.metadata["title"] = "Empty Note"
    post.metadata["tags"] = ["empty", "test"]

    result = dump_frontmatter(post)

    # Should have frontmatter delimiter
    assert result.startswith("---")
    assert result.endswith("---\n")

    # Should have proper tag formatting
    assert "- empty" in result
    assert "- test" in result


def test_roundtrip_compatibility():
    """Test that the formatted output can be parsed back by frontmatter."""
    original_post = frontmatter.Post("Test content")
    original_post.metadata["title"] = "Test Note"
    original_post.metadata["tags"] = ["system", "test", "obsidian"]
    original_post.metadata["type"] = "note"

    # Format with our function
    formatted = dump_frontmatter(original_post)

    # Parse it back
    parsed_post = frontmatter.loads(formatted)

    # Should have same content and metadata
    assert parsed_post.content == original_post.content
    assert parsed_post.metadata["title"] == original_post.metadata["title"]
    assert parsed_post.metadata["tags"] == original_post.metadata["tags"]
    assert parsed_post.metadata["type"] == original_post.metadata["type"]


def test_title_with_colon():
    """Test that titles with colons are properly quoted and don't break YAML parsing."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "L2 Governance Core (Split: Core)"
    post.metadata["type"] = "note"

    result = dump_frontmatter(post)

    # PyYAML uses single quotes for values with special characters
    assert "title: 'L2 Governance Core (Split: Core)'" in result

    # Should be parseable back
    parsed_post = frontmatter.loads(result)
    assert parsed_post.metadata["title"] == "L2 Governance Core (Split: Core)"


def test_title_starting_with_word_and_colon():
    """Test that titles starting with word and colon are properly quoted."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Governance: Rootkeeper Manifest-Diff Prompt"
    post.metadata["type"] = "note"

    result = dump_frontmatter(post)

    # PyYAML auto-quotes values with colons (uses single quotes by default)
    assert "title: 'Governance: Rootkeeper Manifest-Diff Prompt'" in result

    # Should be parseable back
    parsed_post = frontmatter.loads(result)
    assert parsed_post.metadata["title"] == "Governance: Rootkeeper Manifest-Diff Prompt"


def test_multiple_colons_in_title():
    """Test that titles with multiple colons are properly quoted."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "API: HTTP: Response Codes: Overview"
    post.metadata["type"] = "note"

    result = dump_frontmatter(post)

    # PyYAML auto-quotes values with colons
    assert "title: 'API: HTTP: Response Codes: Overview'" in result

    # Should be parseable back
    parsed_post = frontmatter.loads(result)
    assert parsed_post.metadata["title"] == "API: HTTP: Response Codes: Overview"


def test_other_special_characters_in_title():
    """Test that titles with other special YAML characters are properly quoted."""
    special_chars_titles = [
        "Title with @ symbol",
        "Title with # hashtag",
        "Title with & ampersand",
        "Title with * asterisk",
        "Title [with brackets]",
        "Title {with braces}",
        "Title with | pipe",
        "Title with > greater",
    ]

    for title in special_chars_titles:
        post = frontmatter.Post("Test content")
        post.metadata["title"] = title
        post.metadata["type"] = "note"

        result = dump_frontmatter(post)

        # Should be parseable without errors
        parsed_post = frontmatter.loads(result)
        assert parsed_post.metadata["title"] == title


def test_all_string_values_quoted():
    """Test that string values with special characters are automatically quoted."""
    post = frontmatter.Post("Test content")
    post.metadata["title"] = "Test: Title"
    post.metadata["permalink"] = "test-permalink"
    post.metadata["type"] = "note"
    post.metadata["custom_field"] = "value: with colon"

    result = dump_frontmatter(post)

    # PyYAML auto-quotes values with special chars, leaves simple values unquoted
    assert "title: 'Test: Title'" in result  # Has colon, gets quoted
    assert "permalink: test-permalink" in result  # Simple value, no quotes
    assert "type: note" in result  # Simple value, no quotes
    assert "custom_field: 'value: with colon'" in result  # Has colon, gets quoted

    # Should be parseable back correctly
    parsed_post = frontmatter.loads(result)
    assert parsed_post.metadata["title"] == "Test: Title"
    assert parsed_post.metadata["permalink"] == "test-permalink"
    assert parsed_post.metadata["type"] == "note"
    assert parsed_post.metadata["custom_field"] == "value: with colon"
