"""Tests for the POSIX-style read-side MCP tools: cat, grep, ls, find, tail, man (#1399).

Each tool is a thin translation over the same typed clients the canonical tools
use, so these tests run the real ASGI stack via the shared `client` fixture and
assert on the JSON shapes the canonical `output_format="json"` paths produce.
"""

from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import basic_memory.mcp.tools.posix_tools as posix_tools
from basic_memory.mcp.project_context import (
    ProjectPrefixConflictError,
    UnqualifiedPathRefusedError,
)
from basic_memory.mcp.tools import cat, find, grep, ls, man, tail, write_note
from basic_memory.schemas.search import SearchRetrievalMode


@pytest.fixture
def no_project_constraint(monkeypatch):
    """Clear the env project constraint so unqualified routing paths are reachable."""
    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)


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
async def test_cat_section_returns_exact_span(client, test_project):
    await write_note(
        title="Cat Section Note",
        directory="test",
        content="# Guide\nintro\n## First\nalpha\n## Second\nbeta",
        project=test_project.name,
    )
    full = await cat("Cat Section Note", project=test_project.name)
    full_lines = full["content"].splitlines()

    result = await cat("Cat Section Note", project=test_project.name, section="First")

    assert result["section"] == "Guide/First"
    assert result["content"].splitlines() == ["## First", "alpha"]
    assert result["total_lines"] == len(full_lines)
    # Coordinates are document-absolute: they address the same lines in a
    # frontmatter-included follow-up range read.
    assert result["content"] == "\n".join(full_lines[result["start_line"] - 1 : result["end_line"]])
    # Slices never carry a frontmatter block.
    assert result["frontmatter"] is None
    assert "truncated" not in result
    assert "continue_line" not in result


@pytest.mark.asyncio
async def test_cat_section_path_form_disambiguates(client, test_project):
    await write_note(
        title="Cat Section Paths",
        directory="test",
        content="# Auth\n## Decisions\na\n# Ops\n## Decisions\nb",
        project=test_project.name,
    )

    result = await cat("Cat Section Paths", project=test_project.name, section="Ops/Decisions")

    assert result["section"] == "Ops/Decisions"
    assert result["content"].splitlines() == ["## Decisions", "b"]


@pytest.mark.asyncio
async def test_cat_section_bracket_form_addresses_duplicates(client, test_project):
    await write_note(
        title="Cat Section Duplicates",
        directory="test",
        content="# Spec\n## Auth\nfirst\n## Auth\nsecond",
        project=test_project.name,
    )

    result = await cat("Cat Section Duplicates", project=test_project.name, section="Auth[1]")

    assert result["section"] == "Spec/Auth[1]"
    assert result["content"].splitlines() == ["## Auth", "second"]


