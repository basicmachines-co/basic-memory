"""The portable temporal value types and their lexical grammar (SPEC-82).

These values are the shared vocabulary between the markdown parser, the projection, and
both search dialects. Two properties carry the whole design and are pinned here:

* Canonical bounds are fixed width, so byte-lexicographic order *is* chronological
  order -- which is what lets one SQL predicate serve SQLite and PostgreSQL alike.
* Dates and instants are separate axes. A date never gains a time of day or a zone, and
  an instant written without an offset is read as UTC -- the same convention the rest
  of the codebase applies to naive datetimes.
"""

from datetime import datetime, timedelta

import pytest

from basic_memory.temporal import (
    DEFAULT_DATE_ORDER,
    TemporalAssertion,
    TemporalFilter,
    TemporalPoint,
    TemporalQualifierError,
    TemporalRange,
    TemporalRangeAxis,
    TimeKind,
    canonical_bound,
    parse_authored_point,
    parse_point,
    parse_range_literal,
)

DATE = TemporalRangeAxis.DATE
INSTANT = TemporalRangeAxis.INSTANT


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


@pytest.mark.parametrize(
    "bound",
    [
        "9999-12-31T23:59:59-05:00",  # 10000-01-01 in UTC
        "0001-01-01T00:00:00+05:00",  # year 0 in UTC
    ],
)
def test_instant_bounds_that_leave_the_calendar_in_utc_are_refused(bound: str):
    """Normalizing to UTC *moves* a moment, and the move can run off the calendar.

    Refused as a `TemporalQualifierError` like every other unreadable bound, which is
    what makes it survivable: `datetime.astimezone` signals this with `OverflowError`,
    and an `OverflowError` is not a `ValueError`, so it slipped past every handler above
    -- failing a whole note's parse, or a whole search request, over one bound.
    """
    with pytest.raises(TemporalQualifierError, match="leaves the calendar"):
        canonical_bound(bound, INSTANT)


# --- TemporalPoint ---


def test_point_rejects_a_non_canonical_value():
    """A value that skipped canonicalization must not enter the domain."""
    with pytest.raises(TemporalQualifierError, match="not canonical"):
        TemporalPoint(axis=INSTANT, value="2026-07-27T18:42:00Z")


def test_point_renders_its_canonical_value():
    assert str(TemporalPoint(axis=DATE, value="2026-07-27")) == "2026-07-27"


def test_parse_point_infers_the_axis_from_what_was_written():
    assert parse_point("2026-07-27") == TemporalPoint(axis=DATE, value="2026-07-27")
    assert parse_point(" 2026-07-27T18:42:00+02:00 ") == TemporalPoint(
        axis=INSTANT, value="2026-07-27T16:42:00.000000Z"
    )


def test_parse_point_refuses_an_empty_string():
    with pytest.raises(TemporalQualifierError, match="must not be empty"):
        parse_point("   ")


def test_parse_point_reads_a_naive_timestamp_as_utc():
    """The search boundary follows the same naive-is-UTC rule as authored bounds."""
    assert parse_point("2026-07-27T18:42:00") == TemporalPoint(
        axis=INSTANT, value="2026-07-27T18:42:00.000000Z"
    )


# --- Flexible authored points ---
#
# The convenient form. `parse_authored_point` reads whatever dateparser reads and
# canonicalizes it into a TemporalRange, so an author never has to spell out a range
# literal to say when something started.


