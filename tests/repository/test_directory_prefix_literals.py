"""Directory selection preserves literal SQL metacharacters on both database backends."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.repository.entity_repository import EntityRepository


@pytest.mark.parametrize(
    "directory,sibling",
    [("team_a", "teamXa"), ("team%a", "teamXXa"), ("team/a_b%", "team/axb-more")],
)
async def test_directory_prefix_is_literal(
    entity_repository: EntityRepository,
    session_maker: async_sessionmaker[AsyncSession],
    directory: str,
    sibling: str,
) -> None:
    async with db.scoped_session(session_maker) as session:
        session.add_all(
            Entity(
                project_id=entity_repository.project_id,
                title=path,
                note_type="note",
                content_type="text/markdown",
                file_path=path,
            )
            for path in (
                f"{directory}/note.md",
                f"{directory}/sub/nested.md",
                f"{sibling}/other.md",
            )
        )
    async with db.scoped_session(session_maker) as session:
        selected = await entity_repository.find_by_directory_prefix(session, f"/{directory}/")
        assert sorted(entity.file_path for entity in selected) == [
            f"{directory}/note.md",
            f"{directory}/sub/nested.md",
        ]
