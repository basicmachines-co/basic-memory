"""Repository for managing projects in Basic Memory."""

from datetime import datetime
from pathlib import Path
from typing import Any, override, Optional, Sequence, Union


from loguru import logger
from sqlalchemy import Executable, inspect as sa_inspect, select, text, update
from sqlalchemy.exc import NoResultFound, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models.project import AcceptedProjectNoteChange, Project
from basic_memory.repository.repository import Repository
from basic_memory.runtime.project_partition import RuntimeAcceptedProjectNoteChange


async def _load_sqlite_vec_on_session(session) -> bool:
    """Ensure the sqlite-vec extension is loaded on this session's connection.

    Returns True when vec0 is available after the call. Returns False when the
    extension can't be loaded on this Python build (e.g., python.org macOS or
    Windows interpreters without `enable_load_extension`) — every connection in
    the pool shares the same interpreter, so a False here also means no
    embedding row could ever have been written, and skipping the embeddings
    purge is safe.

    Mirrors SQLiteSearchRepository._ensure_sqlite_vec_loaded but as a free
    function: we don't have a SearchRepository instance during project delete,
    and the per-connection nature of extension loading means a pooled connection
    routed to this session might not have vec loaded even when other
    connections wrote embeddings.
    """
    try:
        await session.execute(text("SELECT vec_version()"))
        return True
    except OperationalError:
        pass

    try:
        import sqlite_vec  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("sqlite-vec package not installed; skipping vec purge")
        return False

    async_connection = await session.connection()
    raw_connection = await async_connection.get_raw_connection()
    driver_connection = raw_connection.driver_connection

    if not hasattr(driver_connection, "enable_load_extension"):
        # Trigger: CPython build without sqlite extension support (#711).
        # Why: load_extension is unavailable, so no connection in this pool
        #      can host vec0. No embeddings exist anywhere.
        # Outcome: skip the embeddings purge entirely.
        logger.debug(
            "Skipping search_vector_embeddings purge: this Python build does "
            "not support SQLite extension loading"
        )
        return False

    try:
        await driver_connection.enable_load_extension(True)
        await driver_connection.load_extension(sqlite_vec.loadable_path())
        await driver_connection.enable_load_extension(False)
        await session.execute(text("SELECT vec_version()"))
    except Exception as exc:
        logger.warning(
            "Failed to load sqlite-vec for project delete cleanup; skipping embeddings purge: {}",
            exc,
        )
        return False

    return True


