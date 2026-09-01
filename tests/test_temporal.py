"""The portable temporal value types and their lexical grammar (SPEC-82).

These values are the shared vocabulary between the markdown parser, the projection, and
both search dialects. Two properties carry the whole design and are pinned here:

* Canonical bounds are fixed width, so byte-lexicographic order *is* chronological
  order -- which is what lets one SQL predicate serve SQLite and PostgreSQL alike.
* Dates and instants are separate axes. A date never gains a time of day or a zone, and
  an instant written without an offset is read as UTC -- the same convention the rest
  of the codebase applies to naive datetimes.
"""

from datetime import date, datetime, timedelta

import pytest

from basic_memory.temporal import (
    DEFAULT_DATE_ORDER,
    TemporalAssertion,
    TemporalFilter,
    TemporalPoint,
    TemporalQualifierError,
    TemporalRange,
    TemporalRangeKind,
    TimeRole,
    canonical_bound,
    parse_authored_point,
    parse_point,
    parse_range_literal,
)

DATE = TemporalRangeKind.DATE
INSTANT = TemporalRangeKind.INSTANT


# --- Canonical bounds ---


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("2026-07-27T18:42:00Z", "2026-07-27T18:42:00.000000Z"),
        ("2026-07-27t18:42:00z", "2026-07-27T18:42:00.000000Z"),
        ("2026-07-27T18:42:00+02:00", "2026-07-27T16:42:00.000000Z"),
        ("2026-07-27T18:42:00-05:00", "2026-07-27T23:42:00.000000Z"),
        ("2026-07-27T18:42:00.5Z", "2026-07-27T18:42:00.500000Z"),
        ("2026-07-27T18:42:00.123456Z", "2026-07-27T18:42:00.123456Z"),
    ],
)
def test_instant_bounds_normalize_to_fixed_width_utc(written: str, canonical: str):
    """Every instant lands on the same 27-character UTC form, whatever it was written as."""
    assert canonical_bound(written, INSTANT) == canonical
    assert len(canonical) == 27


def test_canonical_instants_sort_chronologically_as_plain_strings():
    """Fixed width plus fixed separator positions makes string order time order.

    This is the property the SQL predicate relies on: comparing canonical text columns
    with `<` and `>` is comparing moments, on either backend, with no typed date bind.
    """
    written = [
        "2026-07-27T18:42:00+02:00",  # 16:42Z
        "2026-07-27T17:00:00Z",
        "2026-07-26T23:59:59Z",
        "2026-07-27T18:42:00Z",
    ]
    canonical = [canonical_bound(bound, INSTANT) for bound in written]

    assert sorted(canonical) == [
        "2026-07-26T23:59:59.000000Z",
        "2026-07-27T16:42:00.000000Z",
        "2026-07-27T17:00:00.000000Z",
        "2026-07-27T18:42:00.000000Z",
    ]


def test_date_bounds_are_already_canonical():
    assert canonical_bound("2026-07-27", DATE) == "2026-07-27"


@pytest.mark.parametrize(
    "bound",
    [
        "20260727",  # compact ISO: accepted by date.fromisoformat, breaks fixed width
        "2026-7-27",
        "27-07-2026",
        "2026-02-30",
        "not-a-date",
    ],
)
def test_malformed_date_bounds_are_refused(bound: str):
    with pytest.raises(TemporalQualifierError):
        canonical_bound(bound, DATE)


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("2026-07-27T18:42:00", "2026-07-27T18:42:00.000000Z"),
        ("2026-07-27t18:42:00", "2026-07-27T18:42:00.000000Z"),
        ("2026-07-27T18:42:00.5", "2026-07-27T18:42:00.500000Z"),
    ],
)
def test_naive_timestamp_bounds_are_read_as_utc(written: str, canonical: str):
    """A timestamp with no offset is UTC, not an error.

    This is the house convention for every other naive datetime in the codebase, and
    it is what lets an author write a timestamp without learning RFC 3339's offset
    syntax first.
    """
    assert canonical_bound(written, INSTANT) == canonical


@pytest.mark.parametrize(
    "bound",
    [
        "2026-07-27 18:42:00",  # space separator: not the canonical bound shape
        "2026-07-27T18:42",  # no seconds
        "2026-07-27T18:42:00.1234567Z",  # finer than microseconds: would be truncated
        "2026-07-27",
    ],
)
def test_malformed_instant_bounds_are_refused(bound: str):
    with pytest.raises(TemporalQualifierError):
        canonical_bound(bound, INSTANT)


def test_sub_microsecond_precision_is_refused_rather_than_truncated():
    """Dropping digits would make the stored bound name a different instant."""
    with pytest.raises(TemporalQualifierError, match="microsecond precision"):
        canonical_bound("2026-07-27T18:42:00.1234567Z", INSTANT)


