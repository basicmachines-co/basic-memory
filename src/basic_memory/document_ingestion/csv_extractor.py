"""In-process CSV to Markdown preview extraction.

CSV needs no third-party parser: the stdlib reader is pure Python over bytes the
runtime has already bounded, so it runs in a worker thread rather than a child
process. Decoding is strict UTF-8 (BOM tolerated). A guessed charset is how
markitdown produced mojibake on a valid UTF-8 export, so a non-UTF-8 file fails
the extraction instead of being silently mangled.

The Markdown is a preview, not a dump: header, the first ``max_rows`` rows, and
the total row count. A 7,000-row export rendered in full is search noise, not
knowledge; the source file stays in the project for anyone who needs every row.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from basic_memory import __version__ as basic_memory_version
from basic_memory.document_ingestion.raw_document import (
    ExtractedDocument,
    extraction_options_checksum,
)
from basic_memory.schemas.document import DocumentExtractionStatus, DocumentExtractionV1

CSV_ENGINE = "basic-memory/csv"
CSV_MEDIA_TYPE = "text/csv"
CSV_RAW_PIPELINE_VERSION = "csv-raw-v1"
CSV_EXTRACTION_PROFILE = "csv-preview-v1"


class CsvLimits(BaseModel):
    """Bounds on one CSV preview extraction."""

    model_config = ConfigDict(frozen=True, strict=True)

    max_source_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_rows: int = Field(default=200, gt=0)
    max_output_bytes: int = Field(default=5 * 1024 * 1024, gt=0)


class CsvExtractionError(RuntimeError):
    """A CSV preview extraction failed."""


class CsvSourceTooLargeError(CsvExtractionError):
    """Raised before parsing when the source exceeds the configured byte limit."""


class CsvDecodeError(CsvExtractionError):
    """Raised when the source is not UTF-8."""


@dataclass(frozen=True, slots=True)
class CsvPreview:
    """Rendered preview plus the counts that describe what it left out."""

    markdown: str
    row_count: int
    shown_rows: int


@dataclass(frozen=True, slots=True)
class CsvExtractor:
    """Render a bounded Markdown preview of one CSV file."""

    limits: CsvLimits = field(default_factory=CsvLimits)

    async def extract(self, content: bytes, *, file_name: str) -> ExtractedDocument:
        if len(content) > self.limits.max_source_bytes:
            raise CsvSourceTooLargeError("CSV source exceeds the configured extraction byte limit")
        started = time.perf_counter()
        preview = await asyncio.to_thread(
            render_csv_preview, content, max_rows=self.limits.max_rows
        )
        if len(preview.markdown.encode("utf-8")) > self.limits.max_output_bytes:
            raise CsvExtractionError("CSV preview exceeds the configured output byte limit")
        extraction = DocumentExtractionV1(
            engine=CSV_ENGINE,
            engine_version=basic_memory_version,
            profile=CSV_EXTRACTION_PROFILE,
            options_hash=extraction_options_checksum(self.limits),
            classification="csv",
            status=DocumentExtractionStatus.complete,
            extracted_at=datetime.now(tz=UTC),
            duration_ms=int((time.perf_counter() - started) * 1000),
            page_count=0,
            extracted_page_count=0,
            requires_ocr=False,
            ocr_page_count=0,
            has_tables=preview.shown_rows > 0,
        )
        return ExtractedDocument(
            extraction=extraction,
            markdown=preview.markdown,
            kind="csv",
            pipeline_version=CSV_RAW_PIPELINE_VERSION,
        )


def render_csv_preview(content: bytes, *, max_rows: int) -> CsvPreview:
    """Render the header, the first ``max_rows`` rows, and the total row count."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CsvDecodeError("CSV source is not UTF-8 encoded") from error

    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        return CsvPreview(markdown="_Empty CSV file._\n", row_count=0, shown_rows=0)

    shown: list[Sequence[str]] = []
    row_count = 0
    for row in reader:
        row_count += 1
        if len(shown) < max_rows:
            shown.append(row)

    width = len(header)
    lines = [
        _table_row(header, width),
        "| " + " | ".join("---" for _ in range(width)) + " |",
        *(_table_row(row, width) for row in shown),
        "",
    ]
    if row_count > len(shown):
        lines.append(f"_Showing {len(shown)} of {row_count} rows._")
    else:
        lines.append(f"_{row_count} rows._")
    return CsvPreview(markdown="\n".join(lines) + "\n", row_count=row_count, shown_rows=len(shown))


def _table_row(cells: Sequence[str], width: int) -> str:
    # Ragged rows are common in hand-edited exports: pad short rows and drop
    # cells past the header width so every line stays a valid table row.
    padded = [*cells[:width], *([""] * (width - len(cells)))]
    # Backslashes first: a literal `\` before a `|` would otherwise turn the pipe
    # escape into an escaped backslash followed by a live cell separator.
    escaped = (cell.replace("\n", " ").replace("\\", "\\\\").replace("|", "\\|") for cell in padded)
    return "| " + " | ".join(escaped) + " |"
