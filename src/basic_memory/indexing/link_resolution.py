"""Portable exact link-text resolution for indexing follow-up passes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.runtime.contracts import RUNTIME_MARKDOWN_FILE_SUFFIXES

type LinkText = str


@dataclass(frozen=True, slots=True)
class LinkResolutionTarget:
    """Entity fields needed to resolve one link target exactly."""

    entity_id: int | None
    permalink: str | None
    title: str | None
    file_path: str | None


def normalize_link_text(link_text: LinkText) -> LinkText:
    """Normalize a wiki-style link into its lookup key."""
    clean = link_text.strip()
    if clean.startswith("[[") and clean.endswith("]]"):
        clean = clean[2:-2]
    if "|" in clean:
        clean = clean.split("|", 1)[0].strip()
    return clean.lower()


def resolve_link_texts(
    link_texts: Sequence[LinkText],
    targets: Iterable[LinkResolutionTarget],
) -> dict[LinkText, int | None]:
    """Resolve link texts by permalink, title, and file path."""
    by_permalink: dict[str, int] = {}
    by_title: dict[str, int] = {}
    by_file_path: dict[str, int] = {}

    for target in targets:
        if target.entity_id is None:
            continue
        if target.permalink:
            by_permalink[target.permalink.lower()] = target.entity_id
        if target.title:
            title_lower = target.title.lower()
            if title_lower not in by_title:
                by_title[title_lower] = target.entity_id
        if target.file_path:
            file_path_lower = target.file_path.lower()
            by_file_path[file_path_lower] = target.entity_id
            for markdown_suffix in RUNTIME_MARKDOWN_FILE_SUFFIXES:
                if file_path_lower.endswith(markdown_suffix):
                    by_file_path[file_path_lower[: -len(markdown_suffix)]] = target.entity_id
                    break

    resolved: dict[LinkText, int | None] = {}
    for original_link_text in link_texts:
        clean_lower = normalize_link_text(original_link_text)
        entity_id: int | None = None
        if clean_lower in by_permalink:
            entity_id = by_permalink[clean_lower]
        elif clean_lower in by_title:
            entity_id = by_title[clean_lower]
        elif clean_lower in by_file_path:
            entity_id = by_file_path[clean_lower]

        resolved[original_link_text] = entity_id

    return resolved


async def resolve_project_link_texts(
    link_texts: Sequence[LinkText],
    *,
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
) -> dict[LinkText, int | None]:
    """Resolve link texts against all exact targets in one project."""
    if not link_texts:
        return {}

    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            select(
                Entity.id,
                Entity.permalink,
                Entity.title,
                Entity.file_path,
            ).where(Entity.project_id == project_id)
        )
        rows = result.all()

    return resolve_link_texts(
        link_texts,
        (
            LinkResolutionTarget(
                entity_id=row[0],
                permalink=row[1],
                title=row[2],
                file_path=row[3],
            )
            for row in rows
        ),
    )
