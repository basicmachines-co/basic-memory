"""The valid-time search predicate, as one contract over both dialects (SPEC-82).

Acceptance case 4 requires SQLite and PostgreSQL to answer identically. This module is
that contract, expressed once: every test here uses only dialect-neutral fixtures
(`search_repository`, `session_maker`, `test_project`), and the repo runs the whole
`tests/` tree twice -- plain for SQLite, and under `BASIC_MEMORY_TEST_POSTGRES=1` for
PostgreSQL via testcontainers. A divergence therefore fails this same suite on one of
the two runs rather than hiding in a backend-specific file.

The stored ranges below cover the dimensions PostgreSQL's range operators distinguish:
each inclusivity combination, each unbounded side, the fully unbounded range, the empty
range, and a separate instant axis that must never mix with the date axis. Timestamps
are written as explicit constants, never as "now", so the answers are the same on every
run and on every machine.

A second population covers the dimension a *discrete* domain adds: date ranges whose
authored bounds and whose sets of days come apart. Those cases are what the half-open
canonicalization in `basic_memory.temporal` exists for, and both dialects must agree
about them too.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, MemoryTimeIndex, Observation
from basic_memory.models.project import Project
from basic_memory.repository.memory_time_index_repository import MemoryTimeIndexRepository
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.search import SearchItemType
from basic_memory.temporal import (
    TemporalFilter,
    TemporalPoint,
    TemporalRange,
    TemporalRangeAxis,
    TimeKind,
    parse_point,
    parse_range_literal,
)

DATE = TemporalRangeAxis.DATE
INSTANT = TemporalRangeAxis.INSTANT

# Every observation shares this word so one FTS query returns the whole population and
# the temporal predicate is the only thing that narrows it. That also proves the
# predicate composes with a MATCH/bm25 query rather than only with a bare scan.
SHARED_TERM = "cachelayer"


@dataclass(frozen=True, slots=True)
class StoredAssertion:
    """One authored assertion, and the label the expectations refer to it by."""

    label: str
    valid_during: TemporalRange
    kind: TimeKind = TimeKind.EFFECTIVE


def _date_range(literal: str) -> TemporalRange:
    return parse_range_literal(literal, axis=DATE)


def _instant_range(literal: str) -> TemporalRange:
    return parse_range_literal(literal, axis=INSTANT)


# The population under test. Labels are the vocabulary of every expectation below.
STORED_ASSERTIONS: tuple[StoredAssertion, ...] = (
    StoredAssertion("closed_open", _date_range("[2026-06-10,2026-07-27)")),
    StoredAssertion("open_closed", _date_range("(2026-06-10,2026-07-27]")),
    StoredAssertion("closed_closed", _date_range("[2026-06-10,2026-07-27]")),
    StoredAssertion("open_open", _date_range("(2026-06-10,2026-07-27)")),
    StoredAssertion("from_cutover", _date_range("[2026-07-27,)")),
    StoredAssertion("before_june", _date_range("(,2026-06-10)")),
    StoredAssertion("always", TemporalRange(axis=DATE)),
    StoredAssertion("empty", TemporalRange.empty(DATE)),
    StoredAssertion(
        "instant_window",
        _instant_range("[2026-07-27T16:00:00Z,2026-07-27T18:00:00Z)"),
    ),
    StoredAssertion(
        "instant_offset",
        # Authored in +02:00; normalization must make it the UTC window [14:00,15:00).
        _instant_range("[2026-07-27T16:00:00+02:00,2026-07-27T17:00:00+02:00)"),
    ),
    StoredAssertion("due_window", _date_range("[2026-06-10,2026-07-27)"), kind=TimeKind.DUE),
    # --- The discrete population ---
    #
    # Filed as `occurred` time and dated in March so it never widens an expectation
    # above, and so no bound collides with the January bookkeeping timestamps that
    # `test_projection_rows_carry_only_authored_bounds` watches for.
    #
    # Only March 2, and only March 3: adjacent as authored bounds, disjoint as days.
    # This is the pair the half-open canonical form exists to tell apart.
    StoredAssertion("only_mar_02", _date_range("(2026-03-01,2026-03-03)"), kind=TimeKind.OCCURRED),
    StoredAssertion("only_mar_03", _date_range("(2026-03-02,2026-03-04)"), kind=TimeKind.OCCURRED),
    # Back-to-back half-open periods, the shape a sequence of effective windows takes.
    StoredAssertion(
        "half_open_first", _date_range("[2026-03-10,2026-03-12)"), kind=TimeKind.OCCURRED
    ),
    StoredAssertion(
        "half_open_second", _date_range("[2026-03-12,2026-03-14)"), kind=TimeKind.OCCURRED
    ),
    # Closed periods written by an author who means "through the 22nd": they share it.
    StoredAssertion("closed_first", _date_range("[2026-03-20,2026-03-22]"), kind=TimeKind.OCCURRED),
    StoredAssertion(
        "closed_second", _date_range("[2026-03-22,2026-03-24]"), kind=TimeKind.OCCURRED
    ),
    StoredAssertion("one_day", _date_range("[2026-03-30,2026-03-30]"), kind=TimeKind.OCCURRED),
    # After the 5th and before the 6th there is no day, so this authored range is the
    # empty range -- something only the discrete reading can see.
    StoredAssertion("no_such_day", _date_range("(2026-03-05,2026-03-06)"), kind=TimeKind.OCCURRED),
    # An instant range with a closed upper end, so the date rewrite is proven to stop
    # at the date axis rather than pushing this endpoint forward by a day.
    StoredAssertion(
        "instant_closed",
        _instant_range("[2026-07-27T20:00:00Z,2026-07-27T21:00:00Z]"),
        kind=TimeKind.OCCURRED,
    ),
)

DATE_LABELS = frozenset(
    stored.label
    for stored in STORED_ASSERTIONS
    if stored.valid_during.axis is DATE and stored.kind is TimeKind.EFFECTIVE
)
NON_EMPTY_DATE_LABELS = DATE_LABELS - {"empty"}


@pytest_asyncio.fixture
async def temporal_population(
    search_repository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> dict[int, str]:
    """Index one observation per stored assertion and project its valid time.

    Returns the observation id -> label map the assertions read results through, so a
    test never has to know which row id the database happened to mint.
    """
    indexed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    labels_by_id: dict[int, str] = {}

    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="Cache Layer",
            note_type="note",
            permalink="decisions/cache-layer",
            file_path="decisions/cache-layer.md",
            content_type="text/markdown",
            created_at=indexed_at,
            updated_at=indexed_at,
        )
        session.add(entity)
        await session.flush()
        entity_id = entity.id

        for stored in STORED_ASSERTIONS:
            observation = Observation(
                project_id=test_project.id,
                entity_id=entity_id,
                content=f"{SHARED_TERM} decision {stored.label}",
                category="decision",
            )
            session.add(observation)
            await session.flush()
            labels_by_id[observation.id] = stored.label

            session.add(
                MemoryTimeIndex(
                    project_id=test_project.id,
                    entity_id=entity_id,
                    source_type=SearchItemType.OBSERVATION.value,
                    source_id=observation.id,
                    time_kind=stored.kind.value,
                    range_axis=stored.valid_during.axis.value,
                    lower_value=stored.valid_during.lower,
                    upper_value=stored.valid_during.upper,
                    lower_inclusive=stored.valid_during.lower_inclusive,
                    upper_inclusive=stored.valid_during.upper_inclusive,
                    is_empty=stored.valid_during.is_empty,
                    extractor="observation",
                    source_text=str(stored.valid_during),
                )
            )

    for observation_id, label in labels_by_id.items():
        await search_repository.index_item(
            SearchIndexRow(
                id=observation_id,
                type=SearchItemType.OBSERVATION.value,
                title=f"decision: {SHARED_TERM} {label}",
                content_stems=f"{SHARED_TERM} decision {label}",
                content_snippet=f"{SHARED_TERM} decision {label}",
                permalink=f"decisions/cache-layer/observations/decision/{label}",
                file_path="decisions/cache-layer.md",
                category="decision",
                entity_id=entity_id,
                metadata={"tags": None},
                created_at=indexed_at,
                updated_at=indexed_at,
                project_id=test_project.id,
            )
        )
    return labels_by_id


async def _matching_labels(
    search_repository,
    labels_by_id: dict[int, str],
    temporal: TemporalFilter,
    *,
    search_text: str | None = SHARED_TERM,
) -> set[str]:
    """Run one valid-time search and translate the hits back into labels."""
    results = await search_repository.search(
        search_text=search_text,
        search_item_types=[SearchItemType.OBSERVATION],
        temporal=temporal,
        limit=50,
    )
    return {labels_by_id[result.id] for result in results}


# --- Acceptance 4: containment answers identically on both backends ---


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        # Inclusive lower endpoints are owned; exclusive ones are not.
        ("2026-06-10", {"closed_open", "closed_closed", "always"}),
        # Interior points belong to every interval that spans them.
        ("2026-07-01", {"closed_open", "open_closed", "closed_closed", "open_open", "always"}),
        # The cutover: exclusive upper ends have already expired, inclusive ones have not,
        # and the next period's inclusive lower end has begun.
        ("2026-07-27", {"open_closed", "closed_closed", "from_cutover", "always"}),
        # Before every bounded lower end: only the unbounded-below ranges remain.
        ("2026-06-01", {"before_june", "always"}),
        # After every bounded upper end: only the unbounded-above ranges remain.
        ("2026-08-01", {"from_cutover", "always"}),
    ],
    ids=["inclusive-lower", "interior", "cutover", "before-all", "after-all"],
)
@pytest.mark.asyncio
async def test_containment_contract(search_repository, temporal_population, at, expected):
    """`valid_at` returns exactly the ranges containing that date, on either backend."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point(at)),
    )

    assert matched == expected
    # The empty range contains no point, ever.
    assert "empty" not in matched