@pytest.mark.parametrize(
    ("written", "literal", "axis"),
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
def test_authored_point_denotes_the_span_its_precision_covers(written, literal, axis):
    span = parse_authored_point(written)

    assert span is not None
    assert str(span) == literal
    assert span.axis is axis
    assert span.lower_inclusive is True


def test_authored_date_never_acquires_a_time_of_day():
    """A calendar date must not become midnight UTC on the way in.

    Midnight in *which* zone is a question the author never answered, and answering it
    for them would make a date query and an instant query disagree about the same note.
    """
    span = parse_authored_point("2026-06-10")

    assert span is not None
    assert span.axis is DATE
    assert span.lower == "2026-06-10"
    assert "T" not in span.lower and "Z" not in span.lower


def test_authored_naive_timestamp_is_read_as_utc_not_local_time():
    """The two spellings of the same moment produce the same stored bound."""
    naive = parse_authored_point("2026-06-10T14:00:00")
    explicit = parse_authored_point("2026-06-10T14:00:00Z")

    assert naive is not None and explicit is not None
    assert naive == explicit
    assert naive.axis is INSTANT
    assert naive.lower == "2026-06-10T14:00:00.000000Z"


def test_authored_relative_dates_resolve_at_parse_time():
    """`yesterday` is read against the clock now, and re-read on every index pass.

    That is documented behavior rather than a diagnostic: a file edited by hand keeps
    its relative wording, and each pass resolves it fresh.
    """
    span = parse_authored_point("yesterday")

    assert span is not None
    assert span.axis is DATE
    yesterday = datetime.now().date() - timedelta(days=1)
    assert span.lower == yesterday.isoformat()


# The written vocabulary. These are what the *reader* accepts; the qualifier grammar
# then decides how much of a line it can safely claim (see
# tests/markdown/test_temporal_qualifier.py), which is a narrower question.


@pytest.mark.parametrize(
    ("written", "literal", "axis"),
    [
        # Month names, in the orders English writes them.
        ("June 10, 2026", "[2026-06-10,)", DATE),
        ("10 June 2026", "[2026-06-10,)", DATE),
        # The exact forms entity_parser.parse_date already advertises.
        ("Jan 15, 2024", "[2024-01-15,)", DATE),
        ("2024-01-15", "[2024-01-15,)", DATE),
        # A clock reading moves the point onto the instant axis, read as UTC.
        ("2024-01-15 10:00 AM", "[2024-01-15T10:00:00.000000Z,)", INSTANT),
        ("2026-06-10 10:00 AM", "[2026-06-10T10:00:00.000000Z,)", INSTANT),
    ],
)
def test_written_dates_read_on_the_axis_their_precision_names(written, literal, axis):
    """A written date stays a date; adding a clock reading is what makes it an instant.

    `June 10, 2026` must never acquire a time of day -- midnight in which zone is a
    question the author never answered -- while `10:00 AM` with no offset is UTC, the
    same convention every other naive datetime here follows.
    """
    span = parse_authored_point(written)

    assert span is not None
    assert str(span) == literal
    assert span.axis is axis


def test_written_relative_dates_resolve_against_now():
    """dateparser's relative vocabulary is read whole when it is handed a whole phrase."""
    span = parse_authored_point("2 days ago")

    assert span is not None
    assert span.axis is DATE
    assert span.lower == (datetime.now().date() - timedelta(days=2)).isoformat()


@pytest.mark.parametrize(
    ("written", "date_order", "expected_lower"),
    [
        # Year last: YMD cannot apply, so dateparser falls back to day-first and only
        # MDY reads it differently.
        ("03/04/2026", "YMD", "2026-04-03"),
        ("03/04/2026", "DMY", "2026-04-03"),
        ("03/04/2026", "MDY", "2026-03-04"),
        # Year first: now YMD and DMY disagree, so the three orders are pinned pairwise
        # across the two forms and no setting is left unproven.
        ("2026/03/04", "YMD", "2026-03-04"),
        ("2026/03/04", "DMY", "2026-04-03"),
        ("2026/03/04", "MDY", "2026-03-04"),
    ],
)
def test_slash_dates_resolve_by_the_configured_order(written, date_order, expected_lower):
    span = parse_authored_point(written, date_order=date_order)

    assert span is not None
    assert span.lower == expected_lower
    assert span.axis is DATE


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


@pytest.mark.parametrize(
    ("written", "literal"),
    [
        # The last year and the last month have no successor to close at, so the
        # canonical form for them is unbounded -- exactly as it is for an inclusive
        # upper bound on the last date.
        ("9999", "[9999-01-01,)"),
        ("9999-12", "[9999-12-01,)"),
        # The last day was always open-ended, like every other day.
        ("9999-12-31", "[9999-12-31,)"),
    ],
)
def test_periods_at_the_end_of_the_calendar_run_to_the_end_of_it(written: str, literal: str):
    """Unbounded above loses no days: nothing follows 9999-12-31.

    `[9999-12-01,)` holds exactly the days a closed `[9999-12-01,10000-01-01)` would --
    and year 10000 is not a date Python can build. Constructing it raised `ValueError`
    straight through `parse_authored_point` and `parse_temporal_qualifier` into the
    markdown parser, failing the *whole note* over one qualifier, which is the one thing
    the qualifier contract promises can never happen.
    """
    span = parse_authored_point(written)

    assert span is not None
    assert str(span) == literal
    assert span.is_empty is False


@pytest.mark.parametrize(
    ("written", "literal"),
    [("9998", "[9998-01-01,9999-01-01)"), ("9999-11", "[9999-11-01,9999-12-01)")],
)
def test_the_period_before_the_calendar_edge_still_closes(written: str, literal: str):
    """The open upper end is the calendar's edge, not "four digits" or "December"."""
    span = parse_authored_point(written)

    assert span is not None
    assert str(span) == literal


def test_a_year_beyond_the_calendar_is_unread():
    """Year 10000 is not a date at all, so the token names nothing and stays content."""
    assert parse_authored_point("10000") is None


def test_an_authored_instant_that_leaves_the_calendar_in_utc_is_unread():
    """The flexible reader has no bound to refuse, so it reads no date at all.

    Its contract is None-for-unreadable, not an exception: `parse_temporal_qualifier`
    does not guard this call, so anything raised here fails the note.
    """
    assert parse_authored_point("9999-12-31T23:59:59-05:00") is None


# --- TemporalRange normalization ---


def test_unbounded_sides_are_forced_exclusive():
    """PostgreSQL's rule: there is no endpoint to include, so inclusivity is meaningless.

    Asserted on the instant axis so this rule is the only one moving: a date range
    would also be rewritten to `[)`, which is a separate normalization with its own
    tests below.
    """
    span = TemporalRange(
        axis=INSTANT,
        lower=None,
        upper="2026-07-27T00:00:00.000000Z",
        lower_inclusive=True,
        upper_inclusive=True,
    )

    assert span.lower_inclusive is False
    assert span.upper_inclusive is True
    assert str(span) == "(,2026-07-27T00:00:00.000000Z]"


def test_fully_unbounded_range_is_exclusive_on_both_sides():
    span = TemporalRange(axis=DATE, lower_inclusive=True, upper_inclusive=True)

    assert (span.lower_inclusive, span.upper_inclusive) == (False, False)
    assert str(span) == "(,)"


@pytest.mark.parametrize(
    ("lower_inclusive", "upper_inclusive"),
    [(True, False), (False, True), (False, False)],
)
def test_degenerate_range_collapses_to_empty(lower_inclusive: bool, upper_inclusive: bool):
    """`[a,a)`, `(a,a]`, and `(a,a)` contain no points, so they *are* the empty range."""
    span = TemporalRange(
        axis=DATE,
        lower="2026-07-27",
        upper="2026-07-27",
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
    )

    assert span.is_empty is True
    assert span.lower is None and span.upper is None
    assert str(span) == "empty"


def test_closed_single_point_range_is_not_empty():
    """`[a,a]` contains exactly one point, which is a real interval.

    On the date axis that one point is one day, and the canonical form says so by
    closing at the following day rather than by owning both endpoints.
    """
    span = TemporalRange(
        axis=DATE,
        lower="2026-07-27",
        upper="2026-07-27",
        lower_inclusive=True,
        upper_inclusive=True,
    )

    assert span.is_empty is False
    assert str(span) == "[2026-07-27,2026-07-28)"


def test_inverted_range_is_refused():
    with pytest.raises(TemporalQualifierError, match="after upper bound"):
        TemporalRange(axis=DATE, lower="2026-08-01", upper="2026-06-10")


def test_empty_range_cannot_carry_bounds():
    """Two representations of the same interval would make equality lie."""
    with pytest.raises(TemporalQualifierError, match="carries no bounds"):
        TemporalRange(axis=DATE, lower="2026-07-27", is_empty=True)
    with pytest.raises(TemporalQualifierError, match="carries no bounds"):
        TemporalRange(axis=DATE, is_empty=True, upper_inclusive=True)


def test_range_rejects_non_canonical_bounds():
    with pytest.raises(TemporalQualifierError, match="not canonical"):
        TemporalRange(axis=INSTANT, lower="2026-07-27T18:42:00Z")


def test_empty_constructor_builds_the_empty_range_on_one_axis():
    span = TemporalRange.empty(INSTANT)

    assert (span.axis, span.is_empty, span.lower, span.upper) == (INSTANT, True, None, None)


# --- The discrete canonical form ---
#
# Calendar dates are a discrete domain, so every date range is stored half-open, the
# way PostgreSQL canonicalizes `daterange`. Without it the scalar endpoint comparisons
# in `repository.temporal_filters` do not decide membership -- see
# `test_date_ranges_that_share_no_day_do_not_overlap` for the case that proves it.


@pytest.mark.parametrize(
    ("authored", "canonical"),
    [
        # Already half-open: nothing moves.
        ("[2026-06-10,2026-07-27)", "[2026-06-10,2026-07-27)"),
        # An exclusive lower end starts on the following day.
        ("(2026-06-10,2026-07-27)", "[2026-06-11,2026-07-27)"),
        # An inclusive upper end closes at the start of the following day.
        ("[2026-06-10,2026-07-27]", "[2026-06-10,2026-07-28)"),
        ("(2026-06-10,2026-07-27]", "[2026-06-11,2026-07-28)"),
        # An unbounded side has no endpoint to move, whichever side it is.
        ("[2026-06-10,)", "[2026-06-10,)"),
        ("(2026-06-10,)", "[2026-06-11,)"),
        ("(,2026-07-27)", "(,2026-07-27)"),
        ("(,2026-07-27]", "(,2026-07-28)"),
        ("(,)", "(,)"),
        # One authored day is one canonical day.
        ("[2026-07-27,2026-07-27]", "[2026-07-27,2026-07-28)"),
    ],
)
def test_date_ranges_are_stored_half_open(authored: str, canonical: str):
    """Whatever the author wrote, the stored date range is `[lower,upper)`."""
    span = parse_range_literal(authored, axis=DATE)

    assert str(span) == canonical
    # A bounded lower end is always owned, a bounded upper end never is.
    assert span.lower_inclusive is (span.lower is not None)
    assert span.upper_inclusive is False


def test_the_canonical_date_rendering_is_a_fixed_point():
    """Re-parsing what `__str__` produced yields this same value, not a third form."""
    for authored in ("(2026-06-10,2026-07-27]", "[2026-07-27,2026-07-27]", "(,2026-07-27]"):
        span = parse_range_literal(authored, axis=DATE)

        assert parse_range_literal(str(span), axis=DATE) == span, authored


@pytest.mark.parametrize(
    "literal",
    [
        "[2026-07-27,2026-07-27)",  # opens and closes on the same day
        "(2026-07-27,2026-07-27]",  # starts the 28th, ends the 27th
        "(2026-07-27,2026-07-27)",
        # After the 27th and before the 28th there is no day at all. Read as a
        # continuous interval this looks non-empty, which is exactly the confusion
        # the discrete canonical form removes.
        "(2026-07-27,2026-07-28)",
    ],
)
def test_date_ranges_that_admit_no_day_are_the_empty_range(literal: str):
    span = parse_range_literal(literal, axis=DATE)

    assert span.is_empty is True
    assert str(span) == "empty"


def test_an_inclusive_upper_end_on_the_last_date_becomes_unbounded():
    """`9999-12-31` has no successor to close against, and no later day to exclude."""
    span = TemporalRange(
        axis=DATE,
        lower="2026-06-10",
        upper="9999-12-31",
        lower_inclusive=True,
        upper_inclusive=True,
    )

    assert (span.upper, span.upper_inclusive) == (None, False)
    assert str(span) == "[2026-06-10,)"


def test_the_last_date_alone_is_still_one_day_not_the_empty_range():
    """`[9999-12-31,9999-12-31]` survives the rewrite that drops its upper end."""
    span = TemporalRange(
        axis=DATE,
        lower="9999-12-31",
        upper="9999-12-31",
        lower_inclusive=True,
        upper_inclusive=True,
    )

    assert span.is_empty is False
    assert str(span) == "[9999-12-31,)"


def test_an_exclusive_lower_end_on_the_last_date_is_empty():
    """A range beginning strictly after the last date admits no date at all."""
    span = TemporalRange(axis=DATE, lower="9999-12-31")

    assert span.is_empty is True
    assert str(span) == "empty"


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        (
            "(2026-07-27T18:42:00Z,2026-07-27T19:00:00Z]",
            (False, True, "2026-07-27T18:42:00.000000Z", "2026-07-27T19:00:00.000000Z"),
        ),
        (
            "[2026-07-27T18:42:00Z,2026-07-27T19:00:00Z]",
            (True, True, "2026-07-27T18:42:00.000000Z", "2026-07-27T19:00:00.000000Z"),
        ),
        ("(,2026-07-27T19:00:00Z]", (False, True, None, "2026-07-27T19:00:00.000000Z")),
    ],
)
def test_instant_ranges_keep_the_inclusivity_they_were_written_with(literal, expected):
    """Instants are continuous: there is no "next instant" to shift a bound onto.

    Adding a microsecond would be an invented precision, and rewriting an instant the
    way a date is rewritten would move the endpoint to a moment nobody wrote.
    """
    span = parse_range_literal(literal, axis=INSTANT)

    assert (span.lower_inclusive, span.upper_inclusive, span.lower, span.upper) == expected


