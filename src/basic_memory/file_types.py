"""Host-independent file types: extension to MIME type, and MIME type to format.

Both tables live here so they cannot drift: every MIME type the extension table
can assign has a format name in the format table.

``file_content_type`` decides the MIME type an indexed file is stored with.
Python's ``mimetypes`` reads the host's ``/etc/mime.types`` on top of its
built-in table, and slim container images ship no such file, so the same
``.docx`` upload could get a MIME type on a laptop and none in production. That
breaks extractor dispatch (keyed by media type) and the stored format. Every
type Basic Memory supports or extracts is therefore pinned in an explicit table,
and ``mimetypes`` is consulted only for extensions outside it.

``file_entity_metadata`` builds what a non-Markdown file entity stores in
``entity_metadata`` at index time, so clients read the format from the index
instead of guessing it from the extension at display time. Its keys are
deliberately ``content_type`` and ``format``: Markdown readers give ``title``,
``type``, ``permalink``, ``tags``, ``schema``, ``entity``, and ``embed`` meaning,
so file metadata must never use them.
"""

import mimetypes
import re
from collections.abc import Mapping
from pathlib import PurePath
from typing import NotRequired, TypedDict

DEFAULT_CONTENT_TYPE = "text/plain"

# --- Extension -> MIME type ---

# Keys are lowercase suffixes. Values are the standard registrations, which is
# what a full host MIME database returns. On a host without one (Python 3.12's
# built-in table), .docx, .xlsx, .pptx, and .webp previously fell to text/plain.
CONTENT_TYPE_BY_EXTENSION: Mapping[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    # Obsidian canvas files are JSON documents.
    ".canvas": "application/json",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".zip": "application/zip",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


def guess_content_type(path: str | PurePath) -> str | None:
    """Return the MIME type for a file name, or None when nothing knows it.

    The explicit table wins so supported types never depend on the host;
    ``mimetypes`` covers everything else.
    """
    pure_path = PurePath(path)
    if pinned := CONTENT_TYPE_BY_EXTENSION.get(pure_path.suffix.lower()):
        return pinned
    guessed, _ = mimetypes.guess_type(pure_path.name)
    return guessed


def file_content_type(path: str | PurePath) -> str:
    """Return the MIME type a file is stored and indexed with (``text/plain`` if unknown)."""
    return guess_content_type(path) or DEFAULT_CONTENT_TYPE


# --- MIME type -> format ---


class FileEntityMetadata(TypedDict):
    """The ``entity_metadata`` shape of a non-Markdown file entity."""

    content_type: str
    format: NotRequired[str]


# One closed table from stored MIME type to short lowercase format name. It
# covers the types whose MIME subtype is not itself a readable format name
# (vendor trees, "+xml" suffixes, "x-" prefixes) and the common types we name
# explicitly so their spelling is fixed here rather than by a MIME registry.
FILE_FORMAT_BY_CONTENT_TYPE: Mapping[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/vnd.microsoft.icon": "ico",
    "image/x-icon": "ico",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/csv": "csv",
    "text/tab-separated-values": "tsv",
    "application/json": "json",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/html": "html",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "application/gzip": "gz",
    "application/x-tar": "tar",
    "audio/mpeg": "mp3",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
}

# A subtype qualifies as a format name only when it is one plain lowercase word,
# such as "heic" or "mp4". Hyphens, dots, and plus signs mark vendor trees,
# suffixes, or descriptive names ("octet-stream", "vnd.foo", "ld+json") that are
# not format names, so those types get no format rather than an invented one.
_SIMPLE_SUBTYPE = re.compile(r"[a-z0-9]+")


def file_format(content_type: str) -> str | None:
    """Return the short format name for a stored MIME type, or None when it has none.

    The table wins. Otherwise a simple-token subtype is the format; anything else
    has no format.
    """
    mime_type = content_type.split(";", 1)[0].strip().lower()
    if known := FILE_FORMAT_BY_CONTENT_TYPE.get(mime_type):
        return known
    _, separator, subtype = mime_type.partition("/")
    if separator and _SIMPLE_SUBTYPE.fullmatch(subtype):
        return subtype
    return None


def file_entity_metadata(content_type: str) -> FileEntityMetadata:
    """Build the metadata stored on a non-Markdown file entity at index time."""
    metadata: FileEntityMetadata = {"content_type": content_type}
    if (format_name := file_format(content_type)) is not None:
        metadata["format"] = format_name
    return metadata