@pytest.mark.asyncio
async def test_cat_unknown_section_lists_available_headings(client, test_project):
    await write_note(
        title="Cat Section Missing",
        directory="test",
        content="# Guide\n## First\nalpha",
        project=test_project.name,
    )

    with pytest.raises(ToolError, match="Available sections") as excinfo:
        await cat("Cat Section Missing", project=test_project.name, section="Nope")
    assert "Guide/First" in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"section": "A", "start_line": 2}, "cannot be combined with start_line/end_line"),
        ({"section": "A", "end_line": 3}, "cannot be combined with start_line/end_line"),
        ({"max_tokens": 0}, "max_tokens must be >= 1"),
        ({"max_tokens": -5}, "max_tokens must be >= 1"),
        # A line range with max_tokens is document-absolute (frontmatter included);
        # include_frontmatter=False ranges are body-relative — mixing the two would
        # serve frontmatter text despite the opt-out, so the combination is rejected.
        (
            {"max_tokens": 5, "start_line": 2, "include_frontmatter": False},
            "requires include_frontmatter=True",
        ),
        (
            {"max_tokens": 5, "end_line": 3, "include_frontmatter": False},
            "requires include_frontmatter=True",
        ),
    ],
)
async def test_cat_rejects_bad_slice_arguments_before_io(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await cat("anything", **kwargs)


@pytest.mark.asyncio
async def test_cat_max_tokens_truncates_and_resumes(client, test_project):
    await write_note(
        title="Cat Token Budget",
        directory="test",
        content="# One\n" + "a" * 40 + "\n# Two\n" + "b" * 40,
        project=test_project.name,
    )
    full = await cat("Cat Token Budget", project=test_project.name)
    full_lines = full["content"].splitlines()

    truncated = await cat("Cat Token Budget", project=test_project.name, max_tokens=20)

    assert truncated["truncated"] is True
    marker = truncated["content"].splitlines()[-1]
    assert "truncated at max_tokens=20" in marker
    assert f"continue with lines={truncated['continue_line']}-" in marker
    kept_lines = truncated["content"].splitlines()[:-1]

    # Resume flow: a follow-up range read from continue_line returns exactly
    # the remainder, reconstructing the document body.
    rest = await cat(
        "Cat Token Budget",
        project=test_project.name,
        start_line=truncated["continue_line"],
    )
    assert kept_lines + rest["content"].splitlines() == (full_lines[truncated["start_line"] - 1 :])


@pytest.mark.asyncio
async def test_cat_max_tokens_with_line_range_routes_server_side(client, test_project):
    await write_note(
        title="Cat Combined Slice",
        directory="test",
        content="alpha\nbravo\ncharlie\ndelta",
        project=test_project.name,
    )
    full = await cat("Cat Combined Slice", project=test_project.name)
    full_lines = full["content"].splitlines()

    result = await cat(
        "Cat Combined Slice",
        project=test_project.name,
        start_line=2,
        max_tokens=1000,
    )

    assert result["content"] == "\n".join(full_lines[1:])
    assert result["start_line"] == 2
    assert result["end_line"] == len(full_lines)
    assert result["total_lines"] == len(full_lines)
    assert "truncated" not in result
    # The server-side slice agrees with the client-side range read byte for byte.
    client_side = await cat("Cat Combined Slice", project=test_project.name, start_line=2)
    assert result["content"] == client_side["content"]


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
    # New contract (#1415): with no project addressed, ls "/" lists projects as
    # mount points — this test pins the project-scoped case via project=.
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
    # Project-scoped case (#1415): project= bypasses the mount-point view.
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


# --- project-qualified routing (#1415) ---
# Projects are mount points: '<project>/path' inputs route to that project, an
# explicit project must agree with a path prefix, and multi-project configs
# refuse unqualified input with the active project list instead of defaulting.


# -- ls "/" mount-point view --


@pytest.mark.asyncio
async def test_ls_root_lists_projects_as_mount_points(
    client, test_project, second_project, no_project_constraint
):
    """ls "/" with no project addressed is the mount table, sorted by name."""
    result = await ls()

    assert result["total"] == 2
    assert result["has_more"] is False
    assert [node["name"] for node in result["nodes"]] == ["second-project", "test-project"]
    for node in result["nodes"]:
        assert node["type"] == "directory"
        # directory_path is the copyable '/<project>' prefix form.
        assert node["directory_path"] == f"/{node['permalink']}"


@pytest.mark.asyncio
async def test_ls_root_mount_view_in_single_project_config(
    client, test_graph, test_project, no_project_constraint
):
    """The mount view is unconditional (#1415): even a single-project config
    lists the mount table at "/" so in-band discovery is uniform."""
    result = await ls()

    assert result["total"] == 1
    assert result["nodes"][0]["name"] == "test-project"
    assert result["nodes"][0]["directory_path"] == "/test-project"
    assert result["nodes"][0]["type"] == "directory"


@pytest.mark.asyncio
async def test_ls_mount_view_paginates_over_project_rows(
    client, test_project, second_project, no_project_constraint
):
    first = await ls(page=1, page_size=1)
    last = await ls(page=2, page_size=1)

    assert first["total"] == 2
    assert first["has_more"] is True
    assert [node["name"] for node in first["nodes"]] == ["second-project"]
    assert [node["name"] for node in last["nodes"]] == ["test-project"]
    assert last["has_more"] is False


# -- rule 1: first-segment routing per verb --


@pytest.mark.asyncio
async def test_ls_single_segment_project_name_lists_its_root(
    client, test_project, second_project, no_project_constraint
):
    await write_note(
        title="Second Root Note",
        directory="notes",
        content="# Second Root Note\n\nsecond project content",
        project="second-project",
    )

    result = await ls("second-project")

    names = {node["name"] for node in result["nodes"]}
    assert names == {"notes"}


@pytest.mark.asyncio
async def test_ls_qualified_path_routes_into_project_directory(
    client, test_project, second_project, no_project_constraint
):
    await write_note(
        title="Second Dir Note",
        directory="notes",
        content="# Second Dir Note",
        project="second-project",
    )

    result = await ls("/second-project/notes")

    names = {node["name"] for node in result["nodes"]}
    assert names == {"Second Dir Note.md"}


@pytest.mark.asyncio
async def test_find_qualified_path_routes_to_project(
    client, test_project, second_project, no_project_constraint
):
    await write_note(
        title="Second Find Note",
        directory="notes",
        content="# Second Find Note",
        project="second-project",
    )

    result = await find("second-project", name="*.md")

    names = {node["name"] for node in result["nodes"]}
    assert names == {"Second Find Note.md"}


@pytest.mark.asyncio
async def test_cat_qualified_identifier_equals_explicit_project_read(
    client, test_graph, test_project, second_project, no_project_constraint
):
    """'test-project/test/root' with no project param reads the same note as
    project='test-project' + 'test/root'.

    The payloads are not identical, and deliberately so: each answers in the
    addressing frame its caller used, so the file_path it hands back is one the
    same call shape accepts again. Content is what must match.
    """
    qualified = await cat("test-project/test/root")
    explicit = await cat("test/root", project=test_project.name)

    assert qualified["title"] == explicit["title"] == "Root"
    assert qualified["content"] == explicit["content"]

    # Each frame's file_path round-trips in that same frame.
    assert qualified["file_path"] == f"test-project/{explicit['file_path']}"
    assert (await cat(qualified["file_path"]))["title"] == "Root"
    assert (await cat(explicit["file_path"], project=test_project.name))["title"] == "Root"


@pytest.mark.asyncio
async def test_tool_output_permalink_round_trips_into_cat(
    client, test_project, second_project, no_project_constraint
):
    """Round trip: stored permalinks are already project-qualified, so tail's
    output is a valid cat identifier with no project param anywhere — and the
    mount view's prefix is that permalink's first segment."""
    await write_note(
        title="Round Trip",
        directory="notes",
        content="# Round Trip\n\nround trip body",
        project="second-project",
    )

    rows = await tail(project="second-project")
    permalink = next(row["permalink"] for row in rows if row["title"] == "Round Trip")
    assert permalink == "second-project/notes/round-trip"

    mounts = await ls()
    mount_prefixes = {node["directory_path"] for node in mounts["nodes"]}
    assert f"/{permalink.split('/', 1)[0]}" in mount_prefixes

    result = await cat(permalink)

    assert result["title"] == "Round Trip"
    assert "round trip body" in result["content"]


# -- rule 2: explicit project + prefix agree/conflict --


@pytest.mark.asyncio
async def test_explicit_project_with_agreeing_prefix_strips(
    client, test_graph, test_project, second_project, no_project_constraint
):
    qualified = await ls("/test-project/test", project=test_project.name)
    relative = await ls("/test", project=test_project.name)

    # Same listing, each in its caller's addressing frame (see cat's twin above).
    assert qualified["total"] == relative["total"] == 5
    assert [node["name"] for node in qualified["nodes"]] == [
        node["name"] for node in relative["nodes"]
    ]
    assert [node["file_path"] for node in qualified["nodes"]] == [
        f"test-project/{node['file_path']}" for node in relative["nodes"]
    ]


@pytest.mark.asyncio
async def test_explicit_project_with_conflicting_prefix_refuses(
    client, test_project, second_project, no_project_constraint
):
    """A disagreeing prefix is never silently resolved either way."""
    with pytest.raises(
        ProjectPrefixConflictError,
        match="path names project 'second-project' but project 'test-project' was passed",
    ):
        await cat("second-project/notes/foo", project=test_project.name)


# -- rule 4: multi-project unqualified refusal, self-teaching message --


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verb", "kwargs", "subject"),
    [
        ("cat", {"identifier": "notes/foo"}, "no project 'notes'"),
        ("ls", {"path": "/notes"}, "no project 'notes'"),
        ("find", {"path": "/x"}, "no project 'x'"),
        ("grep", {"pattern": "needle"}, "no project specified"),
        ("tail", {}, "no project specified"),
    ],
)
async def test_unqualified_input_refuses_in_multi_project_config(
    client, test_project, second_project, no_project_constraint, verb, kwargs, subject
):
    """Each verb refuses rather than silently defaulting, listing every active
    project in copyable prefix form."""
    tool = {"cat": cat, "ls": ls, "find": find, "grep": grep, "tail": tail}[verb]

    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await tool(**kwargs)

    assert str(excinfo.value) == (f"{subject} — active projects: second-project/, test-project/")


