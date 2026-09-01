"""Portable temporal value types for authored valid time (SPEC-82).

Basic Memory authors time as *semantic* data. A `[decision]` that was effective from
June 10 until the July 27 cutover is a statement about the world, not a record of when
the note was edited. This module owns the values that carry such a statement and the
lexical grammar for the range literals authors write.

PostgreSQL's range conventions are the language contract: `[lower,upper)` with explicit
inclusivity per side, unbounded ends, and a distinguished empty range. That is a
vocabulary choice, not a storage requirement -- these values reduce to portable scalars
so SQLite and Postgres can share one logical model. Its *discrete* canonicalization is
part of the contract too: a date range is stored as `[lower,upper)`, for the reason
`TemporalRange` documents. The author's own spelling is not lost -- it is kept verbatim
on `TemporalAssertion.source_text`.

Two canonical lexical forms carry every bound:

    date     ``YYYY-MM-DD``                   (10 characters)
    instant  ``YYYY-MM-DDTHH:MM:SS.ffffffZ``  (27 characters, always UTC)

Both are fixed width with ASCII digits in fixed positions, so byte-lexicographic order
is chronological order. That is what lets containment and overlap be plain string
comparisons with identical SQL text in either dialect.

The two axes never mix and never convert into one another. A date bound is a calendar
date: it acquires no time of day and no timezone, ever. An instant bound names a moment
and is normalized to UTC, so two instants written in different offsets compare as the
instants they name. A timestamp written without an offset is *read as UTC*, which is
the convention the rest of the codebase already uses for naive datetimes
(`utils.ensure_timezone_aware`, `recent_activity`).

Two authored surfaces reach these values, and they trade precision for convenience in
opposite directions:

* A **range literal** (`[2026-06-10,2026-07-27)`) is the precise form. Its bounds must
  be written in the canonical lexical shapes above, to at most microsecond precision.
* A **point** (`2026-06-10`, `2026-06`, `2026`, `yesterday`) is the convenient form. It
  denotes the span its precision covers, so an author never has to spell out a range to
  say when something started. A point written in ISO calendar syntax is read literally,
  because its text fixes its meaning; any other spelling is read with `dateparser`,
  because there is no literal reading for a guess to contradict.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, assert_never, override

if TYPE_CHECKING:  # pragma: no cover - import exists only for the annotation below
    from dateparser.date import DateDataParser


class TemporalQualifierError(ValueError):
    """A temporal qualifier, range literal, or bound failed to parse or validate."""


class TimeKind(StrEnum):
    """Which kind of time an assertion describes.

    `recorded` is deliberately absent: recorded time is never authored in markdown.
    """

    EFFECTIVE = "effective"
    VALID = "valid"
    OCCURRED = "occurred"
    DUE = "due"
    MENTIONED = "mentioned"


class TemporalRangeAxis(StrEnum):
    """Whether a range is measured in calendar dates or in instants."""

    DATE = "date"
    INSTANT = "instant"


EMPTY_RANGE_LITERAL = "empty"
OBSERVATION_EXTRACTOR = "observation"

# Which component a slash-formatted date leads with. Only ambiguous forms consult it:
# `10/07/2026` is July 10 under YMD/DMY and October 7 under MDY, while `2026-06-10` is
# ISO and is never re-guessed. Mirrored by `BasicMemoryConfig.date_order`.
type DateOrder = Literal["YMD", "DMY", "MDY"]

DEFAULT_DATE_ORDER: DateOrder = "YMD"


# --- Bound grammar ---

# A date bound is exactly the canonical form, so authored and canonical text agree.
# The anchored pattern also rejects the compact `20260610` shape that
# `date.fromisoformat` accepts on 3.11+, which would break fixed-width ordering.
_DATE_BOUND = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Sub-microsecond precision is refused rather than truncated: silently dropping digits
# would make the stored bound name a different instant than the author wrote. The
# offset is optional because a naive timestamp is read as UTC, not rejected.
_INSTANT_BOUND = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[Zz]|[+-]\d{2}:\d{2})?$"
)
_CANONICAL_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

# Anything shaped like a date followed by a time separator is *meant* as a timestamp.
# Classifying it as an instant before validating it is what lets a broken timestamp
# report itself as one instead of as "not a calendar date".
_TIMESTAMP_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]")

# `[lower,upper)` and friends. Bounds carry no brackets and no comma, so one anchored
# pattern splits the literal without any nesting rules.
_RANGE_LITERAL = re.compile(r"^([\[(])([^,\[\]()]*),([^,\[\]()]*)([\])])$")


def _classify_bound(bound: str) -> TemporalRangeAxis:
    """Decide which axis an authored bound is written on."""
    if _TIMESTAMP_SHAPE.match(bound):
        return TemporalRangeAxis.INSTANT
    return TemporalRangeAxis.DATE


def _canonical_date(bound: str) -> str:
    if not _DATE_BOUND.match(bound):
        raise TemporalQualifierError(f"date bound must be YYYY-MM-DD: {bound!r}")
    try:
        return date.fromisoformat(bound).isoformat()
    except ValueError as exc:
        raise TemporalQualifierError(f"not a calendar date: {bound!r}") from exc


def _instant_value(moment: datetime) -> str | None:
    """Render one moment as the canonical fixed-width UTC instant.

    A naive moment is read as UTC rather than refused. That is the house convention for
    every other naive datetime in the codebase, and it is what lets an author write
    `2026-07-27T18:42:00` without learning RFC 3339's offset syntax first.

    None means the moment has no UTC rendering: shifting it by its offset carries it off
    the calendar, as `9999-12-31T23:59:59-05:00` does into year 10000. Reported the way
    `_next_calendar_day` reports its own edge -- each caller decides what running off the
    calendar means for it -- rather than raised, so the overflow can never escape as a
    bare `OverflowError` and fail a whole note's parse.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    try:
        utc = moment.astimezone(UTC)
    except OverflowError:
        return None
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _canonical_instant(bound: str) -> str:
    if not _INSTANT_BOUND.match(bound):
        raise TemporalQualifierError(
            f"timestamp bound must be RFC 3339 to microsecond precision, "
            f"with an optional offset or Z: {bound!r}"
        )
    # RFC 3339 allows lowercase `t`/`z`, which `datetime.fromisoformat` rejects. Every
    # other character in a matched bound is a digit or punctuation, so upper-casing the
    # whole bound only touches those two markers.
    try:
        moment = datetime.fromisoformat(bound.upper())
    except ValueError as exc:
        raise TemporalQualifierError(f"not a valid timestamp: {bound!r}") from exc
    value = _instant_value(moment)
    if value is None:
        raise TemporalQualifierError(
            f"timestamp bound leaves the calendar when converted to UTC: {bound!r}"
        )
    return value


