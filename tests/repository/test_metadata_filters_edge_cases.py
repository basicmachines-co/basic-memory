"""Edge-case tests for metadata filter operators across both backends.

Extends the base metadata filter tests with edge cases that exercise
Postgres JSONB operator behavior alongside SQLite json_extract equivalents.
Runs on both backends via the parameterized search_repository fixture.
"""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.schemas.search import SearchItemType


async def _index_entity_with_metadata(search_repository, session_maker, title, entity_metadata):
    """Helper: create an entity with given metadata and index it for search."""
    slug = "-".join(title.lower().split())
    file_path = f"test/{slug}.md"
    permalink = f"test/{slug}"
    now = datetime.now(timezone.utc)

    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=search_repository.project_id,
            title=title,
            note_type="note",
            permalink=permalink,
            file_path=file_path,
            content_type="text/markdown",
            entity_metadata=entity_metadata,
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        await session.flush()

    search_row = SearchIndexRow(
        id=entity.id,
        type=SearchItemType.ENTITY.value,
        title=entity.title,
        content_stems="metadata edge case test",
        content_snippet="metadata edge case test",
        permalink=entity.permalink,
        file_path=entity.file_path,
        entity_id=entity.id,
        metadata={"note_type": entity.note_type},
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        project_id=search_repository.project_id,
    )
    await search_repository.index_item(search_row)
    return entity


@pytest.mark.asyncio
async def test_filter_missing_metadata_field(search_repository, session_maker):
    """Filtering on a field that doesn't exist in entity_metadata returns no matches."""
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "No Priority Field",
        {"status": "active"},
    )

    # Filter on a field that this entity doesn't have
    results = await search_repository.search(metadata_filters={"priority": "high"})
    assert len(results) == 0


@pytest.mark.asyncio
async def test_null_filter_matches_a_missing_key_and_an_explicit_null(
    search_repository, session_maker
):
    """CONTRACT: {"key": None} is IS NULL, and both dialects draw the same line.

    SQLite's json_extract and Postgres's jsonb_extract_path_text each collapse a
    missing key and an explicit JSON null to SQL NULL, so "which notes have no
    owner?" answers row for row on either backend. The regression this pins is
    the equality clause it replaced: `= NULL` satisfies no row, so the query
    reported an exact zero no matter how many notes were missing the field.
    """
    missing = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Owner Key Absent",
        {"status": "active"},
    )
    explicit = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Owner Explicitly Null",
        {"status": "active", "owner": None},
    )

    results = await search_repository.search(metadata_filters={"owner": None})

    assert {r.id for r in results} == {missing.id, explicit.id}
    assert await search_repository.count(metadata_filters={"owner": None}) == 2


@pytest.mark.asyncio
async def test_null_filter_excludes_a_note_carrying_a_value(search_repository, session_maker):
    """A note with a real value is never a null match.

    The positive control matters: a filter that matched nothing at all would
    also "exclude" this note, which is exactly the bug being fixed.
    """
    alice = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Owner Alice",
        {"owner": "alice"},
    )
    unowned = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Owner Unset",
        {"status": "active"},
    )

    null_matches = await search_repository.search(metadata_filters={"owner": None})
    alice_matches = await search_repository.search(metadata_filters={"owner": "alice"})

    assert {r.id for r in null_matches} == {unowned.id}
    assert {r.id for r in alice_matches} == {alice.id}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "present_value"),
    [("status", "active"), ("type", "spec"), ("tags", ["security"])],
)
async def test_null_filter_on_the_indexed_frontmatter_fields(
    search_repository, session_maker, field, present_value
):
    """The three fields SQLite answers from generated columns behave identically.

    status, type and tags take `entity.frontmatter_status` / `frontmatter_type`
    / `tags_json` on SQLite instead of a json_extract call, while Postgres has
    no such columns and always extracts from the JSONB. Those columns are
    defined AS that json_extract, so IS NULL means the same thing on both sides
    — this is the one place the dialects build structurally different SQL for
    the same filter, so it gets its own contract test.
    """
    missing = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        f"Indexed {field} absent",
        {"unrelated": "x"},
    )
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        f"Indexed {field} present",
        {field: present_value},
    )

    results = await search_repository.search(metadata_filters={field: None})

    assert {r.id for r in results} == {missing.id}


