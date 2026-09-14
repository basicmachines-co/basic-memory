"""Actionable query guidance without changing retrieval or claiming index completeness."""

import unicodedata

from basic_memory.repository.script_ngrams import is_script_search_character
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode


def unspaced_script_query_hint(query: SearchQuery) -> str | None:
    """Explain a plain compound's lexical semantics after an empty first page."""
    if query.retrieval_mode not in (SearchRetrievalMode.FTS, SearchRetrievalMode.HYBRID):
        return None
    if not query.text or query.title or query.permalink or query.permalink_match:
        return None
    text = unicodedata.normalize("NFKC", query.text.strip())
    # Use retrieval's script ranges, including supplementary Han and extended kana.
    # Quotes, operators, identifiers, and shorter words keep their explicit intent.
    if (
        len(text) < 4
        or not text.isalpha()
        or not all(is_script_search_character(char) for char in text)
    ):
        return None
    return (
        "Unspaced text in this script is searched as a continuous sequence, not automatically split "
        "into words. Try a shorter word from your query on its own, or add spaces between "
        "the words you intend to search. An empty result does not rule out notes containing "
        "only part of the compound."
    )
