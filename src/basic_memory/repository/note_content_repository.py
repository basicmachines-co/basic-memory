"""Repository for managing note materialization state."""

from pathlib import Path
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, NoteContent
from basic_memory.repository.repository import Repository

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


class NoteContentRepository(Repository[NoteContent]):
    """Repository for project-scoped note materialization state."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession], project_id: int):
        """Initialize with session maker and project-scoped filtering."""
        super().__init__(session_maker, NoteContent, project_id=project_id)

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

    async def get_by_entity_id(self, entity_id: int) -> Optional[NoteContent]:
        """Get note content by the owning entity identifier."""
        return await self.find_by_id(entity_id)

    async def get_by_external_id(self, external_id: str) -> Optional[NoteContent]:
        """Get note content by the mirrored entity external identifier."""
        query = self.select().where(NoteContent.external_id == external_id)
        return await self.find_one(query)

    async def get_by_file_path(self, file_path: Path | str) -> Optional[NoteContent]:
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

        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(query)
            return result.scalars().first()

    async def create(self, data: Mapping[str, Any] | NoteContent) -> NoteContent:
        """Create a note_content row aligned to its owning entity."""
        note_content, _ = self._coerce_note_content(data)

        async with db.scoped_session(self.session_maker) as session:
            await self._align_identity_fields(session, note_content)
            session.add(note_content)
            await session.flush()

            created = await self.select_by_id(session, note_content.entity_id)
            if created is None:  # pragma: no cover
                raise ValueError(
                    f"Can't find NoteContent for entity {note_content.entity_id} after add"
                )
            return created

    async def upsert(self, data: Mapping[str, Any] | NoteContent) -> NoteContent:
        """Insert or update note_content while keeping mirrored identity fields in sync."""
        note_content, provided_fields = self._coerce_note_content(data)

        async with db.scoped_session(self.session_maker) as session:
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
                raise ValueError(
                    f"Can't find NoteContent for entity {existing.entity_id} after upsert"
                )
            return updated

    async def update_state_fields(self, entity_id: int, **updates: Any) -> Optional[NoteContent]:
        """Update sync fields and re-align project_id, external_id, and file_path from entity."""
        invalid_fields = set(updates) - NOTE_CONTENT_MUTABLE_FIELDS
        if invalid_fields:
            invalid_list = ", ".join(sorted(invalid_fields))
            raise ValueError(f"Unsupported note_content update fields: {invalid_list}")

        async with db.scoped_session(self.session_maker) as session:
            note_content = await self.select_by_id(session, entity_id)
            if note_content is None:
                return None

            await self._align_identity_fields(session, note_content)
            for field_name, value in updates.items():
                setattr(note_content, field_name, value)

            await session.flush()
            updated = await self.select_by_id(session, entity_id)
            if updated is None:  # pragma: no cover
                raise ValueError(f"Can't find NoteContent for entity {entity_id} after update")
            return updated

    async def delete_by_entity_id(self, entity_id: int) -> bool:
        """Delete note_content by entity identifier."""
        async with db.scoped_session(self.session_maker) as session:
            note_content = await self.select_by_id(session, entity_id)
            if note_content is None:
                return False

            await session.delete(note_content)
            return True
