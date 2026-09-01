"""Tests for the POSIX-style read-side MCP tools: cat, grep, ls, find, tail, man (#1399).

Each tool is a thin translation over the same typed clients the canonical tools
use, so these tests run the real ASGI stack via the shared `client` fixture and
assert on the JSON shapes the canonical `output_format="json"` paths produce.
"""

import asyncio
import re
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest
import pytest_asyncio
import yaml
from fastmcp.exceptions import ToolError

import basic_memory.mcp.tools.posix_tools as posix_tools
from basic_memory.mcp.project_context import (
    ProjectPrefixConflictError,
    UnqualifiedPathRefusedError,
)
from basic_memory.mcp.tools import cat, find, grep, ls, man, search_notes, tail, write_note
from basic_memory.repository.metadata_filters import ParsedMetadataFilter, parse_metadata_filters
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


# --- find --meta: predicate parsing ---
# Each predicate string translates onto exactly one metadata_filters entry, in
# the grammar the search API's parse_metadata_filters already supports.

PREDICATE_GRAMMAR = [
    ("status=active", {"status": "active"}),
    ("status = active", {"status": "active"}),
    ("confidence>0.6", {"confidence": {"$gt": 0.6}}),
    ("confidence >= 0.6", {"confidence": {"$gte": 0.6}}),
    ("confidence<0.6", {"confidence": {"$lt": 0.6}}),
    ("confidence<=0.6", {"confidence": {"$lte": 0.6}}),
    ("priority in high,critical", {"priority": {"$in": ["high", "critical"]}}),
    ("priority in high, critical", {"priority": {"$in": ["high", "critical"]}}),
    ("tags has security,oauth", {"tags": ["security", "oauth"]}),
    ("score between 0.3,0.8", {"score": {"$between": [0.3, 0.8]}}),
    # Quoting is the documented escape for literal values, and it holds inside
    # a list: the comma it protects belongs to the value, not to the list.
    ('label in "a,b",c', {"label": {"$in": ["a,b", "c"]}}),
    # A backslash-escaped quote stays inside the value; it neither closes the
    # token nor leaves it looking unterminated.
    ('label in "a\\"b",c', {"label": {"$in": ['a"b', "c"]}}),
    ('note="say \\"hi\\""', {"note": 'say "hi"'}),
    ('tags has "red, green"', {"tags": ["red, green"]}),
    ('name in "quoted"', {"name": {"$in": ["quoted"]}}),
    # An unquoted value may not start with an operator character (a mis-spelled
    # operator is the far likelier reading), so quoting is how a value that
    # genuinely does start with one is expressed — scalars and list elements alike.
    ('range=">=5"', {"range": ">=5"}),
    ('range>"<=5"', {"range": {"$gt": "<=5"}}),
    ('bound in ">=5","<=9"', {"bound": {"$in": [">=5", "<=9"]}}),
    ('marks has "<a>","=b"', {"marks": ["<a>", "=b"]}),
    # Quoting is also the escape for the tokens the grammar refuses unquoted:
    # the non-finite number spellings and null outside equality.
    ('score="NaN"', {"score": "NaN"}),
    ('score>"Infinity"', {"score": {"$gt": "Infinity"}}),
    ('owner in "null","alice"', {"owner": {"$in": ["null", "alice"]}}),
    # Dot-paths address nested frontmatter and pass through verbatim.
    ("review.approved=true", {"review.approved": True}),
    # The one alias search_notes carries, so both surfaces accept one spelling.
    ("note_type=spec", {"type": "spec"}),
]


@pytest.mark.parametrize(("predicate", "expected"), PREDICATE_GRAMMAR)
def test_parse_meta_predicate_grammar(predicate, expected):
    assert posix_tools._parse_meta_predicates([predicate]) == expected


@pytest.mark.parametrize(("predicate", "expected"), PREDICATE_GRAMMAR)
def test_parsed_predicates_are_valid_api_metadata_filters(predicate, expected):
    """Every predicate the parser accepts is a filter the search API accepts.

    parse_metadata_filters is the server-side authority; running the produced
    dict through it proves the CLI/MCP grammar is a strict subset rather than a
    parallel dialect that only fails at request time.
    """
    assert parse_metadata_filters(posix_tools._parse_meta_predicates([predicate]))


