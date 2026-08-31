"""Tests for the POSIX-style read-side MCP tools: cat, grep, ls, find, tail, man (#1399).

Each tool is a thin translation over the same typed clients the canonical tools
use, so these tests run the real ASGI stack via the shared `client` fixture and
assert on the JSON shapes the canonical `output_format="json"` paths produce.
"""

from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import basic_memory.mcp.tools.posix_tools as posix_tools
from basic_memory.mcp.tools import cat, find, grep, ls, man, tail, write_note
from basic_memory.schemas.search import SearchRetrievalMode

# --- cat ---


@pytest.mark.asyncio
async def test_cat_returns_full_note_round_trip(client, test_project):
    await write_note(
        title="Cat Note",
        directory="test",
        content="# Cat Note\n\nline one\nline two",
        project=test_project.name,
    )

    result = await cat("Cat Note", project=test_project.name)

    assert result["title"] == "Cat Note"
    assert result["file_path"] == "test/Cat Note.md"
    assert "line one" in result["content"]
    assert "line two" in result["content"]
    assert result["frontmatter"] is not None
    assert result["frontmatter"]["title"] == "Cat Note"
    # No range requested: the payload carries no slice bookkeeping.
    assert "start_line" not in result
    assert "end_line" not in result
    assert "total_lines" not in result


@pytest.mark.asyncio
async def test_cat_include_frontmatter_toggle(client, test_project):
    await write_note(
        title="Cat Frontmatter Note",
        directory="test",
        content="body text only",
        project=test_project.name,
    )

    with_frontmatter = await cat("Cat Frontmatter Note", project=test_project.name)
    without_frontmatter = await cat(
        "Cat Frontmatter Note", project=test_project.name, include_frontmatter=False
    )

    assert with_frontmatter["content"].startswith("---")
    assert not without_frontmatter["content"].startswith("---")
    assert "body text only" in without_frontmatter["content"]


@pytest.mark.asyncio
async def test_cat_line_range_slices_content(client, test_project):
    await write_note(
        title="Cat Range Note",
        directory="test",
        content="alpha\nbravo\ncharlie\ndelta",
        project=test_project.name,
    )
    full = await cat("Cat Range Note", project=test_project.name, include_frontmatter=False)
    lines = full["content"].splitlines()

    ranged = await cat(
        "Cat Range Note",
        project=test_project.name,
        include_frontmatter=False,
        start_line=2,
        end_line=3,
    )

    assert ranged["content"] == "\n".join(lines[1:3])
    assert ranged["start_line"] == 2
    assert ranged["end_line"] == 3
    assert ranged["total_lines"] == len(lines)


@pytest.mark.asyncio
async def test_cat_start_line_only_runs_to_end(client, test_project):
    await write_note(
        title="Cat Tail Note",
        directory="test",
        content="alpha\nbravo\ncharlie",
        project=test_project.name,
    )
    full = await cat("Cat Tail Note", project=test_project.name, include_frontmatter=False)
    lines = full["content"].splitlines()

    result = await cat(
        "Cat Tail Note", project=test_project.name, include_frontmatter=False, start_line=2
    )

    assert result["content"] == "\n".join(lines[1:])
    assert result["start_line"] == 2
    assert result["end_line"] == len(lines)
    assert result["total_lines"] == len(lines)


@pytest.mark.asyncio
async def test_cat_end_line_clamped_to_total(client, test_project):
    await write_note(
        title="Cat Clamp Note",
        directory="test",
        content="alpha\nbravo",
        project=test_project.name,
    )
    full = await cat("Cat Clamp Note", project=test_project.name, include_frontmatter=False)
    lines = full["content"].splitlines()

    result = await cat(
        "Cat Clamp Note",
        project=test_project.name,
        include_frontmatter=False,
        start_line=1,
        end_line=999,
    )

    assert result["content"] == "\n".join(lines)
    assert result["end_line"] == len(lines)
    assert result["total_lines"] == len(lines)


@pytest.mark.asyncio
async def test_cat_section_not_supported():
    with pytest.raises(ValueError, match="'section' is not yet supported"):
        await cat("anything", section="Overview")


@pytest.mark.asyncio
async def test_cat_max_tokens_not_supported():
    with pytest.raises(ValueError, match="'max_tokens' is not yet supported"):
        await cat("anything", max_tokens=100)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_line", "end_line", "message"),
    [
        (0, None, "start_line must be >= 1"),
        (-1, None, "start_line must be >= 1"),
        (2, 1, "end_line must be >= start_line"),
        (None, 0, "end_line must be >= start_line"),
    ],
)
async def test_cat_rejects_bad_line_ranges(start_line, end_line, message):
    with pytest.raises(ValueError, match=message):
        await cat("anything", start_line=start_line, end_line=end_line)


