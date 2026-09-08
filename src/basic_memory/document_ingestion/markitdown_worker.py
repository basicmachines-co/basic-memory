"""One-shot markitdown conversion process used by the async adapter.

Runs as ``python -m basic_memory.document_ingestion.markitdown_worker`` with the
Office file bytes on stdin and one validated JSON ``MarkitdownOutput`` on stdout.
Resource limits are applied before any source bytes are read so a malformed
archive cannot exhaust the parent process.

The worker calls the converter for the requested format directly. That skips
markitdown's content sniffing and its try-every-converter loop, which is where
the plain-text fallthrough and the ASCII-decode crash live.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import io
import re
import sys
import time
from typing import assert_never

from markitdown import DocumentConverter, StreamInfo
from markitdown.converters import DocxConverter, PptxConverter

from basic_memory.document_ingestion.markitdown_extractor import (
    MEDIA_TYPES_BY_MARKITDOWN_FORMAT,
    MarkitdownFormat,
    MarkitdownOutput,
)
from basic_memory.document_ingestion.worker_limits import apply_cpu_limit, apply_memory_limit

SLIDE_MARKER = "<!-- Slide number:"
_IMAGE_REFERENCE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_IMAGE_FILENAME = re.compile(r"^[\w .-]*\.(?:png|jpe?g|gif|bmp|tiff?|svg|emf|wmf)$", re.IGNORECASE)


def strip_image_references(markdown: str) -> str:
    """Drop picture links, keeping alt text only when it describes the picture.

    markitdown renders pictures as ``![alt](Picture2.jpg)`` or a data URI. Neither
    target exists from the sidecar note, so the link is always dropped. Office
    files rarely carry real alt text; when the alt is just the embedded file
    name (``image.png``) it says nothing about the content and is dropped too.
    """

    def replacement(match: re.Match[str]) -> str:
        alt_text = match.group(1).strip()
        if not alt_text or _IMAGE_FILENAME.match(alt_text):
            return ""
        return alt_text

    return _IMAGE_REFERENCE.sub(replacement, markdown)


def convert_office_bytes(
    data: bytes,
    *,
    format: MarkitdownFormat,
    file_name: str,
    max_output_bytes: int,
) -> MarkitdownOutput:
    """Run one specific markitdown converter in the isolated child process."""
    # The installed library version is part of the deterministic run identity:
    # upgrading markitdown yields a new ingestion run rather than silently
    # rewriting an existing document's provenance.
    engine_version = importlib.metadata.version("markitdown")
    started = time.perf_counter()

    converter: DocumentConverter
    match format:
        case MarkitdownFormat.docx:
            converter = DocxConverter()
        case MarkitdownFormat.pptx:
            converter = PptxConverter()
        case _:  # pragma: no cover - exhaustive over the closed enum
            assert_never(format)

    stream_info = StreamInfo(
        mimetype=MEDIA_TYPES_BY_MARKITDOWN_FORMAT[format],
        extension=f".{format.value}",
        filename=file_name,
    )
    result = converter.convert(io.BytesIO(data), stream_info)
    markdown = strip_image_references(result.markdown)
    # ``MarkItDown._convert`` normalizes whitespace after every converter; calling
    # the converter directly skips that step, so apply the same two rules here.
    markdown = "\n".join(line.rstrip() for line in re.split(r"\r?\n", markdown))
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    if len(markdown.encode("utf-8")) > max_output_bytes:
        raise ValueError("Office document extraction exceeds the configured output byte limit")

    return MarkitdownOutput(
        engine_version=engine_version,
        format=format,
        markdown=markdown,
        processing_time_ms=int((time.perf_counter() - started) * 1000),
        slide_count=markdown.count(SLIDE_MARKER) if format is MarkitdownFormat.pptx else None,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", type=MarkitdownFormat, required=True)
    parser.add_argument("--file-name", required=True)
    parser.add_argument("--max-output-bytes", type=int, required=True)
    parser.add_argument("--max-memory-bytes", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    """Read Office bytes from stdin and emit one validated JSON result to stdout."""
    args = _parse_args()
    apply_memory_limit(args.max_memory_bytes)
    apply_cpu_limit(args.cpu_seconds)
    result = convert_office_bytes(
        sys.stdin.buffer.read(),
        format=args.format,
        file_name=args.file_name,
        max_output_bytes=args.max_output_bytes,
    )
    payload = result.model_dump_json()
    # convert_office_bytes bounds the Markdown body; bound the whole envelope
    # before it crosses the pipe as well.
    if len(payload.encode("utf-8")) > args.max_output_bytes:
        raise ValueError(
            "Office document extraction result exceeds the configured output byte limit"
        )
    sys.stdout.write(payload)


if __name__ == "__main__":
    main()
