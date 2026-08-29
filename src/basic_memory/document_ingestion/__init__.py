"""Portable document ingestion: bounded PDF extraction and raw document mapping.

This package is deliberately light to import. The ``pdf_inspector`` native
library is imported only by the worker subprocess, so ``basic-memory[pdf]``
stays optional for code that merely references the contracts.
"""

from basic_memory.document_ingestion.pdf_inspector import (
    PDF_INSPECTOR_ENGINE,
    PdfInspector,
    PdfInspectorError,
    PdfInspectorLimits,
    PdfInspectorOutput,
    PdfInspectorPdfType,
    PdfInspectorProcessError,
    PdfInspectorSourceTooLargeError,
    PdfInspectorTimeoutError,
)

__all__ = [
    "PDF_INSPECTOR_ENGINE",
    "PdfInspector",
    "PdfInspectorError",
    "PdfInspectorLimits",
    "PdfInspectorOutput",
    "PdfInspectorPdfType",
    "PdfInspectorProcessError",
    "PdfInspectorSourceTooLargeError",
    "PdfInspectorTimeoutError",
]