class ProjectRepository(Repository[Project]):
    """Repository for Project model.

    Projects represent collections of knowledge entities grouped together.
    Each entity, observation, and relation belongs to a specific project.
    """

    def __init__(self):
        """Initialize the repository."""
        super().__init__(Project)

    async def get_by_name(self, session: AsyncSession, name: str) -> Optional[Project]:
        """Get project by name (exact match).

        Args:
            name: Unique name of the project
        """
        query = self.select().where(Project.name == name)
        return await self.find_one(session, query)

    async def get_by_name_case_insensitive(
        self, session: AsyncSession, name: str
    ) -> Optional[Project]:
        """Get project by name (case-insensitive match).

        Args:
            name: Project name (case-insensitive)

        Returns:
            Project if found, None otherwise
        """
        query = self.select().where(Project.name.ilike(name))
        return await self.find_one(session, query)

    async def get_by_permalink(self, session: AsyncSession, permalink: str) -> Optional[Project]:
        """Get project by permalink.

        Args:
            permalink: URL-friendly identifier for the project
        """
        query = self.select().where(Project.permalink == permalink)
        return await self.find_one(session, query)

    async def get_by_path(self, session: AsyncSession, path: Union[Path, str]) -> Optional[Project]:
        """Get project by filesystem path.

        Args:
            path: Path to the project directory (will be converted to string internally)
        """
        query = self.select().where(Project.path == Path(path).as_posix())
        return await self.find_one(session, query)

    async def get_by_id(self, session: AsyncSession, project_id: int) -> Optional[Project]:
        """Get project by numeric ID.

        Args:
            project_id: Numeric project ID

        Returns:
            Project if found, None otherwise
        """
        return await self.select_by_id(session, project_id)

    async def get_by_external_id(
        self, session: AsyncSession, external_id: str
    ) -> Optional[Project]:
        """Get project by external UUID.

        Args:
            external_id: External UUID identifier

        Returns:
            Project if found, None otherwise
        """
        query = self.select().where(Project.external_id == external_id)
        return await self.find_one(session, query)

    async def get_default_project(self, session: AsyncSession) -> Optional[Project]:
        """Get the default project (the one marked as is_default=True)."""
        query = self.select().where(Project.is_default.is_(True))
        return await self.find_one(session, query)

    async def advance_partition_position(
        self,
        session: AsyncSession,
        project_id: int,
    ) -> int:
        """Atomically claim the next strict position in one project partition."""
        statement = (
            update(Project)
            .where(Project.id == project_id)
            .values(partition_position=Project.partition_position + 1)
            .returning(Project.partition_position)
            .execution_options(synchronize_session=False)
        )
        position = (await session.execute(statement)).scalar_one_or_none()
        if position is None:
            raise RuntimeError(f"Project partition is missing for project_id={project_id}")
        return int(position)

    async def record_accepted_note_change(
        self,
        session: AsyncSession,
        change: RuntimeAcceptedProjectNoteChange,
    ) -> None:
        """Persist replayable accepted evidence in the mutation transaction."""
        persisted = AcceptedProjectNoteChange(
            project_id=change.project_id,
            project_external_id=change.project_external_id,
            partition_position=change.partition_position,
            entity_id=change.entity_id,
            note_external_id=change.note_external_id,
            permalink=change.permalink,
            title=change.title,
            operation=change.operation.value,
            file_path=change.file_path,
            previous_file_path=change.previous_file_path,
            accepted_at=change.accepted_at,
            source=change.source,
            db_version=change.db_version,
            db_checksum=change.db_checksum,
            actor_user_profile_id=(
                str(change.actor_user_profile_id)
                if change.actor_user_profile_id is not None
                else None
            ),
            actor_kind=change.actor_kind,
            actor_name=change.actor_name,
        )
        session.add(persisted)
        await session.flush()

    async def list_accepted_note_changes(
        self,
        session: AsyncSession,
        project_id: int,
        *,
        after_position: int = 0,
        through_position: int | None = None,
    ) -> Sequence[AcceptedProjectNoteChange]:
        """List one contiguous project evidence range in strict order."""
        statement = select(AcceptedProjectNoteChange).where(
            AcceptedProjectNoteChange.project_id == project_id,
            AcceptedProjectNoteChange.partition_position > after_position,
        )
        if through_position is not None:
            statement = statement.where(
                AcceptedProjectNoteChange.partition_position <= through_position
            )
        result = await session.execute(
            statement.order_by(AcceptedProjectNoteChange.partition_position)
        )
        return tuple(result.scalars().all())

    async def mark_accepted_note_change_materialized(
        self,
        session: AsyncSession,
        project_id: int,
        partition_position: int,
        *,
        materialized_at: datetime,
    ) -> bool:
        """Record when this note's accepted evidence reached canonical storage."""
        target_note_external_id = (
            await session.execute(
                select(AcceptedProjectNoteChange.note_external_id).where(
                    AcceptedProjectNoteChange.project_id == project_id,
                    AcceptedProjectNoteChange.partition_position == partition_position,
                )
            )
        ).scalar_one_or_none()
        if target_note_external_id is None:
            return False

        # A newer materialized generation is canonical evidence that every older
        # accepted generation of this note has been superseded. Retire those rows
        # together so an obsolete queued write cannot block downstream projectors.
        statement = (
            update(AcceptedProjectNoteChange)
            .where(
                AcceptedProjectNoteChange.project_id == project_id,
                AcceptedProjectNoteChange.note_external_id == target_note_external_id,
                AcceptedProjectNoteChange.partition_position <= partition_position,
                AcceptedProjectNoteChange.materialized_at.is_(None),
            )
            .values(materialized_at=materialized_at)
            .returning(AcceptedProjectNoteChange.id)
            .execution_options(synchronize_session=False)
        )
        return bool((await session.execute(statement)).scalars().all())

    async def get_active_projects(self, session: AsyncSession) -> Sequence[Project]:
        """Get all active projects."""
        query = self.select().where(Project.is_active == True)  # noqa: E712
        result = await self.execute_query(session, query)
        return list(result.scalars().all())

    async def set_as_default(self, session: AsyncSession, project_id: int) -> Optional[Project]:
        """Set a project as the default and unset previous default.

        Args:
            project_id: ID of the project to set as default

        Returns:
            The updated project if found, None otherwise
        """
        target_project = await self.select_by_id(session, project_id)
        if not target_project:
            return None  # pragma: no cover

        # Preserve the target row while clearing previous defaults. Clearing an already-default
        # target through bulk SQL leaves its ORM identity-map value at True, so assigning True
        # again emits no UPDATE and otherwise persists a database with no default project.
        await session.execute(
            text(
                "UPDATE project SET is_default = NULL "
                "WHERE id != :project_id AND is_default IS NOT NULL"
            ),
            {"project_id": project_id},
        )

        target_project.is_default = True
        await session.flush()
        return target_project

    @override
    async def delete(self, session: AsyncSession, entity_id: int) -> bool:
        """Delete a project and its derived search rows in one transaction.

        The cascade picture differs by backend:

        - search_index → project: Postgres has ON DELETE CASCADE FK; SQLite
          stores search_index as an FTS5 virtual table and can't carry FKs,
          so it needs explicit cleanup.
        - search_vector_chunks → project: neither backend has an FK here, so
          both need an explicit DELETE.
        - search_vector_embeddings → search_vector_chunks: Postgres has an FK
          (chunk_id REFERENCES … ON DELETE CASCADE); SQLite stores embeddings
          in a vec0 virtual table keyed by rowid with no cascade. On SQLite
          the embeddings must be purged before the chunk rows, otherwise
          `_run_vector_query` keeps returning stale vectors that crowd out
          live results.

        Each derived table is created lazily (search_index by
        SearchRepository.init_search_index, the vector tables once semantic
        search initializes), so any of them may be absent on minimal test DBs.
        Inspect the connection once and skip whichever is missing.
        """
        logger.debug(f"Deleting Project and search rows for project_id: {entity_id}")
        try:
            result = await session.execute(select(self.Model).filter(self.primary_key == entity_id))
            project = result.scalars().one()
        except NoResultFound:
            logger.debug(f"No Project found to delete: {entity_id}")
            return False

        dialect_name = session.bind.dialect.name if session.bind else "sqlite"
        is_sqlite = dialect_name == "sqlite"

        existing_tables = await session.run_sync(
            lambda sync_session: set(sa_inspect(sync_session.connection()).get_table_names())
        )

        # search_index: SQLite has no FK on the FTS5 virtual table; Postgres
        # cascades from the project FK, so the explicit DELETE is redundant.
        if is_sqlite and "search_index" in existing_tables:
            await session.execute(
                text("DELETE FROM search_index WHERE project_id = :project_id"),
                {"project_id": entity_id},
            )

        # search_vector_chunks: no FK to project on either backend, so both
        # backends need this. SQLite must purge vec0 embeddings first
        # (rowid pseudocolumn — Postgres uses chunk_id and would 500 here);
        # Postgres' chunk_id FK CASCADE handles its embeddings cleanup when
        # we delete the chunk rows below.
        if "search_vector_chunks" in existing_tables:
            if is_sqlite and "search_vector_embeddings" in existing_tables:
                # Extension loading is per-connection. We must load vec0 on
                # *this* session before the DELETE; otherwise a different
                # pooled connection might have written embeddings that we'd
                # silently leave behind.
                if await _load_sqlite_vec_on_session(session):
                    await session.execute(
                        text(
                            "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                            "SELECT id FROM search_vector_chunks "
                            "WHERE project_id = :project_id)"
                        ),
                        {"project_id": entity_id},
                    )
            await session.execute(
                text("DELETE FROM search_vector_chunks WHERE project_id = :project_id"),
                {"project_id": entity_id},
            )

        await session.delete(project)
        await session.flush()
        logger.debug(f"Deleted Project and search rows for project_id: {entity_id}")
        return True

    async def scalar_vec_query(
        self, session: AsyncSession, query: Executable, params: Optional[dict[str, Any]] = None
    ) -> Optional[int]:
        """Run a scalar COUNT query that reads the sqlite-vec vec0 table.

        Extension loading is per-connection, so the bare pooled session used by
        `execute_query` cannot read vec0 virtual tables — SQLite raises
        "no such module: vec0". This helper loads sqlite-vec on the session it
        opens before running the query, reusing the same loader as project delete.

        Returns None when sqlite-vec cannot be loaded on this Python build, so
        callers can fall back to the genuinely-missing-dependency path.
        """
        # Trigger: query reads the vec0-backed search_vector_embeddings table.
        # Why: vec0 modules are only visible on a connection that loaded sqlite-vec.
        # Outcome: load the extension here, or signal absence so the caller degrades.
        if not await _load_sqlite_vec_on_session(session):
            return None
        result = await session.execute(query, params)
        return result.scalar()

    async def update_path(
        self, session: AsyncSession, project_id: int, new_path: str
    ) -> Optional[Project]:
        """Update project path.

        Args:
            project_id: ID of the project to update
            new_path: New filesystem path for the project

        Returns:
            The updated project if found, None otherwise
        """
        project = await self.select_by_id(session, project_id)
        if project:
            project.path = new_path
            await session.flush()
            return project
        return None