@pytest.mark.asyncio
async def test_cat_unknown_identifier_raises(client, test_project):
    with pytest.raises(ToolError):
        await cat("no-such-note-anywhere", project=test_project.name)


# --- grep ---


@pytest.mark.asyncio
async def test_grep_literal_finds_seeded_content(client, test_project):
    await write_note(
        title="Grep Target",
        directory="test",
        content="# Grep Target\n\nThe posixgrepneedle hides here.",
        project=test_project.name,
    )

    result = await grep("posixgrepneedle", literal=True, project=test_project.name)

    assert result["current_page"] == 1
    assert isinstance(result["total_is_exact"], bool)
    titles = [row["title"] for row in result["results"]]
    assert "Grep Target" in titles


@pytest.mark.asyncio
async def test_grep_default_mode_resolves_fts_and_finds(client, test_project, monkeypatch):
    """With semantic search disabled the default mode falls back to full-text."""
    container = SimpleNamespace(config=SimpleNamespace(semantic_search_enabled=False))
    monkeypatch.setattr(posix_tools, "get_container", lambda: container)

    await write_note(
        title="Grep Default Target",
        directory="test",
        content="# Grep Default Target\n\nThe posixdefaultneedle hides here.",
        project=test_project.name,
    )

    result = await grep("posixdefaultneedle", project=test_project.name)

    titles = [row["title"] for row in result["results"]]
    assert "Grep Default Target" in titles


def test_grep_retrieval_mode_literal_is_always_fts():
    assert posix_tools._grep_retrieval_mode(True) is SearchRetrievalMode.FTS


def test_grep_retrieval_mode_hybrid_when_semantic_enabled(monkeypatch):
    container = SimpleNamespace(config=SimpleNamespace(semantic_search_enabled=True))
    monkeypatch.setattr(posix_tools, "get_container", lambda: container)

    assert posix_tools._grep_retrieval_mode(False) is SearchRetrievalMode.HYBRID


def test_grep_retrieval_mode_fts_when_semantic_disabled(monkeypatch):
    container = SimpleNamespace(config=SimpleNamespace(semantic_search_enabled=False))
    monkeypatch.setattr(posix_tools, "get_container", lambda: container)

    assert posix_tools._grep_retrieval_mode(False) is SearchRetrievalMode.FTS


def test_grep_retrieval_mode_falls_back_to_config_manager(monkeypatch):
    """CLI paths call tools before the MCP container exists."""

    def raise_uninitialized():
        raise RuntimeError("MCP container not initialized")

    monkeypatch.setattr(posix_tools, "get_container", raise_uninitialized)
    manager = SimpleNamespace(config=SimpleNamespace(semantic_search_enabled=True))
    monkeypatch.setattr(posix_tools, "ConfigManager", lambda: manager)

    assert posix_tools._grep_retrieval_mode(False) is SearchRetrievalMode.HYBRID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pattern": ""}, "pattern must not be empty"),
        ({"pattern": "   "}, "pattern must not be empty"),
        ({"pattern": "ok", "page": 0}, "page must be >= 1"),
        ({"pattern": "ok", "page_size": 0}, "page_size must be >= 1"),
    ],
)
async def test_grep_rejects_bad_arguments(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await grep(**kwargs)


# --- ls ---


@pytest.mark.asyncio
async def test_ls_root_listing(client, test_graph, test_project):
    result = await ls(project=test_project.name)

    assert result["total"] == 1
    assert result["has_more"] is False
    assert result["nodes"][0]["name"] == "test"
    assert result["nodes"][0]["type"] == "directory"


@pytest.mark.asyncio
async def test_ls_directory_contents(client, test_graph, test_project):
    result = await ls(path="/test", project=test_project.name)

    assert result["total"] == 5
    names = {node["name"] for node in result["nodes"]}
    assert names == {
        "Connected Entity 1.md",
        "Connected Entity 2.md",
        "Deep Entity.md",
        "Deeper Entity.md",
        "Root.md",
    }


@pytest.mark.asyncio
async def test_ls_empty_project(client, test_project):
    result = await ls(project=test_project.name)

    assert result["total"] == 0
    assert result["nodes"] == []
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_ls_pagination(client, test_graph, test_project):
    first_page = await ls(path="/test", page_size=2, project=test_project.name)
    last_page = await ls(path="/test", page=3, page_size=2, project=test_project.name)

    assert len(first_page["nodes"]) == 2
    assert first_page["has_more"] is True
    assert first_page["total"] == 5
    assert len(last_page["nodes"]) == 1
    assert last_page["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page": 0}, "page must be >= 1"),
        ({"page_size": 0}, "page_size must be >= 1"),
        ({"page_size": 201}, "page_size must be <= 200"),
    ],
)
async def test_ls_rejects_bad_arguments(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await ls(**kwargs)


# --- find ---


@pytest.mark.asyncio
async def test_find_glob_recurses_from_root(client, test_graph, test_project):
    result = await find(name="*.md", project=test_project.name)

    assert result["total"] == 5
    names = {node["name"] for node in result["nodes"]}
    assert "Root.md" in names
    assert all(node["type"] == "file" for node in result["nodes"])


@pytest.mark.asyncio
async def test_find_without_name_lists_everything(client, test_graph, test_project):
    result = await find(project=test_project.name)

    # The default depth recurses: the /test directory plus its five files.
    assert result["total"] == 6
    names = {node["name"] for node in result["nodes"]}
    assert "test" in names
    assert "Root.md" in names


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"depth": 0}, "depth must be between 1 and 10"),
        ({"depth": 11}, "depth must be between 1 and 10"),
        ({"page": 0}, "page must be >= 1"),
        ({"page_size": 0}, "page_size must be >= 1"),
        ({"page_size": 201}, "page_size must be <= 200"),
    ],
)
async def test_find_rejects_bad_arguments(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await find(**kwargs)


# --- tail ---


@pytest.mark.asyncio
async def test_tail_returns_recent_rows(client, test_graph, test_project):
    rows = await tail(project=test_project.name)

    assert rows
    for row in rows:
        assert set(row) == {"type", "title", "permalink", "file_path", "created_at"}
        assert row["type"] == "entity"
        assert isinstance(row["created_at"], str)
    titles = {row["title"] for row in rows}
    assert "Root" in titles


@pytest.mark.asyncio
async def test_tail_lines_caps_row_count(client, test_graph, test_project):
    rows = await tail(lines=2, project=test_project.name)

    assert len(rows) <= 2


@pytest.mark.asyncio
async def test_tail_custom_timeframe(client, test_graph, test_project):
    rows = await tail(timeframe="1d", project=test_project.name)

    assert isinstance(rows, list)
    assert {row["title"] for row in rows} >= {"Root"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (0, "lines must be >= 1"),
        (101, "lines must be <= 100"),
    ],
)
async def test_tail_rejects_bad_lines(lines, message):
    with pytest.raises(ValueError, match=message):
        await tail(lines=lines)


