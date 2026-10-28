"""CSV output budgets apply before wide-row padding (#1912)."""

import tracemalloc

import pytest

from basic_memory.document_ingestion.csv_extractor import (
    CsvExtractionError,
    CsvExtractor,
    CsvLimits,
    render_csv_preview,
)


@pytest.mark.asyncio
async def test_wide_header_cannot_amplify_preview_before_budget_check():
    content = b"," * 4999 + b"\n" + b"x\n" * 200
    extractor = CsvExtractor(limits=CsvLimits(max_output_bytes=65536))
    tracemalloc.start()
    try:
        with pytest.raises(CsvExtractionError, match="output byte limit"):
            await extractor.extract(content, file_name="wide.csv")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [b"", b"a,b\nx,y\n", 'é,pipe|\n"a\r\nb",\\\nshort\n'.encode()],
    ids=["empty", "ascii", "escaped-unicode"],
)
async def test_csv_budget_exact_boundary(content: bytes):
    expected = render_csv_preview(content, max_rows=200)
    byte_count = len(expected.markdown.encode("utf-8"))
    exact = CsvExtractor(limits=CsvLimits(max_output_bytes=byte_count))
    result = await exact.extract(content, file_name="note.csv")
    assert result.markdown == expected.markdown
    too_small = CsvExtractor(limits=CsvLimits(max_output_bytes=byte_count - 1))
    with pytest.raises(CsvExtractionError, match="output byte limit"):
        await too_small.extract(content, file_name="note.csv")