def canonical_bound(bound: str, axis: TemporalRangeAxis) -> str:
    """Normalize one authored bound to the canonical fixed-width form for its axis."""
    if axis is TemporalRangeAxis.DATE:
        return _canonical_date(bound)
    return _canonical_instant(bound)


def _require_canonical(value: str, axis: TemporalRangeAxis) -> None:
    """Reject a value that skipped `canonical_bound` on its way into a domain value."""
    pattern = _DATE_BOUND if axis is TemporalRangeAxis.DATE else _CANONICAL_INSTANT
    if not pattern.match(value):
        raise TemporalQualifierError(f"{axis.value} bound is not canonical: {value!r}")


def _next_calendar_day(bound: str) -> str | None:
    """The canonical date after `bound`, or None when the calendar has none.

    Only `9999-12-31` has no successor. Reporting that as None rather than raising lets
    each side of a range decide what running off the end of the calendar means for it:
    an upper end there covers every remaining day, a lower end past it covers none.
    """
    day = date.fromisoformat(bound)
    if day == date.max:
        return None
    return (day + timedelta(days=1)).isoformat()


# --- Values ---


@dataclass(frozen=True, slots=True)
class TemporalPoint:
    """One calendar date or instant that a containment question is asked about."""

    axis: TemporalRangeAxis
    value: str

    def __post_init__(self) -> None:
        _require_canonical(self.value, self.axis)

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TemporalRange:
    """One authored interval on a single time axis.

    Bounds are canonical lexical strings; `None` means unbounded on that side.
    Construction normalizes three PostgreSQL rules so no caller has to remember them:
    an unbounded side is always exclusive, an interval containing no points *is* the
    empty range, and -- exactly as `daterange` does -- a **date** range is rewritten
    into the half-open `[lower,upper)` form.

    That last rule is what makes the scalar SQL predicate correct rather than merely
    tidy. Calendar dates are a *discrete* domain, so `[a,b]` and `[a,b+1)` denote the
    same set of days, but only the half-open spelling lets endpoint comparisons decide
    membership. Left as authored, `(2026-01-01,2026-01-03)` holds only January 2 and
    `(2026-01-02,2026-01-04)` holds only January 3 -- disjoint sets -- yet each raw
    endpoint lies inside the other's bounds, so a comparison of raw endpoints reports
    an overlap that does not exist. Canonicalized to `[2026-01-02,2026-01-03)` and
    `[2026-01-03,2026-01-04)`, the same comparison is right.

    Instants are a continuous domain -- no moment is "the next one" -- so an instant
    range keeps the inclusivity the author wrote and is never rewritten this way.

    Canonicalization changes the *stored* spelling, never the set of times: `[a,a]`
    becomes `[a,a+1)`, the one day `a`. What the author typed is not lost; it is kept
    verbatim on `TemporalAssertion.source_text`, which is what serialization replays
    and what a search result quotes back. `__str__` renders the canonical form, and
    re-parsing that rendering yields this same value.
    """

    axis: TemporalRangeAxis
    lower: str | None = None
    upper: str | None = None
    lower_inclusive: bool = False
    upper_inclusive: bool = False
    is_empty: bool = False

    def __post_init__(self) -> None:
        if self.is_empty:
            # The empty range has no endpoints at all, so inclusivity is meaningless
            # for it; representing it two ways would make equality lie.
            if (
                self.lower is not None
                or self.upper is not None
                or self.lower_inclusive
                or self.upper_inclusive
            ):
                raise TemporalQualifierError("the empty range carries no bounds")
            return

        for bound in (self.lower, self.upper):
            if bound is not None:
                _require_canonical(bound, self.axis)

        # Canonical bounds are fixed width, so string order is chronological order.
        # Judged on the bounds as authored: an interval written backwards is an author
        # error to report, not an empty range to accept silently.
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise TemporalQualifierError(
                f"range lower bound {self.lower} is after upper bound {self.upper}"
            )

        # PostgreSQL: an unbounded side cannot be inclusive; there is no endpoint.
        if self.lower is None:
            object.__setattr__(self, "lower_inclusive", False)
        if self.upper is None:
            object.__setattr__(self, "upper_inclusive", False)

        # --- Discrete canonical form ---
        #
        # Rewrite a date range to `[lower,upper)`. See the class docstring for why the
        # scalar overlap predicate needs this and why instants must not get it.
        if self.axis is TemporalRangeAxis.DATE:
            if self.lower is not None and not self.lower_inclusive:
                after_lower = _next_calendar_day(self.lower)
                if after_lower is None:
                    # Nothing follows 9999-12-31, so a range starting strictly after it
                    # admits no date at all.
                    self._become_empty()
                    return
                object.__setattr__(self, "lower", after_lower)
                object.__setattr__(self, "lower_inclusive", True)
            if self.upper is not None and self.upper_inclusive:
                # None here loses no days: 9999-12-31 is the last date there is, so
                # "through 9999-12-31 inclusive" and "unbounded above" hold the same
                # set, and only the latter is representable in the canonical form.
                object.__setattr__(self, "upper", _next_calendar_day(self.upper))
                object.__setattr__(self, "upper_inclusive", False)

        # PostgreSQL: an interval that admits no point at all *is* the empty range. The
        # endpoints coincide without both being owned (`[a,a)`), or -- only reachable
        # after the rewrite above, from `(a,a)` -- the lower end has overshot the upper.
        if self.lower is not None and self.upper is not None:
            admits_no_date = self.lower > self.upper or (
                self.lower == self.upper and not (self.lower_inclusive and self.upper_inclusive)
            )
            if admits_no_date:
                self._become_empty()

    def _become_empty(self) -> None:
        """Collapse to the one empty representation, whatever bounds were written."""
        object.__setattr__(self, "lower", None)
        object.__setattr__(self, "upper", None)
        object.__setattr__(self, "lower_inclusive", False)
        object.__setattr__(self, "upper_inclusive", False)
        object.__setattr__(self, "is_empty", True)

    @classmethod
    def empty(cls, axis: TemporalRangeAxis) -> "TemporalRange":
        """The empty range on one axis."""
        return cls(axis=axis, is_empty=True)

    @override
    def __str__(self) -> str:
        """Render the canonical PostgreSQL range literal.

        This is the normalized interval, not the author's text -- a date range always
        renders half-open. Feeding the result back to `parse_range_literal` reproduces
        this same value, so the rendering is a fixed point rather than a lossy view.
        """
        if self.is_empty:
            return EMPTY_RANGE_LITERAL
        lower = "" if self.lower is None else self.lower
        upper = "" if self.upper is None else self.upper
        return (
            f"{'[' if self.lower_inclusive else '('}{lower},{upper}"
            f"{']' if self.upper_inclusive else ')'}"
        )


