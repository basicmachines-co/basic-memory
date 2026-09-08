"""Tests for the in-process CSV preview extractor."""

from __future__ import annotations

import csv

import pytest

from basic_memory import __version__ as basic_memory_version
from basic_memory.document_ingestion.csv_extractor import (
    CSV_ENGINE,
    CSV_EXTRACTION_PROFILE,
    CSV_RAW_PIPELINE_VERSION,
    CsvDecodeError,
    CsvExtractionError,
    CsvExtractor,
    CsvLimits,
    CsvSourceTooLargeError,
    render_csv_preview,
)
from basic_memory.schemas.document import DocumentExtractionStatus


def test_render_csv_preview_escapes_cells_and_reports_the_row_count() -> None:
    content = b'\xef\xbb\xbfteam,name\nAST,Ana R\xc3\xados\nSAX,"Lee, Kim"\nSAX,Pat|Quinn\n'

    preview = render_csv_preview(content, max_rows=200)

    assert preview.row_count == 3
    assert preview.shown_rows == 3
    assert preview.markdown == (
        "| team | name |\n"
        "| --- | --- |\n"
        "| AST | Ana Ríos |\n"
        "| SAX | Lee, Kim |\n"
        "| SAX | Pat\\|Quinn |\n"
        "\n"
        "_3 rows._\n"
    )


def test_render_csv_preview_escapes_backslashes_before_pipes() -> None:
    preview = render_csv_preview(b"path\nC:\\dir\\file\na\\|b\n", max_rows=200)

    assert "| C:\\\\dir\\\\file |" in preview.markdown
    assert "| a\\\\\\|b |" in preview.markdown


def test_render_csv_preview_truncates_to_max_rows_but_counts_every_row() -> None:
    content = b"n\n" + b"".join(f"{index}\n".encode() for index in range(10))

    preview = render_csv_preview(content, max_rows=3)

    assert preview.row_count == 10
    assert preview.shown_rows == 3
    assert preview.markdown.endswith("_Showing 3 of 10 rows._\n")
    assert "| 3 |" not in preview.markdown


def test_render_csv_preview_pads_and_trims_ragged_rows() -> None:
    preview = render_csv_preview(b"a,b,c\n1\n1,2,3,4\n", max_rows=200)

    assert "| 1 |  |  |" in preview.markdown
    assert "| 1 | 2 | 3 |" in preview.markdown
    assert "| 4 |" not in preview.markdown


def test_render_csv_preview_flattens_multiline_cells() -> None:
    preview = render_csv_preview(b'note\n"line one\nline two"\n', max_rows=200)

    assert "| line one line two |" in preview.markdown


def test_render_csv_preview_flattens_crlf_and_lone_cr_inside_cells() -> None:
    preview = render_csv_preview(b'note\r\n"line one\r\nline two\rline three"\r\n', max_rows=200)

    assert "| line one line two line three |" in preview.markdown
    assert "\r" not in preview.markdown


def test_render_csv_preview_accepts_fields_larger_than_the_stdlib_default() -> None:
    wide = b"x" * 200_000
    preview = render_csv_preview(b"blob\n" + wide + b"\n", max_rows=200)

    assert preview.row_count == 1
    assert wide.decode() in preview.markdown


def test_render_csv_preview_names_a_parser_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # The lenient reader only fails on a field above the process-wide size limit,
    # which a bounded source cannot produce under default limits; drive the
    # translation directly. Real readers fail while iterating, not on construction.
    class FailingReader:
        def __iter__(self) -> FailingReader:
            return self

        def __next__(self) -> list[str]:
            raise csv.Error("field larger than field limit (131072)")

    monkeypatch.setattr(csv, "reader", lambda *args, **kwargs: FailingReader())

    with pytest.raises(CsvExtractionError, match="could not be parsed: field larger"):
        render_csv_preview(b"a,b\n1,2\n", max_rows=200)


def test_render_csv_preview_only_raises_the_field_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def record_limit(new_limit: int | None = None) -> int:
        if new_limit is not None:
            seen.append(new_limit)
        return 10**9

    monkeypatch.setattr(csv, "field_size_limit", record_limit)

    render_csv_preview(b"a\n1\n", max_rows=200, max_field_bytes=1024)

    assert seen == []


def test_render_csv_preview_names_an_empty_file() -> None:
    preview = render_csv_preview(b"", max_rows=200)

    assert preview.markdown == "_Empty CSV file._\n"
    assert preview.row_count == 0


def test_render_csv_preview_rejects_non_utf8_instead_of_guessing() -> None:
    with pytest.raises(CsvDecodeError, match="not UTF-8"):
        render_csv_preview(b"name\nJos\xe9\n", max_rows=200)


@pytest.mark.asyncio
async def test_csv_extractor_fills_the_neutral_extraction_contract() -> None:
    extractor = CsvExtractor(limits=CsvLimits(max_rows=5))

    extracted = await extractor.extract(b"team,name\nAST,Ana\n", file_name="riders.csv")

    assert extracted.kind == "csv"
    assert extracted.pipeline_version == CSV_RAW_PIPELINE_VERSION
    assert extracted.markdown.startswith("| team | name |\n")
    extraction = extracted.extraction
    assert extraction.engine == CSV_ENGINE
    assert extraction.engine_version == basic_memory_version
    assert extraction.profile == CSV_EXTRACTION_PROFILE
    assert extraction.status is DocumentExtractionStatus.complete
    assert extraction.page_count == 0
    assert extraction.has_tables is True
    assert extraction.classification == "csv"


@pytest.mark.asyncio
async def test_csv_extractor_options_hash_tracks_the_row_cap() -> None:
    small = await CsvExtractor(limits=CsvLimits(max_rows=5)).extract(b"a\n1\n", file_name="x.csv")
    large = await CsvExtractor(limits=CsvLimits(max_rows=50)).extract(b"a\n1\n", file_name="x.csv")

    assert small.extraction.options_hash != large.extraction.options_hash


@pytest.mark.asyncio
async def test_csv_extractor_rejects_an_oversized_source_before_parsing() -> None:
    extractor = CsvExtractor(limits=CsvLimits(max_source_bytes=4))

    with pytest.raises(CsvSourceTooLargeError):
        await extractor.extract(b"a,b,c\n", file_name="x.csv")


@pytest.mark.asyncio
async def test_csv_extractor_rejects_a_preview_over_the_output_limit() -> None:
    extractor = CsvExtractor(limits=CsvLimits(max_output_bytes=8))

    with pytest.raises(CsvExtractionError, match="output byte limit"):
        await extractor.extract(b"a,b\n1,2\n", file_name="x.csv")
