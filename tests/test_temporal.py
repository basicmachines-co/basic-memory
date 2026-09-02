"""The portable temporal value types and their lexical grammar (SPEC-82).

These values are the shared vocabulary between the markdown parser, the projection, and
both search dialects. Two properties carry the whole design and are pinned here:

* Canonical bounds are fixed width, so byte-lexicographic order *is* chronological
  order -- which is what lets one SQL predicate serve SQLite and PostgreSQL alike.
* Dates and instants are separate axes. A date never gains a time of day or a zone, and
  an instant written without an offset is read as UTC -- the same convention the rest
  of the codebase applies to naive datetimes.
"""

import pytest
from freezegun import freeze_time

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
    names_only_a_calendar_period,
    parse_range_literal,
    parse_temporal_filter,
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


def test_a_relative_point_names_nothing_the_note_can_keep():
    """`yesterday` names a different day every day, so it names nothing storable.

    A qualifier's meaning has to be recoverable from the note's own bytes, because those
    are the only thing that travels to a clone or survives a re-import. Read against the
    wall clock, an unedited file's stored range changed on every index pass -- and a
    search that matched it last week stopped matching today with nothing written. The two
    obvious anchors are no better: `created_at` is derived metadata that can shift, and a
    stored resolution cannot be reproduced by a fresh clone reindexing from markdown
    alone. Refusing is what leaves the file as the only source of the answer.
    """
    with freeze_time("2026-09-02"):
        assert parse_authored_point("yesterday") is None
        assert parse_authored_point("2 days ago") is None


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


# Two clocks far enough apart that anything taken from "now" lands somewhere different
# under each. Every accepted spelling must name one interval under both; every refused one
# must be refused under both. This pair is the actual guarantee -- it catches relative
# wording nobody thought to enumerate, in languages nobody thought to test.
_FAR_APART_CLOCKS = ("2026-09-02", "2027-04-19")


@pytest.mark.parametrize(
    "written",
    [
        # ISO, in every precision the reader distinguishes.
        "2026-06-10",
        "2026-06",
        "9999-12",
        "2026",
        "2026-1-5",
        "2026-06-10T14:00",
        "2026-06-10 14:00:00+02:00",
        "2026-06-10 14:00:00.5",
        "2026-01-01T10:00:00.123456",
        # Spelled out, and slash-formatted: absolute, just not machine syntax.
        "June 10, 2026",
        "10 June 2026",
        "Jan 15, 2024",
        "June 2026",
        "2026/03/04",
        "10/07/2026",
        # A clock reading beside an absolute date, in both syntaxes.
        "2026-06-10 10:00 AM",
        "2026-06-10 noon",
        "10/07/2026 14:00",
        "June 10, 2026 2pm",
    ],
)
def test_an_accepted_point_names_the_same_interval_whenever_it_is_read(written: str):
    """Precision the author did not write is fine; meaning that moves is not.

    `2026` and `June 2026` name periods the author delimited, and they must go on reading
    -- refusing everything under-specified would have taken them with the relative forms.
    What separates the two is not syntax or precision but whether the answer depends on
    the day the question is asked.
    """
    readings = []
    for clock in _FAR_APART_CLOCKS:
        with freeze_time(clock):
            readings.append(parse_authored_point(written))

    assert readings[0] is not None
    assert readings[0] == readings[1]