@pytest.mark.asyncio
async def test_grep_argument_validation_precedes_refusal(
    client, test_project, second_project, no_project_constraint
):
    """Bad-argument errors keep firing before any routing decision."""
    with pytest.raises(ValueError, match="pattern must not be empty"):
        await grep("")


# -- rule 5: single-project passthrough and near-collisions --


@pytest.mark.asyncio
async def test_single_project_unqualified_paths_pass_through(
    client, test_graph, test_project, no_project_constraint
):
    """One configured project keeps today's ergonomics: unqualified paths route
    to the default project, and 'test' is not falsely stripped as a prefix of
    'test-project' (permalink comparison, not startswith)."""
    listing = await ls("/test")
    assert listing["total"] == 5

    note = await cat("test/root")
    assert note["title"] == "Root"

    rows = await tail()
    assert {row["title"] for row in rows} >= {"Root"}

    found = await grep("Root", literal=True)
    assert found["results"]


@pytest.mark.asyncio
async def test_project_named_folder_is_reachable_double_qualified(
    client, test_project, second_project, no_project_constraint
):
    """Collision rule: the project wins the first segment, so a top-level folder
    named like its own project is addressed by double-qualifying."""
    await write_note(
        title="Shadowed",
        directory="second-project",
        content="# Shadowed\n\nshadowed body",
        project="second-project",
    )

    listing = await ls("second-project/second-project")
    assert {node["name"] for node in listing["nodes"]} == {"Shadowed.md"}

    result = await cat("second-project/second-project/shadowed")
    assert result["title"] == "Shadowed"


