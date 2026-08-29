"""Tests for the one-shot pdf-inspector worker process."""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from basic_memory.document_ingestion import pdf_inspector_worker
from basic_memory.document_ingestion.pdf_inspector import PDF_INSPECTOR_ENGINE, PdfInspectorOutput


@dataclass(frozen=True)
class FakePage:
    page: int
    needs_ocr: bool
    markdown: str


def fake_engine(
    *,
    classified_pages: int = 2,
    processed_pages: int = 2,
    pages: tuple[FakePage, ...] | None = None,
    pages_needing_ocr: tuple[int, ...] = (2,),
    pages_with_tables: tuple[int, ...] = (),
    pages_with_columns: tuple[int, ...] = (),
) -> SimpleNamespace:
    """Script the three pdf_inspector calls the worker makes."""
    if pages is None:
        pages = (FakePage(0, False, "# Page one\n"), FakePage(1, True, ""))
    return SimpleNamespace(
        classify_pdf_bytes=lambda data: SimpleNamespace(page_count=classified_pages),
        process_pdf_bytes=lambda data: SimpleNamespace(
            page_count=processed_pages,
            pdf_type="mixed",
            title="Doc",
            confidence=0.9,
            processing_time_ms=5,
            has_encoding_issues=False,
        ),
        extract_pages_markdown_bytes=lambda data: SimpleNamespace(
            pages=list(pages),
            pages_needing_ocr=list(pages_needing_ocr),
            is_complex=False,
            pages_with_tables=list(pages_with_tables),
            pages_with_columns=list(pages_with_columns),
        ),
    )


def test_inspect_pdf_bytes_maps_native_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_inspector_worker, "pdf_inspector", fake_engine())

    result = pdf_inspector_worker.inspect_pdf_bytes(
        b"%PDF-test", max_pages=10, max_output_bytes=10_000
    )

    assert result.engine == PDF_INSPECTOR_ENGINE
    assert result.pdf_type == "mixed"
    assert result.title == "Doc"
    assert result.page_count == 2
    assert result.extracted_page_count == 1
    assert result.pages_needing_ocr == (2,)
    assert result.markdown == "<!-- Page 1 -->\n\n# Page one\n\n<!-- Page 2: OCR required -->"


@pytest.mark.parametrize(
    ("engine_kwargs", "limits", "error", "message"),
    [
        ({"classified_pages": 3, "processed_pages": 3}, {"max_pages": 2}, ValueError, "page limit"),
        ({"processed_pages": 3}, {}, RuntimeError, "page counts differ"),
        ({"pages": (FakePage(0, False, "only"),)}, {}, RuntimeError, "omitted pages"),
        ({}, {"max_output_bytes": 5}, ValueError, "output byte limit"),
        ({"pages_needing_ocr": (5,)}, {}, ValueError, "outside the document"),
    ],
)
def test_inspect_pdf_bytes_rejects_inconsistent_native_output(
    monkeypatch: pytest.MonkeyPatch,
    engine_kwargs: dict[str, Any],
    limits: dict[str, int],
    error: type[Exception],
    message: str,
) -> None:
    monkeypatch.setattr(pdf_inspector_worker, "pdf_inspector", fake_engine(**engine_kwargs))

    with pytest.raises(error, match=message):
        pdf_inspector_worker.inspect_pdf_bytes(
            b"%PDF-test",
            max_pages=limits.get("max_pages", 10),
            max_output_bytes=limits.get("max_output_bytes", 10_000),
        )


def fake_posix_resource() -> SimpleNamespace:
    """Stand in for the POSIX ``resource`` module so limit tests run on every OS."""
    return SimpleNamespace(setrlimit=Mock(), RLIMIT_AS="RLIMIT_AS", RLIMIT_CPU="RLIMIT_CPU")


def test_worker_bounds_linux_address_space(monkeypatch: pytest.MonkeyPatch) -> None:
    resource = fake_posix_resource()
    monkeypatch.setattr(pdf_inspector_worker.sys, "platform", "linux")
    monkeypatch.setattr(pdf_inspector_worker, "resource", resource)

    pdf_inspector_worker._apply_memory_limit(512 * 1024 * 1024)

    resource.setrlimit.assert_called_once_with("RLIMIT_AS", (512 * 1024 * 1024, 512 * 1024 * 1024))


def test_worker_skips_address_space_limit_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    resource = fake_posix_resource()
    monkeypatch.setattr(pdf_inspector_worker.sys, "platform", "darwin")
    monkeypatch.setattr(pdf_inspector_worker, "resource", resource)

    pdf_inspector_worker._apply_memory_limit(512 * 1024 * 1024)

    resource.setrlimit.assert_not_called()


def test_worker_bounds_cpu_time(monkeypatch: pytest.MonkeyPatch) -> None:
    resource = fake_posix_resource()
    monkeypatch.setattr(pdf_inspector_worker, "resource", resource)

    pdf_inspector_worker._apply_cpu_limit(25)

    resource.setrlimit.assert_called_once_with("RLIMIT_CPU", (25, 26))


def test_worker_skips_rlimits_without_posix_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no rlimits; the parent's deadline is the only ceiling there."""
    monkeypatch.setattr(pdf_inspector_worker, "resource", None)
    monkeypatch.setattr(pdf_inspector_worker.sys, "platform", "linux")

    pdf_inspector_worker._apply_cpu_limit(25)
    pdf_inspector_worker._apply_memory_limit(1024)


def test_worker_main_writes_one_json_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    applied: list[tuple[str, int]] = []
    monkeypatch.setattr(pdf_inspector_worker, "pdf_inspector", fake_engine())
    monkeypatch.setattr(
        pdf_inspector_worker, "_apply_memory_limit", lambda n: applied.append(("memory", n))
    )
    monkeypatch.setattr(
        pdf_inspector_worker, "_apply_cpu_limit", lambda n: applied.append(("cpu", n))
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"%PDF-test")))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pdf_inspector_worker",
            "--max-pages",
            "10",
            "--max-output-bytes",
            "10000",
            "--max-memory-bytes",
            "1000",
            "--cpu-seconds",
            "5",
        ],
    )

    pdf_inspector_worker.main()

    result = PdfInspectorOutput.model_validate_json(capsys.readouterr().out, strict=True)
    assert result.page_count == 2
    assert applied == [("memory", 1000), ("cpu", 5)]