@pytest.mark.parametrize(
    "written",
    [
        # The reported shapes.
        "yesterday",
        "2 days ago",
        # The rest of the relative vocabulary, in every direction and grain.
        "today",
        "tomorrow",
        "now",
        "last week",
        "next week",
        "last month",
        "next month",
        "last year",
        "next year",
        "in 3 days",
        "3 hours ago",
        "an hour ago",
        "the day before yesterday",
        # Under-specified rather than relative, and just as unstable: the year, or the
        # year and month, would be taken from whenever the index happened to run.
        "March",
        "may",
        "December",
        # dateparser reads far more than English, which is exactly why the rule is
        # determinism and not a list of words somebody wrote down.
        "hace 2 dias",
        "il y a 2 jours",
        "vor 2 tagen",
    ],
)
def test_a_point_whose_meaning_moves_with_the_clock_is_unread(written: str):
    """Refused under every clock, not merely different under two.

    A form that read on one day and not another would be worse than either -- the note
    would gain and lose an assertion as the calendar turned.
    """
    for clock in _FAR_APART_CLOCKS:
        with freeze_time(clock):
            assert parse_authored_point(written) is None


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
    [
        "2026-13-01T10:00:00",  # RFC 3339-shaped, but there is no 13th month
        "2026-02-30T10:00:00Z",  # RFC 3339-shaped, but February has no 30th
        "2026-06-10T25:00:00+02:00",  # RFC 3339-shaped, but there is no 25th hour
    ],
)
def test_impossible_iso_timestamps_are_unread_rather_than_re_interpreted(written: str):
    """dateparser reads `2026-13-01T10:00:00` as 10:00 on the 13th of January.

    The canonical timestamp shape takes the same strict path the canonical date shape
    does, and for the same reason: an instant nobody wrote would be re-projected by every
    reindex, while an unread token merely stays observation content.
    """
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    ("written", "lower"),
    [
        ("2026-06-10T14:00", "2026-06-10T14:00:00.000000Z"),  # no seconds
        ("2026-06-10 10:00 AM", "2026-06-10T10:00:00.000000Z"),  # written the human way
    ],
)
def test_flexible_timestamp_spellings_still_reach_the_lenient_reader(written: str, lower: str):
    """Only the *canonical* timestamp shape is held to the strict parser.

    The strict branch above is a shape test, not a ban on clock readings: a spelling the
    canonical form does not cover is still the convenient form, and dateparser reads it.
    """
    span = parse_authored_point(written)

    assert span is not None
    assert span.axis is INSTANT
    assert span.lower == lower


