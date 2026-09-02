"""Valid-time filtering through the search service (SPEC-82).

The service is where the flat boundary strings become domain values, so this is where a
malformed filter must be refused loudly rather than degraded into a filter that quietly
matches something else. It is also the layer that proves acceptance case 11: a note's
edit bookkeeping is never reinterpreted as the time it claims to be true.
"""

from datetime import datetime, timezone
from textwrap import dedent

import pytest

from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.search import SearchQuery
from basic_memory.services.search_service import (
    build_temporal_filter,
    describe_search_criteria,
)
from basic_memory.temporal import TemporalQualifierError, TimeKind

# The entity is created "now"; the qualifier claims June-July 2026. Keeping the two
# ranges disjoint is what makes acceptance case 11 testable at all.
EFFECTIVE_WINDOW_START = "2026-06-10"
EFFECTIVE_WINDOW_INSIDE = "2026-07-01"
EFFECTIVE_WINDOW_END = "2026-07-27"

CACHE_LAYER_MARKDOWN = dedent("""
    # Cache Layer

    ## Observations
    - [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.
    """)


async def _index_cache_layer_note(entity_service, search_service):
    """Create the dated note through the real write path and index it for search."""
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Cache Layer",
            note_type="note",
            directory="decisions",
            content=CACHE_LAYER_MARKDOWN,
        )
    )
    await search_service.index_entity(entity)
    return entity


# --- Acceptance 11: entity time is never valid time ---


@pytest.mark.asyncio
async def test_entity_timestamps_are_never_used_as_observation_valid_time(
    entity_service, search_service
):
    """The note was written today; it claims to hold in June and July.

    Asking `valid_at` on the day the file was written must return nothing, because no
    observation asserts that day. Asking inside the authored window returns the
    observation. If edit bookkeeping ever leaked into the valid-time axis, the first
    query would match and the distinction the spec draws would be gone.
    """
    entity = await _index_cache_layer_note(entity_service, search_service)
    written_on = entity.updated_at.date().isoformat()
    assert written_on > EFFECTIVE_WINDOW_END, "fixture assumes the note is written after the window"

    at_write_time = await search_service.search(
        SearchQuery(text="cache layer", valid_at=written_on)
    )
    assert at_write_time == []

    inside_window = await search_service.search(
        SearchQuery(text="cache layer", valid_at=EFFECTIVE_WINDOW_INSIDE)
    )
    assert [result.type for result in inside_window] == ["observation"]
    assert "Redis" in (inside_window[0].content_snippet or "")


@pytest.mark.asyncio
async def test_after_date_still_filters_indexed_time_not_valid_time(entity_service, search_service):
    """`after_date` keeps its meaning: it is the note's bookkeeping, not its claim.

    The note was indexed today and asserts a window that ended in July, so a filter on
    each axis answers differently -- which is only possible because they stay separate.
    """
    await _index_cache_layer_note(entity_service, search_service)
    long_ago = datetime(2020, 1, 1, tzinfo=timezone.utc)

    recently_indexed = await search_service.search(
        SearchQuery(text="cache layer", after_date=long_ago)
    )
    assert recently_indexed

    still_effective_today = await search_service.search(
        SearchQuery(text="cache layer", valid_at="2026-12-31")
    )
    assert still_effective_today == []


@pytest.mark.asyncio
async def test_valid_time_filter_narrows_to_the_asserting_observation(
    entity_service, search_service
):
    """A valid-time hit is the observation that carried the claim, not the whole note."""
    await _index_cache_layer_note(entity_service, search_service)

    results = await search_service.search(
        SearchQuery(text="cache layer", time_kind="effective", valid_at=EFFECTIVE_WINDOW_START)
    )

    assert [result.type for result in results] == ["observation"]


@pytest.mark.asyncio
async def test_undated_note_is_excluded_by_a_valid_time_filter(entity_service, search_service):
    """Acceptance 8, at the service layer: no claim means no answer."""
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Queue Layer",
            note_type="note",
            directory="decisions",
            content="# Queue Layer\n\n## Observations\n- [decision] The queue layer uses RabbitMQ.\n",
        )
    )
    await search_service.index_entity(entity)

    unfiltered = await search_service.search(SearchQuery(text="RabbitMQ"))
    assert unfiltered

    filtered = await search_service.search(
        SearchQuery(text="RabbitMQ", valid_at=EFFECTIVE_WINDOW_INSIDE)
    )
    assert filtered == []


