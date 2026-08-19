"""Relaxed-fallback rendering must survive the tokens the eligibility helper emits."""

import sqlite3

import pytest

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

CREATE_FTS = (
    "CREATE VIRTUAL TABLE t USING fts5("
    "body, tokenize='unicode61 tokenchars 0x2F', prefix='1,2,3,4')"
)
DOCUMENT = (
    "don't touch this п’ять проектів об'єкт доступу как отозвать выданный доступ पहुंच कैसे रद्द करें"
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("don't touch this", '"don\'t"* OR touch*'),
        ("скасувати об'єкт виданий доступ", 'скасувати* OR "об\'єкт"* OR виданий* OR доступ*'),
        ("п’ять виданих різних об’єктів", "п’ять* OR виданих* OR різних* OR об’єктів*"),
        ("how to revoke granted access", "revoke* OR granted* OR access*"),
    ],
)
def test_sqlite_relaxed_text_quotes_only_terms_that_need_it(query: str, expected: str) -> None:
    """Apostrophe terms are quoted; every other term renders exactly as before."""
    assert SQLiteSearchRepository._relaxed_fts_text(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "don't touch this",
        "скасувати об'єкт виданий доступ",
        "п’ять виданих різних об’єктів",
        "как отозвать выданный доступ",
        "पहुंच कैसे रद्द करें",
    ],
)
def test_sqlite_relaxed_text_is_accepted_by_fts5(query: str) -> None:
    """The rendered expression must parse.

    An unquoted apostrophe raises `fts5: syntax error`, which the repository
    catches and turns into an empty result — the relaxed retry then silently
    contributes nothing, which is the failure this fallback exists to prevent.
    """
    relaxed = SQLiteSearchRepository._relaxed_fts_text(query)
    assert relaxed is not None

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(CREATE_FTS)
        connection.execute("INSERT INTO t VALUES (?)", (DOCUMENT,))
        rows = connection.execute("SELECT rowid FROM t WHERE t MATCH ?", (relaxed,)).fetchall()
    finally:
        connection.close()
    assert rows, f"relaxed expression matched nothing: {relaxed}"


def test_sqlite_relaxed_text_bare_apostrophe_would_be_rejected() -> None:
    """Pin why the quoting exists, so removing it fails loudly rather than silently."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(CREATE_FTS)
        connection.execute("INSERT INTO t VALUES (?)", (DOCUMENT,))
        with pytest.raises(sqlite3.OperationalError, match="fts5: syntax error"):
            connection.execute("SELECT rowid FROM t WHERE t MATCH ?", ("don't* OR touch*",))
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("don't touch this", "'don''t':* | touch:*"),
        ("скасувати об'єкт виданий доступ", "скасувати:* | 'об''єкт':* | виданий:* | доступ:*"),
        ("how to revoke granted access", "revoke:* | granted:* | access:*"),
    ],
)
def test_postgres_relaxed_tsquery_quotes_apostrophe_lexemes(query: str, expected: str) -> None:
    """Postgres carries the same token shapes, so it needs the same escaping."""
    assert PostgresSearchRepository._relaxed_tsquery_text(query) == expected


@pytest.mark.parametrize(
    ("document", "query"),
    [
        ("перевод права доступа", "пере­vод права доступа"),  # soft hyphen
        ("слово права доступа", "сло⁠во права доступа"),  # word joiner
        ("نمی‌خواهم دسترسی را لغو", "نمی‌خواهم دسترسی را لغو"),  # ZWNJ kept
    ],
)
def test_relaxed_terms_match_the_stored_note(document: str, query: str) -> None:
    """A term must match the note as stored, not as the query happened to be pasted.

    Rendering artifacts — a soft hyphen from a paginated document, a stray word
    joiner — are absent from the note, so carrying them into the term stops it
    matching. The Persian joiner is the opposite case: it is written in the text
    and indexed with it, so removing it would break the match instead.
    """
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(CREATE_FTS)
        connection.execute("INSERT INTO t VALUES (?)", (document,))
        relaxed = SQLiteSearchRepository._relaxed_fts_text(query)
        assert relaxed is not None
        rows = connection.execute("SELECT rowid FROM t WHERE t MATCH ?", (relaxed,)).fetchall()
    finally:
        connection.close()
    assert rows, f"relaxed expression did not match the stored note: {relaxed!r}"


@pytest.mark.parametrize("document", ["foo­bar", "foobar"])
def test_relaxed_terms_match_either_stored_form(document: str) -> None:
    """A format character is invisible, so the note may hold it or not.

    The two forms index differently — "foo­bar" as two tokens, "foobar" as
    one — and neither term matches the other note. Both forms are emitted, and
    the OR relaxation already builds covers whichever the note actually has.
    """
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(CREATE_FTS)
        connection.execute("INSERT INTO t VALUES (?)", (document,))
        relaxed = SQLiteSearchRepository._relaxed_fts_text("foo­bar права доступа")
        assert relaxed is not None
        rows = connection.execute("SELECT rowid FROM t WHERE t MATCH ?", (relaxed,)).fetchall()
    finally:
        connection.close()
    assert rows, f"relaxed expression missed the note {document!r}: {relaxed!r}"


def test_orthographic_joiners_are_not_duplicated_into_a_second_variant() -> None:
    """Joiners are written in the text, so the stored form has them.

    A stripped variant would only widen the OR with a term no note can hold.
    """
    relaxed = SQLiteSearchRepository._relaxed_fts_text("نمی‌خواهم دسترسی را لغو")
    assert relaxed == "نمی‌خواهم* OR دسترسی* OR را* OR لغو*"
