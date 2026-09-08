"""Rooted path relations cannot resolve to semantic aliases."""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models import Entity


@pytest.mark.asyncio
async def test_rooted_path_resolves_only_exact_file(
    link_resolver, entity_repository, test_project, session_maker
):
    now = datetime.now(timezone.utc)
    entity = Entity(
        title="Guide",
        note_type="note",
        content_type="text/markdown",
        file_path="notes/Guide.md",
        permalink="guide",
        created_at=now,
        updated_at=now,
        project_id=test_project.id,
    )
    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(session, entity)
    resolved = await link_resolver.resolve_link("/notes/Guide.md")
    assert resolved is not None
    assert resolved.id == entity.id
    assert await link_resolver.resolve_link("/guide") is None
    assert await link_resolver.resolve_link("/notes/guide.md") is None