# --- Acceptance 4: overlap answers identically on both backends ---


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        # Adjacent half-open periods do not overlap: this is why `[a,b)` is the right
        # shape for a sequence of effective windows.
        ("[2026-07-27,2026-08-01)", {"open_closed", "closed_closed", "from_cutover", "always"}),
        # A window spanning the whole timeline meets every non-empty range.
        ("[2026-06-01,2026-08-01)", NON_EMPTY_DATE_LABELS),
        # An exclusive query lower end does not own the shared endpoint either.
        ("(2026-07-27,2026-08-01)", {"from_cutover", "always"}),
        # Unbounded below: only ranges that start before the exclusive upper end.
        ("(,2026-06-10)", {"before_june", "always"}),
        # Unbounded above: only ranges that have not already ended.
        ("[2026-08-01,)", {"from_cutover", "always"}),
        # A single closed point behaves exactly like containment of that point.
        ("[2026-06-10,2026-06-10]", {"closed_open", "closed_closed", "always"}),
    ],
    ids=[
        "adjacent-half-open",
        "spanning-window",
        "exclusive-lower",
        "unbounded-lower",
        "unbounded-upper",
        "degenerate-point",
    ],
)
@pytest.mark.asyncio
async def test_overlap_contract(search_repository, temporal_population, literal, expected):
    """`valid_overlaps` returns exactly the ranges sharing a point with the window."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, overlaps=_date_range(literal)),
    )

    assert matched == expected


@pytest.mark.asyncio
async def test_overlap_with_fully_unbounded_window_matches_every_non_empty_range(
    search_repository, temporal_population
):
    """A window with no endpoints separates nothing, so only `empty` is excluded."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, overlaps=TemporalRange(axis=DATE)),
    )

    assert matched == NON_EMPTY_DATE_LABELS


