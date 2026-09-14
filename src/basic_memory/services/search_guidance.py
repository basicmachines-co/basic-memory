"""Actionable query guidance without changing retrieval or claiming index completeness."""

import unicodedata

from basic_memory.repository.search_query import RELAXATION_CJK_PATTERN
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode


def unspaced_cjk_query_hint(query: SearchQuery) -> str | None:
    """Explain a plain compound's lexical semantics after an empty first page."""
    if query.retrieval_mode not in (SearchRetrievalMode.FTS, SearchRetrievalMode.HYBRID):
        return None
    if not query.text or query.title or query.permalink or query.permalink_match:
        return None
    text = unicodedata.normalize("NFKC", query.text.strip())
    # Only plain, unspaced CJK compounds qualify. Quotes, operators, identifiers,
    # and shorter words keep their existing guidance and explicit search intent.
    if len(text) < 4 or not all(RELAXATION_CJK_PATTERN.fullmatch(char) for char in text):
        return None
    return (
        "Unspaced CJK text is searched as a continuous sequence, not automatically split "
        "into words. Try a shorter word from your query on its own, or add spaces between "
        "the words you intend to search. An empty result does not rule out notes containing "
        "only part of the compound."
    )
