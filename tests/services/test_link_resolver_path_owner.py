"""Strict file ownership must not change verbatim custom permalink resolution."""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models.knowledge import Entity
from basic_memory.services.link_resolver import LinkResolver


@pytest.mark.asyncio
async def test_literal_markdown_permalink_keeps_precedence(
    entity_repository, search_service, session_maker, app_config
):
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        for title, path, permalink in [
            ("Custom identity", "archive/original.md", "notes/current.md"),
            ("File owner", "notes/current.md", "notes/replacement"),
        ]:
            await entity_repository.add(
                session,
                Entity(
                    title=title,
                    note_type="note",
                    content_type="text/markdown",
                    file_path=path,
                    permalink=permalink,
                    created_at=now,
                    updated_at=now,
                    project_id=entity_repository.project_id,
                ),
            )
    resolver = LinkResolver(entity_repository, search_service, session_maker, app_config)
    result = await resolver.resolve_link("notes/current.md", strict=True)
    assert result is not None
    assert result.file_path == "archive/original.md"
