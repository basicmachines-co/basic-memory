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
    TemporalRangeKind,
    TimeRole,
    parse_point,
    parse_range_literal,
)

DATE = TemporalRangeKind.DATE
INSTANT = TemporalRangeKind.INSTANT

# Every observation shares this word so one FTS query returns the whole population and
# the temporal predicate is the only thing that narrows it. That also proves the
# predicate composes with a MATCH/bm25 query rather than only with a bare scan.
SHARED_TERM = "cachelayer"


@dataclass(frozen=True, slots=True)
class StoredAssertion:
    """One authored assertion, and the label the expectations refer to it by."""

    label: str
    valid_during: TemporalRange
    role: TimeRole = TimeRole.EFFECTIVE


def _date_range(literal: str) -> TemporalRange:
    return parse_range_literal(literal, kind=DATE)


def _instant_range(literal: str) -> TemporalRange:
    return parse_range_literal(literal, kind=INSTANT)


# The population under test. Labels are the vocabulary of every expectation below.
STORED_ASSERTIONS: tuple[StoredAssertion, ...] = (
    StoredAssertion("closed_open", _date_range("[2026-06-10,2026-07-27)")),
    StoredAssertion("open_closed", _date_range("(2026-06-10,2026-07-27]")),
    StoredAssertion("closed_closed", _date_range("[2026-06-10,2026-07-27]")),
    StoredAssertion("open_open", _date_range("(2026-06-10,2026-07-27)")),
    StoredAssertion("from_cutover", _date_range("[2026-07-27,)")),
    StoredAssertion("before_june", _date_range("(,2026-06-10)")),
    StoredAssertion("always", TemporalRange(kind=DATE)),
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
    StoredAssertion("due_window", _date_range("[2026-06-10,2026-07-27)"), role=TimeRole.DUE),
)

DATE_LABELS = frozenset(
    stored.label
    for stored in STORED_ASSERTIONS
    if stored.valid_during.kind is DATE and stored.role is TimeRole.EFFECTIVE
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
                    time_role=stored.role.value,
                    range_kind=stored.valid_during.kind.value,
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
        TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point(at)),
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
        TemporalFilter(role=TimeRole.EFFECTIVE, overlaps=_date_range(literal)),
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
        TemporalFilter(role=TimeRole.EFFECTIVE, overlaps=TemporalRange(kind=DATE)),
    )

    assert matched == NON_EMPTY_DATE_LABELS


@pytest.mark.asyncio
async def test_overlap_with_empty_window_matches_nothing(search_repository, temporal_population):
    """PostgreSQL: nothing overlaps the empty range, not even the empty range."""
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(role=TimeRole.EFFECTIVE, overlaps=TemporalRange.empty(DATE)),
    )

    assert matched == set()


@pytest.mark.asyncio
async def test_stored_empty_range_matches_no_query(search_repository, temporal_population):
    """An empty stored range contains no points, so no containment query finds it."""
    for at in ("2026-06-10", "2026-07-01", "2026-08-01"):
        matched = await _matching_labels(
            search_repository,
            temporal_population,
            TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point(at)),
        )
        assert "empty" not in matched, at


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
        TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-27")),
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
            role=TimeRole.EFFECTIVE,
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
        TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-27T14:30:00Z")),
    )
    assert inside == {"instant_offset"}

    # 16:00 in +02:00 is 14:00Z, so the naive reading of the same digits is outside it.
    outside = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-27T16:30:00Z")),
    )
    assert outside == {"instant_window"}


@pytest.mark.asyncio
async def test_instant_endpoints_respect_inclusivity(search_repository, temporal_population):
    """Instant bounds obey the same endpoint rules as dates, to the microsecond."""
    at_lower = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-27T16:00:00Z")),
    )
    assert at_lower == {"instant_window"}

    at_upper = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-27T18:00:00Z")),
    )
    assert at_upper == set()


# --- Role narrowing ---


@pytest.mark.asyncio
async def test_role_filter_narrows_to_one_axis(search_repository, temporal_population):
    """Two roles can assert the same interval; a role filter separates them."""
    due = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(role=TimeRole.DUE, at=parse_point("2026-07-01")),
    )

    assert due == {"due_window"}


@pytest.mark.asyncio
async def test_filter_without_role_spans_every_axis(search_repository, temporal_population):
    """Omitting the role asks the question of every axis at once."""
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
async def test_role_only_filter_selects_every_source_on_that_axis(
    search_repository, temporal_population
):
    """A role with no window is a legal question, and the empty range still answers it.

    Without a window there is no axis to compare on and no interval to intersect, so
    the filter asks only "does this source assert anything on this role" -- which the
    empty range does.
    """
    matched = await _matching_labels(
        search_repository,
        temporal_population,
        TemporalFilter(role=TimeRole.EFFECTIVE),
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
    temporal = TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-01"))
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
        TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-08-01")),
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
        temporal=TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-01")),
        limit=50,
    )

    assert results == []


@pytest.mark.asyncio
async def test_temporal_point_and_range_agree_on_containment(
    search_repository, temporal_population
):
    """A point question is the degenerate closed range, so the two cannot disagree."""
    point = TemporalFilter(role=TimeRole.EFFECTIVE, at=TemporalPoint(kind=DATE, value="2026-07-27"))
    window = TemporalFilter(
        role=TimeRole.EFFECTIVE,
        overlaps=TemporalRange(
            kind=DATE,
            lower="2026-07-27",
            upper="2026-07-27",
            lower_inclusive=True,
            upper_inclusive=True,
        ),
    )

    assert await _matching_labels(
        search_repository, temporal_population, point
    ) == await _matching_labels(search_repository, temporal_population, window)
