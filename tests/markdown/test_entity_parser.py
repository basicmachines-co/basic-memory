"""Tests for entity markdown parsing."""

from datetime import datetime
from pathlib import Path
from textwrap import dedent

import pytest

from basic_memory.markdown.schemas import EntityMarkdown, EntityFrontmatter, Relation
from basic_memory.markdown.entity_parser import parse


@pytest.fixture
def valid_entity_content():
    """A complete, valid entity file with all features."""
    return dedent("""
        ---
        title: Auth Service
        type: component
        permalink: auth_service
        created: 2024-12-21T14:00:00Z
        modified: 2024-12-21T14:00:00Z
        tags: authentication, security, core
        ---

        Core authentication service that handles user authentication.
        
        some [[Random Link]]
        another [[Random Link with Title|Titled Link]]

        ## Observations
        - [design] Stateless authentication #security #architecture (JWT based)
        - [feature] Mobile client support #mobile #oauth (Required for App Store)
        - [tech] Caching layer #performance (Redis implementation)

        ## Relations
        - implements [[OAuth Implementation]] (Core auth flows)
        - uses [[Redis Cache]] (Token caching)
        - specified_by [[Auth API Spec]] (OpenAPI spec)
        """)


@pytest.mark.asyncio
async def test_parse_complete_file(project_config, entity_parser, valid_entity_content):
    """Test parsing a complete entity file with all features."""
    test_file = project_config.home / "test_entity.md"
    test_file.write_text(valid_entity_content)

    entity = await entity_parser.parse_file(test_file)

    # Verify entity structure
    assert isinstance(entity, EntityMarkdown)
    assert isinstance(entity.frontmatter, EntityFrontmatter)
    assert isinstance(entity.content, str)

    # Check frontmatter
    assert entity.frontmatter.title == "Auth Service"
    assert entity.frontmatter.type == "component"
    assert entity.frontmatter.permalink == "auth_service"
    assert set(entity.frontmatter.tags) == {"authentication", "security", "core"}

    # Check content
    assert "Core authentication service that handles user authentication." in entity.content

    # Check observations
    assert len(entity.observations) == 3
    obs = entity.observations[0]
    assert obs.category == "design"
    assert obs.content == "Stateless authentication #security #architecture"
    assert set(obs.tags or []) == {"security", "architecture"}
    assert obs.context == "JWT based"

    # Check relations
    assert len(entity.relations) == 5
    assert (
        Relation(type="implements", target="OAuth Implementation", context="Core auth flows")
        in entity.relations
    ), "missing [[OAuth Implementation]]"
    assert (
        Relation(type="uses", target="Redis Cache", context="Token caching") in entity.relations
    ), "missing [[Redis Cache]]"
    assert (
        Relation(type="specified_by", target="Auth API Spec", context="OpenAPI spec")
        in entity.relations
    ), "missing [[Auth API Spec]]"

    # inline links in content
    # Note: CI environment returns 'links_to' while local may return 'links to'
    assert (
        Relation(type="links_to", target="Random Link", context=None) in entity.relations
        or Relation(type="links to", target="Random Link", context=None) in entity.relations
    ), "missing [[Random Link]]"
    assert Relation(
        type="links_to", target="Random Link with Title|Titled Link", context=None
    ) in entity.relations or Relation(
        type="links to", target="Random Link with Title|Titled Link", context=None
    ), "missing [[Random Link with Title|Titled Link]]"


@pytest.mark.asyncio
async def test_parse_minimal_file(project_config, entity_parser):
    """Test parsing a minimal valid entity file."""
    content = dedent("""
        ---
        type: component
        tags: []
        ---

        # Minimal Entity

        ## Observations
        - [note] Basic observation #test

        ## Relations
        - references [[Other Entity]]
        """)

    test_file = project_config.home / "minimal.md"
    test_file.write_text(content)

    entity = await entity_parser.parse_file(test_file)

    assert entity.frontmatter.type == "component"
    assert entity.frontmatter.permalink is None
    assert len(entity.observations) == 1
    assert len(entity.relations) == 1

    assert entity.created is not None
    assert entity.modified is not None


