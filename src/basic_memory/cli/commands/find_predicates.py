"""`bm find --meta` predicates: the shell spelling of bm_find's `filters`.

A predicate string such as 'status=active' or 'confidence>0.6' becomes one entry
of the metadata_filters dict bm_find takes, in a subset of the grammar the search
API's parse_metadata_filters supports. Mistakes are refused here, in find's own
words, before any request is built.
"""

import json
import math
import re
from typing import Any

from basic_memory.repository.metadata_filters import parse_metadata_path

# find's `--meta` strings translate onto the search API's metadata_filters dict —
# a subset of the grammar parse_metadata_filters supports (eq, $gt/$gte/$lt/$lte,
# $in, array-contains-all, $between), nothing more. Word ops need whitespace around
# them and symbol ops exclude the key character class, so exactly one regex can
# match any given predicate. Two-char symbols sit first in the alternation so
# ">=" never parses as ">" plus a value starting with "=".
# The key capture admits '.' anywhere on purpose — it is looser than a dot path.
# parse_metadata_path, which owns the frontmatter path grammar, is what decides
# a well-formed one, applied in parse_meta_predicates once the predicate has
# split. Tightening the capture instead would make '.owner=null' match no regex
# at all and be reported as a missing operator rather than as the bad key it is.
_PREDICATE_WORD_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s+(in|has|between)\s+(.+)$")
_PREDICATE_SYMBOL_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*(>=|<=|=|>|<)\s*(.*)$")
_SYMBOL_OPERATORS = {">": "$gt", ">=": "$gte", "<": "$lt", "<=": "$lte"}
_SUPPORTED_PREDICATE_OPS = "= > >= < <= in has between"
# The symbol regex consumes the first operator it recognizes, so an operator
# spelling outside the supported set ("==", "=>", ">>", ">=>") leaves its
# second character at the head of the value. These are the characters that can
# be left behind that way. A set, not a string: "" is a substring of any string
# but is not a member here, so an empty token never reads as operator-prefixed.
_OPERATOR_VALUE_PREFIXES = frozenset("=<>")
# Mirrors bm_find's alias: "note_type" (the entity model column) means the
# frontmatter "type" key, so a repeated key is caught under either spelling.
_METADATA_KEY_ALIASES = {"note_type": "type"}


def _opens_an_unterminated_quote(text: str) -> bool:
    """True when a double quote opens in `text` and nothing closes it.

    One scanner decides this for every value token, scalar or list element, so
    the two paths cannot disagree about what "quoted" means.
    """
    in_quotes = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif in_quotes and char == "\\":
            escaped = True
        elif char == '"':
            in_quotes = not in_quotes
    return in_quotes


def _predicate_scalar(token: str, predicate: str) -> Any:
    """Read one predicate value token, refusing everything a search cannot answer.

    "true"/"false"/"null"/numbers become bool/None/int/float so the produced
    filters dict is byte-equal to what a bm_find caller passes as JSON; a JSON-quoted token ('"true"') forces a literal string; anything that
    is not a JSON scalar stays the raw string.
    """
    text = token.strip()
    # Trigger: an unquoted value opens with one of the operator characters.
    # Why: only the operators in _SUPPORTED_PREDICATE_OPS are real, but the
    #      regexes match the longest supported one and hand the rest to the
    #      value — 'status==active' would filter for the string "=active" and
    #      'count>>3' for ">3", so a typo'd operator answered as an empty (or
    #      worse, a non-empty but wrong) result set instead of the refusal the
    #      grammar documents.
    # Outcome: refuse, naming the supported set and the quoting escape hatch a
    #          value that genuinely starts with '=', '<' or '>' needs.
    if text[:1] in _OPERATOR_VALUE_PREFIXES:
        raise ValueError(
            f"find: unsupported predicate operator in '{predicate}'; "
            f"supported: {_SUPPORTED_PREDICATE_OPS}; quote the value as "
            f'"{text}" to match text that starts with that character'
        )
    # Trigger: a quote opens in the token and never closes.
    # Why: json.loads rejects it, and the raw-text fallback would then keep the
    #      dangling quote as part of a literal value — 'status="active' would
    #      search for the seven-character text '"active', report no matches,
    #      and hide the typo behind an ordinary empty result.
    # Outcome: refuse for every value token, so the scalar operators and the
    #          list operators (where a severed quote would also mis-split the
    #          list) answer a dangling quote the same way.
    if _opens_an_unterminated_quote(text):
        raise ValueError(
            f"find: predicate '{predicate}' has an unterminated quoted value; "
            "close the quote — 'status=\"active\"' forces a literal string, and "
            "'label in \"a,b\",c' protects a comma inside a list element"
        )
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    # Trigger: the token parsed to a non-finite float. Python's JSON reader
    #          accepts NaN/Infinity/-Infinity as an extension, and overflows a
    #          large exponent ("1e999") to infinity.
    # Why: none of those are JSON the request encoder will emit, so the filter
    #      died at transport with "Out of range float values are not JSON
    #      compliant" — a network-shaped error for what is a predicate typo.
    # Outcome: refuse here, in the same shape as the grammar's other refusals.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            f"find: predicate '{predicate}' has a non-finite number '{text}'; "
            f'predicate values must be finite numbers; quote the value as "{text}" '
            "to match that literal text"
        )
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return text


