"""Bounded PostgreSQL full-text search chunks."""

POSTGRES_FTS_CHUNK_SIZE = 8_000
# PostgreSQL ignores lexemes at 2 KiB and above. A 2,048-character overlap is
# therefore conservative for every indexable lexeme, including multi-byte text:
# any token split at one 8,000-character edge is complete in the next chunk.
POSTGRES_FTS_CHUNK_OVERLAP = 2_048


def split_postgres_fts_chunks(content: str | None) -> list[tuple[int, str]]:
    """Split full note text without losing an indexable lexeme at a chunk edge."""
    if not content:
        return []

    step = POSTGRES_FTS_CHUNK_SIZE - POSTGRES_FTS_CHUNK_OVERLAP
    return [
        (chunk_index, content[start : start + POSTGRES_FTS_CHUNK_SIZE])
        for chunk_index, start in enumerate(range(0, len(content), step))
    ]