@dataclass(frozen=True, slots=True)
class TemporalFilter:
    """A valid-time question asked of the stored assertions.

    Exactly one of `at` (containment) or `overlaps` may be given, or neither -- a
    kind-only filter asks for sources that carry *any* assertion of that kind, which
    is a legal and useful question. A filter that asks nothing at all is refused
    rather than silently matching everything.
    """

    kind: TimeKind | None = None
    at: TemporalPoint | None = None
    overlaps: TemporalRange | None = None

    def __post_init__(self) -> None:
        if self.at is not None and self.overlaps is not None:
            raise TemporalQualifierError(
                "a temporal filter asks either 'at' or 'overlaps', never both"
            )
        if self.kind is None and self.at is None and self.overlaps is None:
            raise TemporalQualifierError("a temporal filter must name a kind, a point, or a range")

    @property
    def window(self) -> TemporalRange | None:
        """The interval this filter tests against, or None for a kind-only filter.

        Containment of a point is overlap with the closed range `[p,p]`: both ask
        whether the stored interval and the queried interval share at least one point.
        Collapsing them here lets one predicate answer both questions, which is also
        why the two can never disagree about inclusivity or bounds. On the date axis
        `TemporalRange` canonicalizes that window to `[p,p+1)` -- still the single day
        `p`, now in the half-open form the predicate compares correctly.
        """
        if self.at is not None:
            return TemporalRange(
                axis=self.at.axis,
                lower=self.at.value,
                upper=self.at.value,
                lower_inclusive=True,
                upper_inclusive=True,
            )
        return self.overlaps


