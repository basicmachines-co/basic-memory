"""Peel SPEC-82 temporal qualifiers off observation content.

An observation may carry one qualifier immediately after its category and before its
content. Two authored forms exist, and the role is optional in both:

    - [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.
    - [decision] @effective:2026-07-27 The cache layer will use Memcached.
    - [decision] @2026-07-27 The cache layer will use Memcached.

The bracket form carries a range literal and needs no separator, because no role name
can begin with `[` or `(`. The point form needs the `:` because a date can begin with a
letter (`yesterday`), so nothing else would tell `@occurred:yesterday` from a handle.

**A point is one whitespace-delimited token**, in both forms. dateparser reads far more
than one token -- `June 10, 2026`, `2 days ago`, `2026-06-10 10:00 AM` all resolve, and
`parse_authored_point` accepts them -- but nothing here can tell where such a date ends:
dateparser also reads `June 10, 2026 The` and `2026-06-10 The`, so growing the token
until parsing fails would swallow the author's prose. A multi-word date therefore stays
content; `@occurred:2026-06-10` says the same thing in one token.

That token boundary is also why two shapes that *do* parse are refused, so a truncated
read never becomes a plausible-looking assertion:

* **A short number.** dateparser reads `1` as January and `3.5` as March 5, but at the
  head of a line those are list markers and version numbers. A numeric point must be at
  least as wide as a year.
* **A word naming only a month or a year** (`June`, `may`, `v2`). Alone it is usually
  prose; as the first token of `June 10, 2026` reading it would file June 2026 and leave
  `10, 2026` in the content. A word is taken only when it names a specific day
  (`yesterday`, `today`), in whatever language dateparser resolves it.

Beyond those, one rule decides everything: **if the payload reads as time, the token
becomes a qualifier; if it does not, the token stays ordinary observation content,
silently.** Prose is full of `@` -- email addresses, handles, `@todo:` markers -- and
warning about each one that is not a date would be noise, not help.

The single exception is an **unknown role**. `@asserted:2026-06-10` parses as time and
names an axis, so the author is plainly reaching for this feature and a short list of
valid roles makes the diagnostic actionable.

A refused or unread qualifier is never peeled. Its text stays in the observation
content, so the line indexes exactly as it does today and remains full-text searchable;
only the derived temporal projection is withheld.
"""

import re
from dataclasses import dataclass

from basic_memory.temporal import (
    DateOrder,
    TemporalAssertion,
    TemporalQualifierError,
    TemporalRange,
    TimeRole,
    parse_authored_point,
    parse_range_literal,
)

_ROLE_NAMES = frozenset(role.value for role in TimeRole)

# A point with no role is filed on the axis the feature is named for: the author said
# when the statement holds without narrowing *how* it holds.
DEFAULT_TIME_ROLE = TimeRole.VALID

_ROLE_PATTERN = r"[A-Za-z][A-Za-z0-9_]*"

# `@[role]` glued to one balanced bracket group carrying a range literal's comma. The
# lookahead stops `@effective[a,b)x` from half-matching, and the `^` anchor keeps
# `paul@basicmemory.com` and mid-sentence `@handles` out entirely.
_RANGE_QUALIFIER = re.compile(rf"^@({_ROLE_PATTERN})?([\[(][^\[\]()]*,[^\[\]()]*[\])])(?=\s|$)")

# `@role:<one token>`.
_ROLE_POINT_QUALIFIER = re.compile(rf"^@({_ROLE_PATTERN}):(\S+)")

# `@<one digit-led token>` -- the role-less point. Without a role there is nothing to
# distinguish a word from a handle, so only digits open the form at all.
_BARE_POINT_QUALIFIER = re.compile(r"^@(\d\S*)")

# The width of a year, and the shortest numeric token worth reading as one.
_MIN_NUMERIC_POINT_WIDTH = 4


@dataclass(frozen=True, slots=True)
class ObservationTemporalParse:
    """What a qualifier scan found at the head of one observation's content.

    Exactly three shapes exist: a peel (content shortened, one assertion, no error), an
    unknown-role refusal (content untouched, no assertions, an error message), and no
    qualifier at all (content untouched, nothing found).
    """

    content: str
    assertions: tuple[TemporalAssertion, ...]
    error: str | None