def test_an_instant_range_over_one_day_is_not_widened_by_a_day():
    """The date rewrite must not reach the instant axis: `+1 day` there is a bug."""
    span = parse_range_literal("[2026-07-27T00:00:00Z,2026-07-27T23:59:59Z]", axis=INSTANT)

    assert span.upper == "2026-07-27T23:59:59.000000Z"
    assert span.upper_inclusive is True


def test_a_degenerate_instant_range_still_holds_exactly_one_moment():
    """`[t,t]` on a continuous axis stays `[t,t]`; there is no successor to close at."""
    span = parse_range_literal("[2026-07-27T18:42:00Z,2026-07-27T18:42:00Z]", axis=INSTANT)

    assert span.is_empty is False
    assert str(span) == "[2026-07-27T18:42:00.000000Z,2026-07-27T18:42:00.000000Z]"


# --- Range literals ---


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        # Date literals already in the canonical half-open form.
        ("[2026-06-10,2026-07-27)", (True, False, "2026-06-10", "2026-07-27")),
        ("[2026-06-10,)", (True, False, "2026-06-10", None)),
        ("(,2026-07-27)", (False, False, None, "2026-07-27")),
        # Instant literals, which are stored exactly as written whatever the brackets.
        (
            "(2026-06-10T00:00:00.000000Z,2026-07-27T00:00:00.000000Z]",
            (False, True, "2026-06-10T00:00:00.000000Z", "2026-07-27T00:00:00.000000Z"),
        ),
        (
            "[2026-06-10T00:00:00.000000Z,2026-07-27T00:00:00.000000Z]",
            (True, True, "2026-06-10T00:00:00.000000Z", "2026-07-27T00:00:00.000000Z"),
        ),
        ("(,2026-07-27T00:00:00.000000Z]", (False, True, None, "2026-07-27T00:00:00.000000Z")),
    ],
)
def test_range_literal_round_trips_through_its_canonical_rendering(literal, expected):
    """A literal already in canonical form parses and renders back to itself.

    Date literals written some other way still round trip -- through their canonical
    spelling rather than their authored one -- which
    `test_the_canonical_date_rendering_is_a_fixed_point` pins separately.
    """
    span = parse_range_literal(literal)

    assert (span.lower_inclusive, span.upper_inclusive, span.lower, span.upper) == expected
    assert str(span) == literal