# --- Diagnostics: the boundary refuses every malformed filter ---


def test_unknown_time_kind_is_refused_with_the_known_kinds():
    with pytest.raises(TemporalQualifierError, match="unknown time_kind 'asserted'") as exc_info:
        build_temporal_filter(SearchQuery(text="cache", time_kind="asserted"))

    assert "effective" in str(exc_info.value)


def test_malformed_range_literal_is_refused():
    with pytest.raises(TemporalQualifierError, match="range literal must be"):
        build_temporal_filter(SearchQuery(text="cache", valid_overlaps="2026-06-10..2026-07-27"))


def test_mixed_bound_kinds_are_refused():
    with pytest.raises(TemporalQualifierError, match="mix date-only and timestamp bounds"):
        build_temporal_filter(
            SearchQuery(text="cache", valid_overlaps="[2026-06-10,2026-07-27T00:00:00Z)")
        )


def test_timestamp_without_offset_is_read_as_utc():
    """A naive timestamp is not a rejection: it names the same instant as its `Z` form."""
    naive = build_temporal_filter(SearchQuery(text="cache", valid_at="2026-07-27T18:42:00"))
    explicit = build_temporal_filter(SearchQuery(text="cache", valid_at="2026-07-27T18:42:00Z"))

    assert naive == explicit
    assert naive is not None and naive.at is not None
    assert naive.at.value == "2026-07-27T18:42:00.000000Z"


def test_impossible_range_is_refused():
    with pytest.raises(TemporalQualifierError, match="after upper bound"):
        build_temporal_filter(SearchQuery(text="cache", valid_overlaps="[2026-08-01,2026-06-10)"))


def test_query_without_valid_time_fields_builds_no_filter():
    assert build_temporal_filter(SearchQuery(text="cache")) is None


def test_kind_only_query_builds_a_kind_filter():
    temporal = build_temporal_filter(SearchQuery(text="cache", time_kind="effective"))

    assert temporal is not None
    assert temporal.kind is TimeKind.EFFECTIVE
    assert temporal.at is None and temporal.overlaps is None


def test_valid_at_and_valid_overlaps_are_mutually_exclusive_at_the_schema():
    """The schema refuses the pair before any parsing or SQL can happen."""
    with pytest.raises(ValueError, match="not both"):
        SearchQuery(text="cache", valid_at="2026-07-28", valid_overlaps="[2026-06-10,)")


# --- Query gating and traces ---


def test_a_valid_time_filter_alone_is_enough_criteria():
    """A temporal filter is real criteria; the empty-query guard must not swallow it."""
    assert SearchQuery(valid_at="2026-07-28").no_criteria() is False
    assert SearchQuery(time_kind="effective").no_criteria() is False
    assert SearchQuery(valid_overlaps="[2026-06-10,)").no_criteria() is False
    assert SearchQuery().no_criteria() is True


@pytest.mark.asyncio
async def test_prepared_query_carries_the_parsed_filter(search_service):
    prepared = search_service.prepare_query(
        SearchQuery(text="cache", time_kind="effective", valid_at="2026-07-28")
    )

    assert prepared is not None
    assert prepared.temporal is not None
    assert prepared.temporal.kind is TimeKind.EFFECTIVE
    assert prepared.temporal.at is not None
    assert prepared.temporal.at.value == "2026-07-28"


@pytest.mark.asyncio
async def test_search_trace_describes_the_valid_time_question(search_service):
    """A trace must show the question that actually ran, valid time included."""
    containment = search_service.prepare_query(
        SearchQuery(text="cache", time_kind="effective", valid_at="2026-07-28")
    )
    overlap = search_service.prepare_query(
        SearchQuery(text="cache", valid_overlaps="[2026-06-10,2026-07-27)")
    )
    plain = search_service.prepare_query(SearchQuery(text="cache"))

    assert containment is not None and overlap is not None and plain is not None
    assert "temporal=kind=effective,valid_at=2026-07-28" in describe_search_criteria(containment)
    assert "temporal=valid_overlaps=[2026-06-10,2026-07-27)" in describe_search_criteria(overlap)
    assert "temporal=" not in describe_search_criteria(plain)


# --- Every authored assertion stays queryable by its own time ---