@pytest.mark.asyncio
async def test_overlap_with_empty_window_matches_nothing(search_repository, temporal_population):
    """PostgreSQL: nothing overlaps the empty range, not even the empty range."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, overlaps=TemporalRange.empty(DATE)),
    )

    assert matched == set()


@pytest.mark.asyncio
async def test_stored_empty_range_matches_no_query(search_repository, temporal_population):
    """An empty stored range contains no points, so no containment query finds it."""
    for at in ("2026-06-10", "2026-07-01", "2026-08-01"):
        matched = await _matching_labels(
            search_repository,
            temporal_population,
            TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point(at)),
        )
        assert "empty" not in matched, at


# --- The discrete domain: authored bounds vs. the days they denote ---
#
# Calendar dates are discrete, so an authored bound is not the boundary of the set of
# days it delimits. `basic_memory.temporal` closes that gap by storing every date range
# half-open; these tests are what proves the SQL predicate then answers about *days*
# rather than about endpoint strings -- identically on both backends.


async def _occurred_overlaps(search_repository, labels_by_id, literal: str) -> set[str]:
    return await _matching_labels(
        search_repository,
        labels_by_id,
        TemporalFilter(kind=TimeKind.OCCURRED, overlaps=_date_range(literal)),
    )


async def _occurred_at(search_repository, labels_by_id, at: str) -> set[str]:
    return await _matching_labels(
        search_repository,
        labels_by_id,
        TemporalFilter(kind=TimeKind.OCCURRED, at=parse_point(at)),
    )


@pytest.mark.asyncio
async def test_date_ranges_that_share_no_day_do_not_overlap(search_repository, temporal_population):
    """The case the half-open canonical form exists to get right.

    `(2026-03-01,2026-03-03)` holds only March 2 and `(2026-03-02,2026-03-04)` holds
    only March 3, so the two share nothing. Yet each range's raw endpoints lie inside
    the other's raw bounds, so comparing the bounds *as authored* reports an overlap
    that does not exist. Canonicalized to `[2026-03-02,2026-03-03)` and
    `[2026-03-03,2026-03-04)`, the same scalar comparison is right.
    """
    assert await _occurred_overlaps(
        search_repository, temporal_population, "(2026-03-01,2026-03-03)"
    ) == {"only_mar_02"}

    assert await _occurred_overlaps(
        search_repository, temporal_population, "(2026-03-02,2026-03-04)"
    ) == {"only_mar_03"}

    # And each holds exactly the one day it names.
    assert await _occurred_at(search_repository, temporal_population, "2026-03-02") == {
        "only_mar_02"
    }
    assert await _occurred_at(search_repository, temporal_population, "2026-03-03") == {
        "only_mar_03"
    }


@pytest.mark.asyncio
async def test_adjacent_half_open_ranges_share_no_day(search_repository, temporal_population):
    """`[a,b)` and `[b,c)` meet at b without sharing it -- the point of the shape."""
    assert await _occurred_overlaps(
        search_repository, temporal_population, "[2026-03-10,2026-03-12)"
    ) == {"half_open_first"}

    assert await _occurred_overlaps(
        search_repository, temporal_population, "[2026-03-12,2026-03-14)"
    ) == {"half_open_second"}

    # March 12 belongs to the second period alone.
    assert await _occurred_at(search_repository, temporal_population, "2026-03-11") == {
        "half_open_first"
    }
    assert await _occurred_at(search_repository, temporal_population, "2026-03-12") == {
        "half_open_second"
    }


@pytest.mark.asyncio
async def test_closed_ranges_sharing_an_endpoint_do_overlap(search_repository, temporal_population):
    """`[a,b]` and `[b,c]` both contain b, so they overlap on that one day.

    Canonicalization must preserve that: `[a,b+1)` and `[b,c+1)` still meet on b. An
    author who writes closed bounds means the endpoint day is included, and the stored
    form may not quietly take it away.
    """
    assert await _occurred_overlaps(
        search_repository, temporal_population, "[2026-03-20,2026-03-22]"
    ) == {"closed_first", "closed_second"}

    # Narrowed to the shared day alone, both are still there.
    assert await _occurred_at(search_repository, temporal_population, "2026-03-22") == {
        "closed_first",
        "closed_second",
    }
    assert await _occurred_at(search_repository, temporal_population, "2026-03-21") == {
        "closed_first"
    }
    assert await _occurred_at(search_repository, temporal_population, "2026-03-23") == {
        "closed_second"
    }


@pytest.mark.asyncio
async def test_a_single_day_range_holds_exactly_that_day(search_repository, temporal_population):
    """`[a,a]` is one day: neither empty, nor wider than the day the author wrote."""
    assert await _occurred_at(search_repository, temporal_population, "2026-03-30") == {"one_day"}
    assert await _occurred_at(search_repository, temporal_population, "2026-03-29") == set()
    assert await _occurred_at(search_repository, temporal_population, "2026-03-31") == set()

    assert await _occurred_overlaps(
        search_repository, temporal_population, "[2026-03-30,2026-03-30]"
    ) == {"one_day"}


@pytest.mark.asyncio
async def test_a_date_range_spanning_no_day_is_stored_empty(
    search_repository,
    session_maker: async_sessionmaker[AsyncSession],
    temporal_population,
    test_project: Project,
):
    """`(2026-03-05,2026-03-06)` reads as an interval but names no day.

    Only the discrete reading can tell: as a continuous interval it looks like an
    ordinary bounded range. Stored empty, it answers no question -- not even one about
    the days on either side of it.
    """
    for at in ("2026-03-05", "2026-03-06"):
        assert "no_such_day" not in await _occurred_at(
            search_repository, temporal_population, at
        ), at

    assert await _occurred_overlaps(
        search_repository, temporal_population, "[2026-03-01,2026-03-09)"
    ) == {"only_mar_02", "only_mar_03"}

    repository = MemoryTimeIndexRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        rows = await repository.find_for_sources(
            session,
            [(SearchItemType.OBSERVATION.value, source_id) for source_id in temporal_population],
        )
    row = {temporal_population[row.source_id]: row for row in rows}["no_such_day"]
    assert (row.is_empty, row.lower_value, row.upper_value) == (True, None, None)


@pytest.mark.asyncio
async def test_instant_ranges_are_untouched_by_the_date_canonicalization(
    search_repository, temporal_population
):
    """`instant_closed` is `[20:00Z,21:00Z]`, and stays exactly that.

    Instants are continuous: there is no next moment to close at, so the endpoint stays
    owned and is emphatically not pushed forward by a day the way an inclusive date end
    is. A query one microsecond past it, and one a whole day past it, both miss.
    """
    at_upper = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.OCCURRED, at=parse_point("2026-07-27T21:00:00Z")),
    )
    assert at_upper == {"instant_closed"}

    for outside in ("2026-07-27T21:00:00.000001Z", "2026-07-28T21:00:00Z"):
        missed = await _matching_labels(
            search_repository,
            temporal_population,
            TemporalFilter(kind=TimeKind.OCCURRED, at=parse_point(outside)),
        )
        assert missed == set(), outside


@pytest.mark.asyncio
async def test_projection_stores_date_bounds_in_the_canonical_half_open_form(
    session_maker: async_sessionmaker[AsyncSession],
    temporal_population,
    test_project: Project,
):
    """What actually lands in the columns the SQL predicate reads.

    The predicate compares bound values and inclusivity flags directly, so the
    canonical form has to be in the rows -- not merely in the domain value that built
    them.
    """
    repository = MemoryTimeIndexRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        rows = await repository.find_for_sources(
            session,
            [(SearchItemType.OBSERVATION.value, source_id) for source_id in temporal_population],
        )
    by_label = {temporal_population[row.source_id]: row for row in rows}

    # Authored `(2026-03-01,2026-03-03)`: the exclusive lower end moved to the next day.
    assert (by_label["only_mar_02"].lower_value, by_label["only_mar_02"].upper_value) == (
        "2026-03-02",
        "2026-03-03",
    )
    # Authored `[2026-03-20,2026-03-22]`: the inclusive upper end moved to the next day.
    assert (by_label["closed_first"].lower_value, by_label["closed_first"].upper_value) == (
        "2026-03-20",
        "2026-03-23",
    )
    # Authored `[2026-03-30,2026-03-30]`: one day, spelled half-open.
    assert (by_label["one_day"].lower_value, by_label["one_day"].upper_value) == (
        "2026-03-30",
        "2026-03-31",
    )
    for label in ("only_mar_02", "only_mar_03", "half_open_first", "closed_first", "one_day"):
        row = by_label[label]
        assert (row.lower_inclusive, row.upper_inclusive) == (True, False), label

    # The instant axis keeps the endpoint the author wrote, inclusivity and all.
    instant = by_label["instant_closed"]
    assert (instant.upper_value, instant.upper_inclusive) == ("2026-07-27T21:00:00.000000Z", True)


# --- Acceptance 9 and 10: the two axes are never confused ---


@pytest.mark.asyncio
async def test_date_query_does_not_match_instant_range(search_repository, temporal_population):
    """Acceptance 9: a calendar-date question never reaches an instant range.

    Converting one into the other would have to invent a time of day or a timezone the
    author never wrote, so the axes are simply disjoint.
    """
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-27")),
    )

    assert "instant_window" not in matched
    assert "instant_offset" not in matched


@pytest.mark.asyncio
async def test_instant_query_does_not_match_date_range(search_repository, temporal_population):
    """The mirror image: an instant question never reaches a calendar-date range."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(
            kind=TimeKind.EFFECTIVE,
            at=parse_point("2026-07-27T17:00:00Z"),
        ),
    )

    assert matched == {"instant_window"}
    assert not matched & DATE_LABELS