@pytest.mark.parametrize(
    ("predicate", "expected_value"),
    [
        ("done=true", True),
        ("done=false", False),
        ("owner=null", None),
        ("count=3", 3),
        ("ratio=1.5", 1.5),
        ("status=active", "active"),
        # A JSON-quoted token forces the literal string, escaping the inference.
        ('status="true"', "true"),
        # Non-scalar JSON is not a filter value; the raw text stays a string.
        ("shape=[1,2]", "[1,2]"),
    ],
)
def test_predicate_values_are_json_scalar_inferred(predicate, expected_value):
    """Values type-infer so a predicate string produces the same dict a rich
    search_notes caller would pass as JSON."""
    key = predicate.split("=", 1)[0]

    assert posix_tools._parse_meta_predicates([predicate]) == {key: expected_value}


def test_parse_meta_predicates_and_together():
    assert posix_tools._parse_meta_predicates(["status=active", "confidence>0.6"]) == {
        "status": "active",
        "confidence": {"$gt": 0.6},
    }


@pytest.mark.parametrize(
    ("predicates", "message"),
    [
        # The API has no $ne, so != is deliberately absent from the grammar.
        (["status!=active"], "unsupported predicate operator in 'status!=active'"),
        (["status ~= active"], "unsupported predicate operator"),
        (["priority gte 3"], "unsupported predicate operator"),
        (["priority in"], "unsupported predicate operator"),
        (["nothing"], "unsupported predicate operator"),
        (["score between 0.3"], "'between' needs exactly min,max"),
        (["score between 0.1,0.2,0.3"], "'between' needs exactly min,max"),
        (["priority in high,,low"], "empty list element"),
        # A severed quote would silently filter for values nothing carries.
        (['priority in "high,low'], "unterminated quoted value"),
        (['tags has red,"green'], "unterminated quoted value"),
        (["status="], "has no value"),
        # Non-finite numbers and null-outside-equality: both used to reach the
        # server (or the request encoder) as a query nothing could answer.
        (["score=NaN"], "non-finite number"),
        (["score>null"], "uses null with '>'"),
        (["status=active", "status=draft"], "duplicate predicate key 'status'"),
        # The alias collapses onto the same key, so the collision is still caught.
        (["note_type=note", "type=spec"], "duplicate predicate key 'type'"),
    ],
)
def test_parse_meta_predicates_fails_fast(predicates, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        posix_tools._parse_meta_predicates(predicates)


def test_unsupported_operator_names_the_supported_set():
    """The refusal teaches the whole grammar instead of just rejecting."""
    with pytest.raises(ValueError, match="supported: = > >= < <= in has between"):
        posix_tools._parse_meta_predicates(["status matches active"])


def test_duplicate_key_refusal_points_at_between():
    with pytest.raises(ValueError, match=re.escape("use 'between' for ranges")):
        posix_tools._parse_meta_predicates(["score>0.3", "score<0.8"])


@pytest.mark.parametrize(
    "predicate",
    [
        # Symbol operators: the regex matches the longest SUPPORTED spelling,
        # so the second operator character used to land at the head of the value.
        "status==active",
        "status=>active",
        "status=<active",
        "count>>3",
        "count>=>3",
        "count<<1",
        "count<=<1",
        # Word operators fold the same way — everything after them is the value.
        "priority in >high",
        "tags has =security",
        "score between >0.3,0.8",
        "score between 0.3,<0.8",
    ],
)
def test_malformed_operators_refuse_instead_of_folding_into_the_value(predicate):
    """REGRESSION: a mis-spelled multi-character operator is a refusal, not a value.

    'status==active' used to parse as {"status": "=active"} and 'count>>3' as
    {"count": {"$gt": ">3"}} — filters for text no note carries, so the caller
    got an empty (or worse, a non-empty but wrong) result set where the grammar
    documents an unsupported-operator error.
    """
    with pytest.raises(ValueError, match="unsupported predicate operator"):
        posix_tools._parse_meta_predicates([predicate])


def test_malformed_operator_refusal_teaches_the_grammar_and_the_escape():
    """The refusal names the supported set and the quoting escape, in one message."""
    with pytest.raises(ValueError) as excinfo:
        posix_tools._parse_meta_predicates(["status==active"])

    message = str(excinfo.value)
    assert "unsupported predicate operator in 'status==active'" in message
    assert "supported: = > >= < <= in has between" in message
    assert 'quote the value as "=active"' in message


@pytest.mark.parametrize(
    "predicate",
    [
        # Python's JSON reader accepts these three spellings as an extension...
        "score=NaN",
        "score=Infinity",
        "score=-Infinity",
        # ...and silently overflows an oversized exponent to infinity.
        "score=1e999",
        "score=-1e999",
        # Every operator reads its values through the same scalar reader.
        "score>NaN",
        "score<=Infinity",
        "score in 0.5,NaN",
        "marks has NaN",
        "score between NaN,0.8",
        "score between 0.3,1e999",
    ],
)
def test_non_finite_numbers_refuse_instead_of_failing_at_transport(predicate):
    """REGRESSION: a non-finite number died in the request encoder, not the grammar.

    json.loads builds a real float for NaN/Infinity/-Infinity and overflows
    1e999 to inf, so the parser accepted them into the filters dict. Nothing
    rejected them until httpx serialized the request body and raised "Out of
    range float values are not JSON compliant" — a transport failure standing in
    for a predicate typo, naming neither find nor the offending predicate.
    """
    with pytest.raises(ValueError, match="non-finite number"):
        posix_tools._parse_meta_predicates([predicate])


def test_non_finite_refusal_names_the_predicate_and_the_escape():
    """The refusal is shaped like the grammar's others: what, where, and the way out."""
    with pytest.raises(ValueError) as excinfo:
        posix_tools._parse_meta_predicates(["score=NaN"])

    message = str(excinfo.value)
    assert "predicate 'score=NaN' has a non-finite number 'NaN'" in message
    assert "predicate values must be finite numbers" in message
    assert 'quote the value as "NaN"' in message


@pytest.mark.parametrize(
    "predicate",
    [
        'status="active',
        'status = "active',
        'confidence>"0.6',
        # A quote that opens, closes, and opens again is still unterminated.
        'name="a"b"c',
        # The list operators reach the same check through their split elements.
        'priority in "high,low',
        'tags has red,"green',
        'score between "0.3,0.8',
    ],
)
def test_a_dangling_quote_refuses_for_every_operator(predicate):
    """REGRESSION: the scalar path used to keep the dangling quote as the value.

    'status="active' fails json.loads, and the raw-text fallback then filtered
    for the literal seven characters '"active' — a search that runs, matches
    nothing, and reports an ordinary empty result with the typo buried in it.
    The list operators already refused a severed quote; one shared check now
    gives both paths the same answer.
    """
    with pytest.raises(ValueError, match="unterminated quoted value"):
        posix_tools._parse_meta_predicates([predicate])


def test_unterminated_quote_refusal_teaches_both_uses_of_quoting():
    with pytest.raises(ValueError) as excinfo:
        posix_tools._parse_meta_predicates(['status="active'])

    message = str(excinfo.value)
    assert "predicate 'status=\"active' has an unterminated quoted value" in message
    assert 'status="active"' in message
    assert 'label in "a,b",c' in message


@pytest.mark.parametrize(
    "predicate",
    [
        "score>null",
        "score>=null",
        "score<null",
        "score<=null",
        "priority in null,high",
        "tags has null",
        "score between null,0.8",
    ],
)
def test_null_refuses_outside_equality(predicate):
    """null only compiles to a query through '='.

    Every other operator compares against its value, and a SQL comparison with
    NULL is never true — so these would answer a confident zero for every note
    in the project rather than name the query the search cannot express.
    """
    with pytest.raises(ValueError, match=re.escape("null matches only as equality")):
        posix_tools._parse_meta_predicates([predicate])


def test_null_equality_reaches_the_api_as_an_is_null_clause():
    """'owner=null' is the one null spelling the grammar keeps, and it is not equality.

    The API parser turns it into an IS NULL clause; an ordinary equality clause
    would be `= NULL`, which no row satisfies.
    """
    filters = posix_tools._parse_meta_predicates(["owner=null"])

    assert filters == {"owner": None}
    assert parse_metadata_filters(filters) == [ParsedMetadataFilter(["owner"], "is_null", None)]


@pytest.mark.parametrize(
    ("entity_metadata", "fields", "expected"),
    [
        ({"title": "Alpha"}, ["title"], {"title": "Alpha"}),
        ({"review": {"approved": True}}, ["review.approved"], {"review.approved": True}),
        # A missing key renders as null — the row is never dropped.
        ({"title": "Alpha"}, ["missing"], {"missing": None}),
        # A non-dict intermediate ends the walk at null rather than raising.
        ({"review": "yes"}, ["review.approved"], {"review.approved": None}),
        (None, ["title"], {"title": None}),
        ({"a": {"b": {"c": 1}}}, ["a.b.c", "a.b"], {"a.b.c": 1, "a.b": {"c": 1}}),
    ],
)
def test_project_metadata_fields(entity_metadata, fields, expected):
    assert posix_tools._project_metadata_fields(entity_metadata, fields) == expected


# --- find --meta: metadata search arm ---


@pytest_asyncio.fixture
async def meta_notes(client, test_project):
    """Seed metadata-bearing notes across two directories (one name has a space)."""
    await write_note(
        title="Alpha Spec",
        directory="specs",
        content="# Alpha Spec\n\nalpha body",
        project=test_project.name,
        metadata={
            "status": "active",
            "priority": "high",
            "confidence": 0.9,
            "tags": ["security", "oauth"],
            "review": {"approved": True},
        },
    )
    await write_note(
        title="Beta Spec",
        directory="specs",
        content="# Beta Spec\n\nbeta body",
        project=test_project.name,
        metadata={
            "status": "draft",
            "priority": "low",
            "confidence": 0.2,
            "tags": ["security"],
        },
    )
    await write_note(
        title="Gamma Note",
        directory="My Notes",
        content="# Gamma Note\n\ngamma body",
        project=test_project.name,
        metadata={
            "status": "active",
            "priority": "critical",
            "confidence": 0.5,
            "tags": ["oauth"],
        },
    )


@pytest_asyncio.fixture
async def diverged_permalink_notes(client, test_project):
    """Seed two notes whose permalinks do not mirror their file paths.

    An explicit frontmatter `permalink:` is honored verbatim (#93), which is the
    honest construction here: the shared test config sets
    update_permalinks_on_move=True, so a move would NOT reproduce the split the
    product's own default (False) produces in the field.

    One note lives under specs/ but permalinks under archive/; the other is its
    mirror image. Any scope built from permalinks answers this pair exactly
    backwards, which is what the regression below pins down.
    """
    await write_note(
        title="Housed In Specs",
        directory="specs",
        content=dedent("""
            ---
            permalink: archive/housed-in-specs
            status: active
            ---

            # Housed In Specs
        """).strip(),
        project=test_project.name,
    )
    await write_note(
        title="Housed In Archive",
        directory="archive",
        content=dedent("""
            ---
            permalink: specs/housed-in-archive
            status: active
            ---

            # Housed In Archive
        """).strip(),
        project=test_project.name,
    )


@pytest.mark.asyncio
async def test_find_meta_returns_the_search_response_shape(client, test_project, meta_notes):
    result = await find(meta=["status=active"], project=test_project.name)

    assert set(result) == {
        "results",
        "current_page",
        "page_size",
        "total",
        "total_is_exact",
        "has_more",
    }
    assert result["total"] == 2
    assert result["total_is_exact"] is True
    assert {row["title"] for row in result["results"]} == {"Alpha Spec", "Gamma Note"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("meta", "expected_titles"),
    [
        (["status=active"], {"Alpha Spec", "Gamma Note"}),
        (["confidence>0.5"], {"Alpha Spec"}),
        (["confidence>=0.5"], {"Alpha Spec", "Gamma Note"}),
        (["confidence<0.5"], {"Beta Spec"}),
        (["confidence<=0.5"], {"Beta Spec", "Gamma Note"}),
        (["priority in high,critical"], {"Alpha Spec", "Gamma Note"}),
        # `has` is contains-ALL, so a two-element list narrows to one note.
        (["tags has security"], {"Alpha Spec", "Beta Spec"}),
        (["tags has security,oauth"], {"Alpha Spec"}),
        (["confidence between 0.1,0.6"], {"Beta Spec", "Gamma Note"}),
        (["review.approved=true"], {"Alpha Spec"}),
        # Only Alpha Spec carries review.approved, so null is its complement.
        (["review.approved=null"], {"Beta Spec", "Gamma Note"}),
        (["note_type=note"], {"Alpha Spec", "Beta Spec", "Gamma Note"}),
        # Repeated predicates AND together.
        (["status=active", "priority=high"], {"Alpha Spec"}),
        (["status=active", "priority=nonexistent"], set()),
    ],
)
async def test_find_meta_operators_select_the_right_notes(
    client, test_project, meta_notes, meta, expected_titles
):
    result = await find(meta=meta, project=test_project.name)

    assert {row["title"] for row in result["results"]} == expected_titles


@pytest.mark.asyncio
async def test_find_meta_null_finds_the_notes_carrying_no_value(client, test_project, meta_notes):
    """REGRESSION: 'key=null' answers "which notes have no value here?".

    It used to compile to `= NULL`, which no row satisfies, so find reported an
    exact total of zero however many notes were missing the field — a wrong
    answer wearing the same confident `total_is_exact` as a right one. Asserting
    the complementary query in the same test keeps the null side honest: a
    filter that matched nothing would pass a bare exclusion check.
    """
    absent = await find(meta=["review.approved=null"], project=test_project.name)
    present = await find(meta=["review.approved=true"], project=test_project.name)

    assert {row["title"] for row in absent["results"]} == {"Beta Spec", "Gamma Note"}
    assert absent["total"] == 2
    assert absent["total_is_exact"] is True
    assert {row["title"] for row in present["results"]} == {"Alpha Spec"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", ""])
async def test_find_meta_answers_at_the_project_root(client, test_project, meta_notes, path):
    """Both root spellings reach the metadata search; the predicates are the whole WHERE."""
    result = await find(path, meta=["status=active"], project=test_project.name)

    assert {row["title"] for row in result["results"]} == {"Alpha Spec", "Gamma Note"}
    assert result["total_is_exact"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_titles"),
    [
        ("specs", {"Alpha Spec", "Beta Spec"}),
        ("/specs", {"Alpha Spec", "Beta Spec"}),
        # A spaced directory name is a file path, not a slug: it scopes verbatim.
        ("My Notes", {"Gamma Note"}),
        ("nonexistent", set()),
    ],
)
async def test_find_meta_scopes_by_path_subtree(
    client, test_project, meta_notes, path, expected_titles
):
    """`path` narrows the metadata query to one directory, server-side."""
    result = await find(path, meta=["note_type=note"], project=test_project.name)

    assert {row["title"] for row in result["results"]} == expected_titles
    assert result["total"] == len(expected_titles)
    assert result["total_is_exact"] is True


@pytest.mark.asyncio
async def test_find_meta_scope_stops_at_a_directory_boundary(client, test_project, meta_notes):
    """A sibling directory whose name starts with the scope stays out."""
    await write_note(
        title="Archived Spec",
        directory="specs-archive",
        content="# Archived Spec",
        project=test_project.name,
        metadata={"status": "active"},
    )

    result = await find("specs", meta=["status=active"], project=test_project.name)

    assert {row["title"] for row in result["results"]} == {"Alpha Spec"}


@pytest.mark.asyncio
async def test_scope_follows_the_file_path_not_the_permalink(
    client, test_project, diverged_permalink_notes
):
    """REGRESSION: `find /specs --meta` scopes by where the file lives.

    A permalink stops mirroring its file path once a note pins one in
    frontmatter (#93) or is moved with update_permalinks_on_move disabled (the
    default). A scope built from permalink prefixes therefore answers this pair
    exactly backwards: it would drop "Housed In Specs", which really is under
    specs/, and admit "Housed In Archive", which is not — while still reporting
    that count as exact. Scoping by the indexed file_path answers the question
    the caller asked, so reintroducing a permalink-based scope fails here.
    """
    result = await find("specs", meta=["status=active"], project=test_project.name)

    rows = {row["title"]: row for row in result["results"]}
    assert set(rows) == {"Housed In Specs"}
    assert result["total"] == 1
    assert result["total_is_exact"] is True
    # The hit is the one whose *file* is under specs/, and its permalink is not.
    assert rows["Housed In Specs"]["file_path"] == "specs/Housed In Specs.md"
    assert rows["Housed In Specs"]["permalink"].endswith("archive/housed-in-specs")

    # The mirror image: permalinked under specs/, but housed under archive/.
    archive = await find("archive", meta=["status=active"], project=test_project.name)
    archive_rows = {row["title"]: row for row in archive["results"]}
    assert set(archive_rows) == {"Housed In Archive"}
    assert archive_rows["Housed In Archive"]["file_path"] == "archive/Housed In Archive.md"
    assert archive_rows["Housed In Archive"]["permalink"].endswith("specs/housed-in-archive")


@pytest.mark.asyncio
async def test_find_meta_paginates_with_exact_totals(client, test_project, meta_notes):
    """Scope and predicates AND inside one server-side WHERE, so the total is
    the real match count and every page is reachable."""
    first = await find(meta=["status=active"], page_size=1, project=test_project.name)
    second = await find(meta=["status=active"], page=2, page_size=1, project=test_project.name)

    assert first["total"] == 2
    assert first["total_is_exact"] is True
    assert first["has_more"] is True
    assert len(first["results"]) == 1
    assert second["has_more"] is False
    assert first["results"][0]["permalink"] != second["results"][0]["permalink"]


@pytest.mark.asyncio
async def test_find_meta_projects_requested_fields(client, test_project, meta_notes):
    """Projection reads the entity's normalized frontmatter; a field a hit does
    not carry renders as null instead of dropping the row."""
    result = await find(
        meta=["status=active"],
        fields=["title", "priority", "review.approved", "missing_field"],
        project=test_project.name,
    )

    projected = {row["title"]: row["fields"] for row in result["results"]}
    assert projected["Alpha Spec"] == {
        "title": "Alpha Spec",
        "priority": "high",
        # Frontmatter round-trips the nested boolean as its stored string form.
        "review.approved": "True",
        "missing_field": None,
    }
    assert projected["Gamma Note"] == {
        "title": "Gamma Note",
        "priority": "critical",
        "review.approved": None,
        "missing_field": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        # The search API has no filename-glob facility, so a `name` pattern has
        # no faithful translation — refuse rather than silently ignore it.
        ({"name": "*.md", "meta": ["status=active"]}, "'name' cannot combine with 'meta'"),
        # The file-path scope is whole-subtree; a depth bound is inexpressible.
        ({"depth": 3, "meta": ["status=active"]}, "'depth' cannot combine with 'meta'"),
        ({"fields": ["title"]}, "'fields' requires 'meta' predicates"),
        ({"meta": ["status=active"], "fields": []}, "must be non-empty"),
        ({"meta": ["status=active"], "fields": ["  "]}, "must be non-empty"),
        # An empty predicate list is not "no filter": it would parse to {} and
        # run the metadata search with no WHERE at all, matching every note in
        # the project where the caller asked for a filtered set.
        ({"meta": []}, "'meta' must carry at least one predicate"),
    ],
)
async def test_find_meta_combination_rules_refuse_before_io(kwargs, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        await find(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page": 0}, "page must be >= 1"),
        ({"page_size": 0}, "page_size must be >= 1"),
        ({"page_size": 201}, "page_size must be <= 200"),
    ],
)
async def test_find_meta_rejects_bad_pagination(kwargs, message):
    """The metadata arm bounds pagination exactly as the listing arm does.

    `find_listing` states those bounds for the directory arm, and the metadata
    arm never reaches it — so `find` states them again on the branch it does
    take. Unstated, a `page=0` would route, open a client, and come back as a
    transport error from the search API instead of the refusal the plain
    listing gives for the same argument. No project fixture here is the point:
    the refusal lands before any I/O.
    """
    with pytest.raises(ValueError, match=re.escape(message)):
        await find(meta=["status=active"], **kwargs)


@pytest.mark.asyncio
async def test_find_meta_quoted_list_element_keeps_its_comma(client, test_project):
    """The quoting escape reaches the list operators, not just the scalar ones.

    Splitting the raw value before scalar inference would sever '"a,b"' into
    '"a' and 'b"' and filter for values nothing carries — no error, no hits.
    """
    await write_note(
        title="Comma Note",
        directory="notes",
        content="# Comma Note",
        project=test_project.name,
        metadata={"label": "a,b"},
    )

    result = await find(meta=['label in "a,b",c'], project=test_project.name)

    assert {row["title"] for row in result["results"]} == {"Comma Note"}


@pytest.mark.asyncio
async def test_find_meta_quoting_reaches_operator_prefixed_values(client, test_project):
    """Quoting is the escape for a value that starts with an operator character.

    Unquoted, 'range=>=5' reads as a malformed operator and is refused; quoted,
    the same value is matched literally — proved against the real stack so the
    documented escape hatch is not just a parser property.
    """
    await write_note(
        title="Range Note",
        directory="notes",
        content="# Range Note",
        project=test_project.name,
        metadata={"range": ">=5"},
    )

    with pytest.raises(ValueError, match="unsupported predicate operator"):
        await find(meta=["range=>=5"], project=test_project.name)

    result = await find(meta=['range=">=5"'], project=test_project.name)

    assert {row["title"] for row in result["results"]} == {"Range Note"}


@pytest.mark.asyncio
async def test_find_meta_empty_list_is_not_an_unfiltered_search(client, test_project, meta_notes):
    """meta=[] must never widen to the whole project.

    The MCP surface can produce it directly (`"meta": []`, or `"meta": "[]"`
    through coerce_list), so the refusal is asserted against the real stack —
    where an unfiltered metadata search would return all three seeded notes.
    """
    with pytest.raises(ValueError, match=re.escape("'meta' must carry at least one predicate")):
        await find(meta=[], project=test_project.name)


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "expected_peak"), [(8, 2), (1, 1)])
async def test_find_meta_fields_hydrates_hits_concurrently(
    client, test_project, meta_notes, monkeypatch, limit, expected_peak
):
    """Projection costs one entity GET per hit (no bulk read exists), so the
    reads must overlap up to the bound instead of serializing — a full page
    serialized is up to MAX_DIRECTORY_PAGE_SIZE round trips inside one call."""
    # Import here to mirror the tool's own deferred client import.
    from basic_memory.mcp.clients import KnowledgeClient

    monkeypatch.setattr(posix_tools, "_FIELD_PROJECTION_CONCURRENCY", limit)
    original_get_entity = KnowledgeClient.get_entity
    in_flight = 0
    peak = 0

    async def counting_get_entity(self, entity_id, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            # Yield once so an overlap, if any, is observable deterministically.
            await asyncio.sleep(0)
            return await original_get_entity(self, entity_id, **kwargs)
        finally:
            in_flight -= 1

    monkeypatch.setattr(KnowledgeClient, "get_entity", counting_get_entity)

    result = await find(meta=["status=active"], fields=["title"], project=test_project.name)

    assert peak == expected_peak
    # Order still pairs each hit with its own entity, concurrency notwithstanding.
    assert {row["title"]: row["fields"]["title"] for row in result["results"]} == {
        "Alpha Spec": "Alpha Spec",
        "Gamma Note": "Gamma Note",
    }


@pytest.mark.asyncio
async def test_find_meta_unsupported_key_surfaces_the_api_error(client, test_project, meta_notes):
    """The server owns key validation; its refusal reaches the caller intact."""
    with pytest.raises(ToolError, match="metadata filter key"):
        await find(meta=["status.=active"], project=test_project.name)


@pytest.mark.asyncio
async def test_find_meta_fields_requires_hit_external_ids(client, test_project, monkeypatch):
    """Projection hydrates each hit by external_id, so a server old enough to
    omit it fails fast instead of silently returning unprojected rows."""
    # Import here to mirror the tool's own deferred client import.
    from basic_memory.mcp.clients import SearchClient
    from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

    async def legacy_search(self, query, page=1, page_size=10):
        return SearchResponse(
            results=[
                SearchResult(
                    title="Alpha Spec",
                    type=SearchItemType.ENTITY,
                    score=1.0,
                    permalink="test-project/specs/alpha-spec",
                    file_path="specs/Alpha Spec.md",
                )
            ],
            current_page=page,
            page_size=page_size,
            total=1,
        )

    monkeypatch.setattr(SearchClient, "search", legacy_search)

    with pytest.raises(ToolError, match="server too old for field projection"):
        await find(meta=["status=active"], fields=["title"], project=test_project.name)


@pytest.mark.asyncio
async def test_find_without_meta_stays_the_directory_listing(client, test_graph, test_project):
    """The no-meta arm is untouched: same payload with the new params defaulted
    or passed as None, and no search-shape keys anywhere."""
    baseline = await find(name="*.md", project=test_project.name)
    with_defaults = await find(name="*.md", meta=None, fields=None, project=test_project.name)

    assert with_defaults == baseline
    assert set(baseline) == {"nodes", "page", "page_size", "total", "has_more"}
    assert baseline["total"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("meta", "filters"),
    [
        (["status=active"], {"status": "active"}),
        (["confidence>0.5"], {"confidence": {"$gt": 0.5}}),
        (["priority in high,critical"], {"priority": {"$in": ["high", "critical"]}}),
        (["tags has security,oauth"], {"tags": ["security", "oauth"]}),
        (["confidence between 0.1,0.6"], {"confidence": {"$between": [0.1, 0.6]}}),
        (["review.approved=true"], {"review.approved": True}),
    ],
)
async def test_find_meta_matches_search_notes_parity(
    client, test_project, meta_notes, meta, filters
):
    """PARITY: the POSIX surface and the rich surface answer the same question
    identically — same filters dict, same hits — through the same real stack."""
    assert posix_tools._parse_meta_predicates(meta) == filters

    found = await find(meta=meta, project=test_project.name)
    searched = await search_notes(
        project=test_project.name,
        metadata_filters=filters,
        output_format="json",
    )

    assert isinstance(searched, dict)
    assert found["results"]
    assert {row["permalink"] for row in found["results"]} == {
        row["permalink"] for row in searched["results"]
    }


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
async def test_find_meta_qualified_path_routes_to_project(
    client, test_project, second_project, no_project_constraint
):
    """The meta arm shares find's route resolution, so '<project>/dir' scopes to
    that project's subtree and nothing from the other project leaks in.

    A project prefix is a mount point, not a subtree: a bare 'second-project'
    strips to the empty project-relative path and answers at that project's
    root, while 'second-project/notes' strips to the 'notes' subtree of the
    same project.
    """
    await write_note(
        title="Second Meta Note",
        directory="notes",
        content="# Second Meta Note",
        project="second-project",
        metadata={"status": "active"},
    )
    await write_note(
        title="Home Meta Note",
        directory="notes",
        content="# Home Meta Note",
        project=test_project.name,
        metadata={"status": "active"},
    )

    await write_note(
        title="Second Root Note",
        directory="specs",
        content="# Second Root Note",
        project="second-project",
        metadata={"status": "active"},
    )

    root = await find("second-project", meta=["status=active"])
    scoped = await find("second-project/notes", meta=["status=active"])

    assert {row["title"] for row in root["results"]} == {"Second Meta Note", "Second Root Note"}
    assert {row["title"] for row in scoped["results"]} == {"Second Meta Note"}


@pytest.mark.asyncio
async def test_find_meta_unqualified_refuses_in_multi_project_config(
    client, test_project, second_project, no_project_constraint
):
    """A metadata query is still project-scoped, so an unqualified call in a
    multi-project config refuses with the active project list."""
    with pytest.raises(UnqualifiedPathRefusedError, match="no project specified"):
        await find(meta=["status=active"])


@pytest.mark.asyncio
async def test_find_meta_predicate_errors_precede_routing(
    client, test_project, second_project, no_project_constraint
):
    """Predicate parsing happens before any routing decision, so a bad operator
    reports itself instead of hiding behind the multi-project refusal."""
    with pytest.raises(ValueError, match="unsupported predicate operator") as excinfo:
        await find(meta=["status!=active"])
    assert not isinstance(excinfo.value, UnqualifiedPathRefusedError)


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


@pytest.mark.asyncio
async def test_routed_cat_never_rewrites_note_frontmatter(
    client, test_project, second_project, no_project_constraint
):
    """Re-qualification touches transport metadata, never note content.

    Frontmatter is the author's own YAML and may legitimately carry keys spelled
    like transport fields. Rewriting by key name anywhere in the payload turned
    `file_path: imports/source.md` into `second-project/imports/source.md`, so a
    routed read returned frontmatter that disagreed with both its own `content`
    and the file on disk. Position, not spelling, separates the two.
    """
    await write_note(
        title="Imported Note",
        directory="notes",
        content="# Imported Note\n\nbody",
        project="second-project",
        metadata={"file_path": "imports/source.md", "directory_path": "/imports"},
    )

    payload = await cat("second-project/notes/imported-note")

    # The stored file is the authority: parse its frontmatter block and require
    # the response to reproduce it exactly.
    stored = (Path(second_project.path) / "notes" / "Imported Note.md").read_text()
    _, _, rest = stored.partition("---\n")
    block, _, _ = rest.partition("\n---")
    assert yaml.safe_load(block) == payload["frontmatter"]
    assert payload["frontmatter"]["file_path"] == "imports/source.md"
    assert payload["frontmatter"]["directory_path"] == "/imports"

    # The transport path is still qualified, so the round trip holds.
    assert payload["file_path"] == "second-project/notes/Imported Note.md"
    assert (await cat(payload["file_path"]))["title"] == "Imported Note"
