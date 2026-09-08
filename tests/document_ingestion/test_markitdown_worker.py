"""Tests for the one-shot markitdown worker process."""

from __future__ import annotations

import importlib.metadata
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from basic_memory.document_ingestion import markitdown_worker, worker_limits
from basic_memory.document_ingestion.markitdown_extractor import (
    MARKITDOWN_ENGINE,
    MarkitdownFormat,
    MarkitdownOutput,
)
from tests.document_ingestion.office_fixtures import (
    minimal_docx,
    minimal_pptx,
    pptx_with_picture,
)


def test_convert_office_bytes_renders_docx_headings() -> None:
    output = markitdown_worker.convert_office_bytes(
        minimal_docx(),
        format=MarkitdownFormat.docx,
        file_name="plan.docx",
        max_output_bytes=1_000_000,
    )

    assert output.engine == MARKITDOWN_ENGINE
    assert output.engine_version == importlib.metadata.version("markitdown")
    assert output.format is MarkitdownFormat.docx
    assert output.slide_count is None
    assert output.markdown == "# Goals\n\nShip document ingestion for Office formats."


def test_convert_office_bytes_counts_pptx_slides_and_keeps_notes_and_tables() -> None:
    output = markitdown_worker.convert_office_bytes(
        minimal_pptx(),
        format=MarkitdownFormat.pptx,
        file_name="deck.pptx",
        max_output_bytes=1_000_000,
    )

    assert output.slide_count == 2
    assert "<!-- Slide number: 1 -->" in output.markdown
    assert "Speaker note: mention the sidecar pattern" in output.markdown
    assert "| pdf | pdf-inspector |" in output.markdown
    # markitdown's own convert loop collapses blank runs; the direct call must too.
    assert "\n\n\n" not in output.markdown


def test_convert_office_bytes_drops_picture_references_with_filename_alt_text() -> None:
    output = markitdown_worker.convert_office_bytes(
        pptx_with_picture(),
        format=MarkitdownFormat.pptx,
        file_name="deck.pptx",
        max_output_bytes=1_000_000,
    )

    assert output.markdown == "<!-- Slide number: 1 -->\n# Architecture"


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("Before ![Revenue by region](Picture3.jpg) after", "Before Revenue by region after"),
        ("![image.png](Picture2.jpg)", ""),
        ("![](data:image/png;base64,AAAA)", ""),
        ("![ Chart 1.PNG ](x)", ""),
        ("No pictures here", "No pictures here"),
    ],
)
def test_strip_image_references_keeps_only_descriptive_alt_text(
    markdown: str, expected: str
) -> None:
    assert markitdown_worker.strip_image_references(markdown) == expected


def test_convert_office_bytes_enforces_the_output_limit() -> None:
    with pytest.raises(ValueError, match="output byte limit"):
        markitdown_worker.convert_office_bytes(
            minimal_docx(),
            format=MarkitdownFormat.docx,
            file_name="plan.docx",
            max_output_bytes=10,
        )


def test_convert_office_bytes_fails_on_bytes_that_are_not_an_office_archive() -> None:
    with pytest.raises(zipfile.BadZipFile):
        markitdown_worker.convert_office_bytes(
            b"not a zip archive",
            format=MarkitdownFormat.docx,
            file_name="plan.docx",
            max_output_bytes=1_000_000,
        )


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    data: bytes,
    max_output_bytes: int,
) -> tuple[str, list[tuple[str, int]]]:
    applied: list[tuple[str, int]] = []
    monkeypatch.setattr(
        markitdown_worker, "apply_memory_limit", lambda n: applied.append(("memory", n))
    )
    monkeypatch.setattr(markitdown_worker, "apply_cpu_limit", lambda n: applied.append(("cpu", n)))
    monkeypatch.setattr(
        markitdown_worker.sys,
        "argv",
        [
            "markitdown_worker",
            "--format",
            "docx",
            "--file-name",
            "plan.docx",
            "--max-output-bytes",
            str(max_output_bytes),
            "--max-memory-bytes",
            "1024",
            "--cpu-seconds",
            "5",
        ],
    )
    monkeypatch.setattr(markitdown_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
    markitdown_worker.main()
    return capsys.readouterr().out, applied


def test_main_applies_limits_then_writes_one_validated_json_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload, applied = run_main(
        monkeypatch, capsys, data=minimal_docx(), max_output_bytes=1_000_000
    )

    assert applied == [("memory", 1024), ("cpu", 5)]
    output = MarkitdownOutput.model_validate_json(payload, strict=True)
    assert output.format is MarkitdownFormat.docx
    assert json.loads(payload)["markdown"].startswith("# Goals")


def test_main_bounds_the_whole_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The body alone fits under the cap; the JSON envelope around it does not.
    with pytest.raises(ValueError, match="result exceeds"):
        run_main(monkeypatch, capsys, data=minimal_docx(), max_output_bytes=120)


def test_worker_limits_module_is_the_single_owner_of_rlimits() -> None:
    assert markitdown_worker.apply_cpu_limit is worker_limits.apply_cpu_limit
    assert markitdown_worker.apply_memory_limit is worker_limits.apply_memory_limit