@pytest.mark.asyncio
async def test_instant_ranges_compare_as_instants_across_offsets(
    search_repository, temporal_population
):
    """Acceptance 10: an offset bound names an instant and is compared as one.

    `instant_offset` was authored as `[16:00+02:00,17:00+02:00)`, which is the UTC
    window `[14:00Z,15:00Z)`. A UTC query point inside that window matches it; the same
    clock reading interpreted naively would not.
    """
    inside = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-27T14:30:00Z")),
    )
    assert inside == {"instant_offset"}

    # 16:00 in +02:00 is 14:00Z, so the naive reading of the same digits is outside it.
    outside = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-27T16:30:00Z")),
    )
    assert outside == {"instant_window"}


@pytest.mark.asyncio
async def test_instant_endpoints_respect_inclusivity(search_repository, temporal_population):
    """Instant bounds obey the same endpoint rules as dates, to the microsecond."""
    at_lower = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-27T16:00:00Z")),
    )
    assert at_lower == {"instant_window"}

    at_upper = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-27T18:00:00Z")),
    )
    assert at_upper == set()


# --- Kind narrowing ---


@pytest.mark.asyncio
async def test_kind_filter_narrows_to_one_kind(search_repository, temporal_population):
    """Two kinds can assert the same interval; a kind filter separates them."""
    due = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.DUE, at=parse_point("2026-07-01")),
    )

    assert due == {"due_window"}


