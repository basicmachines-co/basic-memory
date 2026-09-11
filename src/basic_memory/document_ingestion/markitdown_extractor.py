"""Bounded subprocess adapter for Microsoft's markitdown Office converters.

markitdown is a synchronous, in-process library with no resource limits that
parses attacker-controlled Office archives (zip plus XML through lxml, images
through Pillow). Every conversion therefore runs in a killable child process
with explicit source, output, memory, CPU, wall-clock, and concurrency
ceilings, the same shape as the pdf-inspector adapter.

The worker calls one specific converter chosen from the indexed media type. It
never uses markitdown's content-sniffing ``MarkItDown.convert()`` loop, so
magika, the URL converters, zip recursion, and the plain-text fallthrough are
never in play. The ``markitdown`` package is imported only by the worker
module, so ``basic-memory[documents]`` stays optional for callers that merely
reference the contracts.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from basic_memory.document_ingestion.bounded_process import (
    BoundedProcessExitError,
    BoundedProcessOutputLimitError,
    BoundedProcessSpawnError,
    BoundedProcessTimeoutError,
    run_bounded_process,
)
from basic_memory.document_ingestion.raw_document import (
    DocumentExtractor,
    ExtractedDocument,
    extraction_options_checksum,
)
from basic_memory.schemas.document import DocumentExtractionStatus, DocumentExtractionV1

MARKITDOWN_ENGINE = "microsoft/markitdown"
MARKITDOWN_WORKER_MODULE = "basic_memory.document_ingestion.markitdown_worker"
MARKITDOWN_RAW_PIPELINE_VERSION = "markitdown-raw-v1"

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class MarkitdownFormat(StrEnum):
    """Office formats routed to one specific markitdown converter."""

    docx = "docx"
    pptx = "pptx"


# Dispatch key is the exact media type the indexer records for the source entity.
MARKITDOWN_FORMATS_BY_MEDIA_TYPE: Mapping[str, MarkitdownFormat] = {
    DOCX_MEDIA_TYPE: MarkitdownFormat.docx,
    PPTX_MEDIA_TYPE: MarkitdownFormat.pptx,
}
MEDIA_TYPES_BY_MARKITDOWN_FORMAT: Mapping[MarkitdownFormat, str] = {
    fmt: media_type for media_type, fmt in MARKITDOWN_FORMATS_BY_MEDIA_TYPE.items()
}


class MarkitdownLimits(BaseModel):
    """Resource limits enforced around one markitdown conversion."""

    model_config = ConfigDict(frozen=True, strict=True)

    max_source_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_output_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    # lxml, Pillow, and python-pptx need more address space than the Rust PDF
    # parser; 512 MiB made valid decks fail before conversion began.
    max_memory_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0)
    cpu_seconds: int = Field(default=50, gt=0)
    max_concurrency: int = Field(default=1, gt=0)


class MarkitdownOutput(BaseModel):
    """Validated, serialization-safe output from the markitdown worker process."""

    model_config = ConfigDict(frozen=True, strict=True)

    engine: str = MARKITDOWN_ENGINE
    engine_version: str
    format: MarkitdownFormat
    markdown: str
    processing_time_ms: int = Field(ge=0)
    # Slides are the pptx page unit; docx has no page model, so it reports None.
    slide_count: int | None = Field(default=None, ge=0)


class MarkitdownExtractionError(RuntimeError):
    """A bounded markitdown conversion failed."""


class MarkitdownSourceTooLargeError(MarkitdownExtractionError):
    """Raised before spawning when the source exceeds the configured byte limit."""


class MarkitdownNotInstalledError(MarkitdownExtractionError):
    """Raised before spawning when the optional ``documents`` extra is missing."""


@dataclass(slots=True)
class MarkitdownExtractor:
    """Run one markitdown converter in killable, concurrency-bounded subprocesses."""

    format: MarkitdownFormat
    limits: MarkitdownLimits = field(default_factory=MarkitdownLimits)
    python_executable: str = sys.executable
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.limits.max_concurrency)

    async def extract(self, content: bytes, *, file_name: str) -> ExtractedDocument:
        """Convert one Office document without blocking the caller's event loop."""
        if len(content) > self.limits.max_source_bytes:
            raise MarkitdownSourceTooLargeError(
                "Office document source exceeds the configured extraction byte limit"
            )
        # Fail here with an install hint instead of a child-process ImportError
        # traceback: the worker is the only module that imports markitdown.
        if importlib.util.find_spec("markitdown") is None:
            raise MarkitdownNotInstalledError(
                "Office document extraction needs the markitdown package; "
                "install basic-memory[documents]"
            )

        argv = (
            self.python_executable,
            "-m",
            MARKITDOWN_WORKER_MODULE,
            "--format",
            self.format.value,
            "--file-name",
            file_name,
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
                    content,
                    timeout_seconds=self.limits.timeout_seconds,
                    max_output_bytes=self.limits.max_output_bytes,
                )
            except BoundedProcessSpawnError as error:
                raise MarkitdownExtractionError(
                    "Office document extraction needs an event loop with subprocess support; "
                    "Basic Memory runs the selector loop on Windows"
                ) from error
            except BoundedProcessTimeoutError as error:
                raise MarkitdownExtractionError(
                    "Office document extraction exceeded the configured deadline"
                ) from error
            except BoundedProcessOutputLimitError as error:
                raise MarkitdownExtractionError(
                    "Office document extraction exceeded the configured output byte limit "
                    f"on {error.stream_name}"
                ) from error
            except BoundedProcessExitError as error:
                raise MarkitdownExtractionError(
                    f"Office document extraction failed with exit code {error.returncode}: "
                    f"{error.detail}"
                ) from error

        try:
            output = MarkitdownOutput.model_validate_json(stdout, strict=True)
        except ValueError as error:
            raise MarkitdownExtractionError(
                "Office document extraction returned an invalid result"
            ) from error
        if output.format is not self.format:
            raise MarkitdownExtractionError(
                f"Office document extraction converted {output.format.value}, "
                f"expected {self.format.value}"
            )
        return markitdown_extracted_document(
            output, limits=self.limits, extracted_at=datetime.now(tz=UTC)
        )