@pytest.mark.parametrize(
    "written",
    [
        # A month that does not exist, with nothing after it. The two strict branches
        # above match only a token that is *exactly* a canonical date or timestamp, so
        # this shape used to reach dateparser untouched.
        "2026-13",
        "2026-00",
        # ...the same, carrying a time the canonical shape does not cover: separated by
        # a space rather than `T`, or written to minute precision.
        "2026-13-01 10:00:00",
        "2026-13-01 10:00",
        "2026-13-01T10:00",
        "2026-02-30 10:00:00",
        "2026-13-01 10:00:00Z",
        "2026-06-31T09:30",
    ],
)
def test_iso_shaped_points_with_impossible_components_are_unread(written: str):
    """An ISO-shaped point must mean its components literally, whatever trails it.

    dateparser reads month 13 as *day* 13 and then supplies the month from today, so
    these all used to file a date nobody wrote. Guarding only the two canonical shapes
    left every other ISO spelling -- a bare year-month, a space-separated timestamp, a
    minute-precision one -- on the lenient path.
    """
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    "written",
    [
        # The reported shape: a mistyped ISO date whose day ran on into a fourth digit.
        # dateparser chopped it back to a bare year-month and filed the whole of January,
        # so one slipped keystroke widened a single day into a month-long range.
        "2026-01-0100",
        # The same slip one component earlier, which filed the whole *year* 2026.
        "2026-0100",
        # Shorter over-long runs. dateparser already declined to read these, but they are
        # the same malformed shape and the guard now owns them rather than trusting it to.
        "2026-013",
        "2026-06-100",
        "2026-01-011",
        # An unpadded component is a legitimate spelling, so width alone cannot decide:
        # these are refused for their values, exactly as their zero-padded twins are.
        "2026-1-99",
        "2026-0-5",
        # A run long enough to overflow the C long `date` converts to. Refused on width
        # before conversion, so the guard reads it as no date rather than raising.
        "2026-" + "9" * 40,
    ],
)
def test_iso_shaped_points_with_malformed_calendar_runs_are_unread(written: str):
    """A mistyped ISO point must stay content, not round off into a plausible range.

    The guard's first cut matched calendar components at a fixed width, so a run of the
    wrong width matched *nothing* and fell through to dateparser untouched -- the one
    outcome the guard exists to prevent. `2026-01-0100` came back as
    `[2026-01-01,2026-02-01)`: a whole month, indistinguishable in the index from a range
    the author meant to write. A silently wrong date is worse than an unread token, so a
    component too wide to be a month or a day now fails the guard on that basis.
    """
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    "written",
    [
        # The reported shapes: a doubled or dangling separator right after the year, which
        # left no digits for the head to read. Each reached the flexible reader and came
        # back with a date the author never wrote -- January 2026, and the whole of 2026.
        "2026--01",
        "2026---01",
        "2026-",
        "2026--",
        # The same slip with a day still attached, which invented a specific day.
        "2026--01-02",
        # A space where the month should be; dateparser filled the month itself.
        "2026-  01",
        # The worst of them: a stray letter made dateparser abandon the ISO reading and
        # re-guess, answering with October 1st -- a month and a day found nowhere in the
        # text, in a token whose first four characters are the year the author wrote.
        "2026-x01",
        # Spellings this module does not implement. Refusing them is the honest answer;
        # guessing was not.
        "2026-W03",
        "2026-2027",
    ],
)
def test_a_year_and_a_hyphen_that_names_no_date_is_unread(written: str):
    """Opening in ISO syntax settles the question even when the rest is unreadable.

    The head needed digits after the first hyphen, so these matched it not at all and fell
    through to the flexible reader -- the one outcome ISO syntax must never have, and the
    same "not my business" gap every earlier cut of this guard had. A reader that re-guesses
    does not report failure: it answers, confidently, with a date nobody wrote, and every
    reindex reproduces it. A four-digit year followed by a hyphen is a commitment to machine
    syntax, so it is judged as machine syntax or refused.
    """
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    "written",
    [
        # The reported shapes: a calendar date carrying an instant marker with no instant
        # behind it. dateparser drops the marker and answers with the bare date, so the
        # author reached for a moment and the index recorded a whole open-ended day.
        "2026-01-01T",
        "2026-01-01Z",
        "2026-01-01+14:00",  # a real UTC offset -- with no time for it to offset
        "2026-01-01-05:00",
        # The same defect wearing shapes nobody listed. Naming the marker would have
        # caught the three above and missed each of these, which is why the guard asks
        # what the reader *returned* rather than what the suffix looks like.
        "2026-01-01UTC",
        "2026-01-01TZ",
        "2026-01-01T ",
        "2026-01-01,",
        "2026-01-01.",
        # A dangling separator, which the guard's previous cut could not even see: its
        # trailing `(?![\\d-])` lookahead made the head fail to match, so the token
        # skipped the guard entirely and reached the lenient reader.
        "2026-01-01-",
        "2026-01-01-5",
    ],
)
def test_iso_dates_with_a_dangling_instant_suffix_are_unread(written: str):
    """A calendar date is a complete point, so only a clock reading may follow one.

    Each of these was peeled off its observation and filed as `[2026-01-01,)` -- a
    plausible-looking assertion the author never wrote, re-derived identically by every
    reindex. The guard is stated on the whole token rather than on a list of suffixes:
    what the reader hands back must account for everything the author typed.
    """
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    "written",
    [
        # A stray character next to an ISO date makes dateparser abandon the ISO reading
        # and re-guess the components under the configured order: June 10 became
        # *October 6*. Worse than the dangling markers above, which at least kept the day.
        "2026-06-10x",
        "2026-06x",
        # The same re-guess with a clock reading present, so the reader does come back
        # with an instant -- on the wrong date. Only comparing that date against the one
        # the author wrote catches it.
        "2026-06-10 14:00 x",
        "2026-06-10 x 14:00",
        # A relative phrase after an absolute date: the reader answers with *today*
        # shifted, and the ISO date the author wrote is nowhere in the result.
        "2026-06-10 tomorrow 14:00",
        "2026-06-10T14:00 yesterday",
    ],
)
def test_an_iso_date_whose_suffix_re_guesses_it_is_unread(written: str):
    """The reader must come back with the date the author wrote, not a nearby one."""
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    "written",
    [
        # The reported shape: one digit more than a canonical instant carries. The lenient
        # reader truncated it to `...123456Z`, on the very day the head names, so every
        # check the reading makes passed and the index recorded an instant 100ns off the
        # one the author wrote -- re-derived identically by every reindex.
        "2026-01-01T10:00:00.1234567",
        # The same defect wearing every spelling of the syntax around it. None of these is
        # distinguishable by what the reader *returned* -- each truncates and each lands on
        # the right day -- which is why this one is judged on the text instead.
        "2026-01-01t10:00:00.1234567",
        "2026-01-01 10:00:00.1234567",
        "2026-01-01T10:00:00.1234567Z",
        "2026-01-01T10:00:00.1234567z",
        "2026-01-01T10:00:00.1234567+02:00",
        "2026-01-01T10:00:00.1234567-05:00",
        "2026-01-01T10:00:00.1234567+0200",
        # Precision far past anything a clock emits, truncated just as quietly: a 20- and a
        # 30-digit fraction both stored six digits and discarded the rest without a word.
        "2026-01-01T10:00:00.12345678901234567890",
        "2026-01-01T10:00:00." + "1" * 30,
    ],
)
def test_an_iso_point_finer_than_a_microsecond_is_unread(written: str):
    """Over-precision is refused on the lenient path too, not silently rounded.

    `canonical_bound` has always refused these -- dropping digits would store a different
    instant than the author wrote -- but that refusal only governed the strict path. A
    point one digit too precise never matched `_INSTANT_BOUND`, so it fell to the lenient
    reader, which truncated it and answered with a time on the correct day. The day check
    is what guards that path, and a truncated fraction sails straight through it: the
    digits it drops were never in the answer to be checked. Judged on the author's text
    instead, so both readers refuse the same token for the same reason.
    """
    assert parse_authored_point(written) is None


