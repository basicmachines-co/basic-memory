"""Project-index cleanup for entities whose source files disappeared."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.runtime.storage import RuntimeFilePath


class OrphanIndexedEntity(Protocol):
    """Minimal entity shape needed when removing stale indexed files."""

    id: int


class OrphanEntityRepository[EntityT: OrphanIndexedEntity](Protocol):
    """Repository capabilities needed to clean up stale file-backed entities."""

    async def get_all_file_paths(self, session: AsyncSession) -> Sequence[RuntimeFilePath]: ...

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: RuntimeFilePath,
    ) -> EntityT | None: ...

    async def delete_by_fields(
        self,
        session: AsyncSession,
        *,
        id: int,
        file_path: RuntimeFilePath,
    ) -> bool: ...


class OrphanSearchIndex[EntityT: OrphanIndexedEntity](Protocol):
    """Search cleanup capability for an entity that was deleted from storage."""

    async def handle_delete(self, entity: EntityT) -> None: ...


class OrphanCleanupLogger(Protocol):
    """Small logger surface used for skipped cleanup diagnostics."""

    def info(self, message: str, /, **kwargs: object) -> None: ...

    def warning(self, message: str, /, **kwargs: object) -> None: ...


@dataclass(frozen=True, slots=True)
class OrphanEntityCleanupResult:
    """Outcome of removing DB entities no longer backed by source files."""

    orphan_paths: tuple[RuntimeFilePath, ...]
    deleted_paths: tuple[RuntimeFilePath, ...]
    skipped_missing_paths: tuple[RuntimeFilePath, ...]
    skipped_changed_paths: tuple[RuntimeFilePath, ...]

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_paths)


async def cleanup_orphan_entities[EntityT: OrphanIndexedEntity](
    *,
    session_maker: async_sessionmaker[AsyncSession],
    entity_repository: OrphanEntityRepository[EntityT],
    search_service: OrphanSearchIndex[EntityT],
    current_paths: Collection[RuntimeFilePath],
    logger: OrphanCleanupLogger | None = None,
) -> OrphanEntityCleanupResult:
    """Remove indexed entities whose source path is absent from the current file set."""
    async with db.scoped_session(session_maker) as session:
        db_paths = set(await entity_repository.get_all_file_paths(session))

    orphan_paths = tuple(sorted(db_paths - set(current_paths)))
    if not orphan_paths:
        return OrphanEntityCleanupResult(
            orphan_paths=(),
            deleted_paths=(),
            skipped_missing_paths=(),
            skipped_changed_paths=(),
        )

    deleted_paths: list[RuntimeFilePath] = []
    skipped_missing_paths: list[RuntimeFilePath] = []
    skipped_changed_paths: list[RuntimeFilePath] = []
    for orphan_path in orphan_paths:
        async with db.scoped_session(session_maker) as session:
            entity = await entity_repository.get_by_file_path(session, orphan_path)
            if entity is None:
                skipped_missing_paths.append(orphan_path)
                if logger is not None:
                    logger.warning(
                        "Skipping orphan cleanup: entity no longer exists",
                        file_path=orphan_path,
                    )
                continue

            deleted = await entity_repository.delete_by_fields(
                session,
                id=entity.id,
                file_path=orphan_path,
            )
        if not deleted:
            skipped_changed_paths.append(orphan_path)
            if logger is not None:
                logger.info(
                    "Skipping orphan cleanup: entity path changed",
                    entity_id=entity.id,
                    file_path=orphan_path,
                )
            continue

        await search_service.handle_delete(entity)
        deleted_paths.append(orphan_path)

    if logger is not None:
        logger.info(
            "Deleted orphan entities during project reindex",
            orphan_paths=len(orphan_paths),
            deleted_files=len(deleted_paths),
        )
    return OrphanEntityCleanupResult(
        orphan_paths=orphan_paths,
        deleted_paths=tuple(deleted_paths),
        skipped_missing_paths=tuple(skipped_missing_paths),
        skipped_changed_paths=tuple(skipped_changed_paths),
    )
