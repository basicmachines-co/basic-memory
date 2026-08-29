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
    confidence: float = Field(ge=0.0, le=1.0)
    processing_time_ms: int = Field(ge=0)
    is_complex_layout: bool
    pages_with_tables: tuple[int, ...] = ()
    pages_with_columns: tuple[int, ...] = ()
    has_encoding_issues: bool

    @model_validator(mode="after")
    def validate_page_diagnostics(self) -> PdfInspectorOutput:
        """Keep page diagnostics within the document and internally consistent."""
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

        async with self._semaphore:
            process = await asyncio.create_subprocess_exec(
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
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(self.limits.timeout_seconds):
                    stdout, stderr = await process.communicate(input=pdf_bytes)
            except asyncio.CancelledError:
                await _kill_and_wait(process)
                raise
            except TimeoutError as error:
                await _kill_and_wait(process)
                raise PdfInspectorTimeoutError(
                    "PDF extraction exceeded the configured deadline"
                ) from error

        if process.returncode != 0:
            error_detail = stderr.decode("utf-8", errors="replace")[-2000:].strip()
            raise PdfInspectorProcessError(
                f"PDF extraction process failed with exit code {process.returncode}: "
                f"{error_detail or 'no error detail'}"
            )
        if len(stdout) > self.limits.max_output_bytes:
            raise PdfInspectorProcessError(
                "PDF extraction process exceeded the configured output byte limit"
            )

        try:
            return PdfInspectorOutput.model_validate_json(stdout, strict=True)
        except ValueError as error:
            raise PdfInspectorProcessError(
                "PDF extraction process returned an invalid result"
            ) from error


async def _kill_and_wait(process: asyncio.subprocess.Process) -> None:
    """Terminate and reap one extractor child before releasing worker capacity."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            # The child can exit between the returncode check and the signal.
            # Waiting still reaps it and preserves the caller's original outcome.
            pass
    await process.wait()