@pytest.mark.parametrize(
    ("written", "lower"),
    [
        # Exactly six digits: the widest fraction a canonical instant carries, so it is
        # stored whole and nothing is dropped. The refusal above must stop precisely here.
        ("2026-01-01T10:00:00.123456", "2026-01-01T10:00:00.123456Z"),
        ("2026-01-01 10:00:00.123456", "2026-01-01T10:00:00.123456Z"),
        ("2026-01-01T10:00:00.123456Z", "2026-01-01T10:00:00.123456Z"),
        ("2026-01-01T10:00:00.123456+02:00", "2026-01-01T08:00:00.123456Z"),
        # Narrower fractions were never in question, and are pinned so a future widening
        # of the rule cannot quietly take them.
        ("2026-01-01T10:00:00.1", "2026-01-01T10:00:00.100000Z"),
        ("2026-06-10 14:00:00.5", "2026-06-10T14:00:00.500000Z"),
    ],
)
def test_a_fraction_a_canonical_instant_can_hold_still_reads(written: str, lower: str):
    """Refusing over-precision must cost nothing that stores losslessly.

    Six digits is the boundary, not "any fraction is suspicious": these name a moment the
    canonical form records exactly, so there is no truncation to prevent and no reason to
    withhold the assertion.
    """
    span = parse_authored_point(written)

    assert span is not None
    assert span.axis is INSTANT
    assert span.lower == lower


@pytest.mark.parametrize("today", ["2026-03-07", "2026-09-01"])
def test_a_clock_reading_on_a_month_is_not_completed_from_the_indexing_date(today: str):
    """`2026-13`'s disease in the suffix: a time of day needs a day to fall on.

    `2026-06 10:00` gave dateparser a year, a month and a clock but no day, and it filled
    the day from the current date -- `[2026-06-07T10:00...,)` in March,
    `[2026-06-01T10:00...,)` in September. The same note projected different valid time on
    different days. A head that names only a month owns no day, so nothing may trail it.
    """
    with freeze_time(today):
        assert parse_authored_point("2026-06 10:00") is None


@pytest.mark.parametrize(
    ("written", "lower"),
    [
        # The boundary the suffix rule draws is "did the reader turn this into a time on
        # that date?", not "does this look like a clock?". These carry no colon and no
        # digit at all, yet each really is the time it claims to be, so each still reads.
        ("2026-06-10 noon", "2026-06-10T12:00:00.000000Z"),
        ("2026-06-10 midnight", "2026-06-10T00:00:00.000000Z"),
        ("2026-06-10 2pm", "2026-06-10T14:00:00.000000Z"),
        ("2026-06-10 at 14:00", "2026-06-10T14:00:00.000000Z"),
        # An offset and a zone are only dangling when there is no time in front of them.
        ("2026-06-10T14:00Z", "2026-06-10T14:00:00.000000Z"),
        ("2026-06-10 14:00:00.5", "2026-06-10T14:00:00.500000Z"),
        ("2026-06-10 14:00:00 UTC", "2026-06-10T14:00:00.000000Z"),
        ("2026-06-10T14:00:00+0200", "2026-06-10T12:00:00.000000Z"),
    ],
)
def test_a_real_time_of_day_still_follows_an_iso_date(written: str, lower: str):
    """Refusing a dangling suffix must not cost a genuine one.

    A rule written as a grammar for what may follow a date would have taken these with
    it: none of them is RFC 3339, and half of them do not start with a digit. Deciding on
    the reader's answer instead leaves every spelling it can genuinely read.
    """
    span = parse_authored_point(written)

    assert span is not None
    assert span.axis is INSTANT
    assert span.lower == lower


