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
_BULLET_PATTERN = re.compile(r"^[\-\*]\s+")


class SemanticSourceRow(Protocol):
    """Search row fields needed to build semantic chunks."""

    id: int
    type: str
    title: str | None
    permalink: str | None
    content_snippet: str | None
    category: str | None
    relation_type: str | None


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

    # Headers and bullets represent natural semantic boundaries. In particular,
    # keeping bullets separate gives individual facts their own retrieval vector.
    lines = normalized.splitlines()
    sections: list[str] = []
    current_section: list[str] = []
    for line in lines:
        if _HEADER_LINE_PATTERN.match(line) and current_section:
            sections.append("\n".join(current_section).strip())
            current_section = [line]
        elif _BULLET_PATTERN.match(line) and current_section:
            sections.append("\n".join(current_section).strip())
            current_section = [line]
        else:
            current_section.append(line)
    if current_section:
        sections.append("\n".join(current_section).strip())

    chunked_sections: list[str] = []
    current_chunk = ""

    for section in sections:
        is_bullet = bool(_BULLET_PATTERN.match(section))

        if len(section) > MAX_VECTOR_CHUNK_CHARS:
            if current_chunk:
                chunked_sections.append(current_chunk)
                current_chunk = ""
            long_chunks = _split_long_section(section)
            if long_chunks:
                chunked_sections.extend(long_chunks[:-1])
                current_chunk = long_chunks[-1]
            continue

        if is_bullet:
            if current_chunk:
                chunked_sections.append(current_chunk)
                current_chunk = ""
            chunked_sections.append(section)
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


def _split_into_paragraphs(section_text: str) -> list[str]:
    """Split prose and bullet-list sections into semantic paragraphs."""
    raw_paragraphs = [paragraph.strip() for paragraph in section_text.split("\n\n")]
    result: list[str] = []
    for paragraph in raw_paragraphs:
        if not paragraph:
            continue
        lines = paragraph.split("\n")
        if not any(_BULLET_PATTERN.match(line) for line in lines):
            result.append(paragraph)
            continue

        current_item: list[str] = []
        for line in lines:
            if _BULLET_PATTERN.match(line) and current_item:
                result.append("\n".join(current_item).strip())
                current_item = [line]
            else:
                current_item.append(line)
        if current_item:
            result.append("\n".join(current_item).strip())
    return [paragraph for paragraph in result if paragraph]


def _split_long_section(section_text: str) -> list[str]:
    paragraphs = _split_into_paragraphs(section_text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > MAX_VECTOR_CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_by_char_window(paragraph))
            continue

        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= MAX_VECTOR_CHUNK_CHARS:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)
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
