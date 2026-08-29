"""Tests for the bounded pdf-inspector subprocess adapter."""

from __future__ import annotations

import asyncio
import importlib.metadata
from typing import Any, cast

import pytest
from pydantic import ValidationError

from basic_memory.document_ingestion.pdf_inspector import (
    PDF_INSPECTOR_ENGINE,
    PdfInspector,
    PdfInspectorLimits,
    PdfInspectorOutput,
    PdfInspectorPdfType,
    PdfInspectorProcessError,
    PdfInspectorSourceTooLargeError,
    PdfInspectorTimeoutError,
    _kill_and_wait,
)


def minimal_text_pdf() -> bytes:
    """Build one small text PDF without adding a PDF-generation test dependency."""
    stream = b"BT /F1 18 Tf 72 720 Td (Hello Basic Memory) Tj ET"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    )

    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{object_number} 0 obj\n".encode())
        document.extend(body)
        document.extend(b"\nendobj\n")

    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


class FakeProcess:
    """Stand-in for asyncio's subprocess handle with scripted behavior."""

    def __init__(
        self,
        *,
        returncode: int | None = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        hang: bool = False,
        kill_raises: bool = False,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self._kill_raises = kill_raises
        self.communicate_started = asyncio.Event()
        self.killed = False
        self.waited = False

    async def communicate(self, *, input: bytes) -> tuple[bytes, bytes]:
        _ = input
        self.communicate_started.set()
        if self._hang:
            await asyncio.Event().wait()
        return self._stdout, self._stderr

    def kill(self) -> None:
        if self._kill_raises:
            raise ProcessLookupError
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return self.returncode if self.returncode is not None else -9


def install_fake_process(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> None:
    async def create_subprocess(*args: object, **kwargs: object) -> FakeProcess:
        _ = (args, kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)


@pytest.mark.asyncio
async def test_pdf_inspector_extracts_native_text_in_subprocess() -> None:
    inspector = PdfInspector(
        limits=PdfInspectorLimits(
            max_source_bytes=100_000,
            max_pages=5,
            max_output_bytes=100_000,
            timeout_seconds=30.0,
            cpu_seconds=10,
            max_concurrency=1,
        )
    )

    result = await inspector.extract(minimal_text_pdf())

    assert result.engine == PDF_INSPECTOR_ENGINE
    assert result.engine_version == importlib.metadata.version("pdf-inspector")
    assert result.pdf_type == "text_based"
    assert result.page_count == 1
    assert result.extracted_page_count == 1
    assert result.pages_needing_ocr == ()
    assert "Hello Basic Memory" in result.markdown


@pytest.mark.asyncio
async def test_pdf_inspector_rejects_oversized_source_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_subprocess)
    inspector = PdfInspector(limits=PdfInspectorLimits(max_source_bytes=3))

    with pytest.raises(PdfInspectorSourceTooLargeError):
        await inspector.extract(b"four")


@pytest.mark.asyncio
async def test_pdf_inspector_kills_and_reaps_child_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the caller must not leave its native extractor running."""
    process = FakeProcess(returncode=None, hang=True)
    install_fake_process(monkeypatch, process)
    inspector = PdfInspector(limits=PdfInspectorLimits(timeout_seconds=10.0))

    task = asyncio.create_task(inspector.extract(b"%PDF-test"))
    await process.communicate_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed is True
    assert process.waited is True


@pytest.mark.asyncio
async def test_pdf_inspector_kills_child_on_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(returncode=None, hang=True)
    install_fake_process(monkeypatch, process)
    inspector = PdfInspector(limits=PdfInspectorLimits(timeout_seconds=0.01))

    with pytest.raises(PdfInspectorTimeoutError, match="deadline"):
        await inspector.extract(b"%PDF-test")

    assert process.killed is True
    assert process.waited is True


@pytest.mark.asyncio
async def test_pdf_inspector_reports_a_failed_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(monkeypatch, FakeProcess(returncode=3, stderr=b"boom\n"))
    inspector = PdfInspector()

    with pytest.raises(PdfInspectorProcessError, match="exit code 3: boom"):
        await inspector.extract(b"%PDF-test")


@pytest.mark.asyncio
async def test_pdf_inspector_reports_a_failed_child_without_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(monkeypatch, FakeProcess(returncode=1))
    inspector = PdfInspector()

    with pytest.raises(PdfInspectorProcessError, match="no error detail"):
        await inspector.extract(b"%PDF-test")


@pytest.mark.asyncio
async def test_pdf_inspector_rejects_oversized_child_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(monkeypatch, FakeProcess(stdout=b"x" * 11))
    inspector = PdfInspector(limits=PdfInspectorLimits(max_output_bytes=10))

    with pytest.raises(PdfInspectorProcessError, match="output byte limit"):
        await inspector.extract(b"%PDF-test")


@pytest.mark.asyncio
async def test_pdf_inspector_rejects_invalid_child_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(monkeypatch, FakeProcess(stdout=b'{"engine_version": 1}'))
    inspector = PdfInspector()

    with pytest.raises(PdfInspectorProcessError, match="invalid result"):
        await inspector.extract(b"%PDF-test")


@pytest.mark.asyncio
async def test_kill_and_wait_tolerates_a_child_that_already_exited() -> None:
    raced = FakeProcess(returncode=None, kill_raises=True)
    await _kill_and_wait(cast(asyncio.subprocess.Process, raced))
    assert raced.waited is True

    finished = FakeProcess(returncode=0)
    await _kill_and_wait(cast(asyncio.subprocess.Process, finished))
    assert finished.killed is False
    assert finished.waited is True


def output_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "engine_version": "0.2.6",
        "pdf_type": PdfInspectorPdfType.mixed,
        "markdown": "# Partial",
        "page_count": 2,
        "extracted_page_count": 1,
        "pages_needing_ocr": (2,),
        "confidence": 0.5,
        "processing_time_ms": 1,
        "is_complex_layout": False,
        "pages_with_tables": (),
        "pages_with_columns": (),
        "has_encoding_issues": False,
    }
    base.update(overrides)
    return base


def test_pdf_inspector_output_accepts_consistent_diagnostics() -> None:
    result = PdfInspectorOutput(**output_kwargs())
    assert result.engine == PDF_INSPECTOR_ENGINE
    assert result.pages_needing_ocr == (2,)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"pages_needing_ocr": (3,)}, "outside the document"),
        ({"pages_with_tables": (2, 1)}, "sorted and unique"),
        ({"extracted_page_count": 2}, "exclude exactly"),
    ],
)
def test_pdf_inspector_output_rejects_inconsistent_diagnostics(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        PdfInspectorOutput(**output_kwargs(**overrides))


def test_pdf_inspector_limits_are_strict() -> None:
    with pytest.raises(ValidationError):
        PdfInspectorLimits.model_validate({"max_source_bytes": "10"})
