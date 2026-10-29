"""`bm cat/grep/ls/find/tail` against a real project, end to end.

Each test seeds notes with `bm tool write`, runs the POSIX verb through the CLI,
and asserts on its `--json` payload (the stable machine contract) or on the
refusal it prints. Argument refusals that land before any I/O live in
tests/cli/test_cli_posix_argument_errors.py.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml

from typer.testing import CliRunner, Result

from basic_memory import db
from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.clients import SearchClient
from basic_memory.models import Entity
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

pytestmark = pytest.mark.usefixtures("app")

runner = CliRunner()

PROJECT = ["--project", "test-project"]


# --- CLI helpers ---


def bm(*args: str, env: dict[str, str] | None = None) -> Result:
    return runner.invoke(cli_app, list(args), env=env)


def bm_json(*args: str, env: dict[str, str] | None = None) -> Any:
    """Run a command with --json and return its parsed payload."""
    result = bm(*args, "--json", env=env)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def refusal(*args: str, env: dict[str, str] | None = None) -> str:
    """Run a command that must fail and return what it printed on stderr."""
    result = bm(*args, env=env)
    assert result.exit_code == 1, result.output
    return result.stderr


def write_note(
    title: str,
    directory: str,
    content: str,
    *,
    project: str = "test-project",
    metadata: dict[str, Any] | None = None,
) -> None:
    args = ["tool", "write", "--title", title, "--directory", directory, "--content", content]
    if metadata is not None:
        args += ["--metadata", json.dumps(metadata)]
    bm_json(*args, "--project", project)


def titles(payload: dict[str, Any]) -> set[str]:
    return {row["title"] for row in payload["results"]}


# --- Fixtures ---


@pytest.fixture
def test_graph(app) -> None:
    """Five notes under test/, the shape the old ASGI graph fixture seeded."""
    write_note("Deeper Entity", "test", "# Deeper Entity")
    write_note("Deep Entity", "test", "# Deep Entity\n- deeper_connection [[Deeper Entity]]")
    write_note(
        "Connected Entity 2", "test", "# Connected Entity 2\n- deep_connection [[Deep Entity]]"
    )
    write_note(
        "Connected Entity 1",
        "test",
        "# Connected Entity 1\n- [note] Connected 1 note\n- connected_to [[Connected Entity 2]]",
    )
    write_note("Root", "test", "# Root\n- connects_to [[Connected Entity 1]]")


@pytest.fixture
def meta_notes(app) -> None:
    """Metadata-bearing notes across two directories (one name has a space)."""
    write_note(
        "Alpha Spec",
        "specs",
        "# Alpha Spec\n\nalpha body",
        metadata={
            "status": "active",
            "priority": "high",
            "confidence": 0.9,
            "chapter_number": 63,
            "tags": ["security", "oauth"],
            "review": {"approved": True},
        },
    )
    write_note(
        "Beta Spec",
        "specs",
        "# Beta Spec\n\nbeta body",
        metadata={
            "status": "draft",
            "priority": "low",
            "confidence": 0.2,
            "tags": ["security"],
        },
    )
    write_note(
        "Gamma Note",
        "My Notes",
        "# Gamma Note\n\ngamma body",
        metadata={
            "status": "active",
            "priority": "critical",
            "confidence": 0.5,
            "tags": ["oauth"],
        },
    )


@pytest.fixture
def second_project(app, tmp_path_factory) -> Path:
    """Register a second project so unqualified input becomes ambiguous."""
    # Outside the first project's home: nested project paths are refused.
    path = tmp_path_factory.mktemp("second-project-home")
    result = bm("project", "add", "second-project", str(path))
    assert result.exit_code == 0, result.output
    return path


# --- cat ---


def test_cat_returns_full_note_round_trip():
    write_note("Cat Note", "test", "# Cat Note\n\nline one\nline two")

    result = bm_json("cat", "Cat Note", *PROJECT)

    assert result["title"] == "Cat Note"
    assert result["file_path"] == "test/Cat Note.md"
    assert "line one" in result["content"]
    assert "line two" in result["content"]
    assert result["frontmatter"]["title"] == "Cat Note"
    # No range requested: the payload carries no slice bookkeeping.
    assert "start_line" not in result
    assert "end_line" not in result
    assert "total_lines" not in result


def test_cat_frontmatter_toggle():
    write_note("Cat Frontmatter Note", "test", "body text only")

    with_frontmatter = bm_json("cat", "Cat Frontmatter Note", *PROJECT)
    without_frontmatter = bm_json("cat", "Cat Frontmatter Note", "--no-frontmatter", *PROJECT)

    assert with_frontmatter["content"].startswith("---")
    assert not without_frontmatter["content"].startswith("---")
    assert "body text only" in without_frontmatter["content"]


def test_cat_line_ranges_slice_the_body():
    write_note("Cat Range Note", "test", "alpha\nbravo\ncharlie\ndelta")
    body = ["cat", "Cat Range Note", "--no-frontmatter", *PROJECT]
    lines = bm_json(*body)["content"].splitlines()

    ranged = bm_json(*body, "--lines", "2-3")
    assert ranged["content"] == "\n".join(lines[1:3])
    assert (ranged["start_line"], ranged["end_line"], ranged["total_lines"]) == (2, 3, len(lines))

    # "N-" runs to the end.
    to_end = bm_json(*body, "--lines", "2-")
    assert to_end["content"] == "\n".join(lines[1:])
    assert (to_end["start_line"], to_end["end_line"]) == (2, len(lines))

    # "N" is one line.
    one = bm_json(*body, "--lines", "3")
    assert one["content"] == lines[2]

    # An end past the last line clamps to it.
    clamped = bm_json(*body, "--lines", "1-999")
    assert clamped["content"] == "\n".join(lines)
    assert clamped["end_line"] == clamped["total_lines"] == len(lines)


def test_cat_start_line_past_end_is_an_error():
    """Both slicing paths reject a range that starts after the last line (#1634)."""
    write_note("Cat Past End Note", "test", "alpha\nbravo")
    body_lines = len(
        bm_json("cat", "Cat Past End Note", "--no-frontmatter", *PROJECT)["content"].splitlines()
    )
    full_lines = len(bm_json("cat", "Cat Past End Note", *PROJECT)["content"].splitlines())

    # Plain line ranges slice the payload client-side.
    client_side = refusal(
        "cat", "Cat Past End Note", "--no-frontmatter", "--lines", "500-", *PROJECT
    )
    assert f"start_line 500 is past the end of the document ({body_lines} lines)" in client_side

    # --max-tokens sends the range to the server-side slicer.
    server_side = refusal(
        "cat", "Cat Past End Note", "--lines", "500-", "--max-tokens", "50", *PROJECT
    )
    assert f"start_line 500 is past the end of the document ({full_lines} lines)" in server_side


def test_cat_section_returns_exact_span():
    write_note("Cat Section Note", "test", "# Guide\nintro\n## First\nalpha\n## Second\nbeta")
    full_lines = bm_json("cat", "Cat Section Note", *PROJECT)["content"].splitlines()

    result = bm_json("cat", "Cat Section Note", "--section", "First", *PROJECT)

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


def test_cat_section_path_and_bracket_forms():
    write_note("Cat Section Paths", "test", "# Auth\n## Decisions\na\n# Ops\n## Decisions\nb")
    write_note("Cat Section Duplicates", "test", "# Spec\n## Auth\nfirst\n## Auth\nsecond")

    by_parent = bm_json("cat", "Cat Section Paths", "--section", "Ops/Decisions", *PROJECT)
    assert by_parent["section"] == "Ops/Decisions"
    assert by_parent["content"].splitlines() == ["## Decisions", "b"]

    duplicate = bm_json("cat", "Cat Section Duplicates", "--section", "Auth[1]", *PROJECT)
    assert duplicate["section"] == "Spec/Auth[1]"
    assert duplicate["content"].splitlines() == ["## Auth", "second"]


def test_cat_unknown_section_lists_available_headings():
    write_note("Cat Section Missing", "test", "# Guide\n## First\nalpha")

    stderr = refusal("cat", "Cat Section Missing", "--section", "Nope", *PROJECT)

    assert "Available sections" in stderr
    assert "Guide/First" in stderr


def test_cat_max_tokens_truncates_and_resumes():
    write_note("Cat Token Budget", "test", "# One\n" + "a" * 40 + "\n# Two\n" + "b" * 40)
    full_lines = bm_json("cat", "Cat Token Budget", *PROJECT)["content"].splitlines()

    truncated = bm_json("cat", "Cat Token Budget", "--max-tokens", "20", *PROJECT)

    assert truncated["truncated"] is True
    marker = truncated["content"].splitlines()[-1]
    assert "truncated at max_tokens=20" in marker
    assert f"continue with lines={truncated['continue_line']}-" in marker
    kept_lines = truncated["content"].splitlines()[:-1]

    # Resume flow: a follow-up range read from continue_line returns exactly
    # the remainder, reconstructing the document body.
    rest = bm_json("cat", "Cat Token Budget", "--lines", f"{truncated['continue_line']}-", *PROJECT)
    assert kept_lines + rest["content"].splitlines() == full_lines[truncated["start_line"] - 1 :]


def test_cat_max_tokens_with_line_range_matches_the_client_side_range():
    write_note("Cat Combined Slice", "test", "alpha\nbravo\ncharlie\ndelta")
    full_lines = bm_json("cat", "Cat Combined Slice", *PROJECT)["content"].splitlines()

    server_side = bm_json(
        "cat", "Cat Combined Slice", "--lines", "2-", "--max-tokens", "1000", *PROJECT
    )

    assert server_side["content"] == "\n".join(full_lines[1:])
    assert server_side["start_line"] == 2
    assert server_side["end_line"] == server_side["total_lines"] == len(full_lines)
    assert "truncated" not in server_side
    # The server-side slice agrees with the client-side range read byte for byte.
    client_side = bm_json("cat", "Cat Combined Slice", "--lines", "2-", *PROJECT)
    assert server_side["content"] == client_side["content"]


def test_cat_unknown_identifier_is_an_error():
    assert "Error:" in refusal("cat", "no-such-note-anywhere", *PROJECT)


# --- grep ---


def test_grep_keyword_finds_seeded_content():
    write_note("Grep Target", "test", "# Grep Target\n\nThe posixgrepneedle hides here.")

    result = bm_json("grep", "posixgrepneedle", *PROJECT)

    assert result["current_page"] == 1
    assert isinstance(result["total_is_exact"], bool)
    assert "Grep Target" in titles(result)


def test_grep_defaults_to_keyword_matching():
    """A term that appears nowhere lists nothing (#1685); nearest-neighbour
    ranking would have listed unrelated notes instead."""
    write_note("Grep Target", "test", "# Grep Target\n\nThe posixgrepneedle hides here.")

    result = bm_json("grep", "zzqqabsentterm", *PROJECT)

    assert result["results"] == []


# --- ls ---


def test_ls_project_root_listing(test_graph):
    # With a project addressed, "/" is the project root, not the mount table.
    result = bm_json("ls", *PROJECT)

    assert result["total"] == 1
    assert result["has_more"] is False
    assert result["nodes"][0]["name"] == "test"
    assert result["nodes"][0]["type"] == "directory"


def test_ls_directory_contents(test_graph):
    result = bm_json("ls", "/test", *PROJECT)

    assert result["total"] == 5
    assert {node["name"] for node in result["nodes"]} == {
        "Connected Entity 1.md",
        "Connected Entity 2.md",
        "Deep Entity.md",
        "Deeper Entity.md",
        "Root.md",
    }


def test_ls_empty_project():
    result = bm_json("ls", *PROJECT)

    assert result["total"] == 0
    assert result["nodes"] == []
    assert result["has_more"] is False


def test_ls_pagination(test_graph):
    first_page = bm_json("ls", "/test", "--page-size", "2", *PROJECT)
    last_page = bm_json("ls", "/test", "--page", "3", "--page-size", "2", *PROJECT)

    assert len(first_page["nodes"]) == 2
    assert first_page["has_more"] is True
    assert first_page["total"] == 5
    assert len(last_page["nodes"]) == 1
    assert last_page["has_more"] is False


# --- find ---


def test_find_glob_recurses_from_root(test_graph):
    result = bm_json("find", "--name", "*.md", *PROJECT)

    assert result["total"] == 5
    assert "Root.md" in {node["name"] for node in result["nodes"]}
    assert all(node["type"] == "file" for node in result["nodes"])


def test_find_without_name_lists_everything(test_graph):
    result = bm_json("find", *PROJECT)

    # The default depth recurses: the /test directory plus its five files.
    assert result["total"] == 6
    names = {node["name"] for node in result["nodes"]}
    assert "test" in names
    assert "Root.md" in names


def test_find_without_meta_stays_the_directory_listing(test_graph):
    """No search-shape keys anywhere when no predicate is given."""
    result = bm_json("find", "--name", "*.md", *PROJECT)

    assert set(result) == {"nodes", "page", "page_size", "total", "has_more"}


# --- find --meta ---


def test_find_meta_returns_the_search_response_shape(meta_notes):
    result = bm_json("find", "--meta", "status=active", *PROJECT)

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
    assert titles(result) == {"Alpha Spec", "Gamma Note"}


FIND_META_OPERATOR_CASES = [
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
]


def test_find_meta_operators_select_the_right_notes(meta_notes):
    for meta, expected_titles in FIND_META_OPERATOR_CASES:
        meta_args = [arg for predicate in meta for arg in ("--meta", predicate)]

        result = bm_json("find", *meta_args, *PROJECT)

        assert titles(result) == expected_titles, meta


def test_find_meta_null_finds_the_notes_carrying_no_value(meta_notes):
    """REGRESSION: 'key=null' answers "which notes have no value here?".

    It used to compile to `= NULL`, which no row satisfies, so find reported an
    exact total of zero however many notes were missing the field.
    """
    absent = bm_json("find", "--meta", "review.approved=null", *PROJECT)
    present = bm_json("find", "--meta", "review.approved=true", *PROJECT)

    assert titles(absent) == {"Beta Spec", "Gamma Note"}
    assert absent["total"] == 2
    assert absent["total_is_exact"] is True
    assert titles(present) == {"Alpha Spec"}


@pytest_asyncio.fixture
async def scanned_pdf(engine_factory, test_project, search_service) -> None:
    """An indexed regular file: an ENTITY row with no frontmatter keys at all."""
    _, session_maker = engine_factory
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        scan = await EntityRepository(project_id=test_project.id).add(
            session,
            Entity(
                project_id=test_project.id,
                title="Scanned Contract",
                note_type="file",
                content_type="application/pdf",
                file_path="specs/Scanned Contract.pdf",
                permalink="specs/scanned-contract",
                created_at=now,
                updated_at=now,
            ),
        )
    await search_service.index_entity_data(scan)


def test_find_meta_never_returns_a_non_markdown_file(meta_notes, scanned_pdf):
    """REGRESSION: `find --meta` is frontmatter-only, so a PDF is never a hit.

    A regular file carries no frontmatter keys, which is precisely what
    `key=null` asks for, so the null predicate used to return every PDF in the
    project and count it into the exact total.
    """
    absent = bm_json("find", "--meta", "review.approved=null", *PROJECT)
    present = bm_json("find", "--meta", "status=active", *PROJECT)

    assert titles(absent) == {"Beta Spec", "Gamma Note"}
    assert absent["total"] == 2
    assert absent["total_is_exact"] is True
    assert titles(present) == {"Alpha Spec", "Gamma Note"}


def test_find_meta_scopes_by_path_subtree(meta_notes):
    """The path argument narrows the metadata query to one directory, server-side."""
    cases = [
        # Both root spellings reach the metadata search unscoped.
        ("/", {"Alpha Spec", "Beta Spec", "Gamma Note"}),
        ("", {"Alpha Spec", "Beta Spec", "Gamma Note"}),
        ("specs", {"Alpha Spec", "Beta Spec"}),
        ("/specs", {"Alpha Spec", "Beta Spec"}),
        # "./" is relative notation, the same way the directory listing reads it.
        ("./specs", {"Alpha Spec", "Beta Spec"}),
        ("./specs/", {"Alpha Spec", "Beta Spec"}),
        # A spaced directory name is a file path, not a slug: it scopes verbatim.
        ("My Notes", {"Gamma Note"}),
        ("nonexistent", set()),
    ]
    for path, expected_titles in cases:
        result = bm_json("find", path, "--meta", "note_type=note", *PROJECT)

        assert titles(result) == expected_titles, path
        assert result["total"] == len(expected_titles), path
        assert result["total_is_exact"] is True, path


def test_find_meta_scope_stops_at_a_directory_boundary(meta_notes):
    """A sibling directory whose name starts with the scope stays out."""
    write_note("Archived Spec", "specs-archive", "# Archived Spec", metadata={"status": "active"})

    result = bm_json("find", "specs", "--meta", "status=active", *PROJECT)

    assert titles(result) == {"Alpha Spec"}


def test_find_meta_scope_follows_the_file_path_not_the_permalink():
    """REGRESSION: `find specs --meta` scopes by where the file lives.

    An explicit frontmatter permalink is honored verbatim (#93), so these two
    notes live under one directory and permalink under the other. A scope built
    from permalink prefixes answers this pair exactly backwards.
    """
    write_note(
        "Housed In Specs",
        "specs",
        "---\npermalink: archive/housed-in-specs\nstatus: active\n---\n\n# Housed In Specs",
    )
    write_note(
        "Housed In Archive",
        "archive",
        "---\npermalink: specs/housed-in-archive\nstatus: active\n---\n\n# Housed In Archive",
    )

    specs = bm_json("find", "specs", "--meta", "status=active", *PROJECT)
    rows = {row["title"]: row for row in specs["results"]}
    assert set(rows) == {"Housed In Specs"}
    assert specs["total"] == 1
    assert specs["total_is_exact"] is True
    assert rows["Housed In Specs"]["file_path"] == "specs/Housed In Specs.md"
    assert rows["Housed In Specs"]["permalink"].endswith("archive/housed-in-specs")

    archive = bm_json("find", "archive", "--meta", "status=active", *PROJECT)
    archive_rows = {row["title"]: row for row in archive["results"]}
    assert set(archive_rows) == {"Housed In Archive"}
    assert archive_rows["Housed In Archive"]["file_path"] == "archive/Housed In Archive.md"
    assert archive_rows["Housed In Archive"]["permalink"].endswith("specs/housed-in-archive")


def test_find_meta_paginates_with_exact_totals(meta_notes):
    """Scope and predicates AND inside one server-side WHERE, so the total is
    the real match count and every page is reachable."""
    first = bm_json("find", "--meta", "status=active", "--page-size", "1", *PROJECT)
    second = bm_json("find", "--meta", "status=active", "--page", "2", "--page-size", "1", *PROJECT)

    assert first["total"] == 2
    assert first["total_is_exact"] is True
    assert first["has_more"] is True
    assert len(first["results"]) == 1
    assert second["has_more"] is False
    assert first["results"][0]["permalink"] != second["results"][0]["permalink"]


def test_find_meta_projects_requested_fields(meta_notes):
    """Projection reads the canonical typed frontmatter; a field a hit does
    not carry renders as null instead of dropping the row."""
    result = bm_json(
        "find",
        "--meta",
        "status=active",
        "--fields",
        "title,priority,chapter_number,review.approved,tags,missing_field",
        *PROJECT,
    )

    projected = {row["title"]: row["fields"] for row in result["results"]}
    assert projected["Alpha Spec"] == {
        "title": "Alpha Spec",
        "priority": "high",
        "chapter_number": 63,
        "review.approved": True,
        "tags": ["security", "oauth"],
        "missing_field": None,
    }
    assert projected["Gamma Note"] == {
        "title": "Gamma Note",
        "priority": "critical",
        "chapter_number": None,
        "review.approved": None,
        "tags": ["oauth"],
        "missing_field": None,
    }


def test_find_meta_fields_returns_identity_and_projection_only(meta_notes):
    """REGRESSION: a projected row carried the whole note beside the projection.

    A projected row is the note's identity plus the fields requested, and
    nothing else: no note body reaches the caller by any key.
    """
    projected = bm_json("find", "--meta", "status=active", "--fields", "priority", *PROJECT)

    assert projected["results"], "fixture should produce hits to project"
    for row in projected["results"]:
        assert set(row) == {
            "title",
            "permalink",
            "file_path",
            "external_id",
            "updated_at",
            "fields",
        }
        assert row["file_path"] and row["permalink"] and row["external_id"]
        serialized = json.dumps(row)
        assert "alpha body" not in serialized
        assert "gamma body" not in serialized

    assert {row["title"]: row["fields"] for row in projected["results"]} == {
        "Alpha Spec": {"priority": "high"},
        "Gamma Note": {"priority": "critical"},
    }


def test_find_meta_without_fields_still_returns_the_full_search_shape(meta_notes):
    """The narrowing belongs to projection mode only; without --fields the hit
    keeps the search response `bm grep` renders, content included."""
    unprojected = bm_json("find", "--meta", "status=active", *PROJECT)

    assert unprojected["results"]
    for row in unprojected["results"]:
        assert "content" in row
        assert "fields" not in row


def test_find_meta_nested_field_path_projects(meta_notes):
    result = bm_json("find", "--meta", "status=active", "--fields", "review.approved", *PROJECT)

    projected = {row["title"]: row["fields"] for row in result["results"]}
    assert projected["Alpha Spec"] == {"review.approved": True}
    assert projected["Gamma Note"] == {"review.approved": None}


@pytest.mark.parametrize(
    "predicate",
    [f"score>{'9' * 400}", f"score<=-{'9' * 400}", f"score between 0,{'9' * 400}"],
)
def test_find_meta_oversized_integer_refuses_as_a_filter_error(meta_notes, predicate):
    """REGRESSION: a 400-digit comparison bound came back as a server error.

    json.loads keeps an oversized integer as a finite Python int, so it
    travelled and overflowed server-side; the search router now answers it as
    a filter refusal rather than a 500.
    """
    assert "not a finite number" in refusal("find", "--meta", predicate, *PROJECT)


def test_find_meta_quoted_list_element_keeps_its_comma():
    """The quoting escape reaches the list operators, not just the scalar ones."""
    write_note("Comma Note", "notes", "# Comma Note", metadata={"label": "a,b"})

    result = bm_json("find", "--meta", 'label in "a,b",c', *PROJECT)

    assert titles(result) == {"Comma Note"}


def test_find_meta_quoting_reaches_operator_prefixed_values():
    """Unquoted, 'range=>=5' is a malformed operator; quoted, the same value
    matches literally against the real stack."""
    write_note("Range Note", "notes", "# Range Note", metadata={"range": ">=5"})

    assert "unsupported predicate operator" in refusal("find", "--meta", "range=>=5", *PROJECT)

    result = bm_json("find", "--meta", 'range=">=5"', *PROJECT)

    assert titles(result) == {"Range Note"}


def test_find_meta_fields_requires_hit_external_ids(meta_notes, monkeypatch):
    """Projection hydrates each hit by external_id, so a server old enough to
    omit it fails fast instead of silently returning unprojected rows.

    The old server is simulated at the search client: no real server in this
    process can omit the field.
    """

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

    stderr = refusal("find", "--meta", "status=active", "--fields", "title", *PROJECT)

    assert "too old for `fields`" in stderr


@pytest.mark.parametrize(
    ("meta", "filters"),
    [
        ("status=active", {"status": "active"}),
        ("confidence>0.5", {"confidence": {"$gt": 0.5}}),
        ("priority in high,critical", {"priority": {"$in": ["high", "critical"]}}),
        ("tags has security,oauth", {"tags": ["security", "oauth"]}),
        ("confidence between 0.1,0.6", {"confidence": {"$between": [0.1, 0.6]}}),
        ("review.approved=true", {"review.approved": True}),
    ],
)
def test_find_meta_matches_search_filters_parity(meta_notes, meta, filters):
    """PARITY: `bm find --meta` and `bm tool search --filters` answer the same
    question identically through the same real stack."""
    found = bm_json("find", "--meta", meta, *PROJECT)
    searched = bm_json("tool", "search", "--filters", json.dumps(filters), *PROJECT)

    assert found["results"]
    assert {row["file_path"] for row in found["results"]} == {
        row["file_path"] for row in searched["results"]
    }


# --- tail ---


def test_tail_returns_recent_rows_newest_first(test_graph):
    rows = bm_json("tail", *PROJECT)

    assert rows
    for row in rows:
        assert set(row) == {"type", "title", "permalink", "file_path", "created_at", "updated_at"}
        assert row["type"] == "entity"
        assert isinstance(row["created_at"], str)
        # tail orders by last change, so each row says when that was.
        assert isinstance(row["updated_at"], str)
    changed = [row["updated_at"] for row in rows]
    assert changed == sorted(changed, reverse=True)
    assert "Root" in {row["title"] for row in rows}


def test_tail_lines_and_timeframe(test_graph):
    assert len(bm_json("tail", "-n", "2", *PROJECT)) == 2
    assert "Root" in {row["title"] for row in bm_json("tail", "--timeframe", "1d", *PROJECT)}


# --- project-qualified routing (#1415) ---
# Projects are mount points: '<project>/path' inputs route to that project, an
# explicit project must agree with a path prefix, and multi-project configs
# refuse unqualified input with the active project list instead of defaulting.


def test_ls_root_lists_projects_as_mount_points(second_project):
    """ls "/" with no project addressed is the mount table, sorted by name."""
    result = bm_json("ls")

    assert result["total"] == 2
    assert result["has_more"] is False
    assert [node["name"] for node in result["nodes"]] == ["second-project", "test-project"]
    for node in result["nodes"]:
        assert node["type"] == "directory"
        # directory_path is the copyable '/<project>' prefix form.
        assert node["directory_path"] == f"/{node['permalink']}"


def test_ls_root_mount_view_in_single_project_config(test_graph):
    """The mount view is unconditional (#1415): even one project lists the
    mount table at "/" so in-band discovery is uniform."""
    result = bm_json("ls")

    assert result["total"] == 1
    assert result["nodes"][0]["name"] == "test-project"
    assert result["nodes"][0]["directory_path"] == "/test-project"
    assert result["nodes"][0]["type"] == "directory"


def test_ls_mount_view_paginates_over_project_rows(second_project):
    first = bm_json("ls", "--page", "1", "--page-size", "1")
    last = bm_json("ls", "--page", "2", "--page-size", "1")

    assert first["total"] == 2
    assert first["has_more"] is True
    assert [node["name"] for node in first["nodes"]] == ["second-project"]
    assert [node["name"] for node in last["nodes"]] == ["test-project"]
    assert last["has_more"] is False


def test_qualified_paths_route_into_the_named_project(second_project):
    write_note("Second Dir Note", "notes", "# Second Dir Note", project="second-project")

    # A single segment naming a project lists that project's root.
    assert {node["name"] for node in bm_json("ls", "second-project")["nodes"]} == {"notes"}
    # '<project>/dir' lists that directory.
    assert {node["name"] for node in bm_json("ls", "/second-project/notes")["nodes"]} == {
        "Second Dir Note.md"
    }
    # find routes the same way.
    found = bm_json("find", "second-project", "--name", "*.md")
    assert {node["name"] for node in found["nodes"]} == {"Second Dir Note.md"}


def test_find_meta_qualified_path_routes_to_project(second_project):
    """A project prefix is a mount point: a bare 'second-project' answers at that
    project's root, 'second-project/notes' at its notes subtree, and nothing
    from the other project leaks in."""
    active = {"status": "active"}
    write_note("Second Meta Note", "notes", "# M", project="second-project", metadata=active)
    write_note("Home Meta Note", "notes", "# H", metadata=active)
    write_note("Second Root Note", "specs", "# R", project="second-project", metadata=active)

    root = bm_json("find", "second-project", "--meta", "status=active")
    scoped = bm_json("find", "second-project/notes", "--meta", "status=active")

    assert titles(root) == {"Second Meta Note", "Second Root Note"}
    assert titles(scoped) == {"Second Meta Note"}


def test_find_meta_unqualified_refuses_in_multi_project_config(second_project):
    assert "no project specified" in refusal("find", "--meta", "status=active")


def test_find_meta_predicate_errors_precede_routing(second_project):
    """A bad operator reports itself instead of hiding behind the refusal."""
    stderr = refusal("find", "--meta", "status!=active")

    assert "unsupported predicate operator" in stderr
    assert "active projects" not in stderr


def test_cat_qualified_identifier_equals_explicit_project_read(test_graph, second_project):
    """'test-project/test/root' with no --project reads the same note as
    --project test-project + 'test/root'.

    Each answers in its caller's addressing frame, so the file_path it hands
    back is one the same call shape accepts again. Content is what must match.
    """
    qualified = bm_json("cat", "test-project/test/root")
    explicit = bm_json("cat", "test/root", *PROJECT)

    assert qualified["title"] == explicit["title"] == "Root"
    assert qualified["content"] == explicit["content"]
    assert qualified["file_path"] == f"test-project/{explicit['file_path']}"
    assert bm_json("cat", qualified["file_path"])["title"] == "Root"
    assert bm_json("cat", explicit["file_path"], *PROJECT)["title"] == "Root"


def test_tail_permalink_round_trips_into_cat(second_project):
    """Stored permalinks are already project-qualified, so tail's output is a
    valid cat identifier with no --project anywhere, and the mount view's
    prefix is that permalink's first segment."""
    write_note("Round Trip", "notes", "# Round Trip\n\nround trip body", project="second-project")

    rows = bm_json("tail", "--project", "second-project")
    permalink = next(row["permalink"] for row in rows if row["title"] == "Round Trip")
    assert permalink == "second-project/notes/round-trip"

    mount_prefixes = {node["directory_path"] for node in bm_json("ls")["nodes"]}
    assert f"/{permalink.split('/', 1)[0]}" in mount_prefixes

    result = bm_json("cat", permalink)
    assert result["title"] == "Round Trip"
    assert "round trip body" in result["content"]


def test_explicit_project_with_agreeing_prefix_strips(test_graph, second_project):
    qualified = bm_json("ls", "/test-project/test", *PROJECT)
    relative = bm_json("ls", "/test", *PROJECT)

    # Same listing, each in its caller's addressing frame.
    assert qualified["total"] == relative["total"] == 5
    assert [node["name"] for node in qualified["nodes"]] == [
        node["name"] for node in relative["nodes"]
    ]
    assert [node["file_path"] for node in qualified["nodes"]] == [
        f"test-project/{node['file_path']}" for node in relative["nodes"]
    ]


def test_explicit_project_with_conflicting_prefix_refuses(second_project):
    """A disagreeing prefix is never silently resolved either way."""
    stderr = refusal("cat", "second-project/notes/foo", *PROJECT)

    assert "path names project 'second-project' but project 'test-project' was passed" in stderr


@pytest.mark.parametrize(
    ("args", "subject"),
    [
        (["cat", "notes/foo"], "no project 'notes'"),
        (["ls", "/notes"], "no project 'notes'"),
        (["find", "/x"], "no project 'x'"),
        (["grep", "needle"], "no project specified"),
        (["tail"], "no project specified"),
    ],
)
def test_unqualified_input_refuses_in_multi_project_config(second_project, args, subject):
    """Each verb refuses rather than silently defaulting, listing every active
    project in copyable prefix form."""
    stderr = refusal(*args)

    assert f"Error: {subject} — active projects: second-project/, test-project/" in stderr


def test_grep_argument_validation_precedes_refusal(second_project):
    assert "pattern must not be empty" in refusal("grep", "")


def test_single_project_unqualified_paths_pass_through(test_graph):
    """One configured project keeps today's ergonomics: unqualified paths route
    to the default project, and 'test' is not falsely stripped as a prefix of
    'test-project' (permalink comparison, not startswith)."""
    assert bm_json("ls", "/test")["total"] == 5
    assert bm_json("cat", "test/root")["title"] == "Root"
    assert "Root" in {row["title"] for row in bm_json("tail")}
    assert bm_json("grep", "Root")["results"]


def test_project_named_folder_is_reachable_double_qualified(second_project):
    """Collision rule: the project wins the first segment, so a top-level folder
    named like its own project is addressed by double-qualifying."""
    write_note(
        "Shadowed", "second-project", "# Shadowed\n\nshadowed body", project="second-project"
    )

    listing = bm_json("ls", "second-project/second-project")
    assert {node["name"] for node in listing["nodes"]} == {"Shadowed.md"}
    assert bm_json("cat", "second-project/second-project/shadowed")["title"] == "Shadowed"


def test_cat_bare_project_name_is_an_error(second_project):
    assert "names a project, not a note" in refusal("cat", "second-project")


def test_env_constraint_counts_as_explicit_project(test_graph, second_project):
    """BASIC_MEMORY_MCP_PROJECT participates exactly like --project: no refusal,
    agreeing prefixes strip, disagreeing prefixes conflict, and ls "/" lists
    the constrained project's root, not the mount table."""
    env = {"BASIC_MEMORY_MCP_PROJECT": "test-project"}

    assert "Root" in {row["title"] for row in bm_json("tail", env=env)}
    assert bm_json("ls", "/test-project/test", env=env)["total"] == 5
    assert {node["name"] for node in bm_json("ls", env=env)["nodes"]} == {"test"}
    assert "path names project 'second-project'" in refusal(
        "cat", "second-project/notes/foo", env=env
    )


def test_project_id_routes_without_prefix_parsing(test_graph, second_project, test_project):
    """--project-id routes by external UUID and bypasses prefix parsing, so a
    multi-project config needs no qualification."""
    result = bm_json("cat", "test/root", "--project-id", test_project.external_id)

    assert result["title"] == "Root"


# --- round-trip coherence: a returned path is an accepted path (#1421, #1435) ---


def test_ls_returned_paths_route_back_to_the_same_project(second_project):
    write_note("Second Root Note", "notes", "# Second Root Note", project="second-project")

    child = next(
        node for node in bm_json("ls", "second-project")["nodes"] if node["name"] == "notes"
    )
    assert child["directory_path"] == "/second-project/notes"

    # The advertised path is accepted verbatim, with no --project.
    nested = bm_json("ls", child["directory_path"])
    assert {node["name"] for node in nested["nodes"]} == {"Second Root Note.md"}


def test_find_returned_paths_route_back_to_the_same_project(second_project):
    write_note("Second Root Note", "notes", "# Second Root Note", project="second-project")

    listing = bm_json("find", "second-project")
    file_node = next(node for node in listing["nodes"] if node["type"] == "file")
    dir_node = next(node for node in listing["nodes"] if node["type"] == "directory")

    assert file_node["file_path"].startswith("second-project/")
    assert dir_node["directory_path"].startswith("/second-project")
    assert bm_json("cat", file_node["file_path"])["title"] == "Second Root Note"
    assert bm_json("ls", dir_node["directory_path"])["total"] >= 1


def test_find_meta_returned_paths_route_back_to_the_same_project(second_project):
    """find's metadata arm answers with search hits, and those paths must
    round-trip exactly like the listing arm's (#1435)."""
    write_note(
        "Second Meta Note",
        "notes",
        "# Second Meta Note",
        project="second-project",
        metadata={"status": "active"},
    )

    found = bm_json("find", "second-project/notes", "--meta", "status=active")
    row = next(item for item in found["results"] if item["title"] == "Second Meta Note")
    assert row["file_path"] == "second-project/notes/Second Meta Note.md"
    assert bm_json("cat", row["file_path"])["title"] == "Second Meta Note"

    # The projection narrows the row but not its address.
    projected = bm_json(
        "find", "second-project/notes", "--meta", "status=active", "--fields", "status"
    )
    projected_row = next(
        item for item in projected["results"] if item["title"] == "Second Meta Note"
    )
    assert projected_row["file_path"] == row["file_path"]
    assert projected_row["fields"] == {"status": "active"}


def test_unrouted_responses_keep_project_relative_paths(test_graph, second_project):
    """The prefix goes back only when the call put it in the path. An explicit
    --project is a different addressing frame: those paths are fed back with
    the same flag, so re-prefixing them would break that round trip."""
    write_note(
        "Second Meta Note",
        "notes",
        "# Second Meta Note",
        project="second-project",
        metadata={"status": "active"},
    )

    found = bm_json("find", "notes", "--meta", "status=active", "--project", "second-project")
    assert [row["file_path"] for row in found["results"]] == ["notes/Second Meta Note.md"]

    listing = bm_json("ls", "/test", *PROJECT)
    assert all(not node["file_path"].startswith("test-project/") for node in listing["nodes"])


def test_routed_cat_never_rewrites_note_frontmatter(second_project):
    """Re-qualification touches transport metadata, never note content.

    Frontmatter may carry keys spelled like transport fields; a routed read
    must return them exactly as stored.
    """
    write_note(
        "Imported Note",
        "notes",
        "# Imported Note\n\nbody",
        project="second-project",
        metadata={"file_path": "imports/source.md", "directory_path": "/imports"},
    )

    payload = bm_json("cat", "second-project/notes/imported-note")

    # The stored file is the authority: its frontmatter block must come back exactly.
    stored = (second_project / "notes" / "Imported Note.md").read_text()
    _, _, rest = stored.partition("---\n")
    block, _, _ = rest.partition("\n---")
    assert yaml.safe_load(block) == payload["frontmatter"]
    assert payload["frontmatter"]["file_path"] == "imports/source.md"
    assert payload["frontmatter"]["directory_path"] == "/imports"

    # The transport path is still qualified, so the round trip holds.
    assert payload["file_path"] == "second-project/notes/Imported Note.md"
    assert bm_json("cat", payload["file_path"])["title"] == "Imported Note"
