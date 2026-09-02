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
* A **point** (`2026-06-10`, `2026-06`, `2026`, `June 10, 2026`) is the convenient form.
  It denotes the span its precision covers, so an author never has to spell out a range to
  say when something started. A point written in ISO calendar syntax is read literally,
  because its text fixes its meaning; any other spelling is read with `dateparser`,
  because there is no literal reading for a guess to contradict. Either way the reading
  must be the same on every pass -- `yesterday` names no fixed span and is refused, for
  the reasons set out above `parse_authored_point`.
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


TEMPORAL_FILTER_FIELDS = ("valid_at", "valid_overlaps", "time_kind")


def reject_blank_temporal_value(field: str, value: str | None) -> None:
    """Refuse a valid-time field that is present but carries nothing.

    `None` is how a caller says "no valid-time filter"; these fields are declared optional
    precisely so that spelling exists. An empty or whitespace-only string is a different
    statement -- a caller who believes they applied a filter -- and reading it as absence
    is the failure this whole feature keeps having to close: a query that reports itself as
    filtered, runs unfiltered, and answers with the undated rows the filter was meant to
    exclude. Truthiness cannot tell the two apart, so presence is tested against `None`
    everywhere on this path and blankness is refused here.
    """
    if value is not None and not value.strip():
        raise TemporalQualifierError(
            f"{field} was given as an empty value; omit {field} to search without a "
            f"valid-time filter"
        )


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

    Returns None when no valid-time question was asked at all -- which means all three
    fields are absent, not merely falsy. See `reject_blank_temporal_value`.
    """
    for field, value in zip(TEMPORAL_FILTER_FIELDS, (valid_at, valid_overlaps, time_kind)):
        reject_blank_temporal_value(field, value)

    if valid_at is None and valid_overlaps is None and time_kind is None:
        return None

    kind: TimeKind | None = None
    if time_kind is not None:
        try:
            kind = TimeKind(time_kind)
        except ValueError as exc:
            raise TemporalQualifierError(
                f"unknown time_kind {time_kind!r}; expected one of "
                f"{', '.join(item.value for item in TimeKind)}"
            ) from exc

    return TemporalFilter(
        kind=kind,
        at=parse_point(valid_at) if valid_at is not None else None,
        overlaps=parse_range_literal(valid_overlaps) if valid_overlaps is not None else None,
    )


# --- Flexible authored points ---


@lru_cache(maxsize=16)
def _date_data_parser(date_order: DateOrder, relative_base: datetime) -> "DateDataParser":
    """The flexible reader for authored points, built once per order and reference instant.

    Deferred import: dateparser costs ~0.13s and loads locale data, and the modules
    that carry these values are imported on every CLI start (#886). Only an
    observation that already looks like a qualifier ever reaches this function.

    `relative_base` is the instant the reader treats as "now". It is always supplied
    explicitly, never left to the wall clock, because the wall clock is what made a
    reading depend on the day it ran -- see `_STABILITY_PROBE_BASES`.
    """
    from dateparser.date import DateDataParser

    return DateDataParser(
        settings={
            "DATE_ORDER": date_order,
            # Makes `period` report "time" when the author wrote a clock reading,
            # which is exactly the date-vs-instant distinction this module keeps.
            "RETURN_TIME_AS_PERIOD": True,
            # Fixes what "now" means for this reading, so relative wording resolves
            # against a stated instant rather than the moment the indexer happened to run.
            "RELATIVE_BASE": relative_base,
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
# literally or refused. **Everything else** -- `June 10, 2026`, `2026/03/04`, `10/07/2026`
# -- is human syntax with no literal reading to contradict, so the flexible reader is
# trusted with it. Trusted to *read* it, that is: what it hands back must still name the
# same span whenever it is asked, which is what refuses `yesterday` further down.
#
# The variants below are what a point can be once that question is settled, and settling it
# *once* is the whole design. Four review rounds went the other way: each added a shape test
# whose failure meant "not my business", so a token that failed the test fell through to the
# flexible reader and the next round found another shape that failed it. Here the classifier
# is total: a token that opens with ISO syntax is an `_IsoDay`, an `_IsoMonth` or a
# `_MalformedIso`, and there is no fourth answer to fall through on.
#
# What that buys is narrower than "ISO-shaped text never reaches the flexible reader", and
# stating it precisely matters, because the loose version is false. An `_IsoDay`'s *trailing*
# text is still read by the flexible reader -- that is what reads `2026-06-10 10:00 AM`, and
# no grammar of clock spellings could. What the classifier settles for good is the *calendar*:
# a head that names no date dies here, and a real one is carried on the variant so the reading
# below can be held to it. The trailing is fenced by two rules instead, and dateparser's answer
# is believed only when both hold. It must come back as a time of day on the day the head names
# -- checked in `_read_iso_day`, against what it *returned*, since a suffix's looks do not say
# what it will do with it. And the text must not spell precision a canonical instant cannot
# carry -- checked here, on the text, because that is the one defect the returned-value check
# cannot see: a truncated fraction still lands on the right day.

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

# What it takes to be *reaching* for an ISO date, as opposed to naming one. A year and a
# hyphen is a commitment to machine syntax; nothing else is spelled that way. The head
# above still needs digits after that hyphen, so `2026--01`, `2026-` and `2026-x01` matched
# it not at all and fell to the flexible reader -- which invented January 2026, the whole of
# 2026, and *October 1st* respectively, none of which appears in the text. Claiming the
# opening separately is what makes the classifier total in the way it always claimed to be:
# a token that opens in ISO syntax is judged as ISO or refused, never handed on because the
# rest of it was too broken to parse. A bare `2026` carries no hyphen and is untouched.
_ISO_CALENDAR_OPENING = re.compile(r"^\d{4}-")

# A fractional-second run too wide for a canonical instant to carry. `_INSTANT_BOUND` caps the
# fraction at six digits and *refuses* a longer one rather than truncating it, because dropping
# digits would store a different instant than the author wrote -- but that refusal only ever
# governed the strict path. The flexible reader has no such scruple: it truncates
# `2026-01-01T10:00:00.1234567` to `...123456Z` and reports a time on the right day, so every
# check `_read_iso_day` makes passes and the authored instant is quietly rewritten on each
# reindex. Judged on the text so both paths refuse the same token for the same reason, and it
# is the same reason the calendar width rule exists: a digit run wider than the syntax allows
# is a typo, not a shorthand. Six digits and fewer are untouched -- `14:00:00.5` is precision
# a canonical instant holds exactly, so it still reads.
_OVER_PRECISE_FRACTION = re.compile(r"\.\d{7,}")


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
    """A point written in ISO syntax that cannot be read as written.

    `2026-13-01`, `2026-01-0100`, `2026-06 10:00`, `2026-01-01T10:00:00.1234567`. Either the
    components name nothing on the calendar, or they name a moment finer than a canonical
    instant records. The author reached for a machine date and missed, so there is no reading
    to fall back on -- only a guess, which is what this variant exists to make unreachable.
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
        # Trigger: the token opens `YYYY-` but no calendar components could be read from it.
        # Why: the author reached for a machine date and mistyped it. Handing that to the
        #   flexible reader is the one outcome ISO syntax must never have -- it does not
        #   report failure, it re-guesses, and a slipped keystroke becomes a confident date
        #   nobody wrote, reproduced identically by every reindex.
        # Outcome: malformed, so the token stays observation content.
        if _ISO_CALENDAR_OPENING.match(point):
            return _MALFORMED_ISO
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

    # Trigger: the text after the date spells a fraction of a second wider than six digits.
    # Why: no reader here can store it, and the two that try disagree -- `_canonical_instant`
    #   refuses it, while the flexible reader truncates it and still answers with a time on
    #   the head's day, which is precisely what `_read_iso_day`'s returned-value check cannot
    #   catch. A guard that asks what came back cannot see digits that never made it in.
    # Outcome: refused as malformed, so the strict and flexible paths give the same answer to
    #   the same text and the token stays observation content rather than a rounded instant.
    if _OVER_PRECISE_FRACTION.search(trailing):
        return _MALFORMED_ISO
    return _IsoDay(named, trailing)


def _read_iso_day(
    iso: _IsoDay, point: str, date_order: DateOrder, relative_base: datetime
) -> TemporalRange | None:
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
    date_data = _date_data_parser(date_order, relative_base).get_date_data(point)
    moment = date_data.date_obj
    if moment is None or date_data.period != "time" or moment.date() != iso.day:
        return None
    instant = _instant_value(moment)
    if instant is None:
        # A moment that leaves the calendar in UTC names no storable instant, so it reads
        # as no date at all -- the token stays content.
        return None
    return TemporalRange(axis=TemporalRangeAxis.INSTANT, lower=instant, lower_inclusive=True)


# A fully numeric date, with the year written first or last. It is the one non-ISO shape
# whose meaning is *fixed* rather than guessed: `date_order` says which of the other two
# runs is the month, so the text plus one setting determine the date exactly and there is
# nothing left to interpret. dateparser does not treat it that way. Handed a run it cannot
# use where the author put it, it silently moves that run to the other slot and answers with
# a real date -- `2026/13/01` under YMD, or `13/01/2026` under MDY, both come back as
# January 13, a date the configured order does not name and the author did not write, refiled
# identically by every reindex. That is the `2026-13-01` disease in the one syntax the ISO
# classifier deliberately does not claim.
_YEAR_FIRST_NUMERIC_DATE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_YEAR_LAST_NUMERIC_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

# Whether the first of the two non-year runs names the month, given the configured order and
# where the year was written. Five of the six are the order read literally. The sixth is not:
# `YMD` describes no arrangement that ends in the year, so with the year last the reader
# falls back to day-first, and that fallback is this module's behaviour too -- pinned by the
# `10/07/2026` cases rather than left implicit. Naming all six is what lets the check compare
# against a reading it can state, instead of trusting whatever came back.
_MONTH_LEADS_THE_REMAINDER: dict[tuple[DateOrder, bool], bool] = {
    ("YMD", True): True,
    ("MDY", True): True,
    ("DMY", True): False,
    ("MDY", False): True,
    ("DMY", False): False,
    ("YMD", False): False,
}


def _ordered_numeric_date(point: str, date_order: DateOrder) -> date | None | Literal[False]:
    """The date a fully numeric token names under `date_order`.

    `False` means the token is not that shape and this rule has nothing to say about it;
    `None` means it is, and names no date on the calendar.

    A two-digit year is not this shape: `03/04/26` leaves which run is even the year to the
    reader, so there is no stated reading to hold it to.
    """
    year_first = _YEAR_FIRST_NUMERIC_DATE.match(point)
    ordered = year_first or _YEAR_LAST_NUMERIC_DATE.match(point)
    if ordered is None:
        return False

    runs = [int(run) for run in ordered.groups()]
    year = runs.pop(0) if year_first else runs.pop()
    first, second = runs
    month, day = (
        (first, second)
        if _MONTH_LEADS_THE_REMAINDER[(date_order, bool(year_first))]
        else (second, first)
    )
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _read_flexible_point(
    point: str, date_order: DateOrder, relative_base: datetime
) -> TemporalRange | None:
    """Read a point written in no machine syntax, taking the flexible reader at its word."""
    date_data = _date_data_parser(date_order, relative_base).get_date_data(point)
    moment = date_data.date_obj
    if moment is None:
        return None

    # Trigger: a year-first numeric date, whose meaning `date_order` fixes exactly.
    # Why: the reader is free to move a run it cannot use where the author put it, and
    #   `2026/13/01` under YMD comes back as January 13 rather than as the impossible month
    #   the author actually typed. Holding it to the order is the same rule the ISO
    #   classifier applies to `2026-13-01`, in the syntax that classifier does not claim.
    # Outcome: refused when the order names no date, and when the reader answered with a
    #   different one than the order names -- never quietly re-ordered.
    ordered = _ordered_numeric_date(point, date_order)
    if ordered is not False and moment.date() != ordered:
        return None

    # dateparser fills components the author did not write from the reference instant, so
    # only the components `period` vouches for may be read off `moment`. Discarding the
    # rest is also what lets `June 2026` survive the stability check: the filled-in day
    # differs between probes, and the month this builds from it does not.
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


# --- Readings must not depend on when they are taken ---
#
# A qualifier's meaning has to be recoverable from the note's own bytes, because those are
# the only thing that travels. `@occurred:yesterday` failed that: dateparser resolved it
# against the wall clock of each parse, so reindexing an unedited note replaced its stored
# range with a different one -- `[2026-08-31,)` on September 1, `[2026-09-09,)` on the
# 10th -- and a search that matched last week stopped matching today with nothing having
# changed on disk.
#
# The two obvious repairs both reintroduce the drift by another route. Anchoring to the
# entity's `created_at` anchors to derived metadata that can shift on re-import or clone.
# Storing the resolved range makes the projection unreproducible: a fresh clone reindexing
# from markdown alone cannot arrive at it, and this table is rebuilt from markdown by
# definition. Refusing is what keeps the file the sole source of truth, and it is what the
# classifier already does with an ISO-shaped token it cannot pin down.
#
# The rule is *determinism*, not a vocabulary. A list of relative words is the shape this
# guard was refactored away from once already, and it could never have covered the whole
# of dateparser's relative vocabulary in every language it reads. Instead the reading is
# taken twice against two stated reference instants and kept only if it did not move.
# Whatever `now` was reaching -- a word, a phrase, an omitted component -- lands somewhere
# different under each, so it is caught without ever being named.
#
# The comparison is on the resulting *range*, never on dateparser's datetime. A month or a
# year is delimited by what the author wrote, and `_read_flexible_point` discards the
# components its `period` does not vouch for, so `June 2026` and `2026` answer with one
# range from two different datetimes. Comparing datetimes would refuse them.

# A digit run no calendar component could be, and the reason this module refuses to hand
# one to a reader at all. Python declines to convert a decimal string longer than
# `sys.get_int_max_str_digits()` -- 4300 by default, and never settable below 640 -- into an
# int, and dateparser converts the runs it finds without catching that. So a token carrying
# a long enough run raised `ValueError` straight out of the reader and aborted the parse of
# the *entire note*, taking every other observation on the page with it. That is worse than
# any wrong date: elsewhere an unreadable qualifier costs its own token and nothing else.
#
# The cap is far below the smallest limit the runtime allows and far above the widest run a
# point can legitimately carry -- six digits of a fractional second, four of a year -- so it
# separates "no date" from "real date" without ever being the rule that decides a readable
# token's fate.
_UNREADABLE_DIGIT_RUN = re.compile(r"\d{32,}")

# Two reference instants that disagree in every component -- year, month, day, weekday,
# hour, minute, second -- so nothing filled in from "now" can coincide across them.
_STABILITY_PROBE_BASES = (
    datetime(2001, 3, 4, 5, 6, 7),
    datetime(2097, 11, 21, 22, 33, 44),
)


def _read_authored_point(
    point: str, date_order: DateOrder, relative_base: datetime
) -> TemporalRange | None:
    """Read one already-stripped point, treating `relative_base` as the present."""
    match _classify_authored_point(point):
        case _IsoDay() as iso:
            return _read_iso_day(iso, point, date_order, relative_base)
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
            return _read_flexible_point(point, date_order, relative_base)
        case unreachable:  # pragma: no cover - `_AuthoredPoint` is closed
            assert_never(unreachable)


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
    must be a time of day on that date, written to a precision this module can store.
    `2026-06-10 10:00 AM` reads; `2026-01-01T` does not, because the author reached for an
    instant and no instant is there; `2026-01-01T10:00:00.1234567` does not either,
    because storing it would mean dropping the digits that made it worth writing.

    A reading that would depend on when it was taken is refused, however it is spelled:
    `yesterday`, `2 days ago`, `next month`, a bare `March` whose year would come from
    the current one. What the author wrote must name the same interval on every pass, or
    the note does not say when it holds -- see the section comment above for why anchoring
    it elsewhere would not fix that. Precision the author simply did not write is a
    different thing and still reads: `2026` and `June 2026` delimit their own periods.

    Returns None when the text names no date. That is not an error -- the caller leaves
    such a token as ordinary observation content.
    """
    point = text.strip()
    # Trigger: a run of digits too long for any calendar component, or for Python to
    #   convert at all.
    # Why: the readers below call int() on the runs they find, so this is refused before
    #   either sees it -- both of them reach dateparser, and neither can be trusted with a
    #   token that makes it raise.
    # Outcome: no date, the same answer as any other unreadable point, and the rest of the
    #   note goes on indexing.
    if _UNREADABLE_DIGIT_RUN.search(point):
        return None

    early, late = _STABILITY_PROBE_BASES
    reading = _read_authored_point(point, date_order, early)
    if reading is None:
        return None
    # Trigger: the same text named a different interval when "now" was somewhere else.
    # Why: then the note's bytes do not fix its meaning, and every reindex is free to
    #   file a different valid time for a file nobody edited.
    # Outcome: refused like any other unreadable point -- the token stays content.
    #   A point that never consults the flexible reader cannot move, so this second pass
    #   costs those tokens a regex and no date parsing at all.
    if _read_authored_point(point, date_order, late) != reading:
        return None
    return reading


def names_only_a_calendar_period(text: str, *, date_order: DateOrder = DEFAULT_DATE_ORDER) -> bool:
    """Whether the text names a month or a year rather than a specific day.

    This explains a refusal; it never files one. `parse_authored_point` answers "does this
    name one interval, whatever the date?", and a bare month name fails that because its
    year comes from the present. But *that it is a month at all* does not: `June` reads as
    a bounded calendar period under any present, and only which one moves. Asking for the
    shape rather than the value recovers a fact the stability rule would otherwise take
    with it -- the fact that lets `@occurred:June 10, 2026` be told to quote itself instead
    of going silently unread.

    False for text that names nothing and for text that names a specific day, which are
    the two cases with no truncated multi-word date to warn about.
    """
    point = text.strip()
    readings = (_read_authored_point(point, date_order, base) for base in _STABILITY_PROBE_BASES)
    return all(reading is not None and reading.upper is not None for reading in readings)
