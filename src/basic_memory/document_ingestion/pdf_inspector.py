"""Bounded subprocess adapter for Firecrawl's native PDF inspector.

pdf-inspector is a synchronous Rust extractor that parses untrusted bytes. Every
extraction therefore runs in a killable child process with explicit source,
page, output, memory, CPU, wall-clock, and concurrency ceilings. This module owns
those limits and the validated output contract. The ``pdf_inspector`` package is
imported only by the worker module, so callers can construct limits and parse
results without the optional ``basic-memory[pdf]`` extra installed.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from basic_memory.schemas.document_page_map import DocumentPageMapV1
from basic_memory.document_ingestion.bounded_process import (
    BoundedProcessExitError,
    BoundedProcessOutputLimitError,
    BoundedProcessSpawnError,
    BoundedProcessTimeoutError,
    run_bounded_process,
)

PDF_INSPECTOR_ENGINE = "firecrawl/pdf-inspector"
PDF_INSPECTOR_WORKER_MODULE = "basic_memory.document_ingestion.pdf_inspector_worker"


class PdfInspectorPdfType(StrEnum):
    """PDF classifications exposed by pdf-inspector."""

    text_based = "text_based"
    scanned = "scanned"
    image_based = "image_based"
    mixed = "mixed"


class PdfInspectorLimits(BaseModel):
    """Resource limits enforced around one native PDF extraction."""

    model_config = ConfigDict(frozen=True, strict=True)

    max_source_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=100, gt=0)
    max_output_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    max_memory_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
    cpu_seconds: int = Field(default=25, gt=0)
    max_concurrency: int = Field(default=1, gt=0)


class PdfInspectorOutput(BaseModel):
    """Validated, serialization-safe output from the native extractor process."""

    model_config = ConfigDict(frozen=True, strict=True)

    engine: str = PDF_INSPECTOR_ENGINE
    engine_version: str
    pdf_type: PdfInspectorPdfType
    markdown: str
    title: str | None = None
    page_count: int = Field(gt=0)
    extracted_page_count: int = Field(ge=0)
    pages_needing_ocr: tuple[int, ...] = ()
    page_map: DocumentPageMapV1 | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    processing_time_ms: int = Field(ge=0)
    is_complex_layout: bool
    pages_with_tables: tuple[int, ...] = ()
    pages_with_columns: tuple[int, ...] = ()
    has_encoding_issues: bool

    @model_validator(mode="after")
    def validate_page_diagnostics(self) -> PdfInspectorOutput:
        """Keep page diagnostics within the document and internally consistent."""
        if self.page_map is not None:
            if len(self.page_map.pages) != self.page_count:
                raise ValueError("page map must contain every physical page")
            body = self.markdown.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
            body = body + "\n" if body else ""
            self.page_map.resolve_span(body, start=0, end=len(body))
        page_lists = (
            self.pages_needing_ocr,
            self.pages_with_tables,
            self.pages_with_columns,
        )
        for pages in page_lists:
            if tuple(sorted(set(pages))) != pages:
                raise ValueError("PDF diagnostic pages must be sorted and unique")
            if any(page < 1 or page > self.page_count for page in pages):
                raise ValueError("PDF diagnostic page is outside the document")

        expected_extracted_pages = self.page_count - len(self.pages_needing_ocr)
        if self.extracted_page_count != expected_extracted_pages:
            raise ValueError("extracted_page_count must exclude exactly the pages needing OCR")
        return self


class PdfInspectorError(RuntimeError):
    """Base failure for a bounded PDF inspection attempt."""


class PdfInspectorSourceTooLargeError(PdfInspectorError):
    """Raised before spawning when the source exceeds the configured byte limit."""


class PdfInspectorTimeoutError(PdfInspectorError):
    """Raised after terminating an extraction process that exceeded its deadline."""


class PdfInspectorProcessError(PdfInspectorError):
    """Raised when the isolated extractor exits unsuccessfully or returns invalid JSON."""


@dataclass(slots=True)
class PdfInspector:
    """Run synchronous Rust extraction in killable, concurrency-bounded subprocesses."""

    limits: PdfInspectorLimits = field(default_factory=PdfInspectorLimits)
    python_executable: str = sys.executable
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.limits.max_concurrency)

    async def extract(self, pdf_bytes: bytes) -> PdfInspectorOutput:
        """Extract one PDF without blocking the caller's event loop."""
        if len(pdf_bytes) > self.limits.max_source_bytes:
            raise PdfInspectorSourceTooLargeError(
                "PDF source exceeds the configured extraction byte limit"
            )

        argv = (
            self.python_executable,
            "-m",
            PDF_INSPECTOR_WORKER_MODULE,
            "--max-pages",
            str(self.limits.max_pages),
            "--max-output-bytes",
            str(self.limits.max_output_bytes),
            "--max-memory-bytes",
            str(self.limits.max_memory_bytes),
            "--cpu-seconds",
            str(self.limits.cpu_seconds),
        )
        async with self._semaphore:
            try:
                stdout = await run_bounded_process(
                    argv,
                    pdf_bytes,
                    timeout_seconds=self.limits.timeout_seconds,
                    max_output_bytes=self.limits.max_output_bytes,
                )
            except BoundedProcessSpawnError as error:
                raise PdfInspectorProcessError(
                    "PDF extraction needs an event loop with subprocess support; "
                    "Basic Memory runs the selector loop on Windows"
                ) from error
            except BoundedProcessTimeoutError as error:
                raise PdfInspectorTimeoutError(
                    "PDF extraction exceeded the configured deadline"
                ) from error
            except BoundedProcessOutputLimitError as error:
                raise PdfInspectorProcessError(
                    "PDF extraction process exceeded the configured output byte limit "
                    f"on {error.stream_name}"
                ) from error
            except BoundedProcessExitError as error:
                raise PdfInspectorProcessError(
                    f"PDF extraction process failed with exit code {error.returncode}: "
                    f"{error.detail}"
                ) from error

        try:
            return PdfInspectorOutput.model_validate_json(stdout, strict=True)
        except ValueError as error:
            raise PdfInspectorProcessError(
                "PDF extraction process returned an invalid result"
            ) from error
