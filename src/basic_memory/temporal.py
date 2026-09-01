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
* A **point** (`2026-06-10`, `2026-06`, `2026`, `yesterday`) is the convenient form.
  It is read with `dateparser` and denotes the span its precision covers, so an author
  never has to spell out a range to say when something started.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, override

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

    Returns None when the text names no date. That is not an error -- the caller leaves
    such a token as ordinary observation content.
    """
    point = text.strip()
    if _DATE_BOUND.match(point):
        # Trigger: the text is already in the canonical ISO date shape.
        # Why: dateparser is lenient with impossible components -- it reads
        #   "2026-13-01" as the 13th of January -- and a silently wrong date is worse
        #   than an unread token.
        # Outcome: ISO dates are parsed as ISO, or refused.
        try:
            return TemporalRange(
                axis=TemporalRangeAxis.DATE,
                lower=date.fromisoformat(point).isoformat(),
                lower_inclusive=True,
            )
        except ValueError:
            return None

    if _INSTANT_BOUND.match(point):
        # Trigger: the text is already in the canonical RFC 3339 timestamp shape.
        # Why: the leniency the branch above guards against reaches timestamps too --
        #   dateparser reads "2026-13-01T10:00:00" as 10:00 on the 13th of January -- and
        #   every reindex would project that same wrong instant, so it is worse than an
        #   unread token. Only the *shape* is matched here, so the flexible spellings
        #   dateparser alone reads ("2026-06-10 10:00 AM", a timestamp with no seconds)
        #   still reach it below.
        # Outcome: RFC 3339 timestamps are parsed as RFC 3339, or refused.
        try:
            instant = _canonical_instant(point)
        except TemporalQualifierError:
            return None
        return TemporalRange(
            axis=TemporalRangeAxis.INSTANT,
            lower=instant,
            lower_inclusive=True,
        )

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
