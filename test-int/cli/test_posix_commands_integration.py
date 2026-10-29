"""`bm cat/head/grep/ls/find/tree` against a real project, end to end.

Each verb is a thin command over a bm_* tool, so these tests check what the shell
adds (the --lines and --meta spellings, the tree drawing, the default project) and
that each verb reaches its tool's behavior. Notes are seeded with `bm tool write`.
Every CLI call costs a few hundred milliseconds, so each test seeds once and makes
several checks against that state: on Postgres each test also pays a few seconds
of schema reset, so related checks share a test. Argument refusals that land before any I/O live
in tests/cli/test_cli_posix_argument_errors.py.
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
from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.clients import SearchClient
from basic_memory.models import Entity
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

pytestmark = pytest.mark.usefixtures("app")

runner = CliRunner()

SECOND = ["--project", "second-project"]


# --- CLI helpers ---


def bm(*args: str) -> Result:
    return runner.invoke(cli_app, list(args))


def bm_text(*args: str) -> str:
    """Run a command and return its text output."""
    result = bm(*args)
    assert result.exit_code == 0, result.output
    return result.stdout


def bm_json(*args: str) -> Any:
    """Run a command with --json and return its parsed payload."""
    return json.loads(bm_text(*args, "--json"))


def refusal(*args: str) -> str:
    """Run a command that must fail and return what it printed on stderr."""
    result = bm(*args)
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


def meta_args(*predicates: str) -> list[str]:
    return [arg for predicate in predicates for arg in ("--meta", predicate)]


# --- Fixtures ---


def seed_graph() -> None:
    """Five notes under test/."""
    for title in ("Deeper Entity", "Deep Entity", "Connected Entity 2", "Connected Entity 1"):
        write_note(title, "test", f"# {title}")
    write_note("Root", "test", "# Root\n- connects_to [[Connected Entity 1]]")


def seed_meta_notes() -> None:
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
        metadata={"status": "draft", "priority": "low", "confidence": 0.2, "tags": ["security"]},
    )
    write_note(
        "Gamma Note",
        "My Notes",
        "# Gamma Note\n\ngamma body",
        metadata={"status": "active", "priority": "critical", "confidence": 0.5, "tags": ["oauth"]},
    )


@pytest.fixture
def second_project(app, tmp_path_factory) -> Path:
    """A second project, outside the first one's home (nested paths are refused)."""
    path = tmp_path_factory.mktemp("second-project-home")
    result = bm("project", "add", "second-project", str(path))
    assert result.exit_code == 0, result.output
    return path


# --- cat / head ---


def check_cat_whole_notes_and_slices() -> None:
    write_note("Cat Note", "test", "# Cat Note\n\nline one\nline two")
    note = ["cat", "Cat Note"]

    whole = bm_json(*note)
    assert whole["title"] == "Cat Note"
    assert whole["file_path"] == "test/Cat Note.md"
    assert whole["frontmatter"]["title"] == "Cat Note"
    assert whole["content"].startswith("---")
    assert "start_line" not in whole
    # The text result of a whole-note read is the note itself.
    assert bm_text(*note) == whole["content"] + "\n"
    # --no-frontmatter drops the YAML block from a whole-note read.
    body = bm_json(*note, "--no-frontmatter")["content"]
    assert not body.startswith("---")
    assert "line one" in body

    # --lines counts the whole document, frontmatter included, as bm_read does.
    lines = whole["content"].splitlines()
    ranged = bm_json(*note, "--lines", "2-3")
    assert ranged["content"] == "\n".join(lines[1:3])
    assert (ranged["start_line"], ranged["end_line"], ranged["total_lines"]) == (2, 3, len(lines))
    assert ranged["has_more"] is True
    assert bm_json(*note, "--lines", "2-")["content"] == "\n".join(lines[1:])
    assert bm_json(*note, "--lines", "3")["content"] == lines[2]
    clamped = bm_json(*note, "--lines", "1-999")
    assert clamped["content"] == "\n".join(lines)
    assert clamped["end_line"] == len(lines)
    # A range that starts after the last line is an error (#1634).
    assert f"is past the end of the document ({len(lines)} lines)" in refusal(
        *note, "--lines", "500-"
    )
    # The text result of a slice numbers its lines.
    assert f"3: {lines[2]}" in bm_text(*note, "--lines", "3")

    assert "Error:" in refusal("cat", "no-such-note-anywhere")


