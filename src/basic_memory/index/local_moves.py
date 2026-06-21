"""Local filesystem move detection for event-based indexing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.indexing import (
    ProjectIndexMaintenanceRunner,
    ProjectIndexMovedEntitySearchRefresher,
)
from basic_memory.indexing.project_index_workflow import (
    ProjectIndexMoveContentUpdater,
    ProjectIndexMovedFile,
    ProjectIndexMovedFileContentUpdate,
)
from basic_memory.runtime import RuntimeFilePath, StorageEventPayload
from basic_memory.runtime.contracts import (
    STORAGE_OBJECT_CREATED_EVENTS,
    STORAGE_OBJECT_DELETED_EVENT,
)
from basic_memory.services import FileService


class LocalMoveEntitySource(Protocol):
    """Entity shape needed to match watcher delete/create pairs."""

    @property
    def checksum(self) -> object | None: ...


class LocalMoveEntityRepository(Protocol):
    """Repository capability for local watcher move detection."""

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: RuntimeFilePath,
        *,
        load_relations: bool = True,
    ) -> LocalMoveEntitySource | None: ...


class LocalMoveEntityService(Protocol):
    """Entity service behavior needed to update markdown permalinks on move."""

    @property
    def app_config(self) -> BasicMemoryConfig | None: ...

    async def resolve_permalink(
        self,
        file_path: Path | str,
        markdown: object = None,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class LocalProjectIndexMoveContentUpdater(ProjectIndexMoveContentUpdater):
    """Apply local markdown permalink policy for moved files."""

    entity_service: LocalMoveEntityService
    file_service: FileService

    async def update_moved_file_content(
        self,
        session: AsyncSession,
        moved_file: ProjectIndexMovedFile,
    ) -> ProjectIndexMovedFileContentUpdate | None:
        app_config = self.entity_service.app_config
        if app_config is None:
            raise RuntimeError("local move content updates require app_config")
        if app_config.disable_permalinks or not app_config.update_permalinks_on_move:
            return None
        if not self.file_service.is_markdown(moved_file.new_path):
            return None

        permalink = await self.entity_service.resolve_permalink(
            Path(moved_file.new_path),
            skip_conflict_check=True,
            session=session,
        )
        if permalink == moved_file.old_permalink:
            return None

        update = await self.file_service.update_frontmatter_with_result(
            moved_file.new_path,
            {"permalink": permalink},
        )
        return ProjectIndexMovedFileContentUpdate(
            permalink=permalink,
            checksum=update.checksum,
            markdown_content=update.content,
        )


@dataclass(frozen=True, slots=True)
class LocalWatchMoveProcessingResult:
    """Result of removing matched local move events from a watcher batch."""

    remaining_events: tuple[StorageEventPayload, ...]
    processed_moves: int


@dataclass(frozen=True, slots=True)
class LocalWatchMoveProcessor:
    """Detect local watcher delete/create pairs and apply core move maintenance."""

    session_maker: async_sessionmaker[AsyncSession]
    file_service: FileService
    entity_repository: LocalMoveEntityRepository
    maintenance_runner: ProjectIndexMaintenanceRunner
    moved_entity_search_refresher: ProjectIndexMovedEntitySearchRefresher
    batch_size: int = 100

    async def process_moves(
        self,
        events: Sequence[StorageEventPayload],
    ) -> LocalWatchMoveProcessingResult:
        moved_files, moved_event_indexes = await self.detect_moves(events)
        if not moved_files:
            return LocalWatchMoveProcessingResult(
                remaining_events=tuple(events),
                processed_moves=0,
            )

        move_run = await self.maintenance_runner.run_move_batches(
            moved_files=moved_files,
            batch_size=self.batch_size,
        )
        if move_run.moved_entity_ids:
            await self.moved_entity_search_refresher.refresh_moved_entities(
                sorted(move_run.moved_entity_ids)
            )

        transient_event_indexes = await self.detect_transient_missing_events(
            events,
            exclude_indexes=moved_event_indexes,
        )
        moved_old_paths = set(moved_files) - set(move_run.missing_paths)
        moved_new_paths = {moved_files[old_path] for old_path in moved_old_paths}
        retained_events: list[StorageEventPayload] = []
        for index, event in enumerate(events):
            if index in transient_event_indexes:
                continue
            if (
                index in moved_event_indexes
                and event.relative_path in moved_old_paths | moved_new_paths
            ):
                continue
            retained_events.append(event)

        return LocalWatchMoveProcessingResult(
            remaining_events=tuple(retained_events),
            processed_moves=move_run.total_updated_files,
        )

    async def detect_moves(
        self,
        events: Sequence[StorageEventPayload],
    ) -> tuple[dict[RuntimeFilePath, RuntimeFilePath], set[int]]:
        delete_events = local_watch_delete_events(events)
        create_events = local_watch_create_events(events)
        if not delete_events or not create_events:
            return {}, set()

        deleted_checksums = await self.load_deleted_checksums(
            tuple(event.relative_path for _, event in delete_events if event.relative_path)
        )

        moved_files: dict[RuntimeFilePath, RuntimeFilePath] = {}
        moved_event_indexes: set[int] = set()
        used_delete_indexes: set[int] = set()
        for create_index, create_event in create_events:
            new_path = create_event.relative_path
            if new_path is None:
                continue
            new_checksum = await self.current_checksum(new_path)
            if new_checksum is None:
                continue

            for delete_index, delete_event in delete_events:
                old_path = delete_event.relative_path
                if (
                    old_path is None
                    or delete_index in used_delete_indexes
                    or old_path == new_path
                    or deleted_checksums.get(old_path) != new_checksum
                ):
                    continue

                moved_files[old_path] = new_path
                moved_event_indexes.update({delete_index, create_index})
                used_delete_indexes.add(delete_index)
                break

        return moved_files, moved_event_indexes

    async def detect_transient_missing_events(
        self,
        events: Sequence[StorageEventPayload],
        *,
        exclude_indexes: set[int],
    ) -> set[int]:
        delete_indexes_by_path = local_watch_event_indexes_by_path(
            local_watch_delete_events(events),
            exclude_indexes=exclude_indexes,
        )
        create_indexes_by_path = local_watch_event_indexes_by_path(
            local_watch_create_events(events),
            exclude_indexes=exclude_indexes,
        )
        transient_paths = tuple(
            sorted(set(delete_indexes_by_path).intersection(create_indexes_by_path))
        )
        if not transient_paths:
            return set()

        existing_entity_paths = await self.load_deleted_entity_paths(transient_paths)
        transient_event_indexes: set[int] = set()
        for path in transient_paths:
            if path in existing_entity_paths:
                continue
            if await self.file_service.exists(path):
                continue
            transient_event_indexes.update(delete_indexes_by_path[path])
            transient_event_indexes.update(create_indexes_by_path[path])

        return transient_event_indexes

    async def load_deleted_entity_paths(
        self,
        deleted_paths: Sequence[RuntimeFilePath],
    ) -> set[RuntimeFilePath]:
        if not deleted_paths:
            return set()

        paths: set[RuntimeFilePath] = set()
        async with db.scoped_session(self.session_maker) as session:
            for deleted_path in deleted_paths:
                entity = await self.entity_repository.get_by_file_path(
                    session,
                    deleted_path,
                    load_relations=False,
                )
                if entity is not None:
                    paths.add(deleted_path)
        return paths

    async def load_deleted_checksums(
        self,
        deleted_paths: Sequence[RuntimeFilePath],
    ) -> dict[RuntimeFilePath, str]:
        if not deleted_paths:
            return {}

        checksums: dict[RuntimeFilePath, str] = {}
        async with db.scoped_session(self.session_maker) as session:
            for deleted_path in deleted_paths:
                entity = await self.entity_repository.get_by_file_path(
                    session,
                    deleted_path,
                    load_relations=False,
                )
                if entity is not None and entity.checksum is not None:
                    checksums[deleted_path] = str(entity.checksum)
        return checksums

    async def current_checksum(self, file_path: RuntimeFilePath) -> str | None:
        if not await self.file_service.exists(file_path):
            return None
        return await self.file_service.compute_checksum(file_path)


def local_watch_delete_events(
    events: Sequence[StorageEventPayload],
) -> tuple[tuple[int, StorageEventPayload], ...]:
    """Return deleted-object events with their original batch indexes."""
    return tuple(
        (index, event)
        for index, event in enumerate(events)
        if event.event_name == STORAGE_OBJECT_DELETED_EVENT
    )


def local_watch_create_events(
    events: Sequence[StorageEventPayload],
) -> tuple[tuple[int, StorageEventPayload], ...]:
    """Return created-object events with their original batch indexes."""
    return tuple(
        (index, event)
        for index, event in enumerate(events)
        if event.event_name in STORAGE_OBJECT_CREATED_EVENTS
    )


def local_watch_event_indexes_by_path(
    events: Sequence[tuple[int, StorageEventPayload]],
    *,
    exclude_indexes: set[int],
) -> dict[RuntimeFilePath, set[int]]:
    indexes_by_path: dict[RuntimeFilePath, set[int]] = {}
    for index, event in events:
        if index in exclude_indexes:
            continue
        path = event.relative_path
        if not path:
            continue
        indexes_by_path.setdefault(path, set()).add(index)
    return indexes_by_path
