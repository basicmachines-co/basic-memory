"""Peel SPEC-82 temporal qualifiers off observation content.

An observation may carry one qualifier immediately after its category and before its
content. Three authored forms exist, and the kind is optional in all of them:

    - [decision] @effective[2026-06-10,2026-07-27) The cache layer will use Redis.
    - [decision] @effective:2026-07-27 The cache layer will use Memcached.
    - [decision] @effective:"June 10, 2026" The cache layer will use Memcached.
    - [decision] @2026-07-27 The cache layer will use Memcached.

The bracket form carries a range literal and needs no separator, because no kind name
can begin with `[` or `(`. The point forms need the `:` because a date can begin with a
letter (`yesterday`), so nothing else would tell `@occurred:yesterday` from a handle.

**An unquoted point is one whitespace-delimited token.** dateparser reads far more than
one token -- `June 10, 2026`, `2 days ago`, `2026-06-10 10:00 AM` all resolve, and
`parse_authored_point` accepts them -- but nothing here can tell where such a date ends:
dateparser also reads `June 10, 2026 The` and `2026-06-10 The`, so growing the token
until parsing fails would swallow the author's prose.

**A quoted point is exactly what the author put between the quotes**, which is how a
multi-word, relative, or month-only date is written: `@occurred:"June 10, 2026"`,
`@occurred:"2 days ago"`, `@"June 2026"`. The closing quote is the token boundary, so
whatever follows it is ordinary content, and a `\\"` inside the value does not end the
token. The scan mirrors `_split_predicate_items` in `mcp/tools/posix_tools.py`, down to
its rule that an unterminated quote is a typo to report rather than a boundary to guess
at -- scanning on to end of line would hand the author's prose to dateparser. Only the
double quote opens the form: an apostrophe is ordinary punctuation, and a scan looking
for its partner would turn `@note:'s` and its like into diagnostics.

Inside quotes the author delimited the value, so there is nothing to truncate and
dateparser's reading is taken as written. An *unquoted* token is refused in two shapes
that do parse, so a truncated read never becomes a plausible-looking assertion:

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

Three things are reported instead, because each one names its own fix:

* an **unknown kind** (`@asserted:2026-06-10`) -- the payload parses as time and the
  author is plainly reaching for this feature, so a short list of valid kinds helps;
* an **unterminated quote** -- the author opened the quoted form and mistyped;
* an unquoted point refused by the guards above **whose line continues with a digit**
  (`@occurred:June 10, 2026 ...`) -- the one shape where the token rule silently costs
  the author a date they clearly wrote, and the quoted form is what they wanted.

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
    TimeKind,
    parse_authored_point,
    parse_range_literal,
)

_KIND_NAMES = frozenset(kind.value for kind in TimeKind)

# A point with no kind is filed as valid time, the kind this feature is named for: the
# author said when the statement holds without narrowing *how* it holds.
DEFAULT_TIME_KIND = TimeKind.VALID

_KIND_PATTERN = r"[A-Za-z][A-Za-z0-9_]*"

# `@[kind]` glued to one balanced bracket group carrying a range literal's comma. The
# lookahead stops `@effective[a,b)x` from half-matching, and the `^` anchor keeps
# `paul@basicmemory.com` and mid-sentence `@handles` out entirely.
_RANGE_QUALIFIER = re.compile(rf"^@({_KIND_PATTERN})?([\[(][^\[\]()]*,[^\[\]()]*[\])])(?=\s|$)")

# `@[kind:]"` -- the opening of the quoted point. Only the quote is matched here; its
# partner is found by a scan, because a regex cannot honor `\"`.
_QUOTED_POINT_QUALIFIER = re.compile(rf'^@(?:({_KIND_PATTERN}):)?"')

# `@kind:<one token>`.
_KIND_POINT_QUALIFIER = re.compile(rf"^@({_KIND_PATTERN}):(\S+)")

# `@<one digit-led token>` -- the point with no kind. Without one there is nothing to
# distinguish a word from a handle, so only digits open the form at all.
_BARE_POINT_QUALIFIER = re.compile(r"^@(\d\S*)")

_QUOTE = '"'

# The width of a year, and the shortest numeric token worth reading as one.
_MIN_NUMERIC_POINT_WIDTH = 4

# Every diagnostic that a quote would have fixed shows the form rather than describing
# it, so the fix is one copyable edit away.
_QUOTED_EXAMPLE = "June 10, 2026"


@dataclass(frozen=True, slots=True)
class ObservationTemporalParse:
    """What a qualifier scan found at the head of one observation's content.

    Exactly three shapes exist: a peel (content shortened, one assertion, no error), a
    refusal (content untouched, no assertions, an error message naming the fix), and no
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
    kind_name: str | None
    valid_during: TemporalRange


