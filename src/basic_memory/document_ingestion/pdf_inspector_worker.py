"""One-shot native PDF extraction process used by the async adapter.

Runs as ``python -m basic_memory.document_ingestion.pdf_inspector_worker`` with
PDF bytes on stdin and one validated JSON ``PdfInspectorOutput`` on stdout.
Resource limits are applied before any source bytes are read so a malformed
PDF cannot exhaust the parent process.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import resource
import sys

import pdf_inspector

from basic_memory.document_ingestion.pdf_inspector import (
    PDF_INSPECTOR_ENGINE,
    PdfInspectorOutput,
    PdfInspectorPdfType,
)


def _normalized_pages(pages: list[int], *, page_count: int) -> tuple[int, ...]:
    """Validate pdf-inspector's document-level, one-indexed diagnostics."""
    unique_pages = tuple(sorted(set(pages)))
    if any(page < 1 or page > page_count for page in unique_pages):
        raise ValueError("pdf-inspector returned a diagnostic page outside the document")
    return unique_pages


def inspect_pdf_bytes(
    pdf_bytes: bytes,
    *,
    max_pages: int,
    max_output_bytes: int,
) -> PdfInspectorOutput:
    """Run classification and extraction in the isolated child process."""
    # The installed library version is part of the deterministic run identity:
    # upgrading pdf-inspector yields a new ingestion run rather than silently
    # rewriting an existing document's provenance.
    engine_version = importlib.metadata.version("pdf-inspector")

    classification = pdf_inspector.classify_pdf_bytes(pdf_bytes)
    if classification.page_count > max_pages:
        raise ValueError("PDF exceeds the configured extraction page limit")

    result = pdf_inspector.process_pdf_bytes(pdf_bytes)
    if result.page_count != classification.page_count:
        raise RuntimeError("pdf-inspector classification and extraction page counts differ")

    page_result = pdf_inspector.extract_pages_markdown_bytes(pdf_bytes)
    if len(page_result.pages) != result.page_count:
        raise RuntimeError("pdf-inspector omitted pages from the full-document extraction")

    markdown_parts: list[str] = []
    for page in page_result.pages:
        page_number = page.page + 1
        if page.needs_ocr:
            markdown_parts.append(f"<!-- Page {page_number}: OCR required -->")
            continue
        markdown_parts.append(f"<!-- Page {page_number} -->\n\n{page.markdown.strip()}")
    markdown = "\n\n".join(markdown_parts).strip()
    if len(markdown.encode("utf-8")) > max_output_bytes:
        raise ValueError("PDF extraction exceeds the configured output byte limit")

    pages_needing_ocr = _normalized_pages(
        page_result.pages_needing_ocr,
        page_count=result.page_count,
    )
    return PdfInspectorOutput(
        engine=PDF_INSPECTOR_ENGINE,
        engine_version=engine_version,
        pdf_type=PdfInspectorPdfType(result.pdf_type),
        markdown=markdown,
        title=result.title,
        page_count=result.page_count,
        extracted_page_count=sum(not page.needs_ocr for page in page_result.pages),
        pages_needing_ocr=pages_needing_ocr,
        confidence=result.confidence,
        processing_time_ms=result.processing_time_ms,
        is_complex_layout=page_result.is_complex,
        pages_with_tables=_normalized_pages(
            page_result.pages_with_tables,
            page_count=result.page_count,
        ),
        pages_with_columns=_normalized_pages(
            page_result.pages_with_columns,
            page_count=result.page_count,
        ),
        has_encoding_issues=result.has_encoding_issues,
    )


def _apply_cpu_limit(cpu_seconds: int) -> None:
    """Bound native CPU time so a malformed PDF cannot monopolize a worker."""
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))


def _apply_memory_limit(max_memory_bytes: int) -> None:
    """Bound parser address space on Linux before reading source bytes."""
    if sys.platform != "linux":
        # RLIMIT_AS is the deployed Linux isolation boundary. Applying the same
        # byte ceiling on macOS constrains its much larger virtual mappings and
        # makes the local subprocess fail before native parsing begins.
        return
    resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, required=True)
    parser.add_argument("--max-output-bytes", type=int, required=True)
    parser.add_argument("--max-memory-bytes", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    """Read PDF bytes from stdin and emit one validated JSON result to stdout."""
    args = _parse_args()
    _apply_memory_limit(args.max_memory_bytes)
    _apply_cpu_limit(args.cpu_seconds)
    result = inspect_pdf_bytes(
        sys.stdin.buffer.read(),
        max_pages=args.max_pages,
        max_output_bytes=args.max_output_bytes,
    )
    sys.stdout.write(result.model_dump_json())


if __name__ == "__main__":
    main()
