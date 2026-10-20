"""Read-only, project-scoped counts of note writes that have not reached their file."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.models import NoteContent


@dataclass(frozen=True, slots=True)
class UnsyncedNote:
    """One note whose accepted content is not (yet) its file's content."""

    file_path: str
    error: str | None


@dataclass(frozen=True, slots=True)
class NoteMaterializationHealth:
    """Notes by file-write status, with bounded examples of the ones that are stuck.

    ``in_flight`` counts pending and writing rows, which a running materialization
    finishes. ``failed`` rows are re-driven by startup recovery and by
    ``bm reindex --search``. ``external_change`` rows mean the file on disk changed
    under an accepted write, and nothing retries them.
    """

    in_flight: int
    failed: tuple[UnsyncedNote, ...]
    failed_total: int
    external_change: tuple[UnsyncedNote, ...]
    external_change_total: int


async def inspect_note_materialization(
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
    *,
    sample_limit: int = 20,
) -> NoteMaterializationHealth:
    """Count unsynced note writes for one project without changing any of them."""
    async with session_maker() as session:
        totals = {
            status: int(count)
            for status, count in (
                await session.execute(
                    select(NoteContent.file_write_status, func.count())
                    .where(
                        NoteContent.project_id == project_id,
                        NoteContent.file_write_status != "synced",
                    )
                    .group_by(NoteContent.file_write_status)
                )
            ).all()
        }

        async def examples(status: str) -> tuple[UnsyncedNote, ...]:
            rows = await session.execute(
                select(NoteContent.file_path, NoteContent.last_materialization_error)
                .where(
                    NoteContent.project_id == project_id,
                    NoteContent.file_write_status == status,
                )
                .order_by(NoteContent.file_path)
                .limit(sample_limit)
            )
            return tuple(UnsyncedNote(file_path=path, error=error) for path, error in rows.all())

        return NoteMaterializationHealth(
            in_flight=totals.get("pending", 0) + totals.get("writing", 0),
            failed=await examples("failed"),
            failed_total=totals.get("failed", 0),
            external_change=await examples("external_change_detected"),
            external_change_total=totals.get("external_change_detected", 0),
        )