def test_timestamp_shaped_bound_on_a_date_that_does_not_exist_is_refused():
    """The lexical shape admits `2026-02-30T...`; the calendar does not."""
    with pytest.raises(TemporalQualifierError, match="not a valid timestamp"):
        canonical_bound("2026-02-30T10:00:00Z", INSTANT)


# --- TemporalPoint ---


def test_point_rejects_a_non_canonical_value():
    """A value that skipped canonicalization must not enter the domain."""
    with pytest.raises(TemporalQualifierError, match="not canonical"):
        TemporalPoint(kind=INSTANT, value="2026-07-27T18:42:00Z")


def test_point_renders_its_canonical_value():
    assert str(TemporalPoint(kind=DATE, value="2026-07-27")) == "2026-07-27"


def test_parse_point_infers_the_axis_from_what_was_written():
    assert parse_point("2026-07-27") == TemporalPoint(kind=DATE, value="2026-07-27")
    assert parse_point(" 2026-07-27T18:42:00+02:00 ") == TemporalPoint(
        kind=INSTANT, value="2026-07-27T16:42:00.000000Z"
    )


def test_parse_point_refuses_an_empty_string():
    with pytest.raises(TemporalQualifierError, match="must not be empty"):
        parse_point("   ")


def test_parse_point_reads_a_naive_timestamp_as_utc():
    """The search boundary follows the same naive-is-UTC rule as authored bounds."""
    assert parse_point("2026-07-27T18:42:00") == TemporalPoint(
        kind=INSTANT, value="2026-07-27T18:42:00.000000Z"
    )


# --- Flexible authored points ---
#
# The convenient form. `parse_authored_point` reads whatever dateparser reads and
# canonicalizes it into a TemporalRange, so an author never has to spell out a range
# literal to say when something started.


@pytest.mark.parametrize(
    ("written", "literal", "kind"),
    [
        # A year and a month are periods the author delimited by writing them.
        ("2026", "[2026-01-01,2027-01-01)", DATE),
        ("2026-06", "[2026-06-01,2026-07-01)", DATE),
        ("2026-12", "[2026-12-01,2027-01-01)", DATE),
        ("June 2026", "[2026-06-01,2026-07-01)", DATE),
        # A date or a moment is not: it says when something started and left it open.
        ("2026-06-10", "[2026-06-10,)", DATE),
        ("Jan 15, 2024", "[2024-01-15,)", DATE),
        ("2026-06-10T14:00:00", "[2026-06-10T14:00:00.000000Z,)", INSTANT),
        ("2026-06-10T14:00:00Z", "[2026-06-10T14:00:00.000000Z,)", INSTANT),
        ("2026-06-10T14:00:00+02:00", "[2026-06-10T12:00:00.000000Z,)", INSTANT),
        ("  2026-06-10  ", "[2026-06-10,)", DATE),
    ],
)
def test_authored_point_denotes_the_span_its_precision_covers(written, literal, kind):
    span = parse_authored_point(written)

    assert span is not None
    assert str(span) == literal
    assert span.kind is kind
    assert span.lower_inclusive is True


def test_authored_date_never_acquires_a_time_of_day():
    """A calendar date must not become midnight UTC on the way in.

    Midnight in *which* zone is a question the author never answered, and answering it
    for them would make a date query and an instant query disagree about the same note.
    """
    span = parse_authored_point("2026-06-10")

    assert span is not None
    assert span.kind is DATE
    assert span.lower == "2026-06-10"
    assert "T" not in span.lower and "Z" not in span.lower


def test_authored_naive_timestamp_is_read_as_utc_not_local_time():
    """The two spellings of the same moment produce the same stored bound."""
    naive = parse_authored_point("2026-06-10T14:00:00")
    explicit = parse_authored_point("2026-06-10T14:00:00Z")

    assert naive is not None and explicit is not None
    assert naive == explicit
    assert naive.kind is INSTANT
    assert naive.lower == "2026-06-10T14:00:00.000000Z"


def test_authored_relative_dates_resolve_at_parse_time():
    """`yesterday` is read against the clock now, and re-read on every index pass.

    That is documented behavior rather than a diagnostic: a file edited by hand keeps
    its relative wording, and each pass resolves it fresh.
    """
    span = parse_authored_point("yesterday")

    assert span is not None
    assert span.kind is DATE
    yesterday = datetime.now().date() - timedelta(days=1)
    assert span.lower == yesterday.isoformat()