def _no_qualifier(content: str) -> ObservationTemporalParse:
    """Leave the line exactly as authored, with nothing to report."""
    return ObservationTemporalParse(content=content, assertions=(), error=None)


def _refuse(content: str, reason: str) -> ObservationTemporalParse:
    """Keep the line exactly as authored and report why no assertion was derived."""
    return ObservationTemporalParse(content=content, assertions=(), error=reason)


@dataclass(frozen=True, slots=True)
class _ReadQualifier:
    """One token that read as time: how much of the line it spans, and what it says."""

    token: str
    end: int
    role_name: str | None
    valid_during: TemporalRange


def _read_range_qualifier(content: str) -> _ReadQualifier | None:
    """Match the bracket form and parse its literal, or report no usable qualifier."""
    match = _RANGE_QUALIFIER.match(content)
    if match is None:
        return None
    try:
        valid_during = parse_range_literal(match.group(2))
    except TemporalQualifierError:
        # A literal we cannot read is not a qualifier. Saying *how* it is malformed
        # would be a diagnostic about how someone wrote a date, which this feature
        # deliberately does not issue.
        return None
    return _ReadQualifier(match.group(0), match.end(), match.group(1), valid_during)


def _names_a_deliberate_date(point: str, valid_during: TemporalRange) -> bool:
    """Whether a one-token point is specific enough to be an assertion rather than prose.

    The two shapes refused here both parse, which is exactly why they need refusing --
    see the module docstring for what each one costs if it is read.

    A bounded span is how a coarse point announces itself: `parse_authored_point` closes
    a year or a month at its successor and leaves a day or a moment open, so
    `upper is None` *is* "this names a specific day".
    """
    if point[0].isdigit():
        return len(point) >= _MIN_NUMERIC_POINT_WIDTH
    return valid_during.upper is None


def _read_point_qualifier(content: str, date_order: DateOrder | None) -> _ReadQualifier | None:
    """Match either point form and read its date, or report no usable qualifier."""
    roled = _ROLE_POINT_QUALIFIER.match(content)
    bare = None if roled is not None else _BARE_POINT_QUALIFIER.match(content)
    match = roled or bare
    if match is None:
        return None

    # Deferred, following utils.ensure_timezone_aware: the markdown parser is a
    # low-level module that many entrypoints import, and pulling the config stack in at
    # import time couples parsing to configuration load order for no benefit. Resolved
    # here rather than at the top of the scan so only a token that already looks like a
    # qualifier pays for reading the config -- or for loading dateparser.
    from basic_memory.config import ConfigManager

    order = date_order if date_order is not None else ConfigManager().config.date_order
    point = match.group(2) if roled is not None else match.group(1)
    valid_during = parse_authored_point(point, date_order=order)
    if valid_during is None or not _names_a_deliberate_date(point, valid_during):
        return None
    role_name = match.group(1) if roled is not None else None
    return _ReadQualifier(match.group(0), match.end(), role_name, valid_during)


def parse_temporal_qualifier(
    content: str, *, date_order: DateOrder | None = None
) -> ObservationTemporalParse:
    """Split a leading temporal qualifier off observation content.

    The MVP reads at most one qualifier per observation, but the result is a collection
    so supporting several later is not a schema break. `date_order` defaults to the
    configured `date_order`; tests and callers that already hold the config pass it.
    """
    read = _read_range_qualifier(content) or _read_point_qualifier(content, date_order)
    if read is None:
        return _no_qualifier(content)

    role_name = read.role_name
    if role_name is not None and role_name not in _ROLE_NAMES:
        known = ", ".join(sorted(_ROLE_NAMES))
        return _refuse(content, f"unknown temporal role {role_name!r} in {read.token!r} ({known})")

    remainder = content[read.end :].strip()
    if not remainder:
        # A qualifier with nothing to qualify would leave an empty observation, which
        # the plugin drops outright. Keep the line whole instead.
        return _no_qualifier(content)

    assertion = TemporalAssertion(
        time_role=TimeRole(role_name) if role_name is not None else DEFAULT_TIME_ROLE,
        valid_during=read.valid_during,
        source_text=read.token,
    )
    return ObservationTemporalParse(content=remainder, assertions=(assertion,), error=None)
