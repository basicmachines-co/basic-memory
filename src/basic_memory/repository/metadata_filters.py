"""The frontmatter metadata path grammar, and the filters built on top of it.

Every surface that accepts a caller-written dot path into frontmatter — the
search API's ``metadata_filters``, find's ``--meta`` predicates, find's
``--fields`` projection — parses it here, through `parse_metadata_path`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Iterable, List, cast


# Dot-separated name segments of letters, digits, '_' or '-', so a doubled,
# leading or trailing dot is not a path. Private on purpose: `parse_metadata_path`
# is the only way to apply it, which is what keeps the check and the split that
# depends on it from drifting apart in a caller.
_METADATA_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_COMPARISON_OPERATORS = {
    "$gt": "gt",
    "$gte": "gte",
    "$lt": "lt",
    "$lte": "lte",
}


@dataclass(frozen=True)
class MetadataPath:
    """A dot path into a note's frontmatter, already checked against the grammar.

    `parts` lives here and nowhere else, and only `parse_metadata_path` builds
    one — so walking a caller-written path requires having validated it first.
    That is the entire point of the type. Splitting the string at the call site
    is what let `--fields` accept `review..approved` and quietly walk an empty
    segment to null for every hit, indistinguishable from a field that is
    genuinely absent; there is no longer a second `.split(".")` to forget.
    """

    key: str
    parts: tuple[str, ...]


def parse_metadata_path(raw_key: str) -> MetadataPath | None:
    """Parse one frontmatter dot path, or None when the text is not one.

    Returns None instead of raising so each surface refuses in its own words —
    the search API, find's predicates and find's field projection all word it
    differently. The optional return is also the enforcement: a caller that
    skips the check has a `MetadataPath | None` and cannot reach `.parts`
    without the type checker objecting.
    """
    key = raw_key.strip()
    if not _METADATA_KEY_RE.match(key):
        return None
    return MetadataPath(key, tuple(key.split(".")))


@dataclass(frozen=True)
class ParsedMetadataFilter:
    """Normalized metadata filter for SQL generation."""

    path_parts: List[str]
    op: str
    value: Any
    comparison: str | None = None  # "numeric" or "text" for comparisons


def _is_numeric_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(_NUMERIC_RE.match(value.strip()))
    return False


def _is_numeric_collection(values: Iterable[Any]) -> bool:
    return all(_is_numeric_value(v) for v in values)


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    return value


def _normalize_numeric(value: object, raw_key: str) -> float:
    """Normalize a value already proven numeric by _is_numeric_value.

    A comparison bound has to be a finite float — it is what the SQL predicate
    compares against, and neither an infinity nor a magnitude beyond float names
    a value any indexed note can hold.

    Trigger: a number too large for a float reaches a comparison or a range
             bound. `json.loads` keeps a 400-digit literal as an ordinary finite
             `int`, so it passes _is_numeric_value and every check before this.
    Why: the two spellings failed differently and both failed badly. `float()`
         raises OverflowError on the int, and OverflowError is not a ValueError,
         so the search router's translation missed it and the request became a
         500 for what is a filter typo. The same magnitude written as a string
         does not raise at all — `float()` answers it with inf — which silently
         made the bound infinite, matching every note or none.
    Outcome: one refusal covering both, worded like this module's other filter
             errors, so every surface that builds filters (find's predicates,
             search_notes' metadata_filters) reports it as the bad value it is
             and the router answers 400.
    """
    try:
        normalized = float(cast(str | int | float, value))
    except OverflowError:
        normalized = math.inf
    if not math.isfinite(normalized):
        raise ValueError(
            f"numeric metadata filter value for '{raw_key}' is not a finite number: {value}"
        )
    return normalized


def _refuse_null(values: Iterable[Any], raw_key: str, op: str) -> None:
    """Refuse None anywhere but equality.

    Trigger: a null bound, list element, or comparison value.
    Why: every operator but equality compiles to a SQL predicate that compares
         against the value, and a comparison with NULL is never true — so the
         filter would answer zero rows for every note in the project, reporting
         a silent wrong answer instead of naming the query it cannot express.
         Equality is the one place null has a meaning both backends express
         (IS NULL: the key is absent or explicitly null).
    Outcome: refuse, naming the equality form that does work.
    """
    if any(value is None for value in values):
        raise ValueError(
            f"null is not supported by '{op}' in metadata filter for '{raw_key}'; "
            f"use {{'{raw_key}': None}} to match a missing or null value"
        )


def parse_metadata_filters(filters: dict[str, Any]) -> List[ParsedMetadataFilter]:
    """Parse metadata filters into normalized clauses.

    Supported forms:
    - {"status": "in-progress"}
    - {"owner": None}  # is null: the key is absent or explicitly null
    - {"tags": ["security", "oauth"]}  # array contains all
    - {"priority": {"$in": ["high", "critical"]}}
    - {"schema.confidence": {"$gt": 0.7}}
    - {"schema.confidence": {"$between": [0.3, 0.6]}}
    """
    parsed: List[ParsedMetadataFilter] = []

    for raw_key, raw_value in (filters or {}).items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("metadata filter keys must be non-empty strings")
        path = parse_metadata_path(raw_key)
        if path is None:
            raise ValueError(f"Unsupported metadata filter key: {raw_key}")

        path_parts = list(path.parts)

        # Operator form
        if isinstance(raw_value, dict):
            if len(raw_value) != 1:
                raise ValueError(f"Invalid metadata filter for '{raw_key}': {raw_value}")
            raw_op, value = next(iter(raw_value.items()))
            if not isinstance(raw_op, str):
                raise ValueError(
                    f"Unsupported operator '{raw_op}' in metadata filter for '{raw_key}'"
                )
            op = raw_op

            if op == "$in":
                if not isinstance(value, list) or not value:
                    raise ValueError(f"$in requires a non-empty list for '{raw_key}'")
                _refuse_null(value, raw_key, op)
                parsed.append(
                    ParsedMetadataFilter(path_parts, "in", [_normalize_scalar(v) for v in value])
                )
                continue

            if op in _COMPARISON_OPERATORS:
                _refuse_null([value], raw_key, op)
                if _is_numeric_value(value):
                    normalized = _normalize_numeric(value, raw_key)
                    comparison = "numeric"
                else:
                    normalized = _normalize_scalar(value)
                    comparison = "text"
                parsed.append(
                    ParsedMetadataFilter(
                        path_parts,
                        _COMPARISON_OPERATORS[op],
                        normalized,
                        comparison,
                    )
                )
                continue

            if op == "$between":
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError(f"$between requires [min, max] for '{raw_key}'")
                _refuse_null(value, raw_key, op)
                if _is_numeric_collection(value):
                    normalized = [_normalize_numeric(v, raw_key) for v in value]
                    comparison = "numeric"
                else:
                    normalized = [_normalize_scalar(v) for v in value]
                    comparison = "text"
                parsed.append(ParsedMetadataFilter(path_parts, "between", normalized, comparison))
                continue

            raise ValueError(f"Unsupported operator '{op}' in metadata filter for '{raw_key}'")

        # Array contains (all)
        if isinstance(raw_value, list):
            if not raw_value:
                raise ValueError(f"Empty list not allowed for metadata filter '{raw_key}'")
            _refuse_null(raw_value, raw_key, "array contains")
            parsed.append(
                ParsedMetadataFilter(
                    path_parts, "contains", [_normalize_scalar(v) for v in raw_value]
                )
            )
            continue

        # Null equality: the only operator NULL has a meaning for. Both backends
        # extract a missing key and an explicit JSON null as SQL NULL, so one
        # IS NULL clause answers "which notes have no owner?" identically on
        # SQLite and Postgres. Emitted as its own op because `= NULL` is never
        # true in SQL — an ordinary equality clause would report a confident zero.
        if raw_value is None:
            parsed.append(ParsedMetadataFilter(path_parts, "is_null", None))
            continue

        # Simple equality
        parsed.append(ParsedMetadataFilter(path_parts, "eq", _normalize_scalar(raw_value)))

    return parsed


def build_sqlite_json_path(parts: List[str]) -> str:
    """Build a SQLite JSON path for json_extract/json_each."""
    path = "$"
    for part in parts:
        path += f'."{part}"'
    return path


def build_postgres_json_path(parts: List[str]) -> str:
    """Build a Postgres JSON path for #>>/#> operators."""
    return "{" + ",".join(parts) + "}"
