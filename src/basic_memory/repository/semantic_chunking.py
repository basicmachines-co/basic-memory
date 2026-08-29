"""Pure semantic chunk planning for vector indexing."""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, TypedDict

from basic_memory.schemas.search import SearchItemType

MAX_VECTOR_CHUNK_CHARS = 900
VECTOR_CHUNK_OVERLAP_CHARS = 120

_HEADER_LINE_PATTERN = re.compile(r"^\s*#{1,6}\s+")


class SemanticSourceRow(Protocol):
    """Search row fields needed to build semantic chunks."""

    @property
    def id(self) -> int: ...

    @property
    def type(self) -> str: ...

    @property
    def title(self) -> str | None: ...

    @property
    def permalink(self) -> str | None: ...

    @property
    def content_snippet(self) -> str | None: ...

    @property
    def category(self) -> str | None: ...

    @property
    def relation_type(self) -> str | None: ...


class VectorChunkRecord(TypedDict):
    """One deterministic chunk input for vector synchronization."""

    chunk_key: str
    chunk_text: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class VectorChunkBuildResult:
    """Chunk records plus duplicate-source diagnostics for the caller."""

    records: list[VectorChunkRecord]
    duplicate_chunk_keys: int


def compose_row_source_text(row: SemanticSourceRow) -> str:
    """Build the human-readable text embedded for one search row."""
    if row.type == SearchItemType.ENTITY.value:
        row_parts = [
            row.title or "",
            row.permalink or "",
            row.content_snippet or "",
        ]
        return "\n\n".join(part for part in row_parts if part)

    if row.type == SearchItemType.OBSERVATION.value:
        row_parts = [
            row.title or "",
            row.permalink or "",
            row.category or "",
            row.content_snippet or "",
        ]
        return "\n\n".join(part for part in row_parts if part)

    row_parts = [
        row.title or "",
        row.permalink or "",
        row.relation_type or "",
        row.content_snippet or "",
    ]
    return "\n\n".join(part for part in row_parts if part)


def build_vector_chunk_records(rows: Iterable[SemanticSourceRow]) -> VectorChunkBuildResult:
    """Build one deterministic chunk record per logical search-row chunk."""
    records_by_key: dict[str, VectorChunkRecord] = {}
    duplicate_chunk_keys = 0

    for row in rows:
        source_text = compose_row_source_text(row)
        chunks = split_text_into_chunks(source_text)
        for chunk_index, chunk_text in enumerate(chunks):
            chunk_key = f"{row.type}:{row.id}:{chunk_index}"
            source_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            # SQLite FTS5 can return duplicate logical rows because it does not
            # enforce relational uniqueness. Collapse them before the vector
            # writer encounters duplicate chunk keys.
            if chunk_key in records_by_key:
                duplicate_chunk_keys += 1
            records_by_key[chunk_key] = {
                "chunk_key": chunk_key,
                "chunk_text": chunk_text,
                "source_hash": source_hash,
            }

    return VectorChunkBuildResult(
        records=list(records_by_key.values()),
        duplicate_chunk_keys=duplicate_chunk_keys,
    )


def build_entity_fingerprint(chunk_records: Iterable[VectorChunkRecord]) -> str:
    """Hash the semantic chunk inputs for one entity.

    Vector eligibility follows the derived search rows rather than raw file
    bytes. Title, permalink, or observation changes therefore invalidate the
    entity fingerprint even when unrelated file bytes do not.
    """
    canonical_records = [
        {
            "chunk_key": record["chunk_key"],
            "source_hash": record["source_hash"],
        }
        for record in sorted(chunk_records, key=lambda record: record["chunk_key"])
    ]
    payload = json.dumps(canonical_records, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_text_into_chunks(text_value: str) -> list[str]:
    """Split semantic source text at Markdown-aware boundaries."""
    normalized = (text_value or "").strip()
    if not normalized:
        return []

    # Headings are the only structural boundary we split on. Bullets are NOT
    # given their own chunk: a list item is not a standalone unit of meaning
    # here. The entity-body vector carries the note's context, so its bullets
    # stay packed with the surrounding text up to the size budget. Per-fact
    # retrieval vectors come from the entity's observation and relation search
    # rows, which are embedded separately and carry their own identity.
    lines = normalized.splitlines()
    sections: list[str] = []
    current_section: list[str] = []
    for line in lines:
        if _HEADER_LINE_PATTERN.match(line) and current_section:
            sections.append("\n".join(current_section).strip())
            current_section = [line]
        else:
            current_section.append(line)
    if current_section:
        sections.append("\n".join(current_section).strip())

    chunked_sections: list[str] = []
    current_chunk = ""

    for section in sections:
        if len(section) > MAX_VECTOR_CHUNK_CHARS:
            if current_chunk:
                chunked_sections.append(current_chunk)
                current_chunk = ""
            long_chunks = _split_long_section(section)
            if long_chunks:
                chunked_sections.extend(long_chunks[:-1])
                current_chunk = long_chunks[-1]
            continue

        candidate = section if not current_chunk else f"{current_chunk}\n\n{section}"
        if len(candidate) <= MAX_VECTOR_CHUNK_CHARS:
            current_chunk = candidate
            continue

        chunked_sections.append(current_chunk)
        current_chunk = section

    if current_chunk:
        chunked_sections.append(current_chunk)

    return [chunk for chunk in chunked_sections if chunk.strip()]


def _split_long_section(section_text: str) -> list[str]:
    """Break a section that exceeds the size budget at line boundaries.

    Lines — heading, prose, and bullet lines alike — are packed into chunks up
    to the budget, so the heading rides the first packed chunk and a word or a
    ``[[wikilink]]`` is never cut across a chunk edge. A bullet is just a line
    here; it gets no chunk of its own. Only a single line that is itself over
    the budget (an unbroken >900-char line, never a normal list) falls back to
    the character window, which carries the overlap.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        # Drop blank lines at the chunk's edges, but keep the indentation of
        # every remaining line: when a chunk starts mid-block, a naive strip()
        # would de-indent a nested list item or code line and change its
        # Markdown meaning in the embedded text.
        start, end = 0, len(current)
        while start < end and not current[start].strip():
            start += 1
        while end > start and not current[end - 1].strip():
            end -= 1
        packed = "\n".join(current[start:end]).rstrip()
        if packed:
            chunks.append(packed)
        current = []
        current_len = 0

    for line in section_text.splitlines():
        # A single line over the budget cannot be packed at all, so window it on
        # its own after flushing whatever came before it.
        if len(line) > MAX_VECTOR_CHUNK_CHARS:
            flush()
            chunks.extend(_split_by_char_window(line))
            continue

        # +1 accounts for the newline that will rejoin this line to the chunk.
        separator = 1 if current else 0
        if current and current_len + separator + len(line) > MAX_VECTOR_CHUNK_CHARS:
            flush()
            separator = 0
        current.append(line)
        current_len += separator + len(line)

    flush()
    return chunks


def _split_by_char_window(paragraph: str) -> list[str]:
    text_value = paragraph.strip()
    if not text_value:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text_value):
        end = min(len(text_value), start + MAX_VECTOR_CHUNK_CHARS)
        chunk = text_value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text_value):
            break
        start = max(0, end - VECTOR_CHUNK_OVERLAP_CHARS)
    return chunks
