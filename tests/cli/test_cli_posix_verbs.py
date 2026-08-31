"""Tests for the top-level POSIX read verbs (SPEC-47, #1404).

`bm cat/grep/ls/find/tail/head/tree` are thin frontends over the MCP posix
tool functions, so these tests mock the tool functions directly (the same
pattern as test_cli_tool_json_output.py / test_cli_tool_rich_output.py) and
verify three things per verb:

- JSON mode is the tool payload, verbatim (the stable machine contract);
- Rich and plain renderers show the payload without mangling user text;
- CLI flags pass through to the tool call unchanged.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Shared mock payloads (shapes mirror tests/mcp/test_tool_posix.py)
# ---------------------------------------------------------------------------

# cat returns the read_note JSON shape; with the default --frontmatter the
# content is the literal file (frontmatter block included).
CAT_RESULT = {
    "title": "Search Spec",
    "permalink": "specs/search",
    "file_path": "specs/Search Spec.md",
    "content": "---\ntitle: Search Spec\n---\n\n# Search Spec\n\nranking notes\n",
    "frontmatter": {"title": "Search Spec"},
}

# A line-range read adds slice coordinates for follow-up range reads.
CAT_SLICE_RESULT = {
    **CAT_RESULT,
    "content": "# Search Spec\n\nranking notes",
    "start_line": 5,
    "end_line": 7,
    "total_lines": 7,
}

# A max_tokens read that got cut carries truncated/continue_line for resuming.
CAT_TRUNCATED_RESULT = {
    **CAT_RESULT,
    "content": "# Search Spec\n\n> ... truncated at max_tokens=20 ...",
    "section": "Decisions",
    "start_line": 5,
    "end_line": 40,
    "total_lines": 80,
    "truncated": True,
    "continue_line": 42,
}

# grep returns the search-notes response shape (SearchResponse.model_dump).
GREP_RESULT = {
    "total": 2,
    "total_is_exact": True,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Spec [draft] v2",
            "permalink": "specs/spec-draft-v2",
            "file_path": "specs/Spec [draft] v2.md",
            "score": 0.95,
            "matched_chunk": "A snippet about ranking",
            "content": None,
        },
        {
            "type": "entity",
            "title": "Another Note",
            "permalink": "notes/another-note",
            "file_path": "notes/Another Note.md",
            "score": 0.72,
            "matched_chunk": None,
            "content": None,
        },
    ],
}

GREP_RESULT_EMPTY = {
    "total": 0,
    "total_is_exact": True,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [],
}


def _dir_node(**overrides):
    """One DirectoryNode.model_dump(mode="json") dict with realistic defaults."""
    node = {
        "name": "",
        "file_path": None,
        "directory_path": "",
        "type": "file",
        "children": [],
        "title": None,
        "permalink": None,
        "external_id": None,
        "entity_id": None,
        "note_type": None,
        "content_type": None,
        "updated_at": None,
    }
    node.update(overrides)
    return node


# ls/find return DirectoryListResponse.model_dump(mode="json").
LS_RESULT = {
    "nodes": [
        _dir_node(name="specs", directory_path="/specs", type="directory"),
        _dir_node(
            name="Search Spec.md",
            file_path="specs/Search Spec.md",
            directory_path="/specs/Search Spec.md",
            title="Spec [draft] v2",
            permalink="specs/search-spec",
            updated_at="2025-01-01T00:00:00",
        ),
    ],
    "page": 1,
    "page_size": 10,
    "total": 2,
    "has_more": False,
}

LS_RESULT_EMPTY = {"nodes": [], "page": 1, "page_size": 10, "total": 0, "has_more": False}

LS_RESULT_MORE = {**LS_RESULT, "has_more": True}

FIND_RESULT = {
    "nodes": [
        _dir_node(name="specs", directory_path="/specs", type="directory"),
        _dir_node(
            name="search.md",
            file_path="specs/search.md",
            directory_path="/specs/search.md",
            title="Search [draft]",
            permalink="specs/search",
        ),
    ],
    "page": 1,
    "page_size": 10,
    "total": 2,
    "has_more": False,
}

FIND_RESULT_EMPTY = {"nodes": [], "page": 1, "page_size": 10, "total": 0, "has_more": False}

# tail rows: {type, title, permalink, file_path, created_at}, newest first.
TAIL_RESULT = [
    {
        "type": "entity",
        "title": "Note A",
        "permalink": "notes/note-a",
        "file_path": "notes/Note A.md",
        "created_at": "2025-01-02T10:00:00",
    },
    {
        "type": "entity",
        "title": "Note [draft] B",
        "permalink": "notes/note-b",
        "file_path": "notes/Note B.md",
        "created_at": "2025-01-01T09:00:00",
    },
]

# tree consumes find's payload: a FLAT page of nodes (children stripped by the
# API), so nesting must be rebuilt from directory_path segments.
TREE_RESULT = {
    "nodes": [
        _dir_node(name="specs", directory_path="/specs", type="directory"),
        _dir_node(name="search.md", file_path="specs/search.md", directory_path="/specs/search.md"),
        # No node for specs/auth: a glob filter can return files without their
        # parent directories, so "auth/" must be synthesized.
        _dir_node(
            name="deep.md",
            file_path="specs/auth/deep.md",
            directory_path="/specs/auth/deep.md",
        ),
    ],
    "page": 1,
    "page_size": 10,
    "total": 3,
    "has_more": False,
}

# Rooted find: the search root itself comes back as a node and must not render
# as its own child.
TREE_ROOTED_RESULT = {
    "nodes": [
        _dir_node(name="specs", directory_path="/specs", type="directory"),
        _dir_node(name="intro.md", file_path="specs/intro.md", directory_path="/specs/intro.md"),
    ],
    "page": 1,
    "page_size": 10,
    "total": 2,
    "has_more": False,
}

# A directory node arriving after a file already synthesized it, plus a bare
# root node ("/") that contributes no path segment.
TREE_DIR_AFTER_FILE_RESULT = {
    "nodes": [
        _dir_node(name="", directory_path="/", type="directory"),
        _dir_node(name="search.md", file_path="specs/search.md", directory_path="/specs/search.md"),
        _dir_node(name="specs", directory_path="/specs", type="directory"),
    ],
    "page": 1,
    "page_size": 10,
    "total": 3,
    "has_more": False,
}

TREE_RESULT_MORE = {**TREE_RESULT, "has_more": True}

TREE_RESULT_EMPTY = {"nodes": [], "page": 1, "page_size": 10, "total": 0, "has_more": False}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(args, **kwargs):
    """Invoke the CLI wide enough that Rich tables don't truncate cell text."""
    kwargs.setdefault("env", {"COLUMNS": "240"})
    return runner.invoke(cli_app, args, **kwargs)