@dataclass(frozen=True, slots=True)
class TemporalAssertion:
    """One authored statement that a source is valid over a span of time.

    Source identity -- entity, source type, source row id -- is deliberately absent.
    The parser reads markdown, where those ids do not exist yet; the projection layer
    pairs this value with them when it writes derived rows.

    `source_text` is the exact authored token. Serialization replays it verbatim, so a
    parse/serialize round trip reproduces the author's bounds and precision even though
    `valid_during` holds the normalized form.
    """

    time_kind: TimeKind
    valid_during: TemporalRange
    source_text: str
    extractor: str = OBSERVATION_EXTRACTOR
    metadata: dict[str, Any] | None = None


# --- Literal parsing ---


def parse_range_literal(literal: str, *, axis: TemporalRangeAxis | None = None) -> TemporalRange:
    """Parse a PostgreSQL-style range literal into a canonical `TemporalRange`.

    Accepts `[lower,upper)`, `(lower,upper]`, `[lower,)`, `(,upper)`, `(,)`, and the
    bare token `empty`. `axis` asserts the axis the caller expects; when omitted it is
    inferred from the bounds, which is why the bound-less forms require it explicitly.
    """
    text = literal.strip()
    if text == EMPTY_RANGE_LITERAL:
        if axis is None:
            raise TemporalQualifierError(
                "the 'empty' range literal has no bounds, so its axis must be given"
            )
        return TemporalRange.empty(axis)

    match = _RANGE_LITERAL.match(text)
    if match is None:
        raise TemporalQualifierError(
            f"range literal must be [lower,upper), (lower,upper], or 'empty': {literal!r}"
        )
    open_bracket, lower_text, upper_text, close_bracket = match.groups()
    lower_text = lower_text.strip()
    upper_text = upper_text.strip()

    written_axes = {_classify_bound(bound) for bound in (lower_text, upper_text) if bound}
    if len(written_axes) > 1:
        raise TemporalQualifierError(
            f"a range must not mix date-only and timestamp bounds: {literal!r}"
        )
    if not written_axes:
        if axis is None:
            raise TemporalQualifierError(
                f"a fully unbounded range has no bounds to classify: {literal!r}"
            )
        range_axis = axis
    else:
        range_axis = written_axes.pop()
        if axis is not None and range_axis is not axis:
            raise TemporalQualifierError(
                f"expected {axis.value} bounds but found {range_axis.value} bounds: {literal!r}"
            )

    return TemporalRange(
        axis=range_axis,
        lower=canonical_bound(lower_text, range_axis) if lower_text else None,
        upper=canonical_bound(upper_text, range_axis) if upper_text else None,
        lower_inclusive=open_bracket == "[",
        upper_inclusive=close_bracket == "]",
    )


