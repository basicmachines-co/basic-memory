"""Dialect contract for the search_index file-path subtree filter.

Every test here runs against whichever backend the session is configured for —
SQLite by default, PostgreSQL under BASIC_MEMORY_TEST_POSTGRES=1 — through the
shared `search_repository` fixture. The point is that the two dialects must
agree row for row: a subtree scope that means "specs/ and everything under it"
on one backend and something wider on the other would report an exact total for
a match set the other backend never produces.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from basic_memory import db
from basic_memory.models.knowledge import Entity
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import file_path_prefix_condition
from basic_memory.schemas.search import (
    SearchItemType,
    SearchRetrievalMode,
    normalize_file_path_prefix,
)

# file_path -> (title, status). Every decoy here is a real way a naive predicate
# leaks: a sibling directory sharing the scope's name as a prefix, a "_" or "%"
# read as a LIKE wildcard, and a case variant that only one dialect's LIKE folds.
SEEDED_NOTES: dict[str, tuple[str, str]] = {
    "specs/alpha.md": ("Alpha", "active"),
    "specs/nested/beta.md": ("Beta", "draft"),
    "specs-archive/gamma.md": ("Gamma", "active"),
    "Specs/delta.md": ("Delta", "active"),
    "my_notes/epsilon.md": ("Epsilon", "active"),
    "my-notes/zeta.md": ("Zeta", "active"),
    "100%/eta.md": ("Eta", "active"),
    "100pct/theta.md": ("Theta", "active"),
}


async def _index_note(
    search_repository,
    session_maker,
    file_path: str,
    *,
    title: str | None = None,
    status: str | None = None,
    content: str = "subtree scope fixture",
) -> Entity:
    """Index one entity and its search row at an exact file_path."""
    seeded_title, seeded_status = SEEDED_NOTES.get(file_path, ("", "active"))
    title = title if title is not None else seeded_title
    status = status if status is not None else seeded_status
    now = datetime.now(timezone.utc)
    permalink = file_path.removesuffix(".md")

    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=search_repository.project_id,
            title=title,
            note_type="note",
            permalink=permalink,
            file_path=file_path,
            content_type="text/markdown",
            entity_metadata={"status": status},
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        await session.flush()

    await search_repository.index_item(
        SearchIndexRow(
            project_id=search_repository.project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            title=entity.title,
            content_stems=content,
            content_snippet=content,
            permalink=entity.permalink,
            file_path=entity.file_path,
            entity_id=entity.id,
            metadata={"note_type": entity.note_type},
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
    )
    return entity


@pytest.fixture
async def seeded_paths(search_repository, session_maker) -> dict[str, int]:
    """Index every seeded note; yields file_path -> search_index row id."""
    return {
        file_path: (await _index_note(search_repository, session_maker, file_path)).id
        for file_path in SEEDED_NOTES
    }


async def _titles(search_repository, **kwargs) -> set[str]:
    rows = await search_repository.search(limit=100, **kwargs)
    return {row.title for row in rows}


@pytest.mark.asyncio
async def test_scopes_to_the_named_subtree(search_repository, seeded_paths):
    """A prefix admits the directory's own files and everything beneath it."""
    assert await _titles(search_repository, file_path_prefix="specs") == {"Alpha", "Beta"}


@pytest.mark.asyncio
async def test_scopes_to_a_nested_subtree(search_repository, seeded_paths):
    """Multi-segment prefixes address a directory further down the tree."""
    assert await _titles(search_repository, file_path_prefix="specs/nested") == {"Beta"}


