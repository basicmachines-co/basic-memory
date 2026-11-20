"""Bounded PostgreSQL full-text search chunks."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

POSTGRES_FTS_CHUNK_SIZE = 8_000
# PostgreSQL ignores lexemes at 2 KiB and above. A 2,048-character overlap is
# therefore conservative for every indexable lexeme, including multi-byte text:
# any token split at one 8,000-character edge is complete in the next chunk.
POSTGRES_FTS_CHUNK_OVERLAP = 2_048

# Maximum chunk rows or serialized JSONB payload bytes per INSERT statement so
# multi-megabyte notes stay well below Postgres statement_timeout.
POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS = 200
POSTGRES_FTS_CHUNK_INSERT_MAX_BYTES = 2_000_000


def split_postgres_fts_chunks(content: str | None) -> list[tuple[int, str]]:
    """Split full note text without losing an indexable lexeme at a chunk edge."""
    if not content:
        return []

    step = POSTGRES_FTS_CHUNK_SIZE - POSTGRES_FTS_CHUNK_OVERLAP
    return [
        (chunk_index, content[start : start + POSTGRES_FTS_CHUNK_SIZE])
        for chunk_index, start in enumerate(range(0, len(content), step))
    ]


def slice_postgres_fts_chunk_json_batches(
    chunks: Sequence[Mapping[str, Any]],
    *,
    max_rows: int = POSTGRES_FTS_CHUNK_INSERT_MAX_ROWS,
    max_bytes: int = POSTGRES_FTS_CHUNK_INSERT_MAX_BYTES,
) -> list[str]:
    """Serialize FTS chunk rows into bounded JSON array payloads for batched INSERTs."""
    if not chunks:
        return []
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")
    if max_bytes < 2:
        raise ValueError("max_bytes must be at least 2")

    batches: list[str] = []
    current_encoded: list[str] = []
    current_bytes = 2  # enclosing '[' and ']'

    for chunk in chunks:
        encoded = json.dumps(chunk)
        encoded_bytes = len(encoded.encode("utf-8"))
        sep_bytes = 2 if current_encoded else 0
        if current_encoded and (
            len(current_encoded) >= max_rows
            or current_bytes + sep_bytes + encoded_bytes > max_bytes
        ):
            batches.append("[" + ", ".join(current_encoded) + "]")
            current_encoded = [encoded]
            current_bytes = 2 + encoded_bytes
        else:
            current_encoded.append(encoded)
            current_bytes += sep_bytes + encoded_bytes

    if current_encoded:
        batches.append("[" + ", ".join(current_encoded) + "]")

    return batches