def parse_point(text: str) -> TemporalPoint:
    """Parse one authored date or timestamp into a canonical `TemporalPoint`."""
    bound = text.strip()
    if not bound:
        raise TemporalQualifierError("a temporal point must not be empty")
    axis = _classify_bound(bound)
    return TemporalPoint(axis=axis, value=canonical_bound(bound, axis))


def parse_temporal_filter(
    *,
    valid_at: str | None = None,
    valid_overlaps: str | None = None,
    time_kind: str | None = None,
) -> TemporalFilter | None:
    """Parse the three flat boundary fields into one portable filter value.

    Every request surface -- HTTP, MCP, CLI -- carries a valid-time question as these
    three independent strings, so this is the one place that turns them into the domain
    value. Sharing it is what lets a caller validate the question *before* asking it and
    be certain the answer to "is this filter well formed?" is the same one the search
    service will reach.

    Every rejection is deliberate and loud: an unknown kind, a malformed range literal, a
    range mixing calendar dates with instants, or an impossible range raises rather than
    degrading into a filter that quietly matches something else. A timestamp written
    without an offset is not a rejection -- like every other naive datetime in the
    codebase, it is read as UTC.

    Returns None when no valid-time question was asked at all.
    """
    if not (valid_at or valid_overlaps or time_kind):
        return None

    kind: TimeKind | None = None
    if time_kind:
        try:
            kind = TimeKind(time_kind)
        except ValueError as exc:
            raise TemporalQualifierError(
                f"unknown time_kind {time_kind!r}; expected one of "
                f"{', '.join(item.value for item in TimeKind)}"
            ) from exc

    return TemporalFilter(
        kind=kind,
        at=parse_point(valid_at) if valid_at else None,
        overlaps=parse_range_literal(valid_overlaps) if valid_overlaps else None,
    )


# --- Flexible authored points ---


@lru_cache(maxsize=8)
def _date_data_parser(date_order: DateOrder) -> "DateDataParser":
    """The flexible reader for authored points, built once per configured date order.

    Deferred import: dateparser costs ~0.13s and loads locale data, and the modules
    that carry these values are imported on every CLI start (#886). Only an
    observation that already looks like a qualifier ever reaches this function.
    """
    from dateparser.date import DateDataParser

    return DateDataParser(
        settings={
            "DATE_ORDER": date_order,
            # Makes `period` report "time" when the author wrote a clock reading,
            # which is exactly the date-vs-instant distinction this module keeps.
            "RETURN_TIME_AS_PERIOD": True,
        }
    )