def test_range_literal_tolerates_surrounding_whitespace():
    assert str(parse_range_literal("  [2026-06-10, 2026-07-27)  ")) == "[2026-06-10,2026-07-27)"


def test_empty_literal_requires_an_explicit_axis():
    """`empty` carries no bounds to classify, so the caller must name the axis."""
    assert parse_range_literal("empty", axis=DATE).is_empty is True
    with pytest.raises(TemporalQualifierError, match="axis must be given"):
        parse_range_literal("empty")


def test_fully_unbounded_literal_requires_an_explicit_axis():
    assert parse_range_literal("(,)", axis=INSTANT).axis is INSTANT
    with pytest.raises(TemporalQualifierError, match="no bounds to classify"):
        parse_range_literal("(,)")


def test_range_literal_refuses_mixed_axes():
    with pytest.raises(TemporalQualifierError, match="mix date-only and timestamp bounds"):
        parse_range_literal("[2026-06-10,2026-07-27T00:00:00Z)")


def test_range_literal_refuses_an_axis_it_was_not_asked_for():
    with pytest.raises(TemporalQualifierError, match="expected instant bounds"):
        parse_range_literal("[2026-06-10,2026-07-27)", axis=INSTANT)


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
    """A filter that names no kind, point, or range would match everything silently."""
    with pytest.raises(TemporalQualifierError, match="must name a kind"):
        TemporalFilter()