@pytest.mark.parametrize(
    ("date_order", "expected_lower"),
    [("YMD", "2026-07-10"), ("DMY", "2026-07-10"), ("MDY", "2026-10-07")],
)
def test_date_order_decides_an_ambiguous_slash_date(date_order, expected_lower):
    """`10/07/2026` is July 10 or October 7 depending on the configured preference."""
    span = parse_authored_point("10/07/2026", date_order=date_order)

    assert span is not None
    assert span.lower == expected_lower


def test_iso_dates_are_never_re_guessed_by_date_order():
    """An ISO date is unambiguous, so no preference may reinterpret it."""
    for date_order in ("YMD", "DMY", "MDY"):
        span = parse_authored_point("2026-07-10", date_order=date_order)
        assert span is not None
        assert span.lower == "2026-07-10", date_order


def test_the_default_date_order_is_iso():
    assert DEFAULT_DATE_ORDER == "YMD"
    assert parse_authored_point("10/07/2026") == parse_authored_point(
        "10/07/2026", date_order="YMD"
    )


@pytest.mark.parametrize(
    "written",
    [
        "2026-02-30",  # ISO-shaped, but February has no 30th
        "2026-13-01",  # ISO-shaped, but there is no 13th month
    ],
)
def test_impossible_iso_dates_are_unread_rather_than_re_interpreted(written: str):
    """dateparser reads `2026-13-01` as the 13th of January; a wrong date is worse.

    The canonical ISO shape takes the strict path precisely so leniency cannot invent
    a date the author did not write.
    """
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    "written",
    ["paul", "basicmemory.com", "ops@example.com", "someone(2026)", "Redis.", "Q3"],
)
def test_text_that_names_no_date_reads_as_nothing(written: str):
    """No error and no assertion: the caller leaves such a token as content."""
    assert parse_authored_point(written) is None


def test_a_year_with_no_successor_is_unread():
    """Year 9999 has no January 1 after it to close the span with."""
    assert parse_authored_point("9999") is None
    # The year before it still resolves, so the guard is the calendar edge, not 4 digits.
    assert parse_authored_point("9998") == TemporalRange(
        kind=DATE,
        lower=date(9998, 1, 1).isoformat(),
        upper=date(9999, 1, 1).isoformat(),
        lower_inclusive=True,
    )


# --- TemporalRange normalization ---


def test_unbounded_sides_are_forced_exclusive():
    """PostgreSQL's rule: there is no endpoint to include, so inclusivity is meaningless."""
    span = TemporalRange(
        kind=DATE, lower=None, upper="2026-07-27", lower_inclusive=True, upper_inclusive=True
    )

    assert span.lower_inclusive is False
    assert span.upper_inclusive is True
    assert str(span) == "(,2026-07-27]"


def test_fully_unbounded_range_is_exclusive_on_both_sides():
    span = TemporalRange(kind=DATE, lower_inclusive=True, upper_inclusive=True)

    assert (span.lower_inclusive, span.upper_inclusive) == (False, False)
    assert str(span) == "(,)"


@pytest.mark.parametrize(
    ("lower_inclusive", "upper_inclusive"),
    [(True, False), (False, True), (False, False)],
)
def test_degenerate_range_collapses_to_empty(lower_inclusive: bool, upper_inclusive: bool):
    """`[a,a)`, `(a,a]`, and `(a,a)` contain no points, so they *are* the empty range."""
    span = TemporalRange(
        kind=DATE,
        lower="2026-07-27",
        upper="2026-07-27",
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
    )

    assert span.is_empty is True
    assert span.lower is None and span.upper is None
    assert str(span) == "empty"


def test_closed_single_point_range_is_not_empty():
    """`[a,a]` contains exactly one point, which is a real interval."""
    span = TemporalRange(
        kind=DATE,
        lower="2026-07-27",
        upper="2026-07-27",
        lower_inclusive=True,
        upper_inclusive=True,
    )

    assert span.is_empty is False
    assert str(span) == "[2026-07-27,2026-07-27]"


def test_inverted_range_is_refused():
    with pytest.raises(TemporalQualifierError, match="after upper bound"):
        TemporalRange(kind=DATE, lower="2026-08-01", upper="2026-06-10")


def test_empty_range_cannot_carry_bounds():
    """Two representations of the same interval would make equality lie."""
    with pytest.raises(TemporalQualifierError, match="carries no bounds"):
        TemporalRange(kind=DATE, lower="2026-07-27", is_empty=True)
    with pytest.raises(TemporalQualifierError, match="carries no bounds"):
        TemporalRange(kind=DATE, is_empty=True, upper_inclusive=True)


def test_range_rejects_non_canonical_bounds():
    with pytest.raises(TemporalQualifierError, match="not canonical"):
        TemporalRange(kind=INSTANT, lower="2026-07-27T18:42:00Z")


