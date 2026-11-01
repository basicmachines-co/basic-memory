"""File conflict detection through the entity service, against a real database."""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models import Project
from basic_memory.repository import EntityRepository
from basic_memory.services import EntityService


async def _add_file(
    entity_repository: EntityRepository,
    session_maker,
    *,
    file_path: str,
    permalink: str | None,
    content_type: str,
) -> None:
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": file_path,
                "note_type": "note" if permalink else "file",
                "permalink": permalink,
                "file_path": file_path,
                "content_type": content_type,
                "created_at": now,
                "updated_at": now,
            },
        )


@pytest.mark.asyncio
async def test_conflicts_use_stored_permalinks_and_generate_only_missing_ones(
    entity_service: EntityService,
    entity_repository: EntityRepository,
    session_maker,
    test_project: Project,
) -> None:
    project_slug = test_project.permalink
    await _add_file(
        entity_repository,
        session_maker,
        file_path="notes/Foo Bar.md",
        permalink=f"{project_slug}/notes/foo-bar",
        content_type="text/markdown",
    )
    await _add_file(
        entity_repository,
        session_maker,
        file_path="custom/Renamed.md",
        permalink=f"{project_slug}/custom/chosen-slug",
        content_type="text/markdown",
    )
    await _add_file(
        entity_repository,
        session_maker,
        file_path="files/My Image.png",
        permalink=None,
        content_type="image/png",
    )

    # A new path whose canonical permalink equals a stored project-qualified permalink.
    assert await entity_service.detect_file_path_conflicts("notes/foo_bar.md") == [
        "notes/Foo Bar.md"
    ]
    # A stored permalink that no longer comes from its file's path (a frontmatter permalink,
    # or one kept after a move) does not block that name for a new file.
    assert await entity_service.detect_file_path_conflicts("custom/chosen_slug.md") == []
    # A file without a stored permalink is compared by its generated permalink, both ways.
    assert await entity_service.detect_file_path_conflicts("files/my_image.png") == [
        "files/My Image.png"
    ]
    assert await entity_service.detect_file_path_conflicts("files/My Image.png") == []
    # Case-only path differences still conflict.
    assert await entity_service.detect_file_path_conflicts("NOTES/foo bar.md") == [
        "notes/Foo Bar.md"
    ]
    assert await entity_service.detect_file_path_conflicts("notes/unrelated.md") == []
