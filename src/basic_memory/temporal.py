"""Portable temporal value types for authored valid time (SPEC-82).

Basic Memory authors time as *semantic* data. A `[decision]` that was effective from
June 10 until the July 27 cutover is a statement about the world, not a record of when
the note was edited. This module owns the values that carry such a statement and the
lexical grammar for the range literals authors write.

PostgreSQL's range conventions are the language contract: `[lower,upper)` with explicit
inclusivity per side, unbounded ends, and a distinguished empty range. That is a
vocabulary choice, not a storage requirement -- these values reduce to portable scalars
so SQLite and Postgres can share one logical model.

Two canonical lexical forms carry every bound:

    date     ``YYYY-MM-DD``                   (10 characters)
    instant  ``YYYY-MM-DDTHH:MM:SS.ffffffZ``  (27 characters, always UTC)

Both are fixed width with ASCII digits in fixed positions, so byte-lexicographic order
is chronological order. That is what lets containment and overlap be plain string
comparisons with identical SQL text in either dialect.

The two kinds never mix and never convert into one another. A date bound is a calendar
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
from datetime import UTC, date, datetime
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, override

if TYPE_CHECKING:  # pragma: no cover - import exists only for the annotation below
    from dateparser.date import DateDataParser


class TemporalQualifierError(ValueError):
    """A temporal qualifier, range literal, or bound failed to parse or validate."""


class TimeRole(StrEnum):
    """Which time axis an assertion describes.

    `recorded` is deliberately absent: recorded time is never authored in markdown.
    """

    EFFECTIVE = "effective"
    VALID = "valid"
    OCCURRED = "occurred"
    DUE = "due"
    MENTIONED = "mentioned"


class TemporalRangeKind(StrEnum):
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


def _classify_bound(bound: str) -> TemporalRangeKind:
    """Decide which axis an authored bound is written on."""
    if _TIMESTAMP_SHAPE.match(bound):
        return TemporalRangeKind.INSTANT
    return TemporalRangeKind.DATE


def _canonical_date(bound: str) -> str:
    if not _DATE_BOUND.match(bound):
        raise TemporalQualifierError(f"date bound must be YYYY-MM-DD: {bound!r}")
    try:
        return date.fromisoformat(bound).isoformat()
    except ValueError as exc:
        raise TemporalQualifierError(f"not a calendar date: {bound!r}") from exc


def _instant_value(moment: datetime) -> str:
    """Render one moment as the canonical fixed-width UTC instant.

    A naive moment is read as UTC rather than refused. That is the house convention for
    every other naive datetime in the codebase, and it is what lets an author write
    `2026-07-27T18:42:00` without learning RFC 3339's offset syntax first.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


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
    return _instant_value(moment)


def canonical_bound(bound: str, kind: TemporalRangeKind) -> str:
    """Normalize one authored bound to the canonical fixed-width form for its kind."""
    if kind is TemporalRangeKind.DATE:
        return _canonical_date(bound)
    return _canonical_instant(bound)


def _require_canonical(value: str, kind: TemporalRangeKind) -> None:
    """Reject a value that skipped `canonical_bound` on its way into a domain value."""
    pattern = _DATE_BOUND if kind is TemporalRangeKind.DATE else _CANONICAL_INSTANT
    if not pattern.match(value):
        raise TemporalQualifierError(f"{kind.value} bound is not canonical: {value!r}")


# --- Values ---