@dataclass(frozen=True, slots=True)
class _Refusal:
    """A qualifier the author plainly meant, reported instead of silently kept."""

    reason: str


@dataclass(frozen=True, slots=True)
class _PointToken:
    """Where a point form ends, and the text handed to the date reader.

    `quoted` is what separates the two point forms once the boundary is found: the
    author delimited a quoted value, so the truncation guards below have nothing to
    guard against.
    """

    point: str
    end: int
    kind_name: str | None
    quoted: bool


# What a scan of the head of one line can find: a qualifier, a reportable mistake, or
# nothing at all.
type _QualifierScan = _ReadQualifier | _Refusal | None


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


def _scan_quoted_point(content: str, opened_at: int) -> tuple[str, int] | None:
    """Read a quoted payload from `opened_at` to its closing quote.

    One pass with a backslash escape, the same scan `_split_predicate_items` uses for
    find's predicate values: the delimiter rather than whitespace ends the token, and an
    escaped quote belongs to the value. Returns the value and the index just past the
    closing quote, or None when the quote never closed.
    """
    value: list[str] = []
    escaped = False
    for index in range(opened_at, len(content)):
        char = content[index]
        if escaped:
            value.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == _QUOTE:
            return "".join(value), index + 1
        else:
            value.append(char)
    return None


def _locate_point(content: str) -> _PointToken | _Refusal | None:
    """Find a point form at the head of the line and delimit the date it carries."""
    quoted = _QUOTED_POINT_QUALIFIER.match(content)
    if quoted is not None:
        scanned = _scan_quoted_point(content, quoted.end())
        if scanned is None:
            # Trigger: the author opened the quoted form and never closed it.
            # Why: every other reading of the line is a guess -- taking the rest of it
            #   would hand prose to dateparser, and dropping the quote would put the
            #   truncation this form exists to prevent right back.
            # Outcome: the line keeps its text and the author is told which keystroke
            #   is missing.
            return _Refusal(
                f"unterminated quote in temporal qualifier {quoted.group(0)!r}; "
                f'close it, as {quoted.group(0)}{_QUOTED_EXAMPLE}"'
            )
        point, end = scanned
        return _PointToken(point=point, end=end, kind_name=quoted.group(1), quoted=True)

    named = _KIND_POINT_QUALIFIER.match(content)
    bare = None if named is not None else _BARE_POINT_QUALIFIER.match(content)
    match = named or bare
    if match is None:
        return None
    return _PointToken(
        point=match.group(2) if named is not None else match.group(1),
        end=match.end(),
        kind_name=match.group(1) if named is not None else None,
        quoted=False,
    )


def _truncation_reason(point: str, valid_during: TemporalRange) -> str | None:
    """Why an unquoted point is too coarse to file, or None when it names a day.

    The two shapes named here both parse, which is exactly why they need refusing --
    see the module docstring for what each one costs if it is read. The wording is the
    diagnostic's, so the reason a token was refused and the reason it *is* refused stay
    the same sentence.

    A bounded span is how a coarse point announces itself: `parse_authored_point` closes
    a year or a month at its successor and leaves a day or a moment open, so
    `upper is None` *is* "this names a specific day". The one period with no successor
    to close at -- December 9999 -- is left open too, and so reads here as a day; no word
    resolves to it, so the guard never sees that shape.
    """
    if point[0].isdigit():
        return None if len(point) >= _MIN_NUMERIC_POINT_WIDTH else "is narrower than a year"
    return None if valid_during.upper is None else "names only a month or a year"


