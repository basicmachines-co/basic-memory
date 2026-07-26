"""Repository for note_file_vacate markers (move-orphan gate, basic-memory-cloud#1601)."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models.knowledge import Entity, NoteContent, NoteFileVacate
from basic_memory.repository.repository import Repository


@dataclass(frozen=True, slots=True)
class VacateMarker:
    """The moved entity and source content checksum recorded for a vacated path."""

    entity_id: int | None
    file_checksum: str | None


@dataclass(frozen=True, slots=True)
class RecoverableVacate:
    """A checksum-guarded source cleanup whose destination is safe."""

    entity_id: int | None
    file_path: str
    file_checksum: str
    live_file_path: str | None


class NoteFileVacateRepository(Repository[NoteFileVacate]):
    """Records and resolves the source paths a move vacated but that storage may still hold.

    The marker is the durable evidence that a path was vacated *by a move*, which is what lets the
    indexer skip a move's lingering source object without also suppressing a legitimate
    byte-identical copy. All operations are project-scoped by the base repository.
    """

    def __init__(self, project_id: int) -> None:
        super().__init__(NoteFileVacate, project_id=project_id)

    async def record_vacate(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        file_path: str,
        file_checksum: str | None,
    ) -> None:
        """Record (or refresh) the outstanding vacate for one source path.

        One marker per ``(project, path)``: a fresh move onto the same source path replaces the
        prior marker rather than colliding with the unique constraint. The upsert is one statement
        so concurrent cleanup cannot delete a row between a read and ORM update.
        """
        path = Path(file_path).as_posix()
        values = {
            "project_id": self.project_id,
            "entity_id": entity_id,
            "file_path": path,
            "file_checksum": file_checksum,
        }
        dialect_name = session.bind.dialect.name if session.bind is not None else None
        match dialect_name:
            case "postgresql":
                statement = pg_insert(NoteFileVacate).values(**values)
            case "sqlite":
                statement = sqlite_insert(NoteFileVacate).values(**values)
            case _:
                raise ValueError(
                    f"Unsupported database dialect for move-vacate upsert: {dialect_name}"
                )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    NoteFileVacate.project_id,
                    NoteFileVacate.file_path,
                ],
                set_={
                    "entity_id": entity_id,
                    "file_checksum": file_checksum,
                },
            )
        )

    async def load_vacate_markers(
        self,
        session: AsyncSession,
        file_paths: Sequence[str],
    ) -> dict[str, "VacateMarker"]:
        """Return the vacate marker (moved entity id + source checksum) for each marked path."""
        normalized = [Path(file_path).as_posix() for file_path in file_paths]
        if not normalized:
            return {}
        query = self._add_project_filter(
            select(
                NoteFileVacate.file_path,
                NoteFileVacate.entity_id,
                NoteFileVacate.file_checksum,
            ).where(NoteFileVacate.file_path.in_(normalized))
        )
        result = await session.execute(query)
        return {
            str(path): VacateMarker(
                entity_id=None if entity_id is None else int(entity_id),
                file_checksum=file_checksum,
            )
            for path, entity_id, file_checksum in result.all()
        }

    async def list_recoverable_vacates(
        self,
        session: AsyncSession,
    ) -> list[RecoverableVacate]:
        """Return guarded vacates whose destination is synchronized or deleted.

        Recovery must not remove the source while destination materialization is
        pending or failed: those source bytes may still be the only durable copy.
        A deleted destination has no live entity to protect, while a synchronized
        destination carries the live path needed for case-alias protection.
        """
        query = self._add_project_filter(
            select(
                NoteFileVacate.entity_id,
                NoteFileVacate.file_path,
                NoteFileVacate.file_checksum,
                Entity.file_path,
            )
            .outerjoin(Entity, Entity.id == NoteFileVacate.entity_id)
            .outerjoin(NoteContent, NoteContent.entity_id == Entity.id)
            .where(
                NoteFileVacate.file_checksum.is_not(None),
                or_(
                    Entity.id.is_(None),
                    NoteContent.file_write_status == "synced",
                ),
            )
            .order_by(NoteFileVacate.file_path)
        )
        result = await session.execute(query)
        recoverable: list[RecoverableVacate] = []
        for marker_entity_id, file_path, file_checksum, live_file_path in result.all():
            assert file_checksum is not None
            recoverable.append(
                RecoverableVacate(
                    entity_id=(
                        int(marker_entity_id)
                        if marker_entity_id is not None and live_file_path is not None
                        else None
                    ),
                    file_path=str(file_path),
                    file_checksum=str(file_checksum),
                    live_file_path=(str(live_file_path) if live_file_path is not None else None),
                )
            )
        return recoverable

    async def lock_recoverable_vacate(
        self,
        session: AsyncSession,
        *,
        file_path: str,
        file_checksum: str,
    ) -> RecoverableVacate | None:
        """Lock and reload one recovery candidate immediately before storage cleanup.

        The conditional no-op update locks the marker row on both SQLite and
        Postgres. Locking the destination entity too serializes recovery with an
        accepted move back onto the vacated path. The caller must keep this
        transaction open through guarded storage deletion and marker retirement.
        """
        path = Path(file_path).as_posix()
        await session.execute(
            update(NoteFileVacate)
            .where(
                NoteFileVacate.project_id == self.project_id,
                NoteFileVacate.file_path == path,
                NoteFileVacate.file_checksum == file_checksum,
            )
            .values(file_checksum=NoteFileVacate.file_checksum)
        )
        marker = await self._get_marker(session, path)
        if marker is None or marker.file_checksum != file_checksum:
            return None

        if marker.entity_id is None:
            return RecoverableVacate(
                entity_id=None,
                file_path=path,
                file_checksum=file_checksum,
                live_file_path=None,
            )

        entity = await session.get(Entity, marker.entity_id, with_for_update=True)
        if entity is None or entity.project_id != self.project_id:
            return RecoverableVacate(
                entity_id=None,
                file_path=path,
                file_checksum=file_checksum,
                live_file_path=None,
            )

        note_content = await session.get(NoteContent, entity.id)
        if note_content is None or note_content.file_write_status != "synced":
            return None
        return RecoverableVacate(
            entity_id=entity.id,
            file_path=path,
            file_checksum=file_checksum,
            live_file_path=entity.file_path,
        )

    async def clear_vacate(
        self,
        session: AsyncSession,
        *,
        file_path: str,
        file_checksum: str | None,
    ) -> None:
        """Clear the marker once its source object has actually been deleted.

        The checksum comparison is part of the DELETE statement so a concurrent move cannot
        refresh the same path between a Python-side check and marker deletion. A newer checksum
        represents different vacated content and must remain gated until its own cleanup runs.
        """
        path = Path(file_path).as_posix()
        query = delete(NoteFileVacate).where(
            NoteFileVacate.project_id == self.project_id,
            NoteFileVacate.file_path == path,
            NoteFileVacate.file_checksum == file_checksum,
        )
        await session.execute(query)

    async def clear_vacate_path(
        self,
        session: AsyncSession,
        *,
        file_path: str,
    ) -> None:
        """Clear any marker once this path is successfully materialized as a live note."""
        path = Path(file_path).as_posix()
        query = delete(NoteFileVacate).where(
            NoteFileVacate.project_id == self.project_id,
            NoteFileVacate.file_path == path,
        )
        await session.execute(query)

    async def _get_marker(
        self,
        session: AsyncSession,
        file_path: str,
    ) -> NoteFileVacate | None:
        query = self._add_project_filter(
            select(NoteFileVacate).where(NoteFileVacate.file_path == file_path)
        )
        result = await session.execute(query)
        return result.scalars().one_or_none()
