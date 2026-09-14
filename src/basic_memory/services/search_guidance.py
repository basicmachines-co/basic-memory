"""Actionable query guidance without changing retrieval or claiming index completeness."""

import unicodedata

from basic_memory.repository.script_ngrams import script_runs
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode


def unspaced_script_query_hint(query: SearchQuery) -> str | None:
    """Explain a plain compound's lexical semantics after an empty first page."""
    if query.retrieval_mode not in (SearchRetrievalMode.FTS, SearchRetrievalMode.HYBRID):
        return None
    if not query.text or query.title or query.permalink or query.permalink_match:
        return None
    text = (
        unicodedata.normalize("NFKC", query.text.strip())
        .replace("\u200c", "")
        .replace("\u200d", "")
    )
    runs = script_runs(text)
    # One run must consume the entire query, so quotes, operators and mixed words
    # cannot masquerade as a compound. Count retrieval's units: attached marks and
    # join controls must neither veto a word nor pad it past the four-unit guard.
    if (
        len(runs) != 1
        or len(runs[0]) < 4
        or "".join(runs[0]) != text
        or any(unicodedata.category(char).startswith("N") for char in text)
    ):
        return None
    return (
        "Unspaced text in this script is searched as a continuous sequence, not automatically split "
        "into words. Try a shorter word from your query on its own, or add spaces between "
        "the words you intend to search. An empty result does not rule out notes containing "
        "only part of the compound."
    )