def check_cat_sections_and_token_budget() -> None:
    write_note("Sections", "test", "# Guide\nintro\n## First\nalpha\n## Second\nbeta")
    write_note("Paths", "test", "# Auth\n## Decisions\na\n# Ops\n## Decisions\nb")
    write_note("Duplicates", "test", "# Spec\n## Auth\nfirst\n## Auth\nsecond")
    write_note("Token Budget", "test", "# One\n" + "a" * 40 + "\n# Two\n" + "b" * 40)

    full_lines = bm_json("cat", "Sections")["content"].splitlines()
    section = bm_json("cat", "Sections", "--section", "First")
    assert section["section"] == "Guide/First"
    assert section["content"].splitlines() == ["## First", "alpha"]
    # Coordinates are document-absolute, so they address the same lines again.
    assert section["content"] == "\n".join(
        full_lines[section["start_line"] - 1 : section["end_line"]]
    )
    assert section["frontmatter"] is None

    by_parent = bm_json("cat", "Paths", "--section", "Ops/Decisions")
    assert by_parent["content"].splitlines() == ["## Decisions", "b"]
    duplicate = bm_json("cat", "Duplicates", "--section", "Auth[1]")
    assert duplicate["section"] == "Spec/Auth[1]"
    assert duplicate["content"].splitlines() == ["## Auth", "second"]
    missing = refusal("cat", "Sections", "--section", "Nope")
    assert "Available sections" in missing
    assert "Guide/First" in missing

    # A cut read says where to continue; reading from there rebuilds the rest.
    budget_lines = bm_json("cat", "Token Budget")["content"].splitlines()
    truncated = bm_json("cat", "Token Budget", "--max-tokens", "20")
    assert truncated["truncated"] is True
    kept = truncated["content"].splitlines()[:-1]
    assert "truncated at max_tokens=20" in truncated["content"].splitlines()[-1]
    rest = bm_json("cat", "Token Budget", "--lines", f"{truncated['continue_line']}-")
    assert kept + rest["content"].splitlines() == budget_lines[truncated["start_line"] - 1 :]


def check_head_prints_the_first_lines() -> None:
    write_note("Head Note", "test", "alpha\nbravo\ncharlie\ndelta")
    lines = bm_json("cat", "Head Note")["content"].splitlines()

    first_three = bm_json("head", "Head Note", "-n", "3")
    assert first_three["content"] == "\n".join(lines[:3])
    assert (first_three["start_line"], first_three["end_line"]) == (1, 3)
    assert f"1: {lines[0]}" in bm_text("head", "Head Note", "-n", "3")


@pytest_asyncio.fixture
async def text_source(engine_factory, test_project) -> None:
    """A .txt source kept verbatim in the project and indexed, as the
    literary-analysis skill sets up its book."""
    books = Path(test_project.path) / "books"
    books.mkdir()
    (books / "source.txt").write_text(
        "CHAPTER 1\nLoomings.\nCall me Ishmael.\nCHAPTER 2\nThe Carpet-Bag.\n"
    )
    _, session_maker = engine_factory
    runner = LocalProjectIndexRunner(
        project_repository=ProjectRepository(), session_maker=session_maker
    )
    await runner.index_project(test_project.id, force_full=True)


def check_text_source_reads_by_line() -> None:
    """An indexed .txt source reads a range at a time through cat and head."""
    chapter = bm_json("cat", "books/source.txt", "--lines", "2-3")
    assert chapter["content"] == "Loomings.\nCall me Ishmael."
    assert (chapter["start_line"], chapter["end_line"], chapter["total_lines"]) == (2, 3, 5)
    assert "3: Call me Ishmael." in bm_text("cat", "books/source.txt", "--lines", "2-3")
    assert bm_text("head", "books/source.txt", "-n", "1").startswith("Lines 1-1 of 5")
    # Sections are headings, which a text file does not have.
    assert "section and max_tokens apply only to notes" in refusal(
        "cat", "books/source.txt", "--section", "CHAPTER 1"
    )


def test_cat_and_head(text_source):
    check_cat_whole_notes_and_slices()
    check_cat_sections_and_token_budget()
    check_head_prints_the_first_lines()
    check_text_source_reads_by_line()


