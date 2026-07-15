"""Tests for Rich (human-readable) output mode for bm tool commands.

Commands default to Rich output when stdout is a TTY and fall back to raw JSON
when stdout is piped or --json is supplied.  These tests verify both modes.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Shared mock payloads (mirrors test_cli_tool_json_output.py for symmetry)
# ---------------------------------------------------------------------------

READ_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    # Real payloads keep the leading newline left by frontmatter stripping.
    "content": "\n# Test Note\n\nhello world",
    "frontmatter": {"title": "Test Note", "tags": ["test"]},
}

# With --frontmatter the API returns the LITERAL FILE as content
# (frontmatter block included) alongside the parsed frontmatter dict.
READ_NOTE_RESULT_WITH_FRONTMATTER = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "content": "---\ntitle: Test Note\ntags:\n- test\n---\n\n# Test Note\n\nhello world\n\n",
    "frontmatter": {"title": "Test Note", "tags": ["test"]},
}

SEARCH_RESULT = {
    # Real SearchResponse.model_dump() uses "current_page", not "page".
    # No "query" key in the response -- the query comes from the CLI argument.
    "total": 2,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Test Note",
            "permalink": "notes/test-note",
            "file_path": "notes/Test Note.md",
            "score": 0.95,
            "matched_chunk": "A snippet about test notes",
            "content": None,
        },
        {
            "type": "observation",
            "title": "Another Note",
            "permalink": "notes/another-note",
            "file_path": "notes/Another Note.md",
            "score": 0.72,
            "matched_chunk": None,
            "content": "Full content here",
        },
    ],
}

SEARCH_RESULT_EMPTY = {
    "total": 0,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [],
}

BUILD_CONTEXT_RESULT = {
    # Real GraphContext.model_dump() shape: results is a list of ContextResult dicts.
    # Each ContextResult has primary_result + observations + related_results.
    # ObservationSummary fields: type, category, content, permalink, file_path, created_at.
    "results": [
        {
            "primary_result": {
                "type": "entity",
                "external_id": "abc123",
                "title": "Test Note",
                "permalink": "notes/test-note",
                "file_path": "notes/Test Note.md",
                "created_at": "2025-01-01T00:00:00",
            },
            "observations": [
                {
                    "type": "observation",
                    "category": "fact",
                    "content": "This is a key fact about the test note",
                    "permalink": "notes/test-note",
                    "file_path": "notes/Test Note.md",
                    "created_at": "2025-01-01T00:00:00",
                }
            ],
            "related_results": [
                {
                    "type": "relation",
                    "title": "Related Note",
                    "permalink": "notes/related",
                    "file_path": "notes/Related Note.md",
                    "relation_type": "references",
                    "created_at": "2025-01-01T00:00:00",
                }
            ],
        }
    ],
    "metadata": {"uri": "notes/test-note", "depth": 1},
    "page": 1,
    "page_size": 10,
    "has_more": False,
}

BUILD_CONTEXT_EMPTY = {
    "results": [],
    "metadata": {"uri": "notes/test-note", "depth": 1},
    "page": 1,
    "page_size": 10,
    "has_more": False,
}

RECENT_ACTIVITY_RESULT = [
    # Real _extract_recent_rows output keys: type/title/permalink/file_path/created_at
    # (optional: project).  No "updated_at" key in the real output.
    {
        "type": "entity",
        "title": "Note A",
        "permalink": "notes/note-a",
        "file_path": "notes/Note A.md",
        "created_at": "2025-01-01 00:00:00",
    },
    {
        "type": "entity",
        "title": "Note B",
        "permalink": "notes/note-b",
        "file_path": "notes/Note B.md",
        "created_at": "2025-01-02 00:00:00",
    },
]


# ---------------------------------------------------------------------------
# Helper: simulate a TTY by patching _use_rich to return True
# ---------------------------------------------------------------------------


def _tty_runner(args, **kwargs):
    """Invoke CLI as if stdout is a TTY (interactive output enabled)."""
    with patch("basic_memory.cli.commands.tool._use_rich", return_value=True):
        return runner.invoke(cli_app, args, **kwargs)


def _tty_runner_with_style(args, style, **kwargs):
    """Invoke CLI as if stdout is a TTY with a given cli_output_style config value.

    Patches both _use_rich (simulate TTY) and tool.ConfigManager so that
    _resolve_output_mode reads the configured interactive style.
    """
    fake_cm = patch(
        "basic_memory.cli.commands.tool.ConfigManager",
        return_value=SimpleNamespace(config=SimpleNamespace(cli_output_style=style)),
    )
    with patch("basic_memory.cli.commands.tool._use_rich", return_value=True), fake_cm:
        return runner.invoke(cli_app, args, **kwargs)


# ---------------------------------------------------------------------------
# search-notes – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_rich_output_default(mock_mcp):
    """search-notes produces Rich table output when stdout is a TTY."""
    result = _tty_runner(["tool", "search-notes", "test"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Rich output should NOT be valid JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)
    # But it should contain the result titles and partial permalink
    assert "Test Note" in result.output
    assert "Another Note" in result.output
    # Rich may truncate long permalinks with ellipsis; check the prefix.
    assert "notes/test-no" in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_EMPTY,
)
def test_search_notes_rich_empty(mock_mcp):
    """search-notes Rich output handles empty results gracefully."""
    result = _tty_runner(["tool", "search-notes", "nothing"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No results found" in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_json_flag_overrides_tty(mock_mcp):
    """search-notes --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "search-notes", "test", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["total"] == 2
    assert data["results"][0]["title"] == "Test Note"


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_non_tty_gives_json(mock_mcp):
    """search-notes outputs JSON when stdout is not a TTY (default runner behaviour)."""
    # CliRunner does not set isatty(); _use_rich() returns False → JSON path.
    result = runner.invoke(cli_app, ["tool", "search-notes", "test"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["total"] == 2


# ---------------------------------------------------------------------------
# read-note – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_rich_output_default(mock_mcp):
    """read-note produces Rich formatted output when stdout is a TTY."""
    result = _tty_runner(["tool", "read-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Rich output contains the note title
    assert "Test Note" in result.output
    # And the rendered markdown content
    assert "hello world" in result.output
    # Not raw JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_json_flag_overrides_tty(mock_mcp):
    """read-note --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "read-note", "test-note", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    # JSON mode is byte-faithful: the payload's leading newline (frontmatter-strip
    # artifact) is preserved here even though display modes trim it.
    assert data["content"] == "\n# Test Note\n\nhello world"


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value={"title": "", "permalink": "", "content": "", "frontmatter": {}},
)
def test_read_note_rich_empty_content(mock_mcp):
    """read-note Rich output handles empty content without crashing."""
    result = _tty_runner(["tool", "read-note", "empty-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "no content" in result.output.lower()


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_non_tty_gives_json(mock_mcp):
    """read-note outputs JSON when stdout is not a TTY."""
    result = runner.invoke(cli_app, ["tool", "read-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"


# ---------------------------------------------------------------------------
# build-context – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_rich_output_default(mock_mcp):
    """build-context produces Rich tree output when stdout is a TTY."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "notes/test-note" in result.output
    assert "Related Note" in result.output
    # Not raw JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_EMPTY,
)
def test_build_context_rich_empty(mock_mcp):
    """build-context Rich output handles empty results gracefully."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No related content found" in result.output


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_json_flag_overrides_tty(mock_mcp):
    """build-context --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "results" in data
    # Real shape: results[i] is a ContextResult with primary_result nested inside.
    assert data["results"][0]["primary_result"]["title"] == "Test Note"
    assert data["results"][0]["related_results"][0]["title"] == "Related Note"


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_non_tty_gives_json(mock_mcp):
    """build-context outputs JSON when stdout is not a TTY."""
    result = runner.invoke(cli_app, ["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "results" in data


# ---------------------------------------------------------------------------
# recent-activity – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_rich_output_default(mock_mcp):
    """recent-activity produces Rich table output when stdout is a TTY."""
    result = _tty_runner(["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Note A" in result.output
    assert "Note B" in result.output
    assert "notes/note-a" in result.output
    # Not raw JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=[],
)
def test_recent_activity_rich_empty(mock_mcp):
    """recent-activity Rich output handles empty results gracefully."""
    result = _tty_runner(["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No recent activity" in result.output


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_json_flag_overrides_tty(mock_mcp):
    """recent-activity --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "recent-activity", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["title"] == "Note A"


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_non_tty_gives_json(mock_mcp):
    """recent-activity outputs JSON when stdout is not a TTY."""
    result = runner.invoke(cli_app, ["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2


# ---------------------------------------------------------------------------
# read-note – frontmatter rendering (issue #678)
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT_WITH_FRONTMATTER,
)
def test_read_note_rich_include_frontmatter(mock_mcp):
    """read-note --frontmatter renders the panel once, not twice.

    Regression 1: the Rich renderer silently dropped frontmatter even with the
    flag. Regression 2: with the flag, content is the LITERAL FILE, so the
    frontmatter block must be stripped from the Markdown body or it renders
    again under the panel.
    """
    result = _tty_runner(["tool", "read-note", "test-note", "--frontmatter"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Frontmatter panel appears with key/value data
    assert "frontmatter" in result.output
    assert "tags" in result.output
    assert "test" in result.output
    # The note content still appears
    assert "hello world" in result.output
    # The frontmatter block is NOT rendered a second time through Markdown:
    # the raw fence is stripped, and the title key appears only in the panel.
    assert "---" not in result.output
    assert result.output.count("title") == 1


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_rich_no_frontmatter_without_flag(mock_mcp):
    """read-note without --frontmatter must not render the frontmatter panel.

    Regression (Bug 2): the JSON payload always contains a non-empty "frontmatter"
    key, so the previous `if frontmatter:` guard rendered it even without the flag.
    The flag must be threaded into _display_read_note to gate the panel.
    """
    result = _tty_runner(["tool", "read-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # The note title and content must still appear
    assert "Test Note" in result.output
    assert "hello world" in result.output
    # The frontmatter panel must NOT appear
    assert "frontmatter" not in result.output


# ---------------------------------------------------------------------------
# build-context – observations rendering (issue #678)
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_rich_renders_observations(mock_mcp):
    """build-context Rich tree includes observations under each primary node.

    Regression: ContextResult.observations was exposed in JSON output but never
    rendered in the Rich path, so interactive users lost core entity facts.
    """
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # The observation category should appear in the tree
    assert "fact" in result.output
    # The observation content should appear (possibly truncated)
    assert "key fact" in result.output
    # The subtitle should include an observations count
    assert "observations" in result.output


# ---------------------------------------------------------------------------
# Rich markup injection – bracketed user text must survive (Bug 1, issue #678)
# ---------------------------------------------------------------------------

# Search result whose title contains a bracket expression like "[draft]".
SEARCH_RESULT_BRACKETED_TITLE = {
    "total": 1,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Spec [draft] v2",
            "permalink": "specs/spec-draft-v2",
            "file_path": "specs/Spec [draft] v2.md",
            "score": 0.90,
            "matched_chunk": "An important [red] section",
            "content": None,
        },
    ],
}

# build-context payload where the observation category is "fact" — previously
# `[fact]` in the obs_label markup was interpreted as an unknown Rich tag and
# the text was swallowed.
BUILD_CONTEXT_BRACKETED_OBS = {
    "results": [
        {
            "primary_result": {
                "type": "entity",
                "external_id": "xyz",
                "title": "Joanna",
                "permalink": "people/joanna",
                "file_path": "people/Joanna.md",
                "created_at": "2025-01-01T00:00:00",
            },
            "observations": [
                {
                    "type": "observation",
                    "category": "fact",
                    "content": "Joanna lives in Austin",
                    "permalink": "people/joanna",
                    "file_path": "people/Joanna.md",
                    "created_at": "2025-01-01T00:00:00",
                }
            ],
            "related_results": [],
        }
    ],
    "metadata": {"uri": "people/joanna", "depth": 1},
    "page": 1,
    "page_size": 10,
    "has_more": False,
}


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_BRACKETED_TITLE,
)
def test_search_notes_rich_title_with_brackets_survives(mock_mcp):
    """Bracketed text in a search result title must appear literally in Rich output.

    Regression (Bug 1): user-sourced titles were interpolated directly into Rich
    markup strings, so "[draft]" was treated as an unknown style tag and stripped.
    After escaping, the literal text "[draft]" must be present in the output.
    """
    result = _tty_runner(["tool", "search-notes", "spec"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # The full title including the bracket expression must survive
    assert "[draft]" in result.output
    # The snippet "[red]" should also survive (not restyle the output)
    assert "[red]" in result.output


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_BRACKETED_OBS,
)
def test_build_context_rich_observation_category_bracket_survives(mock_mcp):
    """Observation category "[fact]" must appear literally in build-context Rich tree.

    Regression (Bug 1): the obs_label was built as f"[dim][{category}] content[/dim]",
    which caused the inner "[fact]" to be parsed as an unknown Rich tag and dropped,
    rendering "Joanna lives in Austin" without the category prefix.
    After escaping the category, "[fact]" must be present in the tree output.
    """
    result = _tty_runner(["tool", "build-context", "memory://people/joanna"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # The observation category prefix must appear literally
    assert "[fact]" in result.output
    # The observation content must also appear
    assert "Joanna lives in Austin" in result.output


# ---------------------------------------------------------------------------
# search-notes – total=0 with non-empty results subtitle (Bug 3, issue #678)
# ---------------------------------------------------------------------------

# Fixture that mirrors the upstream quirk: total=0 but results is non-empty.
SEARCH_RESULT_ZERO_TOTAL = {
    "total": 0,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Found Note",
            "permalink": "notes/found-note",
            "file_path": "notes/Found Note.md",
            "score": 0.80,
            "matched_chunk": "some content",
            "content": None,
        },
        {
            "type": "entity",
            "title": "Another Found",
            "permalink": "notes/another-found",
            "file_path": "notes/Another Found.md",
            "score": 0.70,
            "matched_chunk": "more content",
            "content": None,
        },
    ],
}


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_ZERO_TOTAL,
)
def test_search_notes_rich_zero_total_falls_back_to_result_count(mock_mcp):
    """When the API returns total=0 but results is non-empty, subtitle shows real count.

    Regression (Bug 3): result.get("total", len(results)) never triggered its
    default because the "total" key exists (with value 0), so the subtitle read
    "0 result(s)" under a table showing rows.  The fix detects a falsy total with
    non-empty results and falls back to len(results).
    """
    result = _tty_runner(["tool", "search-notes", "found"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Both result rows must appear
    assert "Found Note" in result.output
    assert "Another Found" in result.output
    # The subtitle must show the real count (2), not 0
    assert "2 result(s)" in result.output
    assert "0 result(s)" not in result.output


# ---------------------------------------------------------------------------
# --plain output mode (issue #678 follow-up)
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_plain_output(mock_mcp):
    """search-notes --plain emits undecorated numbered text, not JSON or Rich boxes."""
    result = _tty_runner(["tool", "search-notes", "test", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Not JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)
    # Numbered, greppable entries with titles, scores, and permalinks
    assert "1. Test Note" in result.output
    assert "2. Another Note" in result.output
    assert "notes/test-note" in result.output
    assert "0.95" in result.output
    # Snippet line present
    assert "A snippet about test notes" in result.output
    # No Rich box-drawing characters
    assert "─" not in result.output
    assert "│" not in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_BRACKETED_TITLE,
)
def test_search_notes_plain_brackets_survive(mock_mcp):
    """Literal brackets must survive verbatim in plain output (no markup escaping)."""
    result = _tty_runner(["tool", "search-notes", "spec", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # The literal title and snippet brackets must be present unmangled
    assert "Spec [draft] v2" in result.output
    assert "[red]" in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_ZERO_TOTAL,
)
def test_search_notes_plain_zero_total_fallback(mock_mcp):
    """Plain search output shows the corrected count when the API returns total=0."""
    result = _tty_runner(["tool", "search-notes", "found", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "2 result(s)" in result.output
    assert "0 result(s)" not in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_EMPTY,
)
def test_search_notes_plain_empty(mock_mcp):
    """Plain search output handles empty results."""
    result = _tty_runner(["tool", "search-notes", "nothing", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No results found." in result.output


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_plain_output(mock_mcp):
    """read-note --plain emits the note body faithfully, with no decoration."""
    result = _tty_runner(["tool", "read-note", "test-note", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # No synthesized header line -- plain mode is the payload, faithfully.
    assert "[notes/test-note]" not in result.output
    # The body verbatim, trimmed of the API's frontmatter-strip newline artifact.
    assert result.output == "# Test Note\n\nhello world\n"


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT_WITH_FRONTMATTER,
)
def test_read_note_plain_include_frontmatter(mock_mcp):
    """read-note --plain --frontmatter writes the literal file byte-for-byte.

    With the flag, content IS the file (frontmatter block included); plain mode
    must not add decoration or alter leading/trailing newlines.
    """
    result = _tty_runner(["tool", "read-note", "test-note", "--plain", "--frontmatter"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.output == READ_NOTE_RESULT_WITH_FRONTMATTER["content"]


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT_WITH_FRONTMATTER,
)
def test_read_note_include_frontmatter_alias(mock_mcp):
    """--include-frontmatter still works as a deprecated alias for --frontmatter."""
    result = _tty_runner(["tool", "read-note", "test-note", "--plain", "--include-frontmatter"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.output.startswith("---\ntitle: Test Note")


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value={"title": "", "permalink": "", "content": "", "frontmatter": {}},
)
def test_read_note_plain_empty_content(mock_mcp):
    """read-note --plain prints nothing for an empty note (no placeholder)."""
    result = _tty_runner(["tool", "read-note", "empty-note", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.output == ""


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_plain_output(mock_mcp):
    """build-context --plain emits an ASCII-indented outline with observations."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Context: notes/test-note" in result.output
    assert "Test Note" in result.output
    # Observation rendered with literal bracketed category, indented
    assert "  [fact] This is a key fact about the test note" in result.output
    # Related item with relation type, indented
    assert "Related Note" in result.output
    assert "references" in result.output
    # Not JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_BRACKETED_OBS,
)
def test_build_context_plain_bracket_survives(mock_mcp):
    """Observation category [fact] must appear literally in plain build-context output."""
    result = _tty_runner(["tool", "build-context", "memory://people/joanna", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "[fact]" in result.output
    assert "Joanna lives in Austin" in result.output


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_EMPTY,
)
def test_build_context_plain_empty(mock_mcp):
    """build-context --plain handles empty results."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No related content found." in result.output


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_plain_output(mock_mcp):
    """recent-activity --plain emits "- title (type) permalink updated" lines."""
    result = _tty_runner(["tool", "recent-activity", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "- Note A (entity) notes/note-a" in result.output
    assert "- Note B (entity) notes/note-b" in result.output
    # Not JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=[],
)
def test_recent_activity_plain_empty(mock_mcp):
    """recent-activity --plain handles empty results."""
    result = _tty_runner(["tool", "recent-activity", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No recent activity." in result.output


# ---------------------------------------------------------------------------
# Precedence matrix: --json > --plain > non-TTY (JSON) > TTY (config style)
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_json_beats_plain(mock_mcp):
    """--json wins over --plain... when they are not used together, --json alone is JSON.

    The contradictory combination is tested separately (must error); here we
    confirm --json on its own produces JSON even in a TTY.
    """
    result = _tty_runner(["tool", "search-notes", "test", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["total"] == 2


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_json_and_plain_together_errors(mock_mcp):
    """Passing both --json and --plain is a clear, non-zero error."""
    result = _tty_runner(["tool", "search-notes", "test", "--json", "--plain"])

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_json_and_plain_together_errors(mock_mcp):
    """read-note also rejects --json --plain together."""
    result = _tty_runner(["tool", "read-note", "test-note", "--json", "--plain"])

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_plain_forces_plain_when_piped(mock_mcp):
    """--plain forces plain output even when stdout is NOT a TTY (piped)."""
    # No _use_rich patch: the default CliRunner stdout is not a TTY, so absent
    # --plain this would be JSON.  --plain must override that into plain text.
    result = runner.invoke(cli_app, ["tool", "search-notes", "test", "--plain"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)
    assert "1. Test Note" in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_tty_config_rich_renders_rich(mock_mcp):
    """TTY + cli_output_style=rich → Rich output (box-drawing present)."""
    result = _tty_runner_with_style(["tool", "search-notes", "test"], style="rich")

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)
    # Rich draws a Panel border
    assert "─" in result.output or "│" in result.output


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_tty_config_plain_renders_plain(mock_mcp):
    """TTY + cli_output_style=plain → plain output (no box-drawing)."""
    result = _tty_runner_with_style(["tool", "search-notes", "test"], style="plain")

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)
    assert "1. Test Note" in result.output
    assert "─" not in result.output
    assert "│" not in result.output
