"""Tests for the bounded markitdown subprocess adapter."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import sys
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from basic_memory.document_ingestion.markitdown_extractor import (
    DOCX_MEDIA_TYPE,
    MARKITDOWN_ENGINE,
    MARKITDOWN_RAW_PIPELINE_VERSION,
    MARKITDOWN_WORKER_MODULE,
    PPTX_MEDIA_TYPE,
    MarkitdownExtractionError,
    MarkitdownExtractor,
    MarkitdownFormat,
    MarkitdownLimits,
    MarkitdownNotInstalledError,
    MarkitdownOutput,
    MarkitdownSourceTooLargeError,
    markitdown_extracted_document,
    markitdown_extractors,
)
from basic_memory.schemas.document import DocumentExtractionStatus
from tests.document_ingestion.office_fixtures import minimal_pptx
from tests.document_ingestion.process_fakes import FakeProcess, install_fake_process


def valid_output_json(*, format: MarkitdownFormat = MarkitdownFormat.docx) -> bytes:
    return (
        MarkitdownOutput(
            engine_version="0.1.7",
            format=format,
            markdown="# Goals",
            processing_time_ms=3,
            slide_count=2 if format is MarkitdownFormat.pptx else None,
        )
        .model_dump_json()
        .encode("utf-8")
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Basic Memory pins the selector event loop on Windows, which cannot spawn subprocesses",
)
@pytest.mark.asyncio
async def test_markitdown_extractor_converts_pptx_in_subprocess() -> None:
    extractor = MarkitdownExtractor(format=MarkitdownFormat.pptx)

    extracted = await extractor.extract(minimal_pptx(), file_name="deck.pptx")

    assert extracted.kind == "pptx"
    assert extracted.pipeline_version == MARKITDOWN_RAW_PIPELINE_VERSION
    assert "<!-- Slide number: 2 -->" in extracted.markdown
    extraction = extracted.extraction
    assert extraction.engine == MARKITDOWN_ENGINE
    assert extraction.engine_version == importlib.metadata.version("markitdown")
    assert extraction.profile == "markitdown-pptx-v1"
    assert extraction.classification == "pptx"
    assert extraction.status is DocumentExtractionStatus.complete
    assert extraction.page_count == 2
    assert extraction.extracted_page_count == 2
    assert extraction.has_tables is True


@pytest.mark.asyncio
async def test_markitdown_extractor_passes_format_and_limits_to_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(stdout=valid_output_json())
    install_fake_process(monkeypatch, process)
    extractor = MarkitdownExtractor(
        format=MarkitdownFormat.docx,
        limits=MarkitdownLimits(max_output_bytes=1000, max_memory_bytes=2000, cpu_seconds=3),
        python_executable="python-x",
    )

    extracted = await extractor.extract(b"docx-bytes", file_name="plan.docx")

    assert process.argv == (
        "python-x",
        "-m",
        MARKITDOWN_WORKER_MODULE,
        "--format",
        "docx",
        "--file-name",
        "plan.docx",
        "--max-output-bytes",
        "1000",
        "--max-memory-bytes",
        "2000",
        "--cpu-seconds",
        "3",
    )
    assert process.stdin.written == b"docx-bytes"
    assert extracted.kind == "docx"
    assert extracted.markdown == "# Goals"
    assert extracted.extraction.page_count == 0
    assert extracted.extraction.has_tables is False


@pytest.mark.asyncio
async def test_markitdown_extractor_rejects_an_oversized_source_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not spawn for an oversized source")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_subprocess)
    extractor = MarkitdownExtractor(
        format=MarkitdownFormat.docx, limits=MarkitdownLimits(max_source_bytes=3)
    )

    with pytest.raises(MarkitdownSourceTooLargeError):
        await extractor.extract(b"docx-bytes", file_name="plan.docx")


@pytest.mark.asyncio
async def test_markitdown_extractor_names_the_missing_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    with pytest.raises(MarkitdownNotInstalledError, match=r"basic-memory\[documents\]"):
        await MarkitdownExtractor(format=MarkitdownFormat.docx).extract(
            b"docx-bytes", file_name="plan.docx"
        )


@pytest.mark.asyncio
async def test_markitdown_extractor_names_a_loop_without_subprocess_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def selector_loop_spawn(*args: object, **kwargs: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", selector_loop_spawn)

    with pytest.raises(MarkitdownExtractionError, match="subprocess support"):
        await MarkitdownExtractor(format=MarkitdownFormat.docx).extract(
            b"docx-bytes", file_name="plan.docx"
        )


@pytest.mark.asyncio
async def test_markitdown_extractor_kills_the_child_on_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(returncode=None, hang=True)
    install_fake_process(monkeypatch, process)
    extractor = MarkitdownExtractor(
        format=MarkitdownFormat.docx, limits=MarkitdownLimits(timeout_seconds=0.01)
    )

    with pytest.raises(MarkitdownExtractionError, match="deadline"):
        await extractor.extract(b"docx-bytes", file_name="plan.docx")

    assert process.killed
    assert process.waited


@pytest.mark.asyncio
async def test_markitdown_extractor_reports_a_failed_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(monkeypatch, FakeProcess(returncode=3, stderr=b"boom\n"))

    with pytest.raises(MarkitdownExtractionError, match="exit code 3: boom"):
        await MarkitdownExtractor(format=MarkitdownFormat.docx).extract(
            b"docx-bytes", file_name="plan.docx"
        )


@pytest.mark.asyncio
async def test_markitdown_extractor_kills_a_child_that_overruns_a_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(returncode=None, stdout=b"x" * 64, hang=True)
    install_fake_process(monkeypatch, process)
    extractor = MarkitdownExtractor(
        format=MarkitdownFormat.docx, limits=MarkitdownLimits(max_output_bytes=16)
    )

    with pytest.raises(MarkitdownExtractionError, match="output byte limit on stdout"):
        await extractor.extract(b"docx-bytes", file_name="plan.docx")

    assert process.killed


@pytest.mark.asyncio
async def test_markitdown_extractor_rejects_an_invalid_child_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(monkeypatch, FakeProcess(stdout=b'{"engine_version": 1}'))

    with pytest.raises(MarkitdownExtractionError, match="invalid result"):
        await MarkitdownExtractor(format=MarkitdownFormat.docx).extract(
            b"docx-bytes", file_name="plan.docx"
        )


@pytest.mark.asyncio
async def test_markitdown_extractor_rejects_a_worker_that_converted_another_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(
        monkeypatch, FakeProcess(stdout=valid_output_json(format=MarkitdownFormat.pptx))
    )

    with pytest.raises(MarkitdownExtractionError, match="converted pptx, expected docx"):
        await MarkitdownExtractor(format=MarkitdownFormat.docx).extract(
            b"docx-bytes", file_name="plan.docx"
        )


def test_markitdown_extracted_document_uses_slides_as_pages() -> None:
    output = MarkitdownOutput(
        engine_version="0.1.7",
        format=MarkitdownFormat.pptx,
        markdown="<!-- Slide number: 1 -->\n# A\n\n<!-- Slide number: 2 -->\n# B\n\n<!-- Slide number: 3 -->",
        processing_time_ms=3,
        slide_count=3,
    )

    extracted = markitdown_extracted_document(
        output, limits=MarkitdownLimits(), extracted_at=datetime(2026, 9, 7, tzinfo=UTC)
    )

    assert extracted.extraction.page_count == 3
    assert extracted.extraction.extracted_page_count == 3
    assert extracted.extraction.requires_ocr is False


def test_markitdown_extractors_cover_docx_and_pptx_with_shared_limits() -> None:
    limits = MarkitdownLimits(timeout_seconds=5.0)

    extractors = markitdown_extractors(limits, python_executable="python-x")

    assert set(extractors) == {DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE}
    docx = extractors[DOCX_MEDIA_TYPE]
    pptx = extractors[PPTX_MEDIA_TYPE]
    assert isinstance(docx, MarkitdownExtractor) and docx.format is MarkitdownFormat.docx
    assert isinstance(pptx, MarkitdownExtractor) and pptx.format is MarkitdownFormat.pptx
    assert docx.limits is limits and pptx.limits is limits
    assert docx.python_executable == "python-x"


def test_markitdown_limits_are_strict() -> None:
    with pytest.raises(ValidationError):
        MarkitdownLimits.model_validate({"max_source_bytes": "1"})