@pytest.mark.parametrize(
    ("written", "lower"),
    [
        # A clock reading on a date written in no machine syntax at all. The ISO rules
        # never see these -- there is no literal reading to hold them to -- so the
        # flexible reader's answer is taken as given, clock and all.
        ("10/07/2026 14:00", "2026-07-10T14:00:00.000000Z"),
        ("10/07/2026 14:00:00+02:00", "2026-07-10T12:00:00.000000Z"),
        ("June 10, 2026 2pm", "2026-06-10T14:00:00.000000Z"),
        ("June 10, 2026 at 14:00", "2026-06-10T14:00:00.000000Z"),
    ],
)
def test_a_non_iso_date_may_carry_a_clock_reading(written: str, lower: str):
    """Both readers file instants, and only one of them checks the date it was given.

    An ISO head is authoritative, so a clock reading beside one is verified against it.
    These spellings have no such head -- `@occurred:"June 10, 2026 2pm"` says everything
    it means through the flexible reader -- so nothing here is second-guessed.
    """
    span = parse_authored_point(written)

    assert span is not None
    assert span.axis is INSTANT
    assert span.lower == lower


@pytest.mark.parametrize("today", ["2026-03-07", "2026-09-01"])
def test_an_impossible_iso_month_is_not_completed_from_the_indexing_date(today: str):
    """The worst shape of all: a date whose meaning depended on when the reindex ran.

    `2026-13` gave dateparser a year and a day but no month, and it filled the gap from
    the current date -- `[2026-03-13,)` in March, `[2026-09-13,)` in September. The same
    note projected different valid time on different days, so a query that matched it
    last week could stop matching it today with nothing having been edited.
    """
    with freeze_time(today):
        assert parse_authored_point("2026-13") is None


@pytest.mark.parametrize(
    ("written", "literal", "axis"),
    [
        # A real year-month, which is still read as the month it delimits.
        ("2026-06", "[2026-06-01,2026-07-01)", DATE),
        ("9999-12", "[9999-12-01,)", DATE),
        # A real date carrying a time the canonical `T` shape does not cover. These are
        # the spellings the guard above is closest to, so they are pinned explicitly.
        ("2026-06-10 14:00:00", "[2026-06-10T14:00:00.000000Z,)", INSTANT),
        ("2026-06-10 14:00:00Z", "[2026-06-10T14:00:00.000000Z,)", INSTANT),
        ("2026-06-10 14:00:00+02:00", "[2026-06-10T12:00:00.000000Z,)", INSTANT),
        ("2026-06-10 10:00", "[2026-06-10T10:00:00.000000Z,)", INSTANT),
        ("2026-06-10T14:00", "[2026-06-10T14:00:00.000000Z,)", INSTANT),
        ("2026-06-10 10:00 AM", "[2026-06-10T10:00:00.000000Z,)", INSTANT),
        # Not ISO-shaped at all: single-digit components, slashes, words, relative
        # phrases. The guard must not so much as look at these.
        ("2026-1-5", "[2026-01-05,)", DATE),
        ("2026/03/04", "[2026-03-04,)", DATE),
        ("June 10, 2026", "[2026-06-10,)", DATE),
    ],
)
def test_the_iso_guard_leaves_every_readable_spelling_to_the_lenient_reader(
    written: str, literal: str, axis
):
    """The guard is a validity test on ISO components, not a ban on flexible spellings.

    Refusing an impossible ISO date must cost nothing that already reads. Anything whose
    leading components name a real date -- and anything not ISO-shaped at all -- goes on
    reaching dateparser exactly as before, so a future tightening cannot quietly take
    these spellings without failing here.
    """
    span = parse_authored_point(written)

    assert span is not None
    assert str(span) == literal
    assert span.axis is axis


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


@pytest.mark.parametrize(
    "written",
    [
        # The canonical shape, read exactly and refused by the ISO reader...
        "9999-12-31T23:59:59-05:00",
        # ...the same moment spelled loosely, still ISO-headed, so the ISO reader asks
        # the flexible one for the clock and then finds the moment unstorable...
        "9999-12-31 23:59:59 -05:00",
        # ...and the same moment in no machine syntax at all, which the flexible reader
        # owns outright.
        "December 31, 9999 23:59:59 -05:00",
    ],
)
def test_an_authored_instant_that_leaves_the_calendar_in_utc_is_unread(written: str):
    """The flexible reader has no bound to refuse, so it reads no date at all.

    Its contract is None-for-unreadable, not an exception: `parse_temporal_qualifier`
    does not guard this call, so anything raised here fails the note. All three spellings
    are pinned because they take different routes to the same refusal.
    """
    assert parse_authored_point(written) is None


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