TWICE_DATED_MARKDOWN = dedent("""
    # Cache Layer

    ## Observations
    - [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.
    - [decision] @effective[2027-06-10,2027-07-27) The cache layer will use Redis.
    """)

SECOND_WINDOW_INSIDE = "2027-07-01"


@pytest.mark.asyncio
async def test_same_statement_at_two_times_is_queryable_at_each(entity_service, search_service):
    """One note, one sentence, two authored windows -- both must remain findable.

    The qualifier is peeled off before the observation is stored, so these two lines
    persist identical content and derived identical synthetic permalinks. The search
    index is keyed on permalink, so the second observation was skipped as a duplicate
    while its temporal assertion went on addressing a row with no search projection:
    querying 2027 returned nothing, and every reindex reproduced the omission from the
    same markdown. The note says two things happened at two times; both must answer.
    """
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Cache Layer Twice",
            note_type="note",
            directory="decisions",
            content=TWICE_DATED_MARKDOWN,
        )
    )
    await search_service.index_entity(entity)

    # The two rows are distinct statements and must carry distinct addresses.
    first, second = entity.observations
    assert first.permalink != second.permalink

    in_first = await search_service.search(
        SearchQuery(text="cache layer", valid_at=EFFECTIVE_WINDOW_INSIDE)
    )
    in_second = await search_service.search(
        SearchQuery(text="cache layer", valid_at=SECOND_WINDOW_INSIDE)
    )

    assert [result.id for result in in_first] == [first.id]
    assert [result.id for result in in_second] == [second.id]
    # Each window answers with exactly one of them, never the same row twice.
    assert first.id != second.id


MIXED_CATEGORY_MARKDOWN = dedent("""
    # Cache Layer

    ## Observations
    - @effective[2026-06-10,2026-07-27) The cache layer will use Redis. #infra
    - [note] @effective[2027-06-10,2027-07-27) The cache layer will use Redis. #infra
    """)


@pytest.mark.asyncio
async def test_an_omitted_category_is_the_same_category_when_ordinals_are_counted(
    entity_service, search_service
):
    """The ordinal has to be counted on the category the row will hold, not the one given.

    A line promoted to an observation by its hashtags alone carries no `[category]`, so it
    arrives as `None`, while the explicit `[note]` line beside it arrives as `"note"`.
    They look like different identities at the moment the ordinal is assigned and are the
    same category one flush later, when the column default lands -- so both were numbered
    0, derived one permalink, and lost a search row between them exactly as if the ordinal
    had never been added. Normalizing before counting is what keeps the two halves of the
    identity agreeing about what a row is.
    """
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Cache Layer Mixed",
            note_type="note",
            directory="decisions",
            content=MIXED_CATEGORY_MARKDOWN,
        )
    )
    await search_service.index_entity(entity)

    first, second = entity.observations
    # The stored rows agree on category; the addresses must still tell them apart.
    assert first.category == second.category
    assert first.permalink != second.permalink

    in_first = await search_service.search(
        SearchQuery(text="cache layer", valid_at=EFFECTIVE_WINDOW_INSIDE)
    )
    in_second = await search_service.search(
        SearchQuery(text="cache layer", valid_at=SECOND_WINDOW_INSIDE)
    )

    assert [result.id for result in in_first] == [first.id]
    assert [result.id for result in in_second] == [second.id]


@pytest.mark.asyncio
async def test_a_valid_time_query_can_also_scope_by_note_type(entity_service, search_service):
    """Valid time selects observation rows; note type must not then exclude them.

    A note's type lives in its frontmatter, so only its entity row carries it. Reading the
    type off each row made these two filters contradict each other -- every row the
    temporal predicate admitted, the note-type predicate rejected -- so the conjunction
    returned nothing however well the note matched. Resolving the type through the owning
    note is what lets both questions be asked at once.
    """
    entity = await _index_cache_layer_note(entity_service, search_service)

    scoped = await search_service.search(
        SearchQuery(
            text="cache layer",
            valid_at=EFFECTIVE_WINDOW_INSIDE,
            note_types=["note"],
        )
    )

    assert [result.type for result in scoped] == ["observation"]
    assert scoped[0].entity_id == entity.id
    # A type the note does not have still excludes it, so the filter is doing real work.
    unscoped = await search_service.search(
        SearchQuery(
            text="cache layer",
            valid_at=EFFECTIVE_WINDOW_INSIDE,
            note_types=["conversation"],
        )
    )
    assert unscoped == []