def _split_predicate_items(raw_value: str, predicate: str) -> list[str]:
    """Split a list-op value on its top-level commas, refusing empty elements.

    A comma inside a JSON-quoted token belongs to the value, not to the list, so
    the quoting escape hatch the scalar operators document works for `in`, `has`
    and `between` too: 'label in "a,b",c' yields ['"a,b"', 'c'], which
    _predicate_scalar then reads as the literal strings "a,b" and "c". Splitting
    the raw string first would sever the quoted token into '"a' and 'b"' and
    filter for values nothing carries — wrong, and silent.

    A split only happens outside quotes, so an unterminated quote here always
    ends up inside one element and _predicate_scalar refuses it; this function
    does not repeat that check.
    """
    items: list[str] = []
    current: list[str] = []
    in_quotes = False
    escaped = False
    for char in raw_value:
        current.append(char)
        if escaped:
            escaped = False
        elif in_quotes and char == "\\":
            escaped = True
        elif char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            current.pop()
            items.append("".join(current))
            current = []
    items.append("".join(current))
    stripped = [item.strip() for item in items]
    if any(not item for item in stripped):
        raise ValueError(f"find: predicate '{predicate}' has an empty list element")
    return stripped


def _refuse_null_outside_equality(values: list[Any], op: str, predicate: str) -> None:
    """Refuse a null bound, list element, or comparison value.

    Trigger: `null` reached an operator other than '='.
    Why: '=' compiles to IS NULL server-side, which is the question null asks —
         does this note carry a value here at all. Every other operator compares
         against the value, and a SQL comparison with NULL is never true, so
         'score>null' or 'priority in null,high' would answer zero rows for
         every note in the project rather than name the query it cannot run.
    Outcome: refuse, pointing at the equality spelling that does work.
    """
    if any(value is None for value in values):
        raise ValueError(
            f"find: predicate '{predicate}' uses null with '{op}'; null matches only "
            "as equality ('owner=null' finds notes carrying no owner); quote the "
            'value as "null" to match that literal text'
        )


def parse_meta_predicates(predicates: list[str]) -> dict[str, Any]:
    """Translate POSIX-style predicate strings into the search API metadata_filters dict.

    One predicate per string; predicates AND together. Exactly one predicate
    per key — the API admits one operator per key, so a repeated key fails fast
    instead of last-wins. Raises ValueError on any operator outside the supported set, and on any key outside
    the search API's dot-path grammar.
    """
    filters: dict[str, Any] = {}
    for predicate in predicates:
        match = _PREDICATE_WORD_RE.match(predicate.strip()) or _PREDICATE_SYMBOL_RE.match(
            predicate.strip()
        )
        if match is None:
            raise ValueError(
                f"find: unsupported predicate operator in '{predicate}'; "
                f"supported: {_SUPPORTED_PREDICATE_OPS}"
            )
        raw_key, op, raw_value = match.groups()
        key = _METADATA_KEY_ALIASES.get(raw_key, raw_key)
        # Trigger: the key capture accepted something that is not a dot path —
        #          a doubled, leading or trailing dot ('review..approved',
        #          '.owner', 'owner.').
        # Why: the search API refuses these keys, so the query was never going
        #      to run. Letting it travel spends a request to come back with
        #      "Unsupported metadata filter key", which names neither find nor
        #      the shape a key must have — every other predicate mistake is
        #      refused here, before transport, in find's own words.
        # Outcome: refuse locally, naming the offending key and the grammar.
        if parse_metadata_path(key) is None:
            raise ValueError(
                f"find: malformed predicate key '{key}' in '{predicate}'; keys are "
                "dot-separated names of letters, digits, '_' or '-' "
                "(e.g. 'status' or 'review.approved')"
            )
        if key in filters:
            raise ValueError(
                f"find: duplicate predicate key '{key}' in '{predicate}'; "
                "use 'between' for ranges (e.g. 'score between 0.3,0.8')"
            )
        if op in ("in", "has", "between"):
            items = [
                _predicate_scalar(item, predicate)
                for item in _split_predicate_items(raw_value, predicate)
            ]
            _refuse_null_outside_equality(items, op, predicate)
            if op == "between" and len(items) != 2:
                raise ValueError(f"find: 'between' needs exactly min,max in '{predicate}'")
            filters[key] = (
                {"$in": items} if op == "in" else items if op == "has" else {"$between": items}
            )
        else:
            if not raw_value.strip():
                raise ValueError(f"find: predicate '{predicate}' has no value")
            value = _predicate_scalar(raw_value, predicate)
            if op != "=":
                _refuse_null_outside_equality([value], op, predicate)
            filters[key] = value if op == "=" else {_SYMBOL_OPERATORS[op]: value}
    return filters