# --- A present but empty valid-time field ---


@pytest.mark.parametrize("field", ["valid_at", "valid_overlaps", "time_kind"])
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_valid_time_field_is_refused_by_the_shared_parser(field: str, blank: str):
    """The parser every surface shares refuses a field that is present but says nothing.

    `parse_temporal_filter` is the one place HTTP, MCP and CLI all reach, and its contract
    is that a rejection is loud rather than a filter that quietly matches something else.
    Testing the three fields for truthiness broke exactly that: an empty value read as
    absence and the query ran unfiltered.
    """
    with pytest.raises(TemporalQualifierError, match=f"{field} was given as an empty value"):
        parse_temporal_filter(**{field: blank})


def test_the_message_names_the_field_and_the_way_to_say_no_filter():
    """A diagnostic is only useful if the fix is in it."""
    with pytest.raises(TemporalQualifierError) as caught:
        parse_temporal_filter(valid_at="")

    assert "omit valid_at" in str(caught.value)


def test_absent_valid_time_fields_still_build_no_filter():
    """`None` means absent, and must go on meaning that -- only blankness changed."""
    assert parse_temporal_filter() is None
    assert parse_temporal_filter(valid_at=None, valid_overlaps=None, time_kind=None) is None


def test_a_real_value_beside_absent_ones_still_builds_a_filter():
    temporal = parse_temporal_filter(valid_at="2026-07-28")

    assert temporal is not None
    assert temporal.at is not None
    assert temporal.at.value == "2026-07-28"


# --- A numeric date must mean what the configured order says ---


@pytest.mark.parametrize(
    ("written", "date_order"),
    [
        # The reported shape: month 13 is impossible where YMD puts the month, so
        # dateparser moved the 13 into the day slot and answered January 13 -- a date the
        # order does not name and the author did not write.
        ("2026/13/01", "YMD"),
        ("2026/13/01", "MDY"),
        # The same slip in the other slot, and a day the month has no room for.
        ("2026/00/05", "YMD"),
        ("2026/02/30", "YMD"),
        # Read under DMY the runs swap roles, so it is the *other* spellings that name
        # nothing: `12/31` is day 12 of month 31. Which token is malformed depends on the
        # setting, which is exactly why the check has to consult it.
        ("2026/12/31", "DMY"),
        ("2026/03/31", "DMY"),
        # The year written last, where the order applies just as literally. Under MDY the
        # 13 sits in the month slot, and dateparser answered January 13 by moving it to the
        # day -- the reported case, and the one this rule originally scoped itself out of.
        ("13/01/2026", "MDY"),
        ("31/12/2026", "MDY"),
        # `YMD` describes no arrangement ending in the year, so the reader falls back to
        # day-first and the same check applies to that reading: `01/13` is day 1, month 13.
        ("01/13/2026", "YMD"),
        ("12/31/2026", "YMD"),
        # And under DMY, which is day-first by name rather than by fallback.
        ("01/13/2026", "DMY"),
        # Every separator a fully numeric date is written with, not just the slash this
        # rule was first written for. Each of these reached the reader and came back as
        # January 13 while the check enumerated punctuation instead of describing it.
        ("2026.13.01", "YMD"),
        ("2026 13 01", "YMD"),
        ("2026_13_01", "YMD"),
        ("2026\\13\\01", "YMD"),
        ("13.01.2026", "MDY"),
        ("13-01-2026", "MDY"),
        ("13 01 2026", "MDY"),
    ],
)
def test_a_numeric_date_the_configured_order_cannot_name_is_unread(written: str, date_order):
    """A fully numeric date is machine syntax whose reading `date_order` fixes.

    There is nothing left to guess once the setting is known, so a run the order cannot
    use where the author put it is a typo, not an invitation to try the other slot. The
    lenient reader disagrees: it silently reassigns the components and answers with a real
    date, which every reindex then reproduces. This is `2026-13-01`'s disease in the one
    syntax the ISO classifier deliberately does not claim.
    """
    assert parse_authored_point(written, date_order=date_order) is None