@pytest.mark.asyncio
async def test_filter_without_a_kind_spans_every_kind(search_repository, temporal_population):
    """Omitting the kind asks the question of every kind at once."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(at=parse_point("2026-07-01")),
    )

    assert matched == {
        "closed_open",
        "open_closed",
        "closed_closed",
        "open_open",
        "always",
        "due_window",
    }


@pytest.mark.asyncio
async def test_kind_only_filter_selects_every_source_of_that_kind(
    search_repository, temporal_population
):
    """A kind with no window is a legal question, and the empty range still answers it.

    Without a window there is no axis to compare on and no interval to intersect, so
    the filter asks only "does this source assert anything on this kind" -- which the
    empty range does.
    """
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE),
    )

    assert matched == DATE_LABELS | {"instant_window", "instant_offset"}


# --- Acceptance 1 and 11: only authored bounds ever participate ---


@pytest.mark.asyncio
async def test_note_without_qualifier_writes_no_temporal_rows(
    search_repository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    temporal_population,
):
    """Acceptance 1: an undated observation is indexed, and projects no valid time."""
    indexed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="Queue Layer",
            note_type="note",
            permalink="decisions/queue-layer",
            file_path="decisions/queue-layer.md",
            content_type="text/markdown",
            created_at=indexed_at,
            updated_at=indexed_at,
        )
        session.add(entity)
        await session.flush()
        observation = Observation(
            project_id=test_project.id,
            entity_id=entity.id,
            content=f"{SHARED_TERM} undated decision",
            category="decision",
        )
        session.add(observation)
        await session.flush()
        undated_id = observation.id
        undated_entity_id = entity.id

    await search_repository.index_item(
        SearchIndexRow(
            id=undated_id,
            type=SearchItemType.OBSERVATION.value,
            title="decision: undated",
            content_stems=f"{SHARED_TERM} undated decision",
            content_snippet=f"{SHARED_TERM} undated decision",
            permalink="decisions/queue-layer/observations/decision/undated",
            file_path="decisions/queue-layer.md",
            category="decision",
            entity_id=undated_entity_id,
            metadata={"tags": None},
            created_at=indexed_at,
            updated_at=indexed_at,
            project_id=test_project.id,
        )
    )

    # Unfiltered, the undated observation is an ordinary hit.
    unfiltered = await search_repository.search(
        search_text=SHARED_TERM,
        search_item_types=[SearchItemType.OBSERVATION],
        limit=50,
    )
    assert undated_id in {result.id for result in unfiltered}

    # Under any valid-time filter it is absent: it makes no claim to answer with.
    filtered = await search_repository.search(
        search_text=SHARED_TERM,
        search_item_types=[SearchItemType.OBSERVATION],
        temporal=TemporalFilter(at=parse_point("2026-07-01")),
        limit=50,
    )
    assert undated_id not in {result.id for result in filtered}


@pytest.mark.asyncio
async def test_projection_rows_carry_only_authored_bounds(
    session_maker: async_sessionmaker[AsyncSession],
    temporal_population,
    test_project: Project,
):
    """Acceptance 11: nothing but the authored qualifier reaches the stored bounds.

    Entity `created_at`/`updated_at` are deliberately January 1 while every authored
    window is in June/July. If edit bookkeeping ever leaked into the projection, one of
    these bounds would carry a January value.
    """
    repository = MemoryTimeIndexRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        rows = await repository.find_for_sources(
            session,
            [(SearchItemType.OBSERVATION.value, source_id) for source_id in temporal_population],
        )

    by_label = {temporal_population[row.source_id]: row for row in rows}
    assert by_label["closed_open"].lower_value == "2026-06-10"
    assert by_label["closed_open"].upper_value == "2026-07-27"
    assert by_label["from_cutover"].upper_value is None
    assert by_label["always"].lower_value is None and by_label["always"].upper_value is None
    assert by_label["empty"].is_empty is True
    for row in rows:
        for bound in (row.lower_value, row.upper_value):
            assert bound is None or not bound.startswith("2026-01"), row.source_text


# --- Pagination parity: filter and count must agree ---


@pytest.mark.asyncio
async def test_temporal_filter_count_matches_search(search_repository, temporal_population):
    """`count()` runs the same predicate as `search()`, or pagination lies.

    The router gathers the two concurrently and derives `has_more` from the count, so a
    count that ignored the filter would report pages that do not exist.
    """
    temporal = TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-01"))
    results = await search_repository.search(
        search_text=SHARED_TERM,
        search_item_types=[SearchItemType.OBSERVATION],
        temporal=temporal,
        limit=50,
    )
    total = await search_repository.count(
        search_text=SHARED_TERM,
        search_item_types=[SearchItemType.OBSERVATION],
        temporal=temporal,
    )

    assert total == len(results) == 5


@pytest.mark.asyncio
async def test_temporal_filter_applies_without_search_text(search_repository, temporal_population):
    """A valid-time filter is criteria on its own; no MATCH is required to use it."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-08-01")),
        search_text=None,
    )

    assert matched == {"from_cutover", "always"}