def _next_month_start(year: int, month: int) -> date | None:
    """The first day of the month after `year`-`month`, or None past the calendar's end.

    Only December 9999 has no successor month; year 10000 is not a date `datetime` can
    hold. Reported as None for the same reason `_next_calendar_day` reports its own
    edge: the caller decides what running off the end of the calendar means for it.
    """
    if month < 12:
        return date(year, month + 1, 1)
    if year == date.max.year:
        return None
    return date(year + 1, 1, 1)


def _calendar_span(lower: date, upper: date | None) -> TemporalRange:
    """The half-open calendar period `[lower,upper)`, unbounded when it runs to the end.

    A period whose successor is off the calendar needs no upper end: nothing follows
    9999-12-31, so `[lower,)` holds exactly the days `[lower,successor)` would have. It
    is the same equivalence `TemporalRange` applies to an inclusive upper bound on the
    last date, and it is why December 9999 is a period this reader can express rather
    than one it fails on.
    """
    return TemporalRange(
        axis=TemporalRangeAxis.DATE,
        lower=lower.isoformat(),
        upper=None if upper is None else upper.isoformat(),
        lower_inclusive=True,
    )


# --- Which language an authored point is written in ---
#
# An author writes a point in one of two languages, and they come with opposite promises.
# **ISO calendar syntax** is machine syntax: the text fixes the meaning, so it must be read
# literally or refused. **Everything else** -- `June 10, 2026`, `2026/03/04`, `10/07/2026`,
# `yesterday` -- is human syntax with no literal reading to contradict, so the flexible
# reader is trusted with it.
#
# The variants below are what a point can be once that question is settled, and settling it
# *once* is the whole design. Four review rounds went the other way: each added a shape test
# whose failure meant "not my business", so a token that failed the test fell through to the
# flexible reader and the next round found another shape that failed it. Here the classifier
# is total -- a token that opens with ISO syntax is an `_IsoDay`, an `_IsoMonth` or a
# `_MalformedIso`, and none of the three can reach the flexible reader.

# The ISO calendar components a point *opens* with: `YYYY-MM` and an optional `-DD`. A date
# carrying a time (`2026-06-10T14:00`, `2026-06-10 10:00 AM`) is matched on its date part
# alone, because `\d+` cannot cross the separator -- the rest is `trailing`, judged below.
#
# Each component is `\d+` rather than `\d{2}`, and nothing terminates the pattern, so the
# head matches whenever a point opens with ISO syntax at all. Both rules exist because the
# earlier cuts of this guard failed to match a malformed token and so let it escape: against
# `\d{2}` the day of `2026-01-0100` left a trailing `00` and matched nothing, and against a
# trailing `(?![\d-])` lookahead `2026-01-01-` matched nothing. Both reached the flexible
# reader, which is the one outcome ISO syntax must never have.
_ISO_CALENDAR_HEAD = re.compile(r"^(\d{4})-(\d+)(?:-(\d+))?")


def _named_calendar_date(year: str, month: str, day: str | None) -> date | None:
    """The date ISO-shaped calendar components name, or None when they name none.

    A month-only head is placed on the first of that month: the day is a component the
    author did not write, not one to guess at. `date` is the authority rather than a range
    check because it already owns leap years and month lengths.
    """
    # A month or a day is written with one or two digits, and that width is what separates
    # an author's shorthand from an author's typo: `2026-1-5` is a legitimate unpadded
    # spelling of a real date, while the `0100` in `2026-01-0100` is no day at all. Judged
    # before `date`, which takes a C long and raises OverflowError -- not the ValueError
    # below -- once a run of digits grows past it.
    if len(month) > 2 or (day is not None and len(day) > 2):
        return None
    try:
        return date(int(year), int(month), 1 if day is None else int(day))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class _IsoDay:
    """A point whose ISO head names a calendar day, and whatever was written after it.

    The day is authoritative: it is what the author typed, so no reading of `trailing` may
    contradict it. `trailing` is empty for a bare date; when it is not, the point is an
    instant, because a time of day is the only thing that can follow a complete date.
    """

    day: date
    trailing: str