# --- man ---


@pytest.mark.asyncio
async def test_man_index_renders_the_manual(client):
    result = await man()

    assert isinstance(result, str)
    assert result.startswith("# Basic Memory manual")
    assert "## Section 3 — MCP tools" in result


@pytest.mark.asyncio
async def test_man_bundled_page_by_reference():
    result = await man(page="search-notes(3)")

    assert isinstance(result, str)
    assert "# search-notes(3)" in result


@pytest.mark.asyncio
async def test_man_page_and_query_mutually_exclusive():
    with pytest.raises(ValueError, match="not both"):
        await man(page="search-notes(3)", query="search")


@pytest.mark.asyncio
async def test_man_note_fallback_reads_manual_notes(client, test_project):
    await write_note(
        title="posix-custom-guide",
        directory="man",
        content="# posix-custom-guide\n\nCustom manual body for fallback.",
        project=test_project.name,
    )

    result = await man(page="posix-custom-guide", project=test_project.name)

    assert isinstance(result, str)
    assert "Custom manual body for fallback." in result


@pytest.mark.asyncio
async def test_man_missing_page_raises_clear_error(client, test_project):
    with pytest.raises(ToolError, match="No manual entry for totally-unknown-page"):
        await man(page="totally-unknown-page", project=test_project.name)


@pytest.mark.asyncio
async def test_man_unparseable_page_falls_back_to_notes(client, test_project):
    # "docs/..." cannot name a bundled page (the directory is not a section), so
    # the reference goes to the note fallback and misses there too.
    with pytest.raises(ToolError, match="No manual entry for docs/unknown-guide"):
        await man(page="docs/unknown-guide", project=test_project.name)


@pytest.mark.asyncio
async def test_man_query_finds_manpage_notes(client, test_project):
    await write_note(
        title="posix-grep-manual",
        directory="man",
        content="# posix-grep-manual\n\nHow to grep with posixmanualneedle.",
        note_type="manpage",
        project=test_project.name,
    )

    result = await man(query="posixmanualneedle", project=test_project.name)

    assert isinstance(result, dict)
    titles = [row["title"] for row in result["results"]]
    assert "posix-grep-manual" in titles


@pytest.mark.asyncio
async def test_man_query_missing_manual_project_raises(client, test_project):
    # No "manual" project exists in the test config. Unknown project names route
    # cloud by default (get_project_mode defaults CLOUD for unknown identifiers),
    # so without credentials the query fails fast with the setup hint instead of
    # silently searching the wrong project.
    with pytest.raises(RuntimeError, match="no credentials found"):
        await man(query="anything")