def test_empty_constructor_builds_the_empty_range_on_one_axis():
    span = TemporalRange.empty(INSTANT)

    assert (span.kind, span.is_empty, span.lower, span.upper) == (INSTANT, True, None, None)


# --- Range literals ---


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("[2026-06-10,2026-07-27)", (True, False, "2026-06-10", "2026-07-27")),
        ("(2026-06-10,2026-07-27]", (False, True, "2026-06-10", "2026-07-27")),
        ("[2026-06-10,2026-07-27]", (True, True, "2026-06-10", "2026-07-27")),
        ("(2026-06-10,2026-07-27)", (False, False, "2026-06-10", "2026-07-27")),
        ("[2026-06-10,)", (True, False, "2026-06-10", None)),
        ("(,2026-07-27]", (False, True, None, "2026-07-27")),
    ],
)
def test_range_literal_round_trips_through_its_canonical_rendering(literal, expected):
    span = parse_range_literal(literal)

    assert (span.lower_inclusive, span.upper_inclusive, span.lower, span.upper) == expected
    assert str(span) == literal


def test_range_literal_tolerates_surrounding_whitespace():
    assert str(parse_range_literal("  [2026-06-10, 2026-07-27)  ")) == "[2026-06-10,2026-07-27)"


def test_empty_literal_requires_an_explicit_axis():
    """`empty` carries no bounds to classify, so the caller must name the axis."""
    assert parse_range_literal("empty", kind=DATE).is_empty is True
    with pytest.raises(TemporalQualifierError, match="kind must be given"):
        parse_range_literal("empty")


def test_fully_unbounded_literal_requires_an_explicit_axis():
    assert parse_range_literal("(,)", kind=INSTANT).kind is INSTANT
    with pytest.raises(TemporalQualifierError, match="no bounds to classify"):
        parse_range_literal("(,)")


def test_range_literal_refuses_mixed_axes():
    with pytest.raises(TemporalQualifierError, match="mix date-only and timestamp bounds"):
        parse_range_literal("[2026-06-10,2026-07-27T00:00:00Z)")


def test_range_literal_refuses_an_axis_it_was_not_asked_for():
    with pytest.raises(TemporalQualifierError, match="expected instant bounds"):
        parse_range_literal("[2026-06-10,2026-07-27)", kind=INSTANT)


@pytest.mark.parametrize(
    "literal",
    [
        "2026-06-10,2026-07-27",  # no brackets
        "[2026-06-10]",  # no comma
        "[2026-06-10,2026-07-27",  # unbalanced
        "[2026-06-10,2026-07-27,2026-08-01)",  # three bounds
        "",
    ],
)
def test_malformed_range_literals_are_refused(literal: str):
    with pytest.raises(TemporalQualifierError, match="range literal must be"):
        parse_range_literal(literal)


# --- TemporalFilter ---


def test_filter_refuses_asking_two_questions_at_once():
    with pytest.raises(TemporalQualifierError, match="never both"):
        TemporalFilter(
            at=parse_point("2026-07-27"),
            overlaps=parse_range_literal("[2026-06-10,2026-07-27)"),
        )


def test_filter_refuses_asking_nothing_at_all():
    """A filter that names no role, point, or range would match everything silently."""
    with pytest.raises(TemporalQualifierError, match="must name a role"):
        TemporalFilter()


def test_point_filter_window_is_the_degenerate_closed_range():
    """Containment is overlap with `[p,p]`, which is why one predicate answers both."""
    window = TemporalFilter(at=parse_point("2026-07-27")).window

    assert window == TemporalRange(
        kind=DATE,
        lower="2026-07-27",
        upper="2026-07-27",
        lower_inclusive=True,
        upper_inclusive=True,
    )


def test_overlap_filter_window_is_the_range_itself():
    span = parse_range_literal("[2026-06-10,2026-07-27)")

    assert TemporalFilter(overlaps=span).window == span


def test_role_only_filter_has_no_window():
    """Nothing to intersect: the question is only "does this axis carry an assertion"."""
    assert TemporalFilter(role=TimeRole.EFFECTIVE).window is None


# --- TemporalAssertion ---


def test_assertion_defaults_to_the_observation_extractor():
    assertion = TemporalAssertion(
        time_role=TimeRole.EFFECTIVE,
        valid_during=parse_range_literal("[2026-06-10,2026-07-27)"),
        source_text="@effective[2026-06-10,2026-07-27)",
    )

    assert assertion.extractor == "observation"
    assert assertion.metadata is None


def test_recorded_time_is_not_an_authorable_role():
    """Recorded time is never written in markdown, so no role names it."""
    assert "recorded" not in {role.value for role in TimeRole}
    assert {role.value for role in TimeRole} == {
        "effective",
        "valid",
        "occurred",
        "due",
        "mentioned",
    }