@dataclass(frozen=True, slots=True)
class _IsoMonth:
    """A point whose ISO head names a calendar month (`2026-06`), and so denotes it.

    There is deliberately nowhere to put trailing text: nothing may follow a month. A clock
    reading needs a day to fall on, and the flexible reader supplies the day it was not
    given from *today*, so `2026-06 10:00` read as June 7 in March and June 1 in September
    -- the same note projecting different valid time on different indexing days.
    """

    year: int
    month: int


@dataclass(frozen=True, slots=True)
class _MalformedIso:
    """A point written in ISO syntax that names nothing on the calendar.

    `2026-13-01`, `2026-01-0100`, `2026-06 10:00`. The author reached for a machine date
    and missed, so there is no reading to fall back on -- only a guess, which is what this
    variant exists to make unreachable.
    """


@dataclass(frozen=True, slots=True)
class _FlexiblePoint:
    """A point in no machine syntax at all, for the flexible reader to interpret."""


type _AuthoredPoint = _IsoDay | _IsoMonth | _MalformedIso | _FlexiblePoint

_MALFORMED_ISO = _MalformedIso()
_FLEXIBLE_POINT = _FlexiblePoint()


def _classify_authored_point(point: str) -> _AuthoredPoint:
    """Decide which language one authored point is written in, and what it names.

    Total by construction, which is the property the whole design rests on: opening with
    ISO syntax settles the question, and the three ISO variants are all a token can then
    be. There is no "looks ISO but is not this function's business" answer to fall through
    on, which is what every earlier cut of this guard offered and what each review round
    found another way to reach.
    """
    head = _ISO_CALENDAR_HEAD.match(point)
    if head is None:
        return _FLEXIBLE_POINT

    year, month, day = head.groups()
    named = _named_calendar_date(year, month, day)
    if named is None:
        return _MALFORMED_ISO

    trailing = point[head.end() :]
    if day is None:
        # Trigger: the head names a month, with or without text after it.
        # Why: a month is a complete point on its own, so anything following it is part of
        #   a date this head cannot carry -- see `_IsoMonth` for what reading it costs.
        # Outcome: a bare month denotes its own period; a month with anything after it is
        #   malformed.
        return _MALFORMED_ISO if trailing else _IsoMonth(int(year), int(month))
    return _IsoDay(named, trailing)


def _read_iso_day(iso: _IsoDay, point: str, date_order: DateOrder) -> TemporalRange | None:
    """Read a point whose head names a calendar day, holding it to its own text."""
    if not iso.trailing:
        return TemporalRange(
            axis=TemporalRangeAxis.DATE, lower=iso.day.isoformat(), lower_inclusive=True
        )

    if _INSTANT_BOUND.match(point):
        # Trigger: the whole token is canonical RFC 3339.
        # Why: the author wrote the one form this module defines exactly, so it is read
        #   exactly -- to the microsecond, and refused rather than rounded when it names no
        #   moment (`2026-06-10T25:00:00+02:00`) or leaves the calendar in UTC. The flexible
        #   reader is neither that precise nor that strict.
        # Outcome: an instant, or a refusal; never a guess.
        try:
            instant = _canonical_instant(point)
        except TemporalQualifierError:
            return None
        return TemporalRange(axis=TemporalRangeAxis.INSTANT, lower=instant, lower_inclusive=True)

    # The author wrote a clock reading in some spelling of their own, so the flexible reader
    # is asked for it -- but only for it. What it hands back must be a time of day on the
    # very day the head names, which is the check that keeps its guessing out of the answer:
    # dateparser silently drops a suffix it cannot use (`2026-01-01T`, `2026-01-01Z`,
    # `2026-01-01+14:00` all came back as the bare date), and a suffix it half-understands
    # makes it abandon the ISO reading and re-guess the components under the configured
    # order (`2026-06-10x` came back as October 6). Asking what it *returned* rather than
    # what the suffix looks like is what covers every such shape, named or not.
    date_data = _date_data_parser(date_order).get_date_data(point)
    moment = date_data.date_obj
    if moment is None or date_data.period != "time" or moment.date() != iso.day:
        return None
    instant = _instant_value(moment)
    if instant is None:
        # A moment that leaves the calendar in UTC names no storable instant, so it reads
        # as no date at all -- the token stays content.
        return None
    return TemporalRange(axis=TemporalRangeAxis.INSTANT, lower=instant, lower_inclusive=True)