# --- grep ---


def test_grep_matches_keywords_and_lines():
    write_note("Grep Target", "test", "# Grep Target\n\nThe posixgrepneedle hides here.")
    write_note("Other Target", "other", "# Other Target\n\nposixgrepneedle again.")

    found = bm_json("grep", "posixgrepneedle")
    assert titles(found) == {"Grep Target", "Other Target"}
    # Keyword matching is the default (#1685): an absent term lists nothing.
    assert bm_json("grep", "zzqqabsentterm")["results"] == []
    # --path limits the search to one folder.
    assert titles(bm_json("grep", "posixgrepneedle", "--path", "test")) == {"Grep Target"}

    # -C returns the matching lines with their document line numbers.
    with_lines = bm_json("grep", "posixgrepneedle", "-C", "0", "--path", "test")
    [row] = with_lines["results"]
    assert row["file_path"] == "test/Grep Target.md"
    assert row["match_count"] == 1
    [window] = row["windows"]
    assert "posixgrepneedle" in window["content"]
    note_lines = bm_json("cat", "Grep Target")["content"].splitlines()
    assert note_lines[window["match_lines"][0] - 1] == window["content"]


# --- ls ---


def check_ls_lists_one_folder() -> None:
    root = bm_json("ls")
    assert [node["name"] for node in root["nodes"]] == ["test"]
    assert root["nodes"][0]["type"] == "directory"

    folder = bm_json("ls", "test")
    assert folder["total"] == 5
    assert {node["name"] for node in folder["nodes"]} == {
        "Connected Entity 1.md",
        "Connected Entity 2.md",
        "Deep Entity.md",
        "Deeper Entity.md",
        "Root.md",
    }

    first_page = bm_json("ls", "test", "--page-size", "2")
    last_page = bm_json("ls", "test", "--page", "3", "--page-size", "2")
    assert len(first_page["nodes"]) == 2
    assert first_page["has_more"] is True
    assert len(last_page["nodes"]) == 1
    assert last_page["has_more"] is False

    assert {node["name"] for node in bm_json("ls", "test", "--glob", "Deep*")["nodes"]} == {
        "Deep Entity.md",
        "Deeper Entity.md",
    }


def check_ls_and_tree_on_an_empty_project() -> None:
    result = bm_json("ls")

    assert result["total"] == 0
    assert result["nodes"] == []
    assert "empty" in bm_text("tree")


# --- find / tree (listing) ---


def check_find_and_tree_list_recursively() -> None:
    globbed = bm_json("find", "--name", "*.md")
    assert globbed["total"] == 5
    assert all(node["type"] == "file" for node in globbed["nodes"])
    assert set(globbed) == {"nodes", "page", "page_size", "total", "has_more"}

    # The default depth recurses: the test folder plus its five files.
    everything = bm_json("find")
    assert everything["total"] == 6

    # tree draws the same listing as a hierarchy; --json is bm_find's payload.
    drawn = bm_text("tree", "--name", "*.md")
    assert drawn.splitlines()[0] == "/"
    assert "test/" in drawn
    assert "Root.md" in drawn
    assert bm_json("tree")["nodes"] == bm_json("find", "--page-size", "100")["nodes"]

    # A truncated page says so under the tree.
    assert "more entries (page 1; use --page)" in bm_text("tree", "--page-size", "2")

    # A rooted tree does not draw its root as its own child.
    rooted = bm_text("tree", "test")
    assert rooted.splitlines()[0] == "test"
    assert "test/" not in rooted


def test_ls_find_and_tree():
    check_ls_and_tree_on_an_empty_project()
    seed_graph()
    check_ls_lists_one_folder()
    check_find_and_tree_list_recursively()


# --- find --meta ---


FIND_META_CASES = [
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
    # 'key=null' finds the notes carrying no value there: the complement.
    (["review.approved=null"], {"Beta Spec", "Gamma Note"}),
    (["note_type=note"], {"Alpha Spec", "Beta Spec", "Gamma Note"}),
    # Repeated predicates AND together.
    (["status=active", "priority=high"], {"Alpha Spec"}),
    (["status=active", "priority=nonexistent"], set()),
]


