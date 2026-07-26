"""Repository for note_file_vacate markers (move-orphan gate, basic-memory-cloud#1601)."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models.knowledge import NoteFileVacate
from basic_memory.repository.repository import Repository


@dataclass(frozen=True, slots=True)
class VacateMarker:
    """The moved entity and source content checksum recorded for a vacated path."""

    entity_id: int
    file_checksum: str | None


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
        prior marker rather than colliding with the unique constraint.
        """
        path = Path(file_path).as_posix()
        existing = await self._get_marker(session, path)
        if existing is not None:
            existing.entity_id = entity_id
            existing.file_checksum = file_checksum
            return
        session.add(
            NoteFileVacate(
                project_id=self.project_id,
                entity_id=entity_id,
                file_path=path,
                file_checksum=file_checksum,
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
            str(path): VacateMarker(entity_id=int(entity_id), file_checksum=file_checksum)
            for path, entity_id, file_checksum in result.all()
        }

    async def clear_vacate(
        self,
        session: AsyncSession,
        *,
        file_path: str,
        file_checksum: str,
    ) -> None:
        """Clear the marker once its source object has actually been deleted.

        Guarded by checksum: if a newer move (or a re-created file) replaced the marker with a
        different checksum, leave it — that path is vacated for a *different* content and must stay
        gated until its own delete runs.
        """
        path = Path(file_path).as_posix()
        marker = await self._get_marker(session, path)
        if marker is None:
            return
        if marker.file_checksum is not None and marker.file_checksum != file_checksum:
            return
        await session.delete(marker)

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