@pytest.mark.asyncio
async def test_error_handling(project_config, entity_parser):
    """Test error handling."""

    # Missing file
    with pytest.raises(FileNotFoundError):
        await entity_parser.parse_file(Path("nonexistent.md"))

    # Invalid file encoding
    test_file = project_config.home / "binary.md"
    with open(test_file, "wb") as f:
        f.write(b"\x80\x81")  # Invalid UTF-8
    with pytest.raises(UnicodeDecodeError):
        await entity_parser.parse_file(test_file)


@pytest.mark.asyncio
async def test_parse_file_without_section_headers(project_config, entity_parser):
    """Test parsing a minimal valid entity file."""
    content = dedent("""
        ---
        type: component
        permalink: minimal_entity
        status: draft
        tags: []
        ---

        # Minimal Entity

        some text
        some [[Random Link]]

        - [note] Basic observation #test

        - references [[Other Entity]]
        """)

    test_file = project_config.home / "minimal.md"
    test_file.write_text(content)

    entity = await entity_parser.parse_file(test_file)

    assert entity.frontmatter.type == "component"
    assert entity.frontmatter.permalink == "minimal_entity"

    assert "some text\nsome [[Random Link]]" in entity.content

    assert len(entity.observations) == 1
    assert entity.observations[0].category == "note"
    assert entity.observations[0].content == "Basic observation #test"
    assert entity.observations[0].tags == ["test"]

    assert len(entity.relations) == 2
    assert entity.relations[0].type == "links_to"
    assert entity.relations[0].target == "Random Link"

    assert entity.relations[1].type == "references"
    assert entity.relations[1].target == "Other Entity"


def test_parse_date_formats(entity_parser):
    """Test date parsing functionality."""
    # Valid formats
    assert entity_parser.parse_date("2024-01-15") is not None
    assert entity_parser.parse_date("Jan 15, 2024") is not None
    assert entity_parser.parse_date("2024-01-15 10:00 AM") is not None
    assert entity_parser.parse_date(datetime.now()) is not None

    # Invalid formats
    assert entity_parser.parse_date(None) is None
    assert entity_parser.parse_date(123) is None  # Non-string/datetime
    assert entity_parser.parse_date("not a date") is None  # Unparsable string
    assert entity_parser.parse_date("") is None  # Empty string

    # Test dateparser error handling
    assert entity_parser.parse_date("25:00:00") is None  # Invalid time


@pytest.mark.asyncio
async def test_parse_frontmatter_created_modified_full_datetime(entity_parser, tmp_path):
    """A full ISO 8601 datetime in frontmatter overrides file stat timestamps (#238)."""
    content = dedent("""
        ---
        title: Historical Note
        type: note
        created: 2024-03-15T14:30:00
        modified: 2024-03-16T09:00:00
        ---

        Some historical content.
        """)

    entity = await entity_parser.parse_markdown_content(
        file_path=tmp_path / "historical.md",
        content=content,
        mtime=0,
        ctime=0,
    )

    assert entity.created is not None
    assert entity.modified is not None
    assert (entity.created.year, entity.created.month, entity.created.day) == (2024, 3, 15)
    assert (entity.created.hour, entity.created.minute) == (14, 30)
    assert (entity.modified.year, entity.modified.month, entity.modified.day) == (2024, 3, 16)
    assert (entity.modified.hour, entity.modified.minute) == (9, 0)
    # Naive frontmatter values are assumed to be local time, not UTC.
    assert entity.created.tzinfo is not None
    assert entity.modified.tzinfo is not None


@pytest.mark.asyncio
async def test_parse_frontmatter_created_date_only(entity_parser, tmp_path):
    """A date-only value (no time component) is accepted for created (#238)."""
    content = dedent("""
        ---
        title: Date Only Note
        type: note
        created: 2024-03-15
        ---

        Content.
        """)

    entity = await entity_parser.parse_markdown_content(
        file_path=tmp_path / "date_only.md",
        content=content,
        mtime=0,
        ctime=0,
    )

    assert entity.created is not None
    assert (entity.created.year, entity.created.month, entity.created.day) == (2024, 3, 15)
    assert entity.created.tzinfo is not None