def check_find_meta_predicates() -> None:
    shape = bm_json("find", "--meta", "status=active")
    assert set(shape) == {
        "results",
        "current_page",
        "page_size",
        "total",
        "total_is_exact",
        "has_more",
    }
    assert shape["total"] == 2
    assert shape["total_is_exact"] is True
    for row in shape["results"]:
        assert "content" in row
        assert "fields" not in row

    for predicates, expected_titles in FIND_META_CASES:
        assert titles(bm_json("find", *meta_args(*predicates))) == expected_titles, predicates

    # PARITY: the predicate spelling and bm_find's JSON filters are one query.
    searched = bm_json("tool", "find", "--filters", '{"tags": ["security", "oauth"]}')
    assert titles(searched) == {"Alpha Spec"}

    # A 400-digit bound is a filter refusal, not a server error.
    assert "not a finite number" in refusal("find", "--meta", f"score>{'9' * 400}")


def check_find_meta_scopes_paginates_and_projects() -> None:
    write_note("Archived Spec", "specs-archive", "# Archived Spec", metadata={"status": "active"})

    scopes = [
        ("/", {"Alpha Spec", "Beta Spec", "Gamma Note", "Archived Spec"}),
        ("specs", {"Alpha Spec", "Beta Spec"}),
        ("/specs", {"Alpha Spec", "Beta Spec"}),
        ("./specs/", {"Alpha Spec", "Beta Spec"}),
        # A spaced directory name is a file path, not a slug.
        ("My Notes", {"Gamma Note"}),
        ("nonexistent", set()),
    ]
    for path, expected_titles in scopes:
        scoped = bm_json("find", path, "--meta", "note_type=note")
        assert titles(scoped) == expected_titles, path
        assert scoped["total"] == len(expected_titles), path

    first = bm_json("find", "specs", "--meta", "note_type=note", "--page-size", "1")
    second = bm_json("find", "specs", "--meta", "note_type=note", "--page", "2", "--page-size", "1")
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert first["results"][0]["permalink"] != second["results"][0]["permalink"]

    projected = bm_json(
        "find",
        "--meta",
        "priority in high,critical",
        "--fields",
        "title,priority,chapter_number,review.approved,tags,missing_field",
    )
    by_title = {row["title"]: row for row in projected["results"]}
    assert by_title["Alpha Spec"]["fields"] == {
        "title": "Alpha Spec",
        "priority": "high",
        "chapter_number": 63,
        "review.approved": True,
        "tags": ["security", "oauth"],
        "missing_field": None,
    }
    assert by_title["Gamma Note"]["fields"]["review.approved"] is None
    # A projected row is the note's identity plus the fields, and no body.
    for row in projected["results"]:
        assert set(row) == {
            "title",
            "permalink",
            "file_path",
            "external_id",
            "updated_at",
            "fields",
        }
        assert "alpha body" not in json.dumps(row)


def check_find_meta_quoting_escapes() -> None:
    write_note("Comma Note", "notes", "# Comma Note", metadata={"label": "a,b"})
    write_note("Range Note", "notes", "# Range Note", metadata={"range": ">=5"})

    # A quoted list element keeps its comma.
    assert titles(bm_json("find", "--meta", 'label in "a,b",c')) == {"Comma Note"}
    # Unquoted, '>=5' reads as a mis-spelled operator; quoted, it matches literally.
    assert "unsupported predicate operator" in refusal("find", "--meta", "range=>=5")
    assert titles(bm_json("find", "--meta", 'range=">=5"')) == {"Range Note"}


