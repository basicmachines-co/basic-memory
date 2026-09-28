"""Server-stamped authorship fields in accepted note frontmatter.

`created_by` and `updated_by` name the person or agent behind a note so the
Markdown file itself says who wrote it. Only the runtime boundary that knows the
caller's identity can supply the name; whatever the writer submitted for these
keys is replaced, so the fields cannot be set or spoofed through note content.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Self

from basic_memory.file_utils import ParseError, has_frontmatter, parse_frontmatter
from basic_memory.services.note_preparation import PreparedEntityWrite, rewrite_frontmatter_fields

type NoteAuthor = str

CREATED_BY_FIELD = "created_by"
UPDATED_BY_FIELD = "updated_by"
_MAX_AUTHOR_LENGTH = 120


def normalize_note_author(value: str | None) -> NoteAuthor | None:
    """Return a single-line display name, or None when nothing printable remains.

    Unlike object-storage actor labels, display names stay Unicode: frontmatter
    is UTF-8, and "José Núñez" must not be flattened to ASCII.
    """
    if value is None:
        return None
    # Control and format characters (newlines, zero-width, bidi overrides) would
    # break the one-line YAML value or disguise the name when rendered.
    printable = "".join(" " if unicodedata.category(char)[0] == "C" else char for char in value)
    collapsed = " ".join(printable.split())
    return collapsed[:_MAX_AUTHOR_LENGTH].rstrip() or None


def _current_created_by(current_markdown: str) -> NoteAuthor | None:
    if not has_frontmatter(current_markdown):
        return None
    try:
        value = parse_frontmatter(current_markdown).get(CREATED_BY_FIELD)
    except ParseError:
        return None
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class NoteAuthorship:
    """The authorship values one accepted write must leave in frontmatter."""

    created_by: NoteAuthor | None
    updated_by: NoteAuthor

    @classmethod
    def for_write(cls, author: str | None, *, current_markdown: str | None) -> Self | None:
        """Return the stamp for one write, or None when the caller named no author.

        Trigger: the runtime supplied no author (local runtimes, background writers).
        Why: without an identity there is nothing true to stamp.
        Outcome: authorship keys pass through the write untouched.

        `created_by` is fixed when the note is created (`current_markdown` is None)
        and carried forward afterwards. A note that predates authorship keeps no
        `created_by` rather than crediting whoever edits it next.
        """
        normalized = normalize_note_author(author)
        if normalized is None:
            return None
        if current_markdown is None:
            return cls(created_by=normalized, updated_by=normalized)
        return cls(created_by=_current_created_by(current_markdown), updated_by=normalized)


def _patched_metadata(
    metadata: Mapping[str, Any],
    *,
    updates: Mapping[str, str],
    removals: frozenset[str],
) -> dict[str, Any]:
    patched = {key: value for key, value in metadata.items() if key not in removals}
    patched.update(updates)
    return patched


def stamp_note_authorship(
    prepared: PreparedEntityWrite,
    authorship: NoteAuthorship,
) -> PreparedEntityWrite:
    """Overwrite the authorship keys of a prepared write with server values."""
    # Trigger: the accepted note's frontmatter fence holds something that is not YAML.
    # Why: a malformed block cannot be rewritten field by field without guessing
    #   which bytes the author meant as metadata (#1451).
    # Outcome: the write is accepted without authorship rather than rewritten.
    if prepared.entity_markdown.frontmatter_state == "malformed":
        return prepared

    wanted = {CREATED_BY_FIELD: authorship.created_by, UPDATED_BY_FIELD: authorship.updated_by}
    current = prepared.entity_markdown.frontmatter.metadata
    # Unchanged authorship must not rewrite frontmatter: repeated saves by the same
    # author keep their bytes, so checksums and synced files see no extra churn.
    if all(current.get(key) == value for key, value in wanted.items()):
        return prepared

    updates = {key: value for key, value in wanted.items() if value is not None}
    removals = frozenset(key for key, value in wanted.items() if value is None)
    frontmatter = prepared.entity_markdown.frontmatter
    entity_markdown = prepared.entity_markdown.model_copy(
        update={
            "frontmatter": frontmatter.model_copy(
                update={
                    "metadata": _patched_metadata(
                        frontmatter.metadata, updates=updates, removals=removals
                    )
                }
            )
        }
    )
    entity_metadata = _patched_metadata(
        prepared.entity_fields.entity_metadata or {}, updates=updates, removals=removals
    )
    return replace(
        prepared,
        markdown_content=rewrite_frontmatter_fields(
            prepared.markdown_content, updates=updates, removals=removals
        ),
        entity_fields=replace(prepared.entity_fields, entity_metadata=entity_metadata or None),
        entity_markdown=entity_markdown,
    )