@pytest.mark.asyncio
async def test_cat_bare_project_name_is_an_error(
    client, test_project, second_project, no_project_constraint
):
    with pytest.raises(ValueError, match="names a project, not a note"):
        await cat("second-project")


# -- env constraint (BASIC_MEMORY_MCP_PROJECT) --


@pytest.mark.asyncio
async def test_env_constraint_counts_as_explicit_project(
    client, test_graph, test_project, second_project, monkeypatch
):
    """The env constraint participates exactly like the project param: no
    refusal, agreeing prefixes strip, disagreeing prefixes conflict, and
    ls "/" lists the constrained project's root, not the mount table."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    rows = await tail()
    assert {row["title"] for row in rows} >= {"Root"}

    stripped = await ls("/test-project/test")
    assert stripped["total"] == 5

    constrained_root = await ls()
    assert {node["name"] for node in constrained_root["nodes"]} == {"test"}

    with pytest.raises(ProjectPrefixConflictError):
        await cat("second-project/notes/foo")


# -- project_id passthrough --


@pytest.mark.asyncio
async def test_project_id_routes_without_prefix_parsing(
    client, test_graph, test_project, second_project, no_project_constraint
):
    """project_id routes by external UUID and bypasses prefix parsing entirely,
    so a multi-project config needs no qualification."""
    result = await cat("test/root", project_id=test_project.external_id)

    assert result["title"] == "Root"


# -- round-trip coherence: a returned path is an accepted path (#1421) --
# The property belongs to the routing layer, not to any one verb, so these tests
# enumerate the verbs rather than naming them. A seventh posix verb that accepts
# a path has to answer for the property here before it can ship.


def _path_accepting_posix_verbs() -> set[str]:
    """Posix verbs whose first parameter is a routable path or identifier.

    Derived from the tools themselves so a new one cannot slip past the round-trip
    tests below by simply not being listed.
    """
    import inspect

    verbs = {}
    for verb in (cat, grep, ls, find, tail, man):
        fn = getattr(verb, "fn", verb)
        first = next(iter(inspect.signature(fn).parameters), None)
        if first in {"path", "identifier"}:
            verbs[fn.__name__] = verb
    return set(verbs)


def test_path_accepting_verbs_are_the_ones_covered_below():
    """Pins the set the round-trip tests cover. Adding a path-accepting verb
    fails here, which is the prompt to give it the same guarantee — the class is
    closed by this enumeration, not by every author remembering."""
    assert _path_accepting_posix_verbs() == {"cat", "ls", "find"}


@pytest.mark.asyncio
async def test_ls_returned_paths_route_back_to_the_same_project(
    client, test_project, second_project, no_project_constraint
):
    """A qualified `ls` advertises child paths; feeding one back must reach the
    same project. Returning the API's project-relative '/notes' refused as
    unqualified — or, with a project mounted as 'notes', opened that one."""
    await write_note(
        title="Second Root Note",
        directory="notes",
        content="# Second Root Note",
        project="second-project",
    )

    listing = await ls("second-project")
    child = next(node for node in listing["nodes"] if node["name"] == "notes")
    assert child["directory_path"] == "/second-project/notes"

    # The advertised path is accepted verbatim, with no project param.
    nested = await ls(child["directory_path"])
    assert {node["name"] for node in nested["nodes"]} == {"Second Root Note.md"}


@pytest.mark.asyncio
async def test_find_returned_paths_route_back_to_the_same_project(
    client, test_project, second_project, no_project_constraint
):
    """find advertises both directory_path and file_path; each must address the
    project the call routed to, so `cat` and `ls` accept them unchanged."""
    await write_note(
        title="Second Root Note",
        directory="notes",
        content="# Second Root Note",
        project="second-project",
    )

    listing = await find("second-project")
    file_node = next(node for node in listing["nodes"] if node["type"] == "file")
    dir_node = next(node for node in listing["nodes"] if node["type"] == "directory")

    assert file_node["file_path"].startswith("second-project/")
    assert dir_node["directory_path"].startswith("/second-project")

    assert (await cat(file_node["file_path"]))["title"] == "Second Root Note"
    assert (await ls(dir_node["directory_path"]))["total"] >= 1


@pytest.mark.asyncio
async def test_unrouted_listings_keep_project_relative_paths(
    client, test_graph, test_project, second_project, no_project_constraint
):
    """The prefix goes back only when the call put it in the path. An explicit
    project param is a different addressing frame: those paths are fed back with
    the same param, so re-prefixing them would break that round trip instead."""
    listing = await ls("/test", project=test_project.name)

    assert all(not node["file_path"].startswith("test-project/") for node in listing["nodes"])
    assert (await ls("/test", project=test_project.name))["total"] == listing["total"]