@dataclass(frozen=True, slots=True)
class TemporalPoint:
    """One calendar date or instant that a containment question is asked about."""

    kind: TemporalRangeKind
    value: str

    def __post_init__(self) -> None:
        _require_canonical(self.value, self.kind)

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TemporalRange:
    """One authored interval on a single time axis.

    Bounds are canonical lexical strings; `None` means unbounded on that side.
    Construction normalizes two PostgreSQL rules so no caller has to remember them:
    an unbounded side is always exclusive, and a degenerate interval (`[a,a)`,
    `(a,a]`, `(a,a)`) *is* the empty range.

    Unlike PostgreSQL's `daterange`, a discrete date range is not rewritten into the
    canonical `[)` form -- `[a,b]` keeps the inclusivity the author wrote. Evaluating
    the authored flags directly is set-equivalent for containment and overlap and needs
    no date arithmetic; only the rendered literal differs.
    """

    kind: TemporalRangeKind
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
                _require_canonical(bound, self.kind)

        # Canonical bounds are fixed width, so string order is chronological order.
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise TemporalQualifierError(
                f"range lower bound {self.lower} is after upper bound {self.upper}"
            )

        # PostgreSQL: an unbounded side cannot be inclusive; there is no endpoint.
        if self.lower is None:
            object.__setattr__(self, "lower_inclusive", False)
        if self.upper is None:
            object.__setattr__(self, "upper_inclusive", False)

        # PostgreSQL: an interval whose endpoints coincide without including both of
        # them contains no points, and is therefore the empty range.
        if (
            self.lower is not None
            and self.lower == self.upper
            and not (self.lower_inclusive and self.upper_inclusive)
        ):
            object.__setattr__(self, "lower", None)
            object.__setattr__(self, "upper", None)
            object.__setattr__(self, "lower_inclusive", False)
            object.__setattr__(self, "upper_inclusive", False)
            object.__setattr__(self, "is_empty", True)

    @classmethod
    def empty(cls, kind: TemporalRangeKind) -> "TemporalRange":
        """The empty range on one axis."""
        return cls(kind=kind, is_empty=True)

    @override
    def __str__(self) -> str:
        """Render the canonical PostgreSQL range literal."""
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
    role-only filter asks for sources that carry *any* assertion on that axis, which
    is a legal and useful question. A filter that asks nothing at all is refused
    rather than silently matching everything.
    """

    role: TimeRole | None = None
    at: TemporalPoint | None = None
    overlaps: TemporalRange | None = None

    def __post_init__(self) -> None:
        if self.at is not None and self.overlaps is not None:
            raise TemporalQualifierError(
                "a temporal filter asks either 'at' or 'overlaps', never both"
            )
        if self.role is None and self.at is None and self.overlaps is None:
            raise TemporalQualifierError("a temporal filter must name a role, a point, or a range")

    @property
    def window(self) -> TemporalRange | None:
        """The interval this filter tests against, or None for a role-only filter.

        Containment of a point is overlap with the degenerate closed range `[p,p]`:
        both ask whether the stored interval and the queried interval share at least
        one point. Collapsing them here lets one predicate answer both questions,
        which is also why the two can never disagree about inclusivity or bounds.
        """
        if self.at is not None:
            return TemporalRange(
                kind=self.at.kind,
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

    time_role: TimeRole
    valid_during: TemporalRange
    source_text: str
    extractor: str = OBSERVATION_EXTRACTOR
    metadata: dict[str, Any] | None = None


# --- Literal parsing ---


def parse_range_literal(literal: str, *, kind: TemporalRangeKind | None = None) -> TemporalRange:
    """Parse a PostgreSQL-style range literal into a canonical `TemporalRange`.

    Accepts `[lower,upper)`, `(lower,upper]`, `[lower,)`, `(,upper)`, `(,)`, and the
    bare token `empty`. `kind` asserts the expected axis; when omitted the axis is
    inferred from the bounds, which is why the bound-less forms require it explicitly.
    """
    text = literal.strip()
    if text == EMPTY_RANGE_LITERAL:
        if kind is None:
            raise TemporalQualifierError(
                "the 'empty' range literal has no bounds, so its kind must be given"
            )
        return TemporalRange.empty(kind)

    match = _RANGE_LITERAL.match(text)
    if match is None:
        raise TemporalQualifierError(
            f"range literal must be [lower,upper), (lower,upper], or 'empty': {literal!r}"
        )
    open_bracket, lower_text, upper_text, close_bracket = match.groups()
    lower_text = lower_text.strip()
    upper_text = upper_text.strip()

    written_kinds = {_classify_bound(bound) for bound in (lower_text, upper_text) if bound}
    if len(written_kinds) > 1:
        raise TemporalQualifierError(
            f"a range must not mix date-only and timestamp bounds: {literal!r}"
        )
    if not written_kinds:
        if kind is None:
            raise TemporalQualifierError(
                f"a fully unbounded range has no bounds to classify: {literal!r}"
            )
        range_kind = kind
    else:
        range_kind = written_kinds.pop()
        if kind is not None and range_kind is not kind:
            raise TemporalQualifierError(
                f"expected {kind.value} bounds but found {range_kind.value} bounds: {literal!r}"
            )

    return TemporalRange(
        kind=range_kind,
        lower=canonical_bound(lower_text, range_kind) if lower_text else None,
        upper=canonical_bound(upper_text, range_kind) if upper_text else None,
        lower_inclusive=open_bracket == "[",
        upper_inclusive=close_bracket == "]",
    )


def parse_point(text: str) -> TemporalPoint:
    """Parse one authored date or timestamp into a canonical `TemporalPoint`."""
    bound = text.strip()
    if not bound:
        raise TemporalQualifierError("a temporal point must not be empty")
    kind = _classify_bound(bound)
    return TemporalPoint(kind=kind, value=canonical_bound(bound, kind))


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


def _calendar_span(lower: date, upper: date) -> TemporalRange:
    """The half-open calendar period `[lower,upper)`."""
    return TemporalRange(
        kind=TemporalRangeKind.DATE,
        lower=lower.isoformat(),
        upper=upper.isoformat(),
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
                kind=TemporalRangeKind.DATE,
                lower=date.fromisoformat(point).isoformat(),
                lower_inclusive=True,
            )
        except ValueError:
            return None

    date_data = _date_data_parser(date_order).get_date_data(point)
    moment = date_data.date_obj
    if moment is None:
        return None

    # dateparser fills components the author did not write from today's date, so only
    # the components `period` vouches for may be read off `moment`.
    match date_data.period:
        case "time":
            return TemporalRange(
                kind=TemporalRangeKind.INSTANT,
                lower=_instant_value(moment),
                lower_inclusive=True,
            )
        case "year":
            if moment.year >= date.max.year:
                # There is no January 1 after year 9999 to close the span with.
                return None
            return _calendar_span(date(moment.year, 1, 1), date(moment.year + 1, 1, 1))
        case "month":
            first = date(moment.year, moment.month, 1)
            next_month = (
                date(first.year + 1, 1, 1)
                if first.month == 12
                else date(first.year, first.month + 1, 1)
            )
            return _calendar_span(first, next_month)
        case _:
            # Day precision, and any coarser calendar period dateparser resolves to a
            # specific day ("last week"): the day it named, onward.
            return TemporalRange(
                kind=TemporalRangeKind.DATE,
                lower=moment.date().isoformat(),
                lower_inclusive=True,
            )
