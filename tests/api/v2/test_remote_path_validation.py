"""API path checks use storage ownership, not a logical project's host namesake."""

from datetime import UTC, datetime
from pathlib import Path
from typing import override

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.deps import get_file_service_v2_external
from basic_memory.models import Project
from basic_memory.models.knowledge import Entity
from basic_memory.repository import EntityRepository
from basic_memory.services.file_service import FileService
from basic_memory.utils import FilePath


class ObjectStorage(FileService):
    @override
    async def directory_has_files(self, path: FilePath) -> bool:
        return False

    @override
    async def exists(self, path: FilePath) -> bool:
        return True

    @override
    async def read_file_bytes(self, path: FilePath) -> bytes:
        return b"object bytes"


async def test_remote_routes_ignore_escaping_host_symlink(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    root = Path(test_project.path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    storage = ObjectStorage(root, local_filesystem=False)
    app.dependency_overrides[get_file_service_v2_external] = lambda: storage
    now = datetime.now(UTC)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(
            session,
            Entity(
                title="asset.txt",
                note_type="file",
                content_type="text/plain",
                file_path="escape/asset.txt",
                checksum="seeded",
                created_at=now,
                updated_at=now,
            ),
        )
    resource = await client.get(f"{v2_project_url}/resource/{entity.external_id}")
    assert resource.status_code == 200
    assert resource.content == b"object bytes"
    placement = await client.request(
        "QUERY", f"{v2_project_url}/knowledge/placement", json={"identifier": "escape/new.md"}
    )
    assert placement.status_code == 200
    assert placement.json()["directory"] == "escape"
    # A host file must not be imported as cloud content through recovery.
    (outside / "note.md").write_text("# Host-only note")
    for path in ("escape/note.md", "missing.md"):
        response = await client.post(
            f"{v2_project_url}/knowledge/index-file", json={"file_path": path}
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Local file recovery is unavailable"