def test_find_meta_scope_follows_the_file_path_not_the_permalink():
    """REGRESSION: a scope built from permalinks answers this pair backwards.

    An explicit frontmatter permalink is honored verbatim (#93), so these notes
    live under one folder and permalink under the other.
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

    specs = bm_json("find", "specs", "--meta", "status=active")
    archive = bm_json("find", "archive", "--meta", "status=active")

    assert [row["file_path"] for row in specs["results"]] == ["specs/Housed In Specs.md"]
    assert [row["file_path"] for row in archive["results"]] == ["archive/Housed In Archive.md"]


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


def test_find_meta(scanned_pdf):
    """Predicates, scopes, projection and quoting over one seeded set.

    The set includes an indexed PDF. REGRESSION: a regular file carries no
    frontmatter keys, which is what `key=null` asks for, so it used to be counted
    as a hit; the null cases in FIND_META_CASES pin that it no longer is.
    """
    seed_meta_notes()
    check_find_meta_predicates()
    # Each later check adds notes, so the order matters.
    check_find_meta_scopes_paginates_and_projects()
    check_find_meta_quoting_escapes()


def test_find_meta_fields_requires_hit_external_ids(monkeypatch):
    """A server old enough to omit external_id fails fast instead of returning
    unprojected rows. The old server is simulated at the search client: no real
    server in this process can omit the field."""

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

    stderr = refusal("find", "--meta", "status=active", "--fields", "title")

    assert "too old for `fields`" in stderr


# --- projects ---


def check_verbs_address_another_project(test_project) -> None:
    write_note(
        "Second Note",
        "notes",
        "# Second Note\n\nsecond body",
        project="second-project",
        metadata={"status": "active"},
    )

    # --project selects the project; without it the default project answers.
    assert bm_json("cat", "notes/second-note", *SECOND)["title"] == "Second Note"
    assert "Error:" in refusal("cat", "notes/second-note")
    assert titles(bm_json("grep", "second", *SECOND)) == {"Second Note"}
    assert bm_json("grep", "second")["results"] == []

    # A path qualified with the named project strips the prefix and answers in
    # the qualified spelling, so a returned path is accepted again.
    listing = bm_json("ls", "second-project/notes", *SECOND)
    [node] = listing["nodes"]
    assert node["file_path"] == "second-project/notes/Second Note.md"
    assert bm_json("cat", node["file_path"], *SECOND)["title"] == "Second Note"
    found = bm_json("find", "second-project", "--meta", "status=active", *SECOND)
    assert [row["file_path"] for row in found["results"]] == [node["file_path"]]
    # An unqualified call keeps project-relative paths.
    assert bm_json("ls", "notes", *SECOND)["nodes"][0]["file_path"] == "notes/Second Note.md"

    # A prefix naming a different project than --project is refused.
    assert "second-project" in refusal("cat", "second-project/notes/second-note")

    # --project also takes a project's external id.
    write_note("Home Note", "notes", "# Home Note")
    by_id = bm_json("cat", "notes/home-note", "--project", test_project.external_id)
    assert by_id["title"] == "Home Note"


def check_routed_cat_never_rewrites_note_frontmatter(second_project: Path) -> None:
    """Re-qualifying the transport path never touches the note's own YAML,
    even when it carries keys spelled like transport fields."""
    write_note(
        "Imported Note",
        "notes",
        "# Imported Note\n\nbody",
        project="second-project",
        metadata={"file_path": "imports/source.md", "directory_path": "/imports"},
    )

    payload = bm_json("cat", "second-project/notes/imported-note", *SECOND)

    stored = (second_project / "notes" / "Imported Note.md").read_text()
    _, _, rest = stored.partition("---\n")
    block, _, _ = rest.partition("\n---")
    assert yaml.safe_load(block) == payload["frontmatter"]
    assert payload["frontmatter"]["file_path"] == "imports/source.md"
    assert payload["file_path"] == "second-project/notes/Imported Note.md"


def check_tree_under_a_display_name_project(tmp_path_factory) -> None:
    """A project named with a space and capitals answers in its permalink
    spelling ('my-research/specs/...'); tree must strip that, not the caller's
    'My Research/specs', or the project segment nests under the root."""
    home = tmp_path_factory.mktemp("my-research-home")
    added = bm("project", "add", "My Research", str(home))
    assert added.exit_code == 0, added.output
    write_note("Plan", "specs/auth", "# Plan", project="My Research")
    research = ["--project", "My Research"]

    qualified = bm_text("tree", "My Research/specs", *research)
    relative = bm_text("tree", "specs", *research)

    assert qualified.splitlines() == ["My Research/specs", "└── auth/", "    └── Plan.md"]
    # Only the printed root differs between the two spellings of one folder.
    assert qualified.splitlines()[1:] == relative.splitlines()[1:]


def test_verbs_with_two_projects(second_project, test_project, tmp_path_factory):
    check_verbs_address_another_project(test_project)
    check_routed_cat_never_rewrites_note_frontmatter(second_project)
    check_tree_under_a_display_name_project(tmp_path_factory)