def _truncated_point_refusal(content: str, token: _PointToken, reason: str) -> _Refusal | None:
    """Report a refused token that reads as the first word of a longer date.

    Trigger: a known (or omitted) kind, and content after the refused token starting
      with a digit.
    Why: `@occurred:June 10, 2026` is the one shape where the one-token rule silently
      costs the author a date they clearly wrote, and the digit is the only signal that
      the date kept going. Prose after the token (`@occurred:June the cat sat`) is just
      prose, and an unknown kind (`@vol:2 3 pages`) is an ordinary `@word:` marker;
      diagnosing either would fire all over an ordinary vault.
    Outcome: one sentence naming the quoted form that files the whole date. Otherwise
      the token stays ordinary content, silently, exactly as it did before quoting.
    """
    if token.kind_name is not None and token.kind_name not in _KIND_NAMES:
        return None
    rest = content[token.end :].lstrip()
    if not rest or not rest[0].isdigit():
        return None
    prefix = content[: token.end - len(token.point)]
    return _Refusal(
        f"temporal point {content[: token.end]!r} {reason}; "
        f'quote the whole date to file it, as {prefix}"{_QUOTED_EXAMPLE}"'
    )


def _read_point_qualifier(content: str, date_order: DateOrder | None) -> _QualifierScan:
    """Match any point form and read its date, or report why nothing was filed."""
    located = _locate_point(content)
    if located is None or isinstance(located, _Refusal):
        return located

    # Deferred, following utils.ensure_timezone_aware: the markdown parser is a
    # low-level module that many entrypoints import, and pulling the config stack in at
    # import time couples parsing to configuration load order for no benefit. Resolved
    # here rather than at the top of the scan so only a token that already looks like a
    # qualifier pays for reading the config -- or for loading dateparser.
    from basic_memory.config import ConfigManager

    order = date_order if date_order is not None else ConfigManager().config.date_order
    valid_during = parse_authored_point(located.point, date_order=order)
    if valid_during is None:
        return None

    # Quotes are the author's own delimiters, so a quoted value cannot be the truncated
    # head of a longer date and the guards do not apply to it.
    reason = None if located.quoted else _truncation_reason(located.point, valid_during)
    if reason is not None:
        return _truncated_point_refusal(content, located, reason)
    return _ReadQualifier(content[: located.end], located.end, located.kind_name, valid_during)


def parse_temporal_qualifier(
    content: str, *, date_order: DateOrder | None = None
) -> ObservationTemporalParse:
    """Split a leading temporal qualifier off observation content.

    The MVP reads at most one qualifier per observation, but the result is a collection
    so supporting several later is not a schema break. `date_order` defaults to the
    configured `date_order`; tests and callers that already hold the config pass it.
    """
    read = _read_range_qualifier(content) or _read_point_qualifier(content, date_order)
    if isinstance(read, _Refusal):
        return _refuse(content, read.reason)
    if read is None:
        return _no_qualifier(content)

    kind_name = read.kind_name
    if kind_name is not None and kind_name not in _KIND_NAMES:
        known = ", ".join(sorted(_KIND_NAMES))
        return _refuse(content, f"unknown temporal kind {kind_name!r} in {read.token!r} ({known})")

    remainder = content[read.end :].strip()
    if not remainder:
        # A qualifier with nothing to qualify would leave an empty observation, which
        # the plugin drops outright. Keep the line whole instead.
        return _no_qualifier(content)

    assertion = TemporalAssertion(
        time_kind=TimeKind(kind_name) if kind_name is not None else DEFAULT_TIME_KIND,
        valid_during=read.valid_during,
        source_text=read.token,
    )
    return ObservationTemporalParse(content=remainder, assertions=(assertion,), error=None)