def markitdown_extracted_document(
    output: MarkitdownOutput,
    *,
    limits: MarkitdownLimits,
    extracted_at: datetime,
) -> ExtractedDocument:
    """Map worker output into the parser-neutral extraction contract."""
    # markitdown emits one marker per slide, so slides fill the page diagnostics
    # for pptx. docx reports zero pages, which the contract reads as "not
    # paginated" for a complete extraction.
    page_count = output.slide_count or 0
    extraction = DocumentExtractionV1(
        engine=output.engine,
        engine_version=output.engine_version,
        profile=f"markitdown-{output.format.value}-v1",
        options_hash=extraction_options_checksum(limits),
        classification=output.format.value,
        status=DocumentExtractionStatus.complete,
        extracted_at=extracted_at,
        duration_ms=output.processing_time_ms,
        page_count=page_count,
        extracted_page_count=page_count,
        requires_ocr=False,
        ocr_page_count=0,
        pages_needing_ocr=(),
        has_tables=any(line.startswith("|") for line in output.markdown.splitlines()),
    )
    return ExtractedDocument(
        extraction=extraction,
        markdown=output.markdown,
        kind=output.format.value,
        pipeline_version=MARKITDOWN_RAW_PIPELINE_VERSION,
    )


def markitdown_extractors(
    limits: MarkitdownLimits | None = None,
    *,
    python_executable: str = sys.executable,
) -> dict[str, DocumentExtractor]:
    """Return one bounded extractor per supported Office media type."""
    shared_limits = limits or MarkitdownLimits()
    return {
        media_type: MarkitdownExtractor(
            format=fmt, limits=shared_limits, python_executable=python_executable
        )
        for media_type, fmt in MARKITDOWN_FORMATS_BY_MEDIA_TYPE.items()
    }
