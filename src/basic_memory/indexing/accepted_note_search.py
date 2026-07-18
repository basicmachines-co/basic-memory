"""Accepted-note search helpers shared by DB-first note writers."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from basic_memory.file_utils import ParseError, remove_frontmatter
from basic_memory.repository.accepted_note_search_row import AcceptedNoteSearchRow

MAX_ACCEPTED_SEARCH_CONTENT_STEMS_SIZE = 6000


def strip_search_text(value: str | None) -> str:
    """Strip NUL bytes that PostgreSQL text columns cannot store."""
    return (value or "").replace("\x00", "")


def first_markdown_h1(markdown_content: str) -> str | None:
    """Return the first top-level markdown heading title outside fenced code."""
    in_fenced_code = False
    for line in markdown_content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fenced_code = not in_fenced_code
            continue
        if in_fenced_code:
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip()
            return title or None
    return None


def accepted_search_content_from_markdown(markdown_content: str) -> str:
    """Extract note body text for hot search without making moves parse-strict."""
    try:
        return remove_frontmatter(markdown_content)
    except ParseError:
        # DB-only moves should not fail because legacy accepted content has an
        # unterminated frontmatter marker. The async index/repair path still owns
        # full markdown normalization; this hot row only needs searchable text.
        return markdown_content


def search_text_variants(text_value: str | None) -> set[str]:
    """Generate compact text variants for a hot entity search row."""
    if not text_value:
        return set()

    variants = {text_value, text_value.lower()}
    if "/" in text_value:
        variants.update(part.strip() for part in text_value.split("/") if part.strip())
    variants.update(word.strip() for word in text_value.lower().split() if word.strip())
    return variants


def accepted_note_tags(metadata: Mapping[str, object] | None) -> tuple[str, ...]:
    """Extract frontmatter tags for a hot entity search row."""
    metadata = metadata or {}
    tags = metadata.get("tags")
    if isinstance(tags, list):
        return tuple(str(tag) for tag in tags if tag)
    if isinstance(tags, str):
        try:
            parsed_tags = ast.literal_eval(tags)
        except (ValueError, SyntaxError):
            return (tags,) if tags.strip() else ()
        if isinstance(parsed_tags, list):
            return tuple(str(tag) for tag in parsed_tags if tag)
    return ()


def accepted_note_content_stems(
    *,
    title: str | None,
    search_content: str,
    permalink: str | None,
    file_path: str | None,
    tags: Iterable[str] = (),
) -> str:
    """Build the entity-level content_stems value from accepted DB state."""
    content_stems: list[str] = []
    content_stems.extend(search_text_variants(title))
    if search_content:
        content_stems.append(search_content)
    content_stems.extend(search_text_variants(permalink))
    content_stems.extend(search_text_variants(file_path))
    content_stems.extend(tags)

    stems = strip_search_text("\n".join(part for part in content_stems if part and part.strip()))
    if len(stems) > MAX_ACCEPTED_SEARCH_CONTENT_STEMS_SIZE:
        return stems[:MAX_ACCEPTED_SEARCH_CONTENT_STEMS_SIZE]
    return stems


def build_accepted_note_search_row(
    *,
    entity_id: int,
    title: str | None,
    note_type: str | None,
    entity_metadata: Mapping[str, object] | None,
    permalink: str | None,
    file_path: str,
    search_content: str,
    created_at: datetime,
    updated_at: datetime,
    project_id: int,
    item_type: str = "entity",
) -> AcceptedNoteSearchRow:
    """Build the hot entity search row for one accepted note snapshot."""
    return AcceptedNoteSearchRow(
        id=entity_id,
        title=strip_search_text(title),
        content_stems=accepted_note_content_stems(
            title=title,
            search_content=search_content,
            permalink=permalink,
            file_path=file_path,
            tags=accepted_note_tags(entity_metadata),
        ),
        content_snippet=strip_search_text(search_content),
        permalink=permalink,
        file_path=Path(file_path).as_posix(),
        item_type=item_type,
        note_type=note_type,
        entity_id=entity_id,
        created_at=created_at,
        updated_at=updated_at,
        project_id=project_id,
    )