@pytest.mark.asyncio
async def test_prefix_matches_only_on_a_directory_boundary(search_repository, seeded_paths):
    """REGRESSION: "specs" must not admit the sibling "specs-archive/".

    The compared prefix carries its trailing separator precisely so a directory
    whose name merely starts with the scope stays out.
    """
    assert "Gamma" not in await _titles(search_repository, file_path_prefix="specs")
    assert await _titles(search_repository, file_path_prefix="specs-archive") == {"Gamma"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "expected", "excluded"),
    [
        # "_" is LIKE's single-character wildcard; here it is a directory name.
        ("my_notes", {"Epsilon"}, "Zeta"),
        # "%" is LIKE's any-length wildcard; unescaped, "100%/" would also admit
        # "100pct/" (and anything else starting with "100").
        ("100%", {"Eta"}, "Theta"),
    ],
)
async def test_wildcard_characters_in_a_directory_name_are_literal(
    search_repository, seeded_paths, scope, expected, excluded
):
    """REGRESSION: a directory named with "_" or "%" is not a pattern."""
    titles = await _titles(search_repository, file_path_prefix=scope)

    assert titles == expected
    assert excluded not in titles


@pytest.mark.asyncio
async def test_prefix_is_case_sensitive_on_both_backends(search_repository, seeded_paths):
    """CONTRACT: casing decides membership identically on SQLite and Postgres.

    SQLite's LIKE is ASCII-case-insensitive and Postgres's is case-sensitive, so
    a LIKE-based scope would put "Specs/delta.md" inside "specs" on one backend
    and outside it on the other. The shared predicate compares the stored bytes.
    """
    assert await _titles(search_repository, file_path_prefix="specs") == {"Alpha", "Beta"}
    assert await _titles(search_repository, file_path_prefix="Specs") == {"Delta"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["specs", "/specs", "specs/", "/specs/", "  specs  "])
async def test_scope_spellings_normalize_to_one_query(search_repository, seeded_paths, scope):
    """Leading, trailing, and surrounding whitespace/slashes name one subtree."""
    assert await _titles(search_repository, file_path_prefix=scope) == {"Alpha", "Beta"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "", "/", "   "])
async def test_root_spellings_apply_no_scope(search_repository, seeded_paths, scope):
    """The root is the absence of a subtree predicate, not a prefix of "/"."""
    titles = await _titles(
        search_repository,
        file_path_prefix=scope,
        search_item_types=[SearchItemType.ENTITY],
    )

    assert titles == {title for title, _status in SEEDED_NOTES.values()}


@pytest.mark.asyncio
async def test_scope_composes_with_metadata_filters_and_counts_agree(
    search_repository, seeded_paths
):
    """The scope ANDs into the same WHERE, so count() describes the page's query.

    A total drawn from a different WHERE than the page is the exact failure the
    file-path filter exists to prevent: it would advertise pages that hold
    nothing and hide pages that hold matches.
    """
    kwargs = {"file_path_prefix": "specs", "metadata_filters": {"status": "active"}}

    assert await _titles(search_repository, **kwargs) == {"Alpha"}
    assert await search_repository.count(**kwargs) == 1
    # Unscoped, the same predicate also reaches every decoy directory.
    active_notes = sum(1 for _title, status in SEEDED_NOTES.values() if status == "active")
    assert await search_repository.count(metadata_filters={"status": "active"}) == active_notes


@pytest.mark.asyncio
async def test_scope_composes_with_text_search(search_repository, seeded_paths):
    """The scope narrows a full-text query rather than replacing it."""
    scoped = await _titles(search_repository, search_text="subtree", file_path_prefix="specs")

    assert scoped == {"Alpha", "Beta"}


@pytest.mark.asyncio
async def test_scope_composes_with_an_unsegmented_script_query(search_repository, session_maker):
    """The scope survives the script-ngram query shape both backends build.

    A CJK query plus metadata filters takes each backend's most rearranged FROM
    clause — SQLite ranks the MATCH inside a derived table aliased back to
    `search_index`, Postgres joins a script-ngram candidate set — so a predicate
    that referenced the base table by any other name would fail here rather than
    at some later runtime.
    """
    await _index_note(
        search_repository,
        session_maker,
        "specs/cjk.md",
        title="Scoped Script",
        content="適者生存",
    )
    await _index_note(
        search_repository,
        session_maker,
        "notes/cjk.md",
        title="Unscoped Script",
        content="適者生存",
    )

    titles = await _titles(
        search_repository,
        search_text="適者生存",
        file_path_prefix="specs",
        metadata_filters={"status": "active"},
    )

    assert titles == {"Scoped Script"}


