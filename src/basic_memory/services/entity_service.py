"""Service for managing entities in the database."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.file_utils import remove_frontmatter
from basic_memory.markdown import EntityMarkdown
from basic_memory.markdown.entity_parser import (
    EntityParser,
    normalize_frontmatter_metadata,
)
from basic_memory.markdown.utils import entity_model_from_markdown
from basic_memory.models import Entity as EntityModel
from basic_memory.models import Observation, Relation
from basic_memory.models.knowledge import Entity
from basic_memory.repository import ObservationRepository, RelationRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.runtime.note_move import normalize_note_move_destination_path
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.base import Permalink
from basic_memory.schemas.response import (
    DirectoryMoveResult,
    DirectoryMoveError,
    DirectoryDeleteResult,
    DirectoryDeleteError,
)
from basic_memory.services import BaseService, FileService
from basic_memory.services.exceptions import (
    EntityAlreadyExistsError,
    EntityCreationError,
    EntityNotFoundError,
)
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.note_preparation import (
    NotePreparation,
    NotePreparationDependencies,
    PreparedEntityFields,
    PreparedEntityMove,
    PreparedEntityWrite,
    _fenced_code_line_flags as _note_fenced_code_line_flags,
    apply_prepared_entity_fields,
    apply_edit_operation as apply_note_edit_operation,
    insert_relative_to_section as insert_note_relative_to_section,
    replace_section_content as replace_note_section_content,
)
from basic_memory.services.search_service import SearchService

__all__ = [
    "EntityService",
    "PreparedEntityFields",
    "PreparedEntityMove",
    "PreparedEntityWrite",
    "apply_prepared_entity_fields",
]

_fenced_code_line_flags = _note_fenced_code_line_flags


@dataclass(frozen=True)
class EntityWriteResult:
    """Persisted entity plus the response/search content produced during this call."""

    entity: EntityModel
    content: str
    search_content: str


class EntityService(BaseService[EntityModel]):
    """Service for managing entities in the database."""

    def __init__(
        self,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        relation_repository: RelationRepository,
        file_service: FileService,
        link_resolver: LinkResolver,
        session_maker: async_sessionmaker[AsyncSession],
        search_service: Optional[SearchService] = None,
        app_config: Optional[BasicMemoryConfig] = None,
    ):
        super().__init__(entity_repository)
        self.observation_repository = observation_repository
        self.relation_repository = relation_repository
        self.entity_parser = entity_parser
        self.file_service = file_service
        self.link_resolver = link_resolver
        self.session_maker = session_maker
        self.search_service = search_service
        self.app_config = app_config
        self._note_preparation = NotePreparation(
            NotePreparationDependencies(
                entity_parser=entity_parser,
                entity_repository=entity_repository,
                file_service=file_service,
                session_maker=session_maker,
                app_config=app_config,
            )
        )
        # Callable that returns the current user ID (cloud user_profile_id UUID as string).
        # Default returns None for local/CLI usage. Cloud overrides this to read from UserContext.
        self.get_user_id: Callable[[], Optional[str]] = lambda: None

    async def detect_file_path_conflicts(
        self,
        file_path: str,
        skip_check: bool = False,
        session: AsyncSession | None = None,
    ) -> List[str]:
        """Delegate file-path conflict detection to the shared preparation capability."""
        return await self._note_preparation.detect_file_path_conflicts(
            file_path,
            skip_check=skip_check,
            session=session,
        )

    async def resolve_permalink(
        self,
        file_path: Permalink | Path,
        markdown: Optional[EntityMarkdown] = None,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> str:
        """Delegate permalink resolution to the shared preparation capability."""
        return await self._note_preparation.resolve_permalink(
            file_path,
            markdown,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    def _coerce_schema_input(self, schema: EntitySchema | EntityModel) -> EntitySchema:
        """Normalize legacy Entity-like inputs into the schema shape prepare methods expect."""
        if isinstance(schema, EntitySchema):
            return schema

        # create_or_update_entity historically tolerated callers passing an ORM entity that had
        # been annotated with ad-hoc content. Preserve that compatibility at the wrapper boundary
        # so the prepare layer itself can stay strict and schema-focused.
        directory = Path(schema.file_path).parent.as_posix()
        normalized = EntitySchema(
            title=schema.title,
            content=getattr(schema, "content", None),
            directory="" if directory == "." else directory,
            note_type=schema.note_type,
            entity_metadata=schema.entity_metadata,
            content_type=schema.content_type,
        )
        normalized._permalink = schema.permalink
        return normalized

    def _sync_prepared_schema_state(
        self,
        source_schema: EntitySchema | EntityModel,
        prepared: PreparedEntityWrite,
    ) -> None:
        """Preserve the legacy side effect where write helpers populate the caller's schema."""
        if not isinstance(source_schema, EntitySchema):
            return

        # Older service flows mutated the request schema with the resolved permalink and any
        # frontmatter-derived note type. Several callers and tests still rely on that behavior
        # after create/update returns.
        source_schema.title = prepared.entity_fields.title
        source_schema.note_type = prepared.entity_fields.note_type
        source_schema.content_type = prepared.entity_fields.content_type
        source_schema.entity_metadata = prepared.entity_fields.entity_metadata

        if self.app_config and self.app_config.disable_permalinks:
            source_schema._permalink = ""
        else:
            source_schema._permalink = prepared.entity_fields.permalink

    async def _read_persisted_write_content(self, file_path: Path) -> tuple[str, str]:
        """Read the stored markdown after write-time formatting has finished."""
        # Trigger: format-on-save or platform-specific text writes can change the stored markdown
        # after prepare accepted the request.
        # Why: API responses and inline search indexing should describe the note that actually
        #      landed on disk, not the pre-write snapshot.
        # Outcome: write helpers return persisted markdown plus search content derived from it.
        persisted_content = await self.file_service.read_file_content(file_path)
        return persisted_content, remove_frontmatter(persisted_content)

    def _paths_share_storage_target(self, left: Path, right: Path) -> bool:
        """Return whether two relative project paths point at the same stored file."""
        left_abs_path = self.file_service.base_path / left
        right_abs_path = self.file_service.base_path / right
        if not left_abs_path.exists() or not right_abs_path.exists():
            return False
        try:
            return left_abs_path.samefile(right_abs_path)
        except OSError:
            return False

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = True,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        """Derive accepted markdown and entity fields for a new note.

        This is a public prepare step: it resolves frontmatter overrides,
        permalink semantics, and the final markdown body, but it does not write
        files or mutate database rows.

        Storage touch points:
            - When ``check_storage_exists`` is ``True`` (the default), this method
              calls ``file_service.exists(file_path)`` and raises
              ``EntityAlreadyExistsError`` if the target already exists.
            - When ``check_storage_exists`` is ``False``, callers opt into DB-first
              acceptance and must perform any external storage conflict handling
              themselves.
        """
        return await self._note_preparation.prepare_create_entity_content(
            schema,
            check_storage_exists=check_storage_exists,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    async def prepare_update_entity_content(
        self,
        entity: EntityModel,
        schema: EntitySchema,
        existing_content: str,
        *,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        """Derive accepted markdown and entity fields for a full note replacement.

        This method does not read or write storage on its own. The caller must
        supply ``existing_content`` for the current note body because full updates
        preserve unrecognized frontmatter keys from that explicit base content.
        No database rows are mutated here.
        """
        return await self._note_preparation.prepare_update_entity_content(
            entity,
            schema,
            existing_content,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    async def prepare_edit_entity_content(
        self,
        entity: EntityModel,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
        replace_subsections: bool = True,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> PreparedEntityWrite:
        """Derive accepted markdown and entity fields for an edit request.

        This method operates only on the caller-provided ``current_content``. It
        does not read files, write files, or mutate database rows. That makes the
        edit base explicit so higher layers can reject stale content instead of
        silently editing whichever storage copy happens to be newest.
        """
        return await self._note_preparation.prepare_edit_entity_content(
            entity,
            current_content,
            operation=operation,
            content=content,
            section=section,
            find_text=find_text,
            expected_replacements=expected_replacements,
            replace_subsections=replace_subsections,
            skip_conflict_check=skip_conflict_check,
            session=session,
        )

    async def prepare_move_entity_content(
        self,
        entity: EntityModel,
        current_content: str,
        destination_path: str,
        *,
        session: AsyncSession | None = None,
    ) -> PreparedEntityMove:
        """Derive accepted markdown and permalink state for a note move.

        This method does not read files, write files, or mutate database rows.
        The caller supplies the current accepted markdown because cloud DB-first
        moves may need to use note_content rather than a materialized file.
        """
        return await self._note_preparation.prepare_move_entity_content(
            entity,
            current_content,
            destination_path,
            session=session,
        )

    async def verify_move_destination_absent(
        self,
        *,
        source_file_path: str,
        destination_file_path: str,
    ) -> None:
        """Reject a local move onto a destination that exists on disk but is unindexed.

        Mirrors the create/PUT storage-existence guard: committing the move would
        point DB/search at an unchanged file and leave the source to be reindexed as
        a duplicate. A case-only rename or shared storage target (same physical file)
        is allowed. Cloud is DB-first and opts out via verify_storage_absent_on_create.
        """
        await self._note_preparation.verify_move_destination_absent(
            source_file_path=source_file_path,
            destination_file_path=destination_file_path,
        )

    async def create_or_update_entity(self, schema: EntitySchema) -> Tuple[EntityModel, bool]:
        """Create new entity or update existing one.
        Returns: (entity, is_new) where is_new is True if a new entity was created
        """
        logger.debug(
            f"Creating or updating entity: {schema.file_path}, permalink: {schema.permalink}"
        )

        # Try to find existing entity using strict resolution (no fuzzy search)
        # This prevents incorrectly matching similar file paths like "Node A.md" and "Node C.md"
        existing = await self.link_resolver.resolve_link(
            schema.file_path,
            strict=True,
            load_relations=False,
        )
        if not existing and schema.permalink:
            existing = await self.link_resolver.resolve_link(
                schema.permalink,
                strict=True,
                load_relations=False,
            )

        if existing:
            logger.debug(f"Found existing entity: {existing.file_path}")
            return await self.update_entity(existing, self._coerce_schema_input(schema)), False
        else:
            # Create new entity
            return await self.create_entity(self._coerce_schema_input(schema)), True

    async def create_entity(self, schema: EntitySchema) -> EntityModel:
        """Create a new entity and write to filesystem."""
        return (await self.create_entity_with_content(schema)).entity

    async def create_entity_with_content(self, schema: EntitySchema) -> EntityWriteResult:
        """Create a new entity and return both the entity row and written markdown."""
        logger.debug(f"Creating entity: {schema.title}")
        async with db.scoped_session(self.session_maker) as session:
            # --- Prepare Accepted State ---
            # Derive the canonical markdown/entity fields before touching the filesystem.
            prepared = await self.prepare_create_entity_content(schema, session=session)
            self._sync_prepared_schema_state(schema, prepared)
            # --- Persist File, Then Indexable DB State ---
            # Local mode still writes the file immediately; the prepare object keeps semantics separate
            # from that persistence step.
            checksum = await self.file_service.write_file(
                prepared.file_path, prepared.markdown_content
            )
            entity = await self.upsert_entity_from_markdown(
                prepared.file_path,
                prepared.entity_markdown,
                is_new=True,
                session=session,
            )
            updated = await self.repository.update(session, entity.id, {"checksum": checksum})
            if not updated:  # pragma: no cover
                raise ValueError(f"Failed to update entity checksum after create: {entity.id}")
            persisted_content, search_content = await self._read_persisted_write_content(
                prepared.file_path
            )
            return EntityWriteResult(
                entity=updated,
                content=persisted_content,
                search_content=search_content,
            )

    async def update_entity(self, entity: EntityModel, schema: EntitySchema) -> EntityModel:
        """Update an entity's content and metadata."""
        return (
            await self.update_entity_with_content(entity, self._coerce_schema_input(schema))
        ).entity

    async def update_entity_with_content(
        self, entity: EntityModel, schema: EntitySchema
    ) -> EntityWriteResult:
        """Update an entity and return both the entity row and written markdown."""
        schema = self._coerce_schema_input(schema)
        logger.debug(
            f"Updating entity with permalink: {entity.permalink} content-type: {schema.content_type}"
        )

        async with db.scoped_session(self.session_maker) as session:
            # --- Read Current File State ---
            # Full replacements merge with existing frontmatter, so local mode still needs the current
            # file contents as input to the prepare step.
            existing_content = await self.file_service.read_file_content(entity.file_path)
            prepared = await self.prepare_update_entity_content(
                entity,
                schema,
                existing_content,
                session=session,
            )
            self._sync_prepared_schema_state(schema, prepared)
            previous_file_path = Path(entity.file_path)
            # Trigger: a full replacement also renames the note to a different canonical path.
            # Why: Path.replace() overwrites existing files, so the destination must be conflict-free
            #      before we write or we can clobber another note and only fail later at the DB layer.
            # Outcome: conflicting rename attempts fail before touching either file on disk.
            if (
                prepared.file_path.as_posix() != previous_file_path.as_posix()
                and await self.file_service.exists(prepared.file_path)
                and not self._paths_share_storage_target(previous_file_path, prepared.file_path)
            ):
                raise EntityAlreadyExistsError(
                    f"file already exists at destination path: {prepared.file_path.as_posix()}"
                )
            # --- Persist Prepared State ---
            checksum = await self.file_service.write_file(
                prepared.file_path,
                prepared.markdown_content,
            )
            entity = await self.upsert_entity_from_markdown(
                prepared.file_path,
                prepared.entity_markdown,
                is_new=False,
                existing_entity=entity,
                session=session,
            )
            if prepared.file_path.as_posix() != previous_file_path.as_posix():
                # Trigger: a full replacement changed the canonical note path.
                # Why: the new file has already been written and the entity now points at it.
                # Outcome: remove the stale old file so local Basic Memory mirrors cloud's queued cleanup.
                if not self._paths_share_storage_target(previous_file_path, prepared.file_path):
                    await self.file_service.delete_file(previous_file_path)
            entity = await self.repository.update(session, entity.id, {"checksum": checksum})
            if not entity:  # pragma: no cover
                raise ValueError(
                    f"Failed to update entity checksum after update: {prepared.file_path}"
                )
            persisted_content, search_content = await self._read_persisted_write_content(
                prepared.file_path
            )

            return EntityWriteResult(
                entity=entity,
                content=persisted_content,
                search_content=search_content,
            )

    async def delete_entity(self, permalink_or_id: str | int) -> bool:
        """Delete entity and its file."""
        logger.debug(f"Deleting entity: {permalink_or_id}")

        try:
            # Get entity first for file deletion
            if isinstance(permalink_or_id, str):
                entity = await self.get_by_permalink(permalink_or_id)
            else:
                entities = await self.get_entities_by_id([permalink_or_id])
                if len(entities) == 0:
                    # Entity already deleted (concurrent delete or race condition)
                    logger.info("Entity already deleted", entity_id=permalink_or_id)
                    return True
                if len(entities) != 1:  # pragma: no cover
                    logger.error(
                        "Entity lookup error", entity_id=permalink_or_id, found_count=len(entities)
                    )
                    raise ValueError(
                        f"Expected 1 entity with ID {permalink_or_id}, got {len(entities)}"
                    )
                entity = entities[0]

            # Delete from search index first (if search_service is available)
            if self.search_service:
                try:
                    await self.search_service.handle_delete(entity)
                except Exception:
                    # Search cleanup is best-effort during concurrent deletes.
                    # Relationships may have been cascade-deleted by a concurrent request.
                    logger.warning(
                        "Search cleanup failed for entity (likely concurrent delete)",
                        permalink_or_id=permalink_or_id,
                        exc_info=True,
                    )

            # Delete file
            await self.file_service.delete_entity_file(entity)

            # Delete from DB (this will cascade to observations/relations)
            # Trigger: repository.delete returns False when entity is already gone (NoResultFound)
            # Why: concurrent delete_directory requests can race to delete the same entity
            # Outcome: treat as success since the entity is deleted either way
            async with db.scoped_session(self.session_maker) as session:
                deleted = await self.repository.delete(session, entity.id)
            if not deleted:
                logger.info("Entity already removed from DB", entity_id=permalink_or_id)
            return True

        except EntityNotFoundError:
            logger.info(f"Entity not found: {permalink_or_id}")
            return True  # Already deleted

    async def get_by_permalink(self, permalink: str) -> EntityModel:
        """Get entity by type and name combination."""
        logger.debug(f"Getting entity by permalink: {permalink}")
        async with db.scoped_session(self.session_maker) as session:
            db_entity = await self.repository.get_by_permalink(session, permalink)
        if not db_entity:
            raise EntityNotFoundError(f"Entity not found: {permalink}")
        return db_entity

    async def get_entities_by_id(self, ids: List[int]) -> Sequence[EntityModel]:
        """Get specific entities and their relationships."""
        logger.debug(f"Getting entities: {ids}")
        async with db.scoped_session(self.session_maker) as session:
            return await self.repository.find_by_ids(session, ids)

    async def get_entities_by_permalinks(self, permalinks: List[str]) -> Sequence[EntityModel]:
        """Get specific nodes and their relationships."""
        logger.debug(f"Getting entities permalinks: {permalinks}")
        async with db.scoped_session(self.session_maker) as session:
            return await self.repository.find_by_permalinks(session, permalinks)

    async def delete_entity_by_file_path(self, file_path: Union[str, Path]) -> None:
        """Delete entity by file path."""
        async with db.scoped_session(self.session_maker) as session:
            await self.repository.delete_by_file_path(session, str(file_path))

    async def create_entity_from_markdown(
        self,
        file_path: Path,
        markdown: EntityMarkdown,
        session: AsyncSession | None = None,
    ) -> EntityModel:
        """Create entity and observations only.

        Creates the entity with null checksum to indicate sync not complete.
        Relations will be added in second pass.

        Uses UPSERT approach to handle permalink/file_path conflicts cleanly.
        """
        logger.debug(f"Creating entity: {markdown.frontmatter.title} file_path: {file_path}")
        model = entity_model_from_markdown(
            file_path, markdown, project_id=self.repository.project_id
        )

        # Mark as incomplete because we still need to add relations
        model.checksum = None

        # Set user tracking fields for cloud usage
        user_id = self.get_user_id()
        if user_id is not None:
            model.created_by = user_id
            model.last_updated_by = user_id

        async with db.scoped_session(self.session_maker, session) as active_session:
            # Use UPSERT to handle conflicts cleanly
            try:
                return await self.repository.upsert_entity(active_session, model)
            except Exception as e:
                logger.error(f"Failed to upsert entity for {file_path}: {e}")
                raise EntityCreationError(f"Failed to create entity: {str(e)}") from e

    async def update_entity_and_observations(
        self,
        file_path: Path,
        markdown: EntityMarkdown,
        *,
        existing_entity: EntityModel | None = None,
        session: AsyncSession | None = None,
    ) -> EntityModel:
        """Update entity fields and observations.

        Updates everything except relations and sets null checksum
        to indicate sync not complete.
        """
        logger.debug(f"Updating entity and observations: {file_path}")

        async with db.scoped_session(self.session_maker, session) as active_session:
            if existing_entity is not None:
                db_entity = await self.repository.get_by_id(
                    active_session,
                    existing_entity.id,
                    load_relations=False,
                )
            else:
                db_entity = await self.repository.get_by_file_path(
                    active_session,
                    file_path.as_posix(),
                    load_relations=False,
                )
            if db_entity is None:  # pragma: no cover
                raise EntityNotFoundError(f"Entity not found for file path: {file_path}")

            # Observations are owned by the markdown file, so re-indexing replaces the old set.
            # We only need the entity id here; loading the old relationship collection is wasted work.
            await self.observation_repository.delete_by_fields(
                active_session, entity_id=db_entity.id
            )

            observations = [
                Observation(
                    project_id=self.observation_repository.project_id,
                    entity_id=db_entity.id,
                    content=obs.content,
                    category=obs.category,
                    context=obs.context,
                    tags=obs.tags,
                )
                for obs in markdown.observations
            ]
            await self.observation_repository.add_all_no_return(active_session, observations)

            self._apply_markdown_entity_fields(db_entity, file_path, markdown)

            # checksum value is None == not finished with sync
            db_entity.checksum = None

            # Set last_updated_by for cloud usage (preserve existing created_by)
            user_id = self.get_user_id()
            if user_id is not None:
                db_entity.last_updated_by = user_id

            await active_session.flush()
            return db_entity

    def _apply_markdown_entity_fields(
        self,
        entity: EntityModel,
        file_path: Path,
        markdown: EntityMarkdown,
    ) -> None:
        """Apply parsed markdown scalar fields without touching ORM relationships."""
        if not markdown.created or not markdown.modified:  # pragma: no cover
            raise ValueError("Both created and modified dates are required in markdown")

        entity.title = markdown.frontmatter.title
        entity.note_type = markdown.frontmatter.type
        if markdown.frontmatter.permalink is not None:
            entity.permalink = markdown.frontmatter.permalink
        entity.file_path = file_path.as_posix()
        entity.content_type = "text/markdown"
        entity.created_at = markdown.created
        entity.updated_at = markdown.modified

        normalized_metadata = normalize_frontmatter_metadata(markdown.frontmatter.metadata or {})
        entity.entity_metadata = {
            key: value for key, value in normalized_metadata.items() if value is not None
        }

    async def upsert_entity_from_markdown(
        self,
        file_path: Path,
        markdown: EntityMarkdown,
        *,
        is_new: bool,
        existing_entity: EntityModel | None = None,
        resolve_relations: bool = True,
        reload_entity: bool = True,
        session: AsyncSession | None = None,
    ) -> EntityModel:
        """Create/update entity and relations from parsed markdown."""
        async with db.scoped_session(self.session_maker, session) as active_session:
            if is_new:
                created = await self.create_entity_from_markdown(
                    file_path, markdown, session=active_session
                )
            else:
                created = await self.update_entity_and_observations(
                    file_path,
                    markdown,
                    existing_entity=existing_entity,
                    session=active_session,
                )
            # Pass the entity through so relation work does not have to rediscover the source row.
            return await self.update_entity_relations(
                created,
                markdown,
                resolve_targets=resolve_relations,
                reload_entity=reload_entity,
                session=active_session,
            )

    async def update_entity_relations(
        self,
        entity: EntityModel,
        markdown: EntityMarkdown,
        *,
        resolve_targets: bool = True,
        reload_entity: bool = True,
        session: AsyncSession | None = None,
    ) -> EntityModel:
        """Update relations for entity.

        Accepts the entity object directly to avoid a redundant DB fetch.
        Only entity.id and entity.permalink are used from the passed-in object.
        """
        entity_id = entity.id
        logger.debug(f"Updating relations for entity: {entity.file_path}")

        async with db.scoped_session(self.session_maker, session) as active_session:
            # Clear existing relations first
            await self.relation_repository.delete_outgoing_relations_from_entity(
                active_session, entity_id
            )

            if markdown.relations:
                if resolve_targets:
                    # Exact target resolution is useful for local sync, but expensive for cloud
                    # one-file jobs. Cloud can write unresolved rows and let a relation repair pass
                    # fill in to_id later.
                    resolved_entities: list[Entity | Exception | None] = []
                    for rel in markdown.relations:
                        try:
                            # Savepoint: a DB-level lookup failure would otherwise
                            # poison the caller's write transaction — on Postgres
                            # every later statement raises PendingRollbackError with
                            # the root cause hidden. Scoping the lookup keeps the
                            # relation writes below alive.
                            async with active_session.begin_nested():
                                resolved = await self.link_resolver.resolve_link(
                                    rel.target,
                                    strict=True,
                                    load_relations=False,
                                    session=active_session,
                                )
                        except Exception as exc:
                            # The failure intentionally degrades to a forward
                            # reference below, but losing the error silently hides
                            # real defects — log it with the link context.
                            logger.warning(
                                f"Relation target resolution failed for '{rel.target}' "
                                f"from entity {entity.file_path}; keeping forward reference",
                                entity_id=entity_id,
                                error=str(exc),
                            )
                            resolved = exc
                        resolved_entities.append(resolved)
                else:
                    resolved_entities = [None] * len(markdown.relations)

                # Process results and create relation records
                relations_to_add = []
                for rel, resolved in zip(markdown.relations, resolved_entities):
                    # Handle exceptions from gather and None results
                    target_entity: Optional[Entity] = None
                    if not isinstance(resolved, Exception):
                        # Relation target resolution keeps exceptions as values so a failed lookup
                        # becomes an unresolved forward reference instead of aborting the write.
                        target_entity = resolved

                    if target_entity is None and not resolve_targets:
                        target_entity = await self.resolve_deferred_self_relation(
                            rel.target, entity, session=active_session
                        )

                    # if the target is found, store the id
                    target_id = target_entity.id if target_entity else None
                    # if the target is found, store the title, otherwise add the target for a "forward link"
                    target_name = target_entity.title if target_entity else rel.target

                    # Create the relation
                    relation = Relation(
                        project_id=self.relation_repository.project_id,
                        from_id=entity_id,
                        to_id=target_id,
                        to_name=target_name,
                        relation_type=rel.type,
                        context=rel.context,
                    )
                    relations_to_add.append(relation)

                # Batch insert all relations
                if relations_to_add:
                    await self.relation_repository.add_all_ignore_duplicates(
                        active_session, relations_to_add
                    )

            if not reload_entity:
                return entity

            # Reload entity with relations via PK lookup (faster than get_by_file_path string match).
            reloaded = await self.repository.find_by_ids(active_session, [entity_id])
            return reloaded[0]

    async def resolve_deferred_self_relation(
        self, target: str, entity: EntityModel, session: AsyncSession | None = None
    ) -> EntityModel | None:
        """Resolve only self-relations that are safe to identify in deferred mode."""
        return await self._note_preparation.resolve_deferred_self_relation(
            target,
            entity,
            session=session,
        )

    async def edit_entity(
        self,
        identifier: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
        replace_subsections: bool = True,
    ) -> EntityModel:
        """Edit an existing entity's content using various operations.

        Args:
            identifier: Entity identifier (permalink, title, etc.)
            operation: The editing operation (append, prepend, find_replace, replace_section)
            content: The content to add or use for replacement
            section: For replace_section operation - the markdown header
            find_text: For find_replace operation - the text to find and replace
            expected_replacements: For find_replace operation - expected number of replacements (default: 1)
            replace_subsections: For replace_section operation - replace nested
                subsections along with the section body (default True); False stops
                at the first heading of any level, preserving them

        Returns:
            The updated entity model

        Raises:
            EntityNotFoundError: If the entity cannot be found
            ValueError: If required parameters are missing for the operation or replacement count doesn't match expected
        """
        return (
            await self.edit_entity_with_content(
                identifier=identifier,
                operation=operation,
                content=content,
                section=section,
                find_text=find_text,
                expected_replacements=expected_replacements,
                replace_subsections=replace_subsections,
            )
        ).entity

    async def edit_entity_with_content(
        self,
        identifier: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
        replace_subsections: bool = True,
    ) -> EntityWriteResult:
        """Edit an entity and return both the entity row and written markdown."""
        logger.debug(f"Editing entity: {identifier}, operation: {operation}")

        entity = await self.link_resolver.resolve_link(
            identifier,
            strict=True,
            load_relations=False,
        )
        if not entity:
            raise EntityNotFoundError(f"Entity not found: {identifier}")

        file_path = Path(entity.file_path)
        current_content, _ = await self.file_service.read_file(file_path)
        async with db.scoped_session(self.session_maker) as session:
            # --- Prepare Against Explicit Base Content ---
            # The edit operation is the semantic step; file/DB writes below are just persistence of that
            # accepted result.
            prepared = await self.prepare_edit_entity_content(
                entity,
                current_content,
                operation=operation,
                content=content,
                section=section,
                find_text=find_text,
                expected_replacements=expected_replacements,
                replace_subsections=replace_subsections,
                session=session,
            )

            checksum = await self.file_service.write_file(
                file_path,
                prepared.markdown_content,
            )

            # --- Rebuild Structured Knowledge State ---
            # Non-fast edits remain fully synchronous locally: once the file write succeeds, we refresh
            # observations, relations, and checksum in the same request.
            entity = await self.upsert_entity_from_markdown(
                file_path,
                prepared.entity_markdown,
                is_new=False,
                session=session,
            )

            entity = await self.repository.update(session, entity.id, {"checksum": checksum})
            if not entity:  # pragma: no cover
                raise ValueError(f"Failed to update entity checksum after edit: {file_path}")
            persisted_content, search_content = await self._read_persisted_write_content(file_path)

            return EntityWriteResult(
                entity=entity,
                content=persisted_content,
                search_content=search_content,
            )

    def apply_edit_operation(
        self,
        current_content: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
        replace_subsections: bool = True,
    ) -> str:
        """Apply the specified edit operation to the current content."""
        return apply_note_edit_operation(
            current_content,
            operation,
            content,
            section,
            find_text,
            expected_replacements,
            replace_subsections,
        )

    def replace_section_content(
        self,
        current_content: str,
        section_header: str,
        new_content: str,
        replace_subsections: bool = True,
    ) -> str:
        """Replace content under a specific markdown section header.

        By default a section owns everything through the next heading of the same
        or higher level in the original document (issue #1012):

        - Replacing "## Section" replaces its body and all "###"+ subsections,
          stopping at the next "##" or "#" heading — how Obsidian and most
          markdown tools scope a section.
        - Passing replace_subsections=False restores the earlier conservative
          behavior: only the immediate content under the header is replaced and
          consumption stops at the next heading of ANY level, preserving
          subsections.

        The boundary is always computed from the original document, so new_content
        may freely introduce new headings without shifting where the replaced span
        ends. Heading detection ignores lines inside ``` fenced code blocks, so a
        '# comment' in a code sample never matches or terminates a section.

        Args:
            current_content: The current markdown content
            section_header: The section header to find and replace (e.g., "## Section Name")
            new_content: The new content to replace the section with (should not include the header itself)
            replace_subsections: Replace nested subsections along with the section
                body (default True); False stops at the first heading of any level.

        Returns:
            The updated content with the section replaced

        Raises:
            ValueError: If multiple sections with the same header are found
        """
        return replace_note_section_content(
            current_content,
            section_header,
            new_content,
            replace_subsections=replace_subsections,
        )

    def insert_relative_to_section(
        self,
        current_content: str,
        section_header: str,
        new_content: str,
        position: str,
    ) -> str:
        """Insert content before or after a section heading without consuming it.

        Unlike replace_section_content, this preserves the section heading and its
        existing content. The new content is inserted immediately before or after
        the heading line.

        Args:
            current_content: The current markdown content
            section_header: The section header to anchor on (e.g., "## Section Name")
            new_content: The content to insert
            position: "before" to insert above the heading, "after" to insert below it

        Returns:
            The updated content with new_content inserted relative to the heading

        Raises:
            ValueError: If the section header is not found or appears more than once
        """
        return insert_note_relative_to_section(
            current_content,
            section_header,
            new_content,
            position,
        )

    async def move_entity(
        self,
        identifier: str,
        destination_path: str,
        project_config: ProjectConfig,
        app_config: BasicMemoryConfig,
    ) -> EntityModel:
        """Move entity to new location with database consistency.

        Args:
            identifier: Entity identifier (title, permalink, or memory:// URL)
            destination_path: New path relative to project root
            project_config: Project configuration for file operations
            app_config: App configuration for permalink update settings

        Returns:
            Success message with move details

        Raises:
            EntityNotFoundError: If the entity cannot be found
            ValueError: If move operation fails due to validation or filesystem errors
        """
        logger.debug(f"Moving entity: {identifier} to {destination_path}")

        # 1. Resolve identifier to entity with strict mode for destructive operations
        entity = await self.link_resolver.resolve_link(identifier, strict=True)
        if not entity:
            raise EntityNotFoundError(f"Entity not found: {identifier}")

        current_path = entity.file_path
        old_permalink = entity.permalink

        # 2. Validate and normalize the destination with the shared move-path
        # rules so move_entity and move_note accept identical inputs.
        destination_path = normalize_note_move_destination_path(destination_path)

        # 3. Validate paths
        # NOTE: In tenantless/cloud mode, we cannot rely on local filesystem paths.
        # Use FileService for existence checks and moving.
        if not await self.file_service.exists(current_path):
            raise ValueError(f"Source file not found: {current_path}")

        if await self.file_service.exists(destination_path):
            raise ValueError(f"Destination already exists: {destination_path}")

        try:
            # 4. Ensure destination directory if needed (no-op for S3)
            await self.file_service.ensure_directory(Path(destination_path).parent)

            # 5. Move physical file via FileService (filesystem rename or cloud move)
            await self.file_service.move_file(current_path, destination_path)
            logger.info(f"Moved file: {current_path} -> {destination_path}")

            # 6. Prepare database updates
            updates = {"file_path": destination_path}

            # 7. Update permalink if configured or if entity has null permalink (unless disabled)
            if not app_config.disable_permalinks and (
                app_config.update_permalinks_on_move or old_permalink is None
            ):
                # Generate new permalink from destination path
                new_permalink = await self.resolve_permalink(destination_path)

                # Update frontmatter with new permalink
                await self.file_service.update_frontmatter(
                    destination_path, {"permalink": new_permalink}
                )

                updates["permalink"] = new_permalink
                if old_permalink is None:
                    logger.info(
                        f"Generated permalink for entity with null permalink: {new_permalink}"
                    )
                else:
                    logger.info(f"Updated permalink: {old_permalink} -> {new_permalink}")

            # 8. Recalculate checksum
            new_checksum = await self.file_service.compute_checksum(destination_path)
            updates["checksum"] = new_checksum

            # 9. Update database
            async with db.scoped_session(self.session_maker) as session:
                updated_entity = await self.repository.update(session, entity.id, updates)
                if not updated_entity:
                    raise ValueError(f"Failed to update entity in database: {entity.id}")

                return updated_entity

        except Exception as e:
            # Rollback: try to restore original file location if move succeeded
            try:
                if await self.file_service.exists(
                    destination_path
                ) and not await self.file_service.exists(current_path):
                    await self.file_service.move_file(destination_path, current_path)
                    logger.info(f"Rolled back file move: {destination_path} -> {current_path}")
            except Exception as rollback_error:  # pragma: no cover
                logger.error(f"Failed to rollback file move: {rollback_error}")

            # Re-raise the original error with context
            raise ValueError(f"Move failed: {str(e)}") from e

    async def move_directory(
        self,
        source_directory: str,
        destination_directory: str,
        project_config: ProjectConfig,
        app_config: BasicMemoryConfig,
    ) -> DirectoryMoveResult:
        """Move all entities in a directory to a new location.

        This operation moves all files within a source directory to a destination
        directory, updating database records and search indexes. The operation
        tracks successes and failures individually to provide detailed feedback.

        Args:
            source_directory: Source directory path relative to project root
            destination_directory: Destination directory path relative to project root
            project_config: Project configuration for file operations
            app_config: App configuration for permalink update settings

        Returns:
            DirectoryMoveResult with counts and details of moved files

        Raises:
            ValueError: If source directory is empty or destination conflicts exist
        """

        logger.info(f"Moving directory: {source_directory} -> {destination_directory}")

        # Normalize directory paths (remove trailing slashes)
        source_directory = source_directory.strip("/")
        destination_directory = destination_directory.strip("/")

        # Find all entities in the source directory
        async with db.scoped_session(self.session_maker) as session:
            entities = await self.repository.find_by_directory_prefix(session, source_directory)

        if not entities:
            logger.warning(f"No entities found in directory: {source_directory}")
            return DirectoryMoveResult(
                total_files=0,
                successful_moves=0,
                failed_moves=0,
                moved_files=[],
                errors=[],
            )

        # Track results
        moved_files: list[str] = []
        errors: list[DirectoryMoveError] = []
        successful_moves = 0
        failed_moves = 0

        # Process each entity
        for entity in entities:
            try:
                # Calculate new path by replacing source prefix with destination
                old_path = entity.file_path
                # Replace only the first occurrence of the source directory prefix
                if old_path.startswith(f"{source_directory}/"):
                    new_path = old_path.replace(
                        f"{source_directory}/", f"{destination_directory}/", 1
                    )
                else:  # pragma: no cover
                    # Entity is directly in the source directory (shouldn't happen with prefix match)
                    new_path = f"{destination_directory}/{old_path}"

                # Move the individual entity
                await self.move_entity(
                    identifier=entity.file_path,
                    destination_path=new_path,
                    project_config=project_config,
                    app_config=app_config,
                )

                moved_files.append(new_path)
                successful_moves += 1
                logger.debug(f"Moved entity: {old_path} -> {new_path}")

            except Exception as e:  # pragma: no cover
                failed_moves += 1
                errors.append(DirectoryMoveError(path=entity.file_path, error=str(e)))
                logger.error(f"Failed to move entity {entity.file_path}: {e}")

        logger.info(
            f"Directory move complete: {successful_moves} succeeded, {failed_moves} failed "
            f"(source={source_directory}, dest={destination_directory})"
        )

        return DirectoryMoveResult(
            total_files=len(entities),
            successful_moves=successful_moves,
            failed_moves=failed_moves,
            moved_files=moved_files,
            errors=errors,
        )

    async def delete_directory(
        self,
        directory: str,
    ) -> DirectoryDeleteResult:
        """Delete all entities in a directory.

        This operation deletes all files within a directory, updating database
        records and search indexes. The operation tracks successes and failures
        individually to provide detailed feedback.

        Args:
            directory: Directory path relative to project root

        Returns:
            DirectoryDeleteResult with counts and details of deleted files
        """
        logger.info(f"Deleting directory: {directory}")

        # Normalize directory path (remove trailing slashes)
        directory = directory.strip("/")

        # Find all entities in the directory
        async with db.scoped_session(self.session_maker) as session:
            entities = await self.repository.find_by_directory_prefix(session, directory)

        if not entities:
            logger.warning(f"No entities found in directory: {directory}")
            return DirectoryDeleteResult(
                total_files=0,
                successful_deletes=0,
                failed_deletes=0,
                deleted_files=[],
                errors=[],
            )

        # Track results
        deleted_files: list[str] = []
        errors: list[DirectoryDeleteError] = []
        successful_deletes = 0
        failed_deletes = 0

        # Process each entity
        for entity in entities:
            try:
                file_path = entity.file_path

                # Delete the entity (this handles file deletion and database cleanup)
                deleted = await self.delete_entity(entity.id)

                if deleted:
                    deleted_files.append(file_path)
                    successful_deletes += 1
                    logger.debug(f"Deleted entity: {file_path}")
                else:  # pragma: no cover
                    failed_deletes += 1
                    errors.append(
                        DirectoryDeleteError(path=file_path, error="Delete returned False")
                    )
                    logger.warning(f"Delete returned False for entity: {file_path}")

            except Exception as e:  # pragma: no cover
                failed_deletes += 1
                errors.append(DirectoryDeleteError(path=entity.file_path, error=str(e)))
                logger.error(f"Failed to delete entity {entity.file_path}: {e}")

        logger.info(
            f"Directory delete complete: {successful_deletes} succeeded, {failed_deletes} failed "
            f"(directory={directory})"
        )

        return DirectoryDeleteResult(
            total_files=len(entities),
            successful_deletes=successful_deletes,
            failed_deletes=failed_deletes,
            deleted_files=deleted_files,
            errors=errors,
        )