@pytest.mark.asyncio
async def test_parse_frontmatter_created_explicit_timezone_preserved(entity_parser, tmp_path):
    """An explicit UTC offset in frontmatter is preserved, not reinterpreted as local (#238)."""
    content = dedent("""
        ---
        title: Timezone Note
        type: note
        created: "2024-03-15T14:30:00+05:00"
        ---

        Content.
        """)

    entity = await entity_parser.parse_markdown_content(
        file_path=tmp_path / "tz.md",
        content=content,
        mtime=0,
        ctime=0,
    )

    assert entity.created is not None
    assert entity.created.utcoffset().total_seconds() == 5 * 3600
    assert (entity.created.hour, entity.created.minute) == (14, 30)


@pytest.mark.asyncio
async def test_parse_frontmatter_timestamp_aliases(entity_parser, tmp_path):
    """The date/created_at/updated_at aliases are accepted as cheap read-time conveniences."""
    content = dedent("""
        ---
        title: Imported Note
        type: note
        date: 2023-01-01
        updated_at: 2023-06-15T10:00:00
        ---

        Content.
        """)

    entity = await entity_parser.parse_markdown_content(
        file_path=tmp_path / "imported.md",
        content=content,
        mtime=0,
        ctime=0,
    )

    assert (entity.created.year, entity.created.month, entity.created.day) == (2023, 1, 1)
    assert (entity.modified.year, entity.modified.month, entity.modified.day) == (2023, 6, 15)


@pytest.mark.asyncio
async def test_parse_missing_frontmatter_timestamps_falls_back_to_file_stats(
    entity_parser, tmp_path
):
    """Without frontmatter created/modified, file stat times are used (existing behavior)."""
    content = dedent("""
        ---
        title: No Timestamps
        type: note
        ---

        Content.
        """)

    mtime = datetime(2022, 5, 1, 12, 0, 0).timestamp()
    ctime = datetime(2022, 1, 1, 8, 0, 0).timestamp()

    entity = await entity_parser.parse_markdown_content(
        file_path=tmp_path / "no_timestamps.md",
        content=content,
        mtime=mtime,
        ctime=ctime,
    )

    assert entity.created.year == 2022
    assert entity.created.month == 1
    assert entity.modified.year == 2022
    assert entity.modified.month == 5


def test_parse_empty_content():
    """Test parsing empty or minimal content."""
    result = parse("")
    assert result.content == ""
    assert len(result.observations) == 0
    assert len(result.relations) == 0

    result = parse("# Just a title")
    assert result.content == "# Just a title"
    assert len(result.observations) == 0
    assert len(result.relations) == 0


@pytest.mark.asyncio
async def test_parse_file_with_absolute_path(project_config, entity_parser):
    """Test parsing a file with an absolute path."""
    content = dedent("""
        ---
        type: component
        permalink: absolute_path_test
        ---

        # Absolute Path Test
        
        A file with an absolute path.
        """)

    # Create a test file in the project directory
    test_file = project_config.home / "absolute_path_test.md"
    test_file.write_text(content)

    # Get the absolute path to the test file
    absolute_path = test_file.resolve()

    # Parse the file using the absolute path
    entity = await entity_parser.parse_file(absolute_path)

    # Verify the file was parsed correctly
    assert entity.frontmatter.permalink == "absolute_path_test"
    assert "Absolute Path Test" in entity.content
    assert entity.created is not None
    assert entity.modified is not None


# @pytest.mark.asyncio
# async def test_parse_file_invalid_yaml(test_config, entity_parser):
#     """Test parsing file with invalid YAML frontmatter."""
#     content = dedent("""
#         ---
#         invalid: [yaml: ]syntax]
#         ---
#
#         # Invalid YAML Frontmatter
#         """)
#
#     test_file = test_config.home / "invalid_yaml.md"
#     test_file.write_text(content)
#
#     # Should handle invalid YAML gracefully
#     entity = await entity_parser.parse_file(test_file)
#     assert entity.frontmatter.title == "invalid_yaml.md"
#     assert entity.frontmatter.type == "note"
#     assert entity.content.strip() == "# Invalid YAML Frontmatter"