def _tty_invoke(args, **kwargs):
    """Invoke the CLI as if stdout were an interactive TTY."""
    with patch("basic_memory.cli.commands.tool._use_rich", return_value=True):
        return _invoke(args, **kwargs)


def _flattened(output: str) -> str:
    # Rich wraps console output at the terminal width; collapse all whitespace
    # so phrase assertions can't be split by a line break.
    return " ".join(output.split())


def _assert_not_json(output: str) -> None:
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(output)


# Every verb with its patch target and a realistic payload. head shares cat's
# tool and payload; tree shares find's — their JSON contracts are identical.
VERB_CASES = [
    pytest.param(["cat", "specs/search"], "basic_memory.mcp.tools.cat", CAT_RESULT, id="cat"),
    pytest.param(["head", "specs/search"], "basic_memory.mcp.tools.cat", CAT_RESULT, id="head"),
    pytest.param(["grep", "ranking"], "basic_memory.mcp.tools.grep", GREP_RESULT, id="grep"),
    pytest.param(["ls", "/specs"], "basic_memory.mcp.tools.ls", LS_RESULT, id="ls"),
    pytest.param(["find", "/specs"], "basic_memory.mcp.tools.find", FIND_RESULT, id="find"),
    pytest.param(["tail"], "basic_memory.mcp.tools.tail", TAIL_RESULT, id="tail"),
    pytest.param(["tree", "/specs"], "basic_memory.mcp.tools.find", TREE_RESULT, id="tree"),
]


# ---------------------------------------------------------------------------
# JSON contract: non-TTY auto-JSON and --json are the tool payload, verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("args", "target", "payload"), VERB_CASES)
def test_verb_non_tty_outputs_tool_payload_verbatim(args, target, payload):
    """Piped output is the MCP tool's return value, unchanged (auto-JSON)."""
    with patch(target, new_callable=AsyncMock, return_value=payload) as mock_tool:
        result = _invoke(args)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    mock_tool.assert_called_once()