def test_point_filter_window_is_the_degenerate_closed_range():
    """Containment is overlap with `[p,p]`, which is why one predicate answers both."""
    window = TemporalFilter(at=parse_point("2026-07-27")).window

    assert window == TemporalRange(
        axis=DATE,
        lower="2026-07-27",
        upper="2026-07-27",
        lower_inclusive=True,
        upper_inclusive=True,
    )
    # Canonicalized like any other date range: still the single day 2026-07-27, now in
    # the half-open form the SQL predicate compares correctly.
    assert str(window) == "[2026-07-27,2026-07-28)"


def test_instant_point_filter_window_stays_a_closed_moment():
    """The instant axis has no successor to close at, so `[t,t]` is the window."""
    window = TemporalFilter(at=parse_point("2026-07-27T18:42:00Z")).window

    assert str(window) == "[2026-07-27T18:42:00.000000Z,2026-07-27T18:42:00.000000Z]"


def test_overlap_filter_window_is_the_range_itself():
    span = parse_range_literal("[2026-06-10,2026-07-27)")

    assert TemporalFilter(overlaps=span).window == span


def test_kind_only_filter_has_no_window():
    """Nothing to intersect: the question is only "does this axis carry an assertion"."""
    assert TemporalFilter(kind=TimeKind.EFFECTIVE).window is None


# --- TemporalAssertion ---


def test_assertion_defaults_to_the_observation_extractor():
    assertion = TemporalAssertion(
        time_kind=TimeKind.EFFECTIVE,
        valid_during=parse_range_literal("[2026-06-10,2026-07-27)"),
        source_text="@effective[2026-06-10,2026-07-27)",
    )

    assert assertion.extractor == "observation"
    assert assertion.metadata is None


def test_recorded_time_is_not_an_authorable_kind():
    """Recorded time is never written in markdown, so no kind names it."""
    assert "recorded" not in {kind.value for kind in TimeKind}
    assert {kind.value for kind in TimeKind} == {
        "effective",
        "valid",
        "occurred",
        "due",
        "mentioned",
    }
