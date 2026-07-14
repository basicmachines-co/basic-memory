"""Repository for managing note materialization state."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import Entity, NoteContent
from basic_memory.repository.repository import Repository


class NoteContentVersionConflict(Exception):
    """An accepted note_content write lost an optimistic-concurrency race.

    Raised when a write planned as ``prior_db_version + 1`` cannot land because a
    concurrent accepted write already advanced the row's ``db_version``. The
    caller surfaces this as a 409 so the loser retries against fresh state rather
    than silently clobbering the winner (last-write-wins).
    """

NOTE_CONTENT_MUTABLE_FIELDS = frozenset(
    {
        "markdown_content",
        "db_version",
        "db_checksum",
        "file_version",
        "file_checksum",
        "file_write_status",
        "last_source",
        "updated_at",
        "file_updated_at",
        "last_materialization_error",
        "last_materialization_attempt_at",
    }
)


@dataclass(frozen=True, slots=True)
class AcceptedNoteContentWrite:
    """DB-accepted note_content snapshot before file materialization catches up."""

    entity_id: int
    markdown_content: str
    db_version: int
    db_checksum: str
    last_source: str | None
    updated_at: datetime


class NoteContentRepository(Repository[NoteContent]):
    """Repository for project-scoped note materialization state."""

    def __init__(self, project_id: int):
        """Initialize with project-scoped filtering."""
        super().__init__(NoteContent, project_id=project_id)

    def _coerce_note_content(
        self, data: Mapping[str, Any] | NoteContent
    ) -> tuple[NoteContent, set[str]]:
        """Convert input data to a NoteContent model and track explicit fields."""
        if isinstance(data, NoteContent):
            model_data = {
                key: value for key, value in data.__dict__.items() if key in self.valid_columns
            }
        else:
            model_data = {key: value for key, value in data.items() if key in self.valid_columns}

        entity_id = model_data.get("entity_id")
        if entity_id is None:
            raise ValueError("entity_id is required for note_content writes")

        return NoteContent(**model_data), set(model_data)

    async def _load_entity_identity(self, session: AsyncSession, entity_id: int) -> Entity:
        """Load the owning entity so duplicated identity fields stay aligned."""
        result = await session.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
        if entity is None:
            raise ValueError(f"Entity {entity_id} does not exist")

        if self.project_id is not None and entity.project_id != self.project_id:
            raise ValueError(
                f"Entity {entity_id} belongs to project {entity.project_id}, "
                f"not repository project {self.project_id}"
            )

        return entity

    async def _align_identity_fields(
        self, session: AsyncSession, note_content: NoteContent
    ) -> None:
        """Mirror project identity from entity before persisting note content."""
        entity = await self._load_entity_identity(session, note_content.entity_id)
        note_content.project_id = entity.project_id
        note_content.external_id = entity.external_id
        note_content.file_path = Path(entity.file_path).as_posix()

    async def get_by_entity_id(
        self, session: AsyncSession, entity_id: int
    ) -> Optional[NoteContent]:
        """Get note content by the owning entity identifier."""
        return await self.find_by_id(session, entity_id)

    async def get_by_external_id(
        self, session: AsyncSession, external_id: str
    ) -> Optional[NoteContent]:
        """Get note content by the mirrored entity external identifier."""
        query = self.select().where(NoteContent.external_id == external_id)
        return await self.find_one(session, query)

    async def get_by_file_path(
        self, session: AsyncSession, file_path: Path | str
    ) -> Optional[NoteContent]:
        """Get note content by file path, preferring rows whose entity still owns that path."""
        normalized_path = Path(file_path).as_posix()

        # Trigger: note_content mirrors entity.file_path but does not enforce project-level uniqueness.
        # Why: entity renames can leave stale mirrored paths behind until note_content realigns.
        # Outcome: prefer the row whose current entity path still matches, then the newest mirror.
        query = (
            self.select()
            .join(Entity, Entity.id == NoteContent.entity_id)
            .where(NoteContent.file_path == normalized_path)
            .order_by(
                (Entity.file_path == normalized_path).desc(),
                NoteContent.updated_at.desc(),
                NoteContent.entity_id.desc(),
            )
            .limit(1)
            .options(*self.get_load_options())
        )

        result = await session.execute(query)
        return result.scalars().first()

    async def create(
        self, session: AsyncSession, data: Mapping[str, Any] | NoteContent
    ) -> NoteContent:
        """Create a note_content row aligned to its owning entity."""
        note_content, _ = self._coerce_note_content(data)

        await self._align_identity_fields(session, note_content)
        session.add(note_content)
        await session.flush()

        created = await self.select_by_id(session, note_content.entity_id)
        if created is None:  # pragma: no cover
            raise ValueError(
                f"Can't find NoteContent for entity {note_content.entity_id} after add"
            )
        return created

    async def upsert(
        self, session: AsyncSession, data: Mapping[str, Any] | NoteContent
    ) -> NoteContent:
        """Insert or update note_content while keeping mirrored identity fields in sync."""
        note_content, provided_fields = self._coerce_note_content(data)

        await self._align_identity_fields(session, note_content)
        existing = await self.select_by_id(session, note_content.entity_id)

        if existing is None:
            session.add(note_content)
            await session.flush()
            created = await self.select_by_id(session, note_content.entity_id)
            if created is None:  # pragma: no cover
                raise ValueError(
                    f"Can't find NoteContent for entity {note_content.entity_id} after upsert"
                )
            return created

        fields_to_update = (provided_fields - {"entity_id"}) | {
            "project_id",
            "external_id",
            "file_path",
        }
        for column_name in fields_to_update:
            setattr(existing, column_name, getattr(note_content, column_name))

        await session.flush()
        updated = await self.select_by_id(session, existing.entity_id)
        if updated is None:  # pragma: no cover
            raise ValueError(f"Can't find NoteContent for entity {existing.entity_id} after upsert")
        return updated

    async def accept_write(
        self,
        session: AsyncSession,
        write: AcceptedNoteContentWrite,
    ) -> NoteContent:
        """Insert or update the DB-accepted note snapshot for a pending file write."""
        note_content = NoteContent(
            entity_id=write.entity_id,
            markdown_content=write.markdown_content,
            db_version=write.db_version,
            db_checksum=write.db_checksum,
            file_write_status="pending",
            last_source=write.last_source,
            updated_at=write.updated_at,
            file_version=None,
            file_checksum=None,
            file_updated_at=None,
            last_materialization_error=None,
            last_materialization_attempt_at=None,
        )
        await self._align_identity_fields(session, note_content)

        existing = await self.select_by_id(session, write.entity_id)
        if existing is None:
            session.add(note_content)
            await session.flush()
            created = await self.select_by_id(session, write.entity_id)
            if created is None:  # pragma: no cover
                raise ValueError(
                    f"Can't find NoteContent for entity {write.entity_id} after accept_write"
                )
            return created

        # Optimistic concurrency guard. write.db_version was planned as
        # prior_db_version + 1 from a plain read earlier in this transaction, so a
        # concurrent accepted write could have advanced the row in between. A
        # conditional UPDATE guarded on the expected prior version is the portable
        # compare-and-set (with_for_update is a no-op on SQLite; a rowcount check
        # works on both SQLite and Postgres): if it matches zero rows the write
        # lost the race and we refuse instead of clobbering the winner. Only guard
        # updates that were planned against a prior version (db_version > 1);
        # db_version == 1 is only reached with no prior row, handled above.
        update_stmt = update(NoteContent).where(NoteContent.entity_id == write.entity_id)
        if write.db_version > 1:
            update_stmt = update_stmt.where(NoteContent.db_version == write.db_version - 1)
        # synchronize_session="evaluate" mirrors the persisted values back onto the
        # identity-mapped row from the Python .values() (no DB round-trip), which
        # keeps the returned datetimes tz-aware — SQLite would hand back naive
        # datetimes on a refresh — and matches the prior ORM-setattr behavior.
        result = cast(
            CursorResult[Any],
            await session.execute(
                update_stmt.values(
                    project_id=note_content.project_id,
                    external_id=note_content.external_id,
                    file_path=note_content.file_path,
                    markdown_content=write.markdown_content,
                    db_version=write.db_version,
                    db_checksum=write.db_checksum,
                    file_write_status="pending",
                    last_source=write.last_source,
                    updated_at=write.updated_at,
                    last_materialization_error=None,
                    last_materialization_attempt_at=None,
                ).execution_options(synchronize_session="evaluate")
            ),
        )
        if write.db_version > 1 and result.rowcount == 0:
            raise NoteContentVersionConflict(
                f"note_content for entity {write.entity_id} changed concurrently: "
                f"expected db_version {write.db_version - 1}"
            )

        return existing

    async def update_state_fields(
        self,
        session: AsyncSession,
        entity_id: int,
        *,
        expected_db_version: int | None = None,
        **updates: Any,
    ) -> Optional[NoteContent]:
        """Update sync fields and re-align project_id, external_id, and file_path from entity.

        When ``expected_db_version`` is given the write is a compare-and-set: it
        only applies while the row is still at that db_version, so a caller that
        read state at version N (a reconciler, an out-of-order materialization)
        cannot clobber an accepted API write that advanced the row to N+1 in
        between. On a lost race it returns None — a benign skip, not a 409 —
        because a fresh reconcile/materialization will converge the new state.
        """
        invalid_fields = set(updates) - NOTE_CONTENT_MUTABLE_FIELDS
        if invalid_fields:
            invalid_list = ", ".join(sorted(invalid_fields))
            raise ValueError(f"Unsupported note_content update fields: {invalid_list}")

        note_content = await self.select_by_id(session, entity_id)
        if note_content is None:
            return None

        if expected_db_version is not None:
            # Compare-and-set on db_version. Identity fields are read straight
            # from the entity (rather than mutating the ORM row) so the whole
            # write is the single conditional UPDATE whose rowcount decides the
            # race, portably across SQLite and Postgres.
            entity = await self._load_entity_identity(session, entity_id)
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(NoteContent)
                    .where(NoteContent.entity_id == entity_id)
                    .where(NoteContent.db_version == expected_db_version)
                    .values(
                        project_id=entity.project_id,
                        external_id=entity.external_id,
                        file_path=Path(entity.file_path).as_posix(),
                        **updates,
                    )
                    .execution_options(synchronize_session=False)
                ),
            )
            if result.rowcount == 0:
                return None
            # The Core UPDATE bypassed the ORM; refresh so the returned row
            # reflects the values that actually landed.
            await session.refresh(note_content)
            return note_content

        await self._align_identity_fields(session, note_content)
        for field_name, value in updates.items():
            setattr(note_content, field_name, value)

        await session.flush()
        updated = await self.select_by_id(session, entity_id)
        if updated is None:  # pragma: no cover
            raise ValueError(f"Can't find NoteContent for entity {entity_id} after update")
        return updated

    async def find_stuck_materializations(
        self, session: AsyncSession
    ) -> Sequence[NoteContent]:
        """Return accepted notes whose file write never completed.

        ``accept_write`` marks a row ``pending`` and the materialization preflight
        flips it to ``writing`` before the file is written and the publisher records
        ``synced``/``written``. A process crash between those points leaves the row
        stuck in ``writing``/``pending`` forever and the source-of-truth markdown
        file never (re)materialized. A transient write error (ENOSPC, permissions)
        publishes ``failed`` instead — equally terminal without a retry, and for a
        new note the file never exists, so the next scan's delete reconciliation
        would destroy the entity and its accepted content. The recovery sweep
        re-drives all three states; the db_version compare-and-set guard in the
        preflight and publisher keeps the retry safe (an older recovery attempt can
        never revert a newer accepted write).
        """
        query = self.select().where(
            NoteContent.file_write_status.in_(("writing", "pending", "failed"))
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    async def delete_by_entity_id(self, session: AsyncSession, entity_id: int) -> bool:
        """Delete note_content by entity identifier."""
        note_content = await self.select_by_id(session, entity_id)
        if note_content is None:
            return False

        await session.delete(note_content)
        await session.flush()
        return True