@pytest.mark.parametrize(("args", "target", "payload"), VERB_CASES)
def test_verb_json_flag_overrides_tty(args, target, payload):
    """--json wins over the interactive renderer on a TTY."""
    with patch(target, new_callable=AsyncMock, return_value=payload):
        result = _tty_invoke([*args, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_json_and_plain_together_errors(mock_cat):
    """The contradictory --json --plain combination is a clear non-zero error."""
    result = _tty_invoke(["cat", "specs/search", "--json", "--plain"])

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output
    mock_cat.assert_not_called()


# ---------------------------------------------------------------------------
# cat / head rendering
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_cat_rich_output(mock_cat):
    result = _tty_invoke(["cat", "specs/search"])

    assert result.exit_code == 0, result.output
    _assert_not_json(result.output)
    assert "Search Spec" in result.output
    assert "ranking notes" in result.output


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_cat_plain_output_is_exact_content(mock_cat):
    """Plain stdout is the content byte-for-byte: pipes and redirects round-trip."""
    result = _tty_invoke(["cat", "specs/search", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == CAT_RESULT["content"]
    assert result.stderr == ""


@patch(
    "basic_memory.mcp.tools.cat",
    new_callable=AsyncMock,
    return_value={**CAT_RESULT, "content": None},
)
def test_cat_plain_output_without_content_is_empty(mock_cat):
    result = _tty_invoke(["cat", "specs/search", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_SLICE_RESULT)
def test_cat_rich_slice_footer(mock_cat):
    """A ranged read shows its coordinates so follow-up range reads are easy."""
    result = _tty_invoke(["cat", "specs/search", "--lines", "5-7"])

    assert result.exit_code == 0, result.output
    assert "lines 5-7 of 7" in _flattened(result.output)


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_SLICE_RESULT)
def test_cat_plain_slice_footer_goes_to_stderr(mock_cat):
    """Plain mode keeps stdout content-only; slice info lands on stderr."""
    result = _tty_invoke(["cat", "specs/search", "--lines", "5-7", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == CAT_SLICE_RESULT["content"]
    assert "lines 5-7 of 7" in result.stderr
    # The slice content carries no trailing newline, so the footer must open
    # with one on stderr — otherwise it visually concatenates onto the last
    # content line in a terminal while stdout stays byte-exact for pipes.
    assert result.stderr.startswith("\n")


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_TRUNCATED_RESULT)
def test_cat_rich_truncation_footer(mock_cat):
    """A truncated read names the section, the range, and the resume command."""
    result = _tty_invoke(["cat", "specs/search", "--section", "Decisions", "--max-tokens", "20"])

    flat = _flattened(result.output)
    assert result.exit_code == 0, result.output
    assert "lines 5-40 of 80" in flat
    assert "section Decisions" in flat
    assert "truncated" in flat
    assert "--lines 42-" in flat


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_TRUNCATED_RESULT)
def test_cat_plain_truncation_keeps_stdout_pure(mock_cat):
    result = _tty_invoke(["cat", "specs/search", "--max-tokens", "20", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == CAT_TRUNCATED_RESULT["content"]
    assert "--lines 42-" in result.stderr


@patch(
    "basic_memory.mcp.tools.cat",
    new_callable=AsyncMock,
    return_value={**CAT_RESULT, "start_line": 3, "end_line": 4},
)
def test_cat_footer_without_total_lines(mock_cat):
    """A slice payload without total_lines still reports the range, without ' of N'."""
    result = _tty_invoke(["cat", "specs/search", "--lines", "3-4"])

    flat = _flattened(result.output)
    assert result.exit_code == 0, result.output
    assert "lines 3-4" in flat
    assert "lines 3-4 of" not in flat


@patch(
    "basic_memory.mcp.tools.cat",
    new_callable=AsyncMock,
    return_value={**CAT_RESULT, "truncated": True},
)
def test_cat_footer_truncated_without_continue_line(mock_cat):
    result = _tty_invoke(["cat", "specs/search", "--max-tokens", "20"])

    flat = _flattened(result.output)
    assert result.exit_code == 0, result.output
    assert "truncated" in flat
    assert "--lines" not in flat


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_head_plain_output(mock_cat):
    """head shares cat's payload and renderers."""
    result = _tty_invoke(["head", "specs/search", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == CAT_RESULT["content"]


# ---------------------------------------------------------------------------
# cat / head argument pass-through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lines", "expected_start", "expected_end"),
    [("5-10", 5, 10), ("5-", 5, None), ("5", 5, 5)],
)
@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_cat_lines_forms_parse_to_start_end(mock_cat, lines, expected_start, expected_end):
    result = _invoke(["cat", "specs/search", "--lines", lines])

    assert result.exit_code == 0, result.output
    assert mock_cat.call_args.args == ("specs/search",)
    assert mock_cat.call_args.kwargs["start_line"] == expected_start
    assert mock_cat.call_args.kwargs["end_line"] == expected_end


@pytest.mark.parametrize("bad_lines", ["abc", "5-x", "-5"])
@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_cat_lines_garbage_is_a_clear_error(mock_cat, bad_lines):
    result = _invoke(["cat", "specs/search", "--lines", bad_lines])

    assert result.exit_code == 1
    assert "N-M" in result.output
    mock_cat.assert_not_called()


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_cat_section_and_budget_passthrough(mock_cat):
    result = _invoke(
        ["cat", "specs/search", "--section", "Decisions", "--max-tokens", "200", "--no-frontmatter"]
    )

    assert result.exit_code == 0, result.output
    kwargs = mock_cat.call_args.kwargs
    assert kwargs["section"] == "Decisions"
    assert kwargs["max_tokens"] == 200
    assert kwargs["include_frontmatter"] is False
    assert kwargs["start_line"] is None
    assert kwargs["end_line"] is None


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_cat_project_passthrough(mock_cat):
    uuid = "11111111-1111-1111-1111-111111111111"
    result = _invoke(["cat", "specs/search", "--project", "research", "--project-id", uuid])

    assert result.exit_code == 0, result.output
    assert mock_cat.call_args.kwargs["project"] == "research"
    assert mock_cat.call_args.kwargs["project_id"] == uuid


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_head_is_a_fixed_range_cat(mock_cat):
    """head -n 3 is cat with start_line=1, end_line=3."""
    result = _invoke(["head", "specs/search", "-n", "3"])

    assert result.exit_code == 0, result.output
    assert mock_cat.call_args.args == ("specs/search",)
    assert mock_cat.call_args.kwargs["start_line"] == 1
    assert mock_cat.call_args.kwargs["end_line"] == 3


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.grep", new_callable=AsyncMock, return_value=GREP_RESULT)
def test_grep_rich_output(mock_grep):
    result = _tty_invoke(["grep", "ranking"])

    assert result.exit_code == 0, result.output
    _assert_not_json(result.output)
    # User-sourced bracketed titles survive Rich markup parsing.
    assert "[draft]" in result.output
    assert "Another Note" in result.output


@patch("basic_memory.mcp.tools.grep", new_callable=AsyncMock, return_value=GREP_RESULT)
def test_grep_plain_output(mock_grep):
    result = _tty_invoke(["grep", "ranking", "--plain"])

    assert result.exit_code == 0, result.output
    assert "1. Spec [draft] v2" in result.output
    assert "─" not in result.output
    assert "│" not in result.output


@patch("basic_memory.mcp.tools.grep", new_callable=AsyncMock, return_value=GREP_RESULT_EMPTY)
def test_grep_rich_empty(mock_grep):
    result = _tty_invoke(["grep", "nothing"])

    assert result.exit_code == 0, result.output
    assert "No results found" in result.output


@patch("basic_memory.mcp.tools.grep", new_callable=AsyncMock, return_value=GREP_RESULT)
def test_grep_literal_and_paging_passthrough(mock_grep):
    """-F requests literal full-text matching instead of semantic search."""
    result = _invoke(["grep", "needle", "-F", "--page", "2", "--page-size", "5"])

    assert result.exit_code == 0, result.output
    assert mock_grep.call_args.args == ("needle",)
    kwargs = mock_grep.call_args.kwargs
    assert kwargs["literal"] is True
    assert kwargs["page"] == 2
    assert kwargs["page_size"] == 5


@patch("basic_memory.mcp.tools.grep", new_callable=AsyncMock, return_value=GREP_RESULT)
def test_grep_defaults_to_semantic(mock_grep):
    result = _invoke(["grep", "needle"])

    assert result.exit_code == 0, result.output
    assert mock_grep.call_args.kwargs["literal"] is False


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.ls", new_callable=AsyncMock, return_value=LS_RESULT)
def test_ls_rich_output(mock_ls):
    result = _tty_invoke(["ls", "/specs"])

    flat = _flattened(result.output)
    assert result.exit_code == 0, result.output
    _assert_not_json(result.output)
    assert "specs/" in result.output  # directories get a trailing slash
    assert "[draft]" in result.output  # bracketed titles survive escaping
    assert "specs/search-spec" in flat
    assert "page 1" in flat
    assert "total 2" in flat


@patch("basic_memory.mcp.tools.ls", new_callable=AsyncMock, return_value=LS_RESULT_MORE)
def test_ls_rich_reports_more_pages(mock_ls):
    result = _tty_invoke(["ls", "/specs"])

    assert result.exit_code == 0, result.output
    assert "more available" in _flattened(result.output)


@patch("basic_memory.mcp.tools.ls", new_callable=AsyncMock, return_value=LS_RESULT_EMPTY)
def test_ls_rich_empty_directory(mock_ls):
    result = _tty_invoke(["ls", "/empty"])

    assert result.exit_code == 0, result.output
    assert "Empty directory." in result.output


@patch("basic_memory.mcp.tools.ls", new_callable=AsyncMock, return_value=LS_RESULT)
def test_ls_plain_is_one_path_per_line(mock_ls):
    result = _tty_invoke(["ls", "/specs", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "/specs/\n/specs/Search Spec.md\n"


@patch("basic_memory.mcp.tools.ls", new_callable=AsyncMock, return_value=LS_RESULT)
def test_ls_defaults_and_paging_passthrough(mock_ls):
    result = _invoke(["ls", "--page", "2", "--page-size", "50"])

    assert result.exit_code == 0, result.output
    assert mock_ls.call_args.args == ("/",)  # default path is the project root
    assert mock_ls.call_args.kwargs["page"] == 2
    assert mock_ls.call_args.kwargs["page_size"] == 50


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=FIND_RESULT)
def test_find_rich_output(mock_find):
    result = _tty_invoke(["find", "/specs", "--name", "*.md"])

    flat = _flattened(result.output)
    assert result.exit_code == 0, result.output
    _assert_not_json(result.output)
    assert "*.md" in result.output  # the glob shows in the panel title
    assert "specs/search.md" in flat
    assert "[draft]" in result.output


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=FIND_RESULT_EMPTY)
def test_find_rich_no_matches(mock_find):
    # No --name: the panel title carries no glob suffix on this path.
    result = _tty_invoke(["find", "/specs"])

    assert result.exit_code == 0, result.output
    assert "No matches." in result.output
    assert "--name" not in result.output


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=FIND_RESULT)
def test_find_plain_is_one_path_per_line(mock_find):
    """find(1) style: file_path when present, directory_path for directories."""
    result = _tty_invoke(["find", "/specs", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "/specs\nspecs/search.md\n"


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=FIND_RESULT)
def test_find_name_and_depth_passthrough(mock_find):
    result = _invoke(["find", "/specs", "--name", "*.md", "--depth", "3"])

    assert result.exit_code == 0, result.output
    assert mock_find.call_args.args == ("/specs",)
    assert mock_find.call_args.kwargs["name"] == "*.md"
    assert mock_find.call_args.kwargs["depth"] == 3


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.tail", new_callable=AsyncMock, return_value=TAIL_RESULT)
def test_tail_rich_output(mock_tail):
    result = _tty_invoke(["tail"])

    assert result.exit_code == 0, result.output
    _assert_not_json(result.output)
    assert "Note A" in result.output
    assert "[draft]" in result.output
    assert "notes/note-a" in _flattened(result.output)


@patch("basic_memory.mcp.tools.tail", new_callable=AsyncMock, return_value=[])
def test_tail_rich_empty(mock_tail):
    result = _tty_invoke(["tail"])

    assert result.exit_code == 0, result.output
    assert "No recent changes." in result.output


@patch("basic_memory.mcp.tools.tail", new_callable=AsyncMock, return_value=TAIL_RESULT)
def test_tail_plain_is_tab_separated(mock_tail):
    result = _tty_invoke(["tail", "--plain"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "2025-01-02T10:00:00\tentity\tNote A\tnotes/note-a\tnotes/Note A.md"
    assert lines[1].startswith("2025-01-01T09:00:00\tentity\tNote [draft] B\t")


@patch("basic_memory.mcp.tools.tail", new_callable=AsyncMock, return_value=TAIL_RESULT)
def test_tail_lines_and_timeframe_passthrough(mock_tail):
    result = _invoke(["tail", "-n", "5", "--timeframe", "1d"])

    assert result.exit_code == 0, result.output
    assert mock_tail.call_args.kwargs["lines"] == 5
    assert mock_tail.call_args.kwargs["timeframe"] == "1d"


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=TREE_RESULT)
def test_tree_plain_rebuilds_nesting_from_flat_nodes(mock_find):
    """The API page is flat; nesting is rebuilt from directory_path segments,
    including a synthesized parent for files whose directory node was filtered
    out by the glob."""
    result = _tty_invoke(["tree", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "/\n  specs/\n    search.md\n    auth/\n      deep.md\n"


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=TREE_RESULT)
def test_tree_rich_output(mock_find):
    result = _tty_invoke(["tree"])

    assert result.exit_code == 0, result.output
    _assert_not_json(result.output)
    assert "specs/" in result.output
    assert "search.md" in result.output
    assert "deep.md" in result.output


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=TREE_ROOTED_RESULT)
def test_tree_rooted_path_skips_the_root_node(mock_find):
    """The search root's own node must not render as its own child."""
    result = _tty_invoke(["tree", "/specs", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "/specs\n  intro.md\n"


@patch(
    "basic_memory.mcp.tools.find",
    new_callable=AsyncMock,
    return_value=TREE_DIR_AFTER_FILE_RESULT,
)
def test_tree_directory_node_after_synthesized_intermediate(mock_find):
    """A directory listed after a file already implied it stays one directory."""
    result = _tty_invoke(["tree", "--plain"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "/\n  specs/\n    search.md\n"


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=TREE_RESULT_MORE)
def test_tree_rich_reports_more_entries(mock_find):
    result = _tty_invoke(["tree"])

    assert result.exit_code == 0, result.output
    assert "more entries" in _flattened(result.output)


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=TREE_RESULT_MORE)
def test_tree_plain_more_entries_note_goes_to_stderr(mock_find):
    result = _tty_invoke(["tree", "--plain"])

    assert result.exit_code == 0, result.output
    assert "more entries" not in result.stdout
    assert "more entries" in result.stderr


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=TREE_RESULT_EMPTY)
def test_tree_rich_empty(mock_find):
    result = _tty_invoke(["tree"])

    assert result.exit_code == 0, result.output
    assert "empty" in result.output


@patch("basic_memory.mcp.tools.find", new_callable=AsyncMock, return_value=TREE_RESULT)
def test_tree_passes_find_arguments_through(mock_find):
    result = _invoke(["tree", "/specs", "--name", "*.md", "--depth", "2"])

    assert result.exit_code == 0, result.output
    assert mock_find.call_args.args == ("/specs",)
    assert mock_find.call_args.kwargs["name"] == "*.md"
    assert mock_find.call_args.kwargs["depth"] == 2


# ---------------------------------------------------------------------------
# Errors and routing (shared command scaffold, exercised per verb)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("args", "target", "payload"), VERB_CASES)
def test_verb_tool_error_exits_nonzero(args, target, payload):
    """A strict-resolve miss (ToolError) becomes stderr + exit 1, per verb."""
    with patch(target, new_callable=AsyncMock, side_effect=ToolError("Entity not found: nope")):
        result = _invoke(args)

    assert result.exit_code == 1
    assert "Error: Entity not found: nope" in result.output


@patch(
    "basic_memory.mcp.tools.cat",
    new_callable=AsyncMock,
    side_effect=ValueError("start_line must be >= 1, got 0"),
)
def test_verb_value_error_exits_nonzero(mock_cat):
    """The tool's own argument validation surfaces as a user-facing error."""
    result = _invoke(["cat", "specs/search"])

    assert result.exit_code == 1
    assert "Error: start_line must be >= 1" in result.output


@patch("basic_memory.mcp.tools.cat", new_callable=AsyncMock, return_value=CAT_RESULT)
def test_local_and_cloud_together_errors(mock_cat):
    result = _invoke(["cat", "specs/search", "--local", "--cloud"])

    assert result.exit_code == 1
    assert "Cannot specify both --local and --cloud" in result.output
    mock_cat.assert_not_called()


@patch("basic_memory.mcp.tools.tail", new_callable=AsyncMock)
def test_local_flag_forces_local_routing_during_the_call(mock_tail):
    """--local sets BASIC_MEMORY_FORCE_LOCAL for exactly the tool call's duration."""
    seen: dict[str, str | None] = {}

    async def capture(*args, **kwargs):
        seen["force_local"] = os.environ.get("BASIC_MEMORY_FORCE_LOCAL")
        return []

    mock_tail.side_effect = capture
    result = _invoke(["tail", "--local"])

    assert result.exit_code == 0, result.output
    assert seen["force_local"] == "true"