def _read_flexible_point(point: str, date_order: DateOrder) -> TemporalRange | None:
    """Read a point written in no machine syntax, taking the flexible reader at its word."""
    date_data = _date_data_parser(date_order).get_date_data(point)
    moment = date_data.date_obj
    if moment is None:
        return None

    # dateparser fills components the author did not write from today's date, so only
    # the components `period` vouches for may be read off `moment`.
    match date_data.period:
        case "time":
            instant = _instant_value(moment)
            if instant is None:
                # A moment that leaves the calendar in UTC names no storable instant,
                # so it reads as no date at all -- the token stays content.
                return None
            return TemporalRange(
                axis=TemporalRangeAxis.INSTANT,
                lower=instant,
                lower_inclusive=True,
            )
        case "year":
            # The month after December is the following January 1 -- except at year
            # 9999, where there is none and `_calendar_span` leaves the span open at
            # `[9999-01-01,)`, which is still exactly that year.
            return _calendar_span(date(moment.year, 1, 1), _next_month_start(moment.year, 12))
        case "month":
            return _calendar_span(
                date(moment.year, moment.month, 1),
                _next_month_start(moment.year, moment.month),
            )
        case _:
            # Day precision, and any coarser calendar period dateparser resolves to a
            # specific day ("last week"): the day it named, onward.
            return TemporalRange(
                axis=TemporalRangeAxis.DATE,
                lower=moment.date().isoformat(),
                lower_inclusive=True,
            )


def parse_authored_point(
    text: str, *, date_order: DateOrder = DEFAULT_DATE_ORDER
) -> TemporalRange | None:
    """Read one authored point into the interval its precision denotes.

    The precision the author wrote is the meaning:

        2026                    -> [2026-01-01,2027-01-01)   the year
        2026-06                 -> [2026-06-01,2026-07-01)   the month
        2026-06-10              -> [2026-06-10,)             from that date onward
        2026-06-10T14:00:00     -> [that instant,)           from that moment onward

    A year or a month is a period the author delimited by writing it. A date or a
    moment is not: `@effective 2026-06-10` means the decision took effect that day and
    still holds, so closing the range at midnight would expire it overnight. Callers
    that need a closed interval write the range literal instead.

    Non-ISO spellings are read leniently, because guessing at `June 10, 2026` is the
    whole point of this reader. A token that *is* ISO-shaped is held to its own text
    instead: its calendar components must name a real date, and anything trailing them
    must be a time of day on that date. `2026-06-10 10:00 AM` reads; `2026-01-01T` does
    not, because the author reached for an instant and no instant is there.

    Returns None when the text names no date. That is not an error -- the caller leaves
    such a token as ordinary observation content.
    """
    point = text.strip()
    match _classify_authored_point(point):
        case _IsoDay() as iso:
            return _read_iso_day(iso, point, date_order)
        case _IsoMonth() as iso:
            return _calendar_span(
                date(iso.year, iso.month, 1), _next_month_start(iso.year, iso.month)
            )
        case _MalformedIso():
            # The author wrote a machine date that names nothing. Refusal is `None`, as
            # everywhere else here: the token stays ordinary observation content,
            # unindexed but still full-text searchable.
            return None
        case _FlexiblePoint():
            return _read_flexible_point(point, date_order)
        case unreachable:  # pragma: no cover - `_AuthoredPoint` is closed
            assert_never(unreachable)