@pytest.mark.parametrize(
    ("written", "date_order", "literal"),
    [
        # The same tokens under an order that *can* name them still read, so the guard is
        # reading the setting rather than banning a shape.
        ("2026/13/01", "DMY", "[2026-01-13,)"),
        ("2026/12/31", "YMD", "[2026-12-31,)"),
        # The spellings the guard test pins, across every order.
        ("2026/03/04", "YMD", "[2026-03-04,)"),
        ("2026/03/04", "DMY", "[2026-04-03,)"),
        ("2026/03/04", "MDY", "[2026-03-04,)"),
        ("2026/1/5", "YMD", "[2026-01-05,)"),
        # Year-last forms under each order, including the two the guard test pins. `YMD`
        # cannot describe them, so its day-first fallback is what they are held to -- named
        # explicitly rather than trusted, which is what makes these the same rule and not
        # an exception to it.
        ("10/07/2026", "YMD", "[2026-07-10,)"),
        ("10/07/2026", "MDY", "[2026-10-07,)"),
        ("10/07/2026", "DMY", "[2026-07-10,)"),
        ("01/02/2026", "YMD", "[2026-02-01,)"),
        ("13/01/2026", "DMY", "[2026-01-13,)"),
        ("01/13/2026", "MDY", "[2026-01-13,)"),
        # A two-digit year names no shape this rule can state -- which run is even the year
        # is the reader's call -- so it is left alone.
        ("03/04/26", "MDY", "[2026-03-04,)"),
        # The same separators carrying a date the order *can* name still read, so widening
        # the rule cost none of them.
        ("2026.03.04", "YMD", "[2026-03-04,)"),
        ("2026 03 04", "YMD", "[2026-03-04,)"),
        ("2026_03_04", "YMD", "[2026-03-04,)"),
        ("03.04.2026", "MDY", "[2026-03-04,)"),
    ],
)
def test_a_numeric_date_the_order_can_name_still_reads(written: str, date_order, literal: str):
    """Refusing an impossible ordering must cost nothing that the ordering allows."""
    span = parse_authored_point(written, date_order=date_order)

    assert span is not None
    assert str(span) == literal


# --- A token no reader can be handed ---

# Comfortably past Python's default integer-conversion limit of 4300 digits.
_OVERSIZED_RUN = "9" * 5000


@pytest.mark.parametrize(
    "written",
    [
        # The reported shape: a bare numeric token long enough that converting it to an
        # int is itself an error.
        _OVERSIZED_RUN,
        # The same run behind an ISO date, which reaches the flexible reader by the other
        # route -- `_read_iso_day` hands a trailing clock reading to exactly the same
        # parser, so guarding only the bare form would have left this one crashing.
        f"2026-06-10 {_OVERSIZED_RUN}",
        f"2026-06-10T{_OVERSIZED_RUN}",
        # Runs far shorter than Python's limit but still no calendar component.
        "9" * 32,
        "1" * 64,
    ],
)
def test_a_digit_run_no_component_could_be_is_unread(written: str):
    """An unreadable qualifier must cost its own token and nothing else.

    dateparser converts the digit runs it finds without catching Python's refusal to
    convert one longer than `sys.get_int_max_str_digits()`, so `ValueError` came straight
    back out of the reader. Every other unreadable point returns None and leaves the token
    as content; this one aborted the parse of the whole note, which is the one outcome
    worse than a wrong date -- it costs every other observation on the page.
    """
    assert parse_authored_point(written) is None


def test_a_digit_run_just_inside_the_limit_still_reads_as_no_date():
    """The guard reports "not a date", never an error, on either side of the boundary."""
    assert parse_authored_point("9" * 4299) is None
    assert parse_authored_point("9" * 4301) is None


def test_the_diagnostic_reader_is_guarded_too():
    """The oversized-run guard has to sit where *every* reading passes through.

    `names_only_a_calendar_period` exists to explain a refusal, and it reaches the reader by
    its own route rather than through `parse_authored_point`. A guard on the public entry
    alone therefore left this path raising, and a word-led token is what finds it: the point
    itself is refused safely, and then the truncation diagnostic asks the same question again
    and crashes the note.
    """
    word_led = "x" + "9" * 5000

    assert parse_authored_point(word_led) is None
    assert names_only_a_calendar_period(word_led) is False