@pytest.mark.asyncio
async def test_count_matches_the_paged_rows(search_repository, seeded_paths):
    """Pagination over a scoped query stays reachable end to end."""
    total = await search_repository.count(file_path_prefix="specs")
    first = await search_repository.search(file_path_prefix="specs", limit=1, offset=0)
    second = await search_repository.search(file_path_prefix="specs", limit=1, offset=1)

    assert total == 2
    assert {first[0].title, second[0].title} == {"Alpha", "Beta"}


@pytest.mark.asyncio
async def test_nonexistent_scope_is_empty_not_unfiltered(search_repository, seeded_paths):
    """A directory with no notes answers zero rows, never the whole project."""
    assert await _titles(search_repository, file_path_prefix="nowhere") == set()
    assert await search_repository.count(file_path_prefix="nowhere") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("retrieval_mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
async def test_semantic_retrieval_honors_the_scope(
    search_repository, seeded_paths, monkeypatch, retrieval_mode
):
    """The vector and hybrid paths apply the scope, not just the FTS path.

    Semantic retrieval has no SQL WHERE of its own: it post-filters its nearest
    neighbours through a scoped FTS query. A scope threaded only into the FTS
    entry point would leave `search(retrieval_mode="vector")` answering
    project-wide, which is the half-wired failure this test rules out. The
    nearest-neighbour stage is stubbed to return every seeded note so anything
    that survives did so through the filter.
    """
    monkeypatch.setattr(search_repository, "_semantic_enabled", True)
    monkeypatch.setattr(search_repository, "_semantic_min_similarity", 0.0)
    monkeypatch.setattr(
        search_repository,
        "_embedding_provider",
        SimpleNamespace(
            dimensions=4,
            model_name="stub",
            embed_query=AsyncMock(return_value=[0.0, 0.0, 0.0, 1.0]),
        ),
    )
    monkeypatch.setattr(search_repository, "_ensure_vector_tables", AsyncMock())
    monkeypatch.setattr(search_repository, "_prepare_vector_session", AsyncMock())
    monkeypatch.setattr(
        search_repository,
        "_run_vector_query",
        AsyncMock(
            return_value=[
                {
                    "entity_id": row_id,
                    "chunk_key": f"entity:{row_id}:0",
                    "chunk_text": "subtree scope fixture",
                    "best_similarity": 0.9,
                }
                for row_id in seeded_paths.values()
            ]
        ),
    )

    rows = await search_repository.search(
        search_text="subtree",
        file_path_prefix="specs",
        retrieval_mode=retrieval_mode,
        limit=100,
    )

    assert {row.title for row in rows} == {"Alpha", "Beta"}


def test_condition_is_one_shared_predicate_for_both_dialects():
    """The SQL text and its parameters are backend-independent by construction.

    Both `_build_fts_query_parts` implementations call this one helper, so the
    identical-behavior claim above is structural rather than a coincidence two
    hand-written predicates happen to share.
    """
    params: dict[str, object] = {}
    condition = file_path_prefix_condition("/specs/", params)

    assert condition == (
        "SUBSTR(search_index.file_path, 1, :file_path_prefix_length) = :file_path_prefix"
    )
    assert params == {"file_path_prefix": "specs/", "file_path_prefix_length": len("specs/")}


@pytest.mark.parametrize("scope", [None, "", "/", "  /  "])
def test_condition_declines_a_root_scope(scope):
    """No predicate at all, so the root query is not silently narrowed."""
    params: dict[str, object] = {}

    assert file_path_prefix_condition(scope, params) is None
    assert params == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("/", None),
        ("   ", None),
        ("specs", "specs"),
        ("/specs/", "specs"),
        ("  /specs/nested/  ", "specs/nested"),
    ],
)
def test_normalize_collapses_root_spellings(value, expected):
    assert normalize_file_path_prefix(value) == expected
