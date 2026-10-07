"""Directory moves accept each note's move and move regular files' stored bytes."""

from hashlib import sha256
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, NoteContent, Project


async def test_directory_move_carries_accepted_note_content_and_regular_file_bytes(
    client: AsyncClient,
    test_project: Project,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """The note's accepted content follows it, so nothing can write it back at the old path."""
    _, session_maker = engine_factory
    base = f"/v2/projects/{test_project.external_id}/knowledge"
    created = await client.post(
        f"{base}/write",
        json={"note": {"title": "Plan", "directory": "drafts", "content": "- [a] stays put"}},
    )
    assert created.json()["kind"] == "created", created.text
    home = Path(test_project.path)
    image_bytes = b"\x89PNG\r\n\x1a\nnot really a png"
    (home / "drafts/diagram.png").write_bytes(image_bytes)
    async with db.scoped_session(session_maker) as session:
        session.add(
            Entity(
                project_id=test_project.id,
                title="diagram.png",
                note_type="file",
                content_type="image/png",
                file_path="drafts/diagram.png",
                checksum=sha256(image_bytes).hexdigest(),
            )
        )

    response = await client.post(
        f"{base}/move-directory",
        json={"source_directory": "drafts", "destination_directory": "archive/drafts"},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["failed_moves"] == 0, result["errors"]
    assert sorted(result["moved_files"]) == ["archive/drafts/Plan.md", "archive/drafts/diagram.png"]

    async with db.scoped_session(session_maker) as session:
        note_content = await session.scalar(
            select(NoteContent)
            .join(Entity, Entity.id == NoteContent.entity_id)
            .where(Entity.title == "Plan")
        )
    assert note_content is not None
    # The accepted move recorded a new revision at the new path; before, only the
    # entity moved and note_content kept the old path and revision.
    assert (note_content.file_path, note_content.db_version) == ("archive/drafts/Plan.md", 2)
    assert (home / "archive/drafts/Plan.md").read_text().endswith("- [a] stays put")
    assert not (home / "drafts/Plan.md").exists()
    assert (home / "archive/drafts/diagram.png").read_bytes() == image_bytes
    assert not (home / "drafts/diagram.png").exists()


async def test_directory_move_reports_a_refused_note_and_moves_the_rest(
    client: AsyncClient,
    test_project: Project,
) -> None:
    base = f"/v2/projects/{test_project.external_id}/knowledge"
    for title, directory in (("Clash", "drafts"), ("Fine", "drafts"), ("Clash", "archive")):
        created = await client.post(
            f"{base}/write",
            json={"note": {"title": title, "directory": directory, "content": title}},
        )
        assert created.json()["kind"] == "created", created.text

    response = await client.post(
        f"{base}/move-directory",
        json={"source_directory": "drafts", "destination_directory": "archive"},
    )

    result = response.json()
    assert result["moved_files"] == ["archive/Fine.md"]
    assert [error["path"] for error in result["errors"]] == ["drafts/Clash.md"]
    assert (result["successful_moves"], result["failed_moves"]) == (1, 1)


async def test_directory_move_reports_where_each_note_landed(
    client: AsyncClient,
    test_project: Project,
) -> None:
    """A note can adopt an existing folder's casing, and the result names its real path."""
    base = f"/v2/projects/{test_project.external_id}/knowledge"
    for title, directory in (("Plan", "drafts"), ("Existing", "Archive")):
        created = await client.post(
            f"{base}/write",
            json={"note": {"title": title, "directory": directory, "content": title}},
        )
        assert created.json()["kind"] == "created", created.text

    response = await client.post(
        f"{base}/move-directory",
        json={"source_directory": "drafts", "destination_directory": "archive"},
    )

    result = response.json()
    assert result["moved_files"] == ["Archive/Plan.md"], result
    assert (Path(test_project.path) / "Archive/Plan.md").exists()


async def test_directory_move_keeps_paths_below_a_case_variant_source(
    client: AsyncClient,
    test_project: Project,
    db_backend: str,
) -> None:
    """SQLite matches the source folder in any casing; the path below it must survive."""
    if db_backend != "sqlite":
        pytest.skip("Postgres LIKE is case-sensitive, so a case-variant source matches nothing")
    base = f"/v2/projects/{test_project.external_id}/knowledge"
    created = await client.post(
        f"{base}/write",
        json={"note": {"title": "Plan", "directory": "Drafts/sub", "content": "Plan"}},
    )
    assert created.json()["kind"] == "created", created.text

    response = await client.post(
        f"{base}/move-directory",
        json={"source_directory": "drafts", "destination_directory": "archive"},
    )

    result = response.json()
    assert result["failed_moves"] == 0, result["errors"]
    assert result["moved_files"] == ["archive/sub/Plan.md"]