@pytest.mark.asyncio
async def test_null_filter_walks_a_nested_path(search_repository, session_maker):
    """A dot-path null match asks the same question one level down."""
    unreviewed = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Nested Review Missing",
        {"review": {"reviewer": "alice"}},
    )
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Nested Review Approved",
        {"review": {"reviewer": "alice", "approved": True}},
    )

    results = await search_repository.search(metadata_filters={"review.approved": None})

    assert {r.id for r in results} == {unreviewed.id}


@pytest.mark.asyncio
async def test_filter_multiple_conditions_and_logic(search_repository, session_maker):
    """Multiple metadata_filters are combined with AND logic."""
    entity_both = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Both Match",
        {"status": "active", "priority": "high"},
    )
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Status Only",
        {"status": "active", "priority": "low"},
    )
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Priority Only",
        {"status": "archived", "priority": "high"},
    )

    results = await search_repository.search(
        metadata_filters={"status": "active", "priority": "high"}
    )
    assert {r.id for r in results} == {entity_both.id}


@pytest.mark.asyncio
async def test_filter_contains_single_element_array(search_repository, session_maker):
    """Contains filter with a single-element array matches entities that have that tag."""
    entity_match = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Has Security Tag",
        {"tags": ["security", "auth"]},
    )
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "No Security Tag",
        {"tags": ["database", "migration"]},
    )

    results = await search_repository.search(metadata_filters={"tags": ["security"]})
    assert {r.id for r in results} == {entity_match.id}


@pytest.mark.asyncio
async def test_filter_nested_path_missing_intermediate(search_repository, session_maker):
    """Filtering on a nested path where intermediate keys are missing returns no match."""
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Shallow Metadata",
        {"status": "active"},
    )

    # Filter on deeply nested path — entity only has flat metadata
    results = await search_repository.search(metadata_filters={"schema.confidence": {"$gt": 0.5}})
    assert len(results) == 0


@pytest.mark.asyncio
async def test_filter_gte_and_lte_operators(search_repository, session_maker):
    """$gte and $lte boundary comparisons work correctly."""
    entity_exact = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Exact Boundary",
        {"schema": {"confidence": 0.5}},
    )
    entity_above = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Above Boundary",
        {"schema": {"confidence": 0.8}},
    )
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Below Boundary",
        {"schema": {"confidence": 0.2}},
    )

    # $gte should include the boundary value
    results = await search_repository.search(metadata_filters={"schema.confidence": {"$gte": 0.5}})
    result_ids = {r.id for r in results}
    assert entity_exact.id in result_ids
    assert entity_above.id in result_ids

    # $lte should include the boundary value
    results = await search_repository.search(metadata_filters={"schema.confidence": {"$lte": 0.5}})
    result_ids = {r.id for r in results}
    assert entity_exact.id in result_ids


@pytest.mark.asyncio
async def test_filter_between_inclusive_boundaries(search_repository, session_maker):
    """$between includes both boundary values."""
    entity_low = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "At Low Boundary",
        {"schema": {"confidence": 0.3}},
    )
    entity_high = await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "At High Boundary",
        {"schema": {"confidence": 0.7}},
    )
    await _index_entity_with_metadata(
        search_repository,
        session_maker,
        "Outside Range",
        {"schema": {"confidence": 0.9}},
    )

    results = await search_repository.search(
        metadata_filters={"schema.confidence": {"$between": [0.3, 0.7]}}
    )
    result_ids = {r.id for r in results}
    assert entity_low.id in result_ids
    assert entity_high.id in result_ids