@pytest.mark.asyncio
async def test_temporal_filter_is_scoped_to_its_project(
    search_repository,
    session_maker: async_sessionmaker[AsyncSession],
    project_repository,
    temporal_population,
):
    """Assertions belong to a project; another project's rows can never match here."""
    async with db.scoped_session(session_maker) as session:
        other_project = await project_repository.create(
            session,
            {
                "name": "other-project",
                "description": "Isolation check",
                "path": "/other/project",
                "is_active": True,
                "is_default": None,
            },
        )

    other_repository = type(search_repository)(
        search_repository.session_maker, project_id=other_project.id
    )
    results = await other_repository.search(
        search_text=SHARED_TERM,
        search_item_types=[SearchItemType.OBSERVATION],
        temporal=TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-01")),
        limit=50,
    )

    assert results == []


@pytest.mark.asyncio
async def test_temporal_point_and_range_agree_on_containment(
    search_repository, temporal_population
):
    """A point question is the degenerate closed range, so the two cannot disagree."""
    point = TemporalFilter(kind=TimeKind.EFFECTIVE, at=TemporalPoint(axis=DATE, value="2026-07-27"))
    window = TemporalFilter(
        kind=TimeKind.EFFECTIVE,
        overlaps=TemporalRange(
            axis=DATE,
            lower="2026-07-27",
            upper="2026-07-27",
            lower_inclusive=True,
            upper_inclusive=True,
        ),
    )

    assert await _matching_labels(
        search_repository, temporal_population, point
    ) == await _matching_labels(search_repository, temporal_population, window)
