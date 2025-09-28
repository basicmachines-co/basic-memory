"""Service for syncing files between filesystem and database."""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from loguru import logger
from sqlalchemy.exc import IntegrityError

from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import has_frontmatter
from basic_memory.markdown import EntityParser
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository, RelationRepository
from basic_memory.services import EntityService, FileService
from basic_memory.services.search_service import SearchService
from basic_memory.services.sync_status_service import sync_status_tracker, SyncStatus


@dataclass
class SyncReport:
    """Report of file changes found compared to database state.

    Attributes:
        total: Total number of files in directory being synced
        new: Files that exist on disk but not in database
        modified: Files that exist in both but have different checksums
        deleted: Files that exist in database but not on disk
        moves: Files that have been moved from one location to another
        checksums: Current checksums for files on disk
    """

    # We keep paths as strings in sets/dicts for easier serialization
    new: Set[str] = field(default_factory=set)
    modified: Set[str] = field(default_factory=set)
    deleted: Set[str] = field(default_factory=set)
    moves: Dict[str, str] = field(default_factory=dict)  # old_path -> new_path
    checksums: Dict[str, str] = field(default_factory=dict)  # path -> checksum

    @property
    def total(self) -> int:
        """Total number of changes."""
        return len(self.new) + len(self.modified) + len(self.deleted) + len(self.moves)


@dataclass
class ScanResult:
    """Result of scanning a directory."""

    # file_path -> checksum
    files: Dict[str, str] = field(default_factory=dict)

    # checksum -> file_path
    checksums: Dict[str, str] = field(default_factory=dict)

    # file_path -> error message
    errors: Dict[str, str] = field(default_factory=dict)


class SyncService:
    """Syncs documents and knowledge files with database."""

    def __init__(
        self,
        app_config: BasicMemoryConfig,
        entity_service: EntityService,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        relation_repository: RelationRepository,
        search_service: SearchService,
        file_service: FileService,
    ):
        self.app_config = app_config
        self.entity_service = entity_service
        self.entity_parser = entity_parser
        self.entity_repository = entity_repository
        self.relation_repository = relation_repository
        self.search_service = search_service
        self.file_service = file_service
        self._thread_pool = ThreadPoolExecutor(max_workers=app_config.sync_thread_pool_size)

    async def _read_file_async(self, file_path: Path) -> str:
        """Read file content in thread pool to avoid blocking the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._thread_pool, file_path.read_text, "utf-8")

    async def _compute_checksum_async(self, path: str) -> str:
        """Compute file checksum in thread pool to avoid blocking the event loop."""

        def _sync_compute_checksum(path_str: str) -> str:
            # Synchronous version for thread pool execution
            path_obj = self.file_service.base_path / path_str

            if self.file_service.is_markdown(path_str):
                content = path_obj.read_text(encoding="utf-8")
            else:
                content = path_obj.read_bytes()

            # Use the synchronous version of compute_checksum
            import hashlib

            if isinstance(content, str):
                content_bytes = content.encode("utf-8")
            else:
                content_bytes = content
            return hashlib.sha256(content_bytes).hexdigest()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._thread_pool, _sync_compute_checksum, path)

    def __del__(self):
        """Cleanup thread pool when service is destroyed."""
        if hasattr(self, "_thread_pool"):
            self._thread_pool.shutdown(wait=False)

    async def sync(self, directory: Path, project_name: Optional[str] = None) -> SyncReport:
        """Sync all files with database."""

        start_time = time.time()
        logger.info(f"Sync operation started for directory: {directory}")

        # Start tracking sync for this project if project name provided
        if project_name:
            sync_status_tracker.start_project_sync(project_name)

        # initial paths from db to sync
        # path -> checksum
        report = await self.scan(directory)

        # Update progress with file counts
        if project_name:
            sync_status_tracker.update_project_progress(
                project_name=project_name,
                status=SyncStatus.SYNCING,
                message="Processing file changes",
                files_total=report.total,
                files_processed=0,
            )

        # order of sync matters to resolve relations effectively
        logger.info(
            f"Sync changes detected: new_files={len(report.new)}, modified_files={len(report.modified)}, "
            + f"deleted_files={len(report.deleted)}, moved_files={len(report.moves)}"
        )

        files_processed = 0

        # sync moves first
        for old_path, new_path in report.moves.items():
            # in the case where a file has been deleted and replaced by another file
            # it will show up in the move and modified lists, so handle it in modified
            if new_path in report.modified:
                report.modified.remove(new_path)
                logger.debug(
                    f"File marked as moved and modified: old_path={old_path}, new_path={new_path}"
                )
            else:
                await self.handle_move(old_path, new_path)

            files_processed += 1
            if project_name:
                sync_status_tracker.update_project_progress(  # pragma: no cover
                    project_name=project_name,
                    status=SyncStatus.SYNCING,
                    message="Processing moves",
                    files_processed=files_processed,
                )

        # deleted next
        for path in report.deleted:
            await self.handle_delete(path)
            files_processed += 1
            if project_name:
                sync_status_tracker.update_project_progress(  # pragma: no cover
                    project_name=project_name,
                    status=SyncStatus.SYNCING,
                    message="Processing deletions",
                    files_processed=files_processed,
                )

        # then new and modified
        for path in report.new:
            await self.sync_file(path, new=True)
            files_processed += 1
            if project_name:
                sync_status_tracker.update_project_progress(
                    project_name=project_name,
                    status=SyncStatus.SYNCING,
                    message="Processing new files",
                    files_processed=files_processed,
                )

        for path in report.modified:
            await self.sync_file(path, new=False)
            files_processed += 1
            if project_name:
                sync_status_tracker.update_project_progress(  # pragma: no cover
                    project_name=project_name,
                    status=SyncStatus.SYNCING,
                    message="Processing modified files",
                    files_processed=files_processed,
                )

        await self.resolve_relations()

        # Mark sync as completed
        if project_name:
            sync_status_tracker.complete_project_sync(project_name)

        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"Sync operation completed: directory={directory}, total_changes={report.total}, duration_ms={duration_ms}"
        )

        return report

    async def scan(self, directory):
        """Scan directory for changes compared to database state."""

        db_paths = await self.get_db_file_state()
        logger.info(f"Scanning directory {directory}. Found {len(db_paths)} db paths")

        # Track potentially moved files by checksum
        scan_result = await self.scan_directory(directory)
        report = SyncReport()

        # First find potential new files and record checksums
        # if a path is not present in the db, it could be new or could be the destination of a move
        for file_path, checksum in scan_result.files.items():
            if file_path not in db_paths:
                report.new.add(file_path)
                report.checksums[file_path] = checksum

        # Now detect moves and deletions
        for db_path, db_checksum in db_paths.items():
            local_checksum_for_db_path = scan_result.files.get(db_path)

            # file not modified
            if db_checksum == local_checksum_for_db_path:
                pass

            # if checksums don't match for the same path, its modified
            if local_checksum_for_db_path and db_checksum != local_checksum_for_db_path:
                report.modified.add(db_path)
                report.checksums[db_path] = local_checksum_for_db_path

            # check if it's moved or deleted
            if not local_checksum_for_db_path:
                # if we find the checksum in another file, it's a move
                if db_checksum in scan_result.checksums:
                    new_path = scan_result.checksums[db_checksum]
                    report.moves[db_path] = new_path

                    # Remove from new files if present
                    if new_path in report.new:
                        report.new.remove(new_path)

                # deleted
                else:
                    report.deleted.add(db_path)
        logger.info(f"Completed scan for directory {directory}, found {report.total} changes.")
        return report

    async def get_db_file_state(self) -> Dict[str, str]:
        """Get file_path and checksums from database.
        Args:
            db_records: database records
        Returns:
            Dict mapping file paths to FileState
            :param db_records: the data from the db
        """
        db_records = await self.entity_repository.find_all()
        logger.info(f"Found {len(db_records)} db records")
        return {r.file_path: r.checksum or "" for r in db_records}

    async def sync_file(
        self, path: str, new: bool = True
    ) -> Tuple[Optional[Entity], Optional[str]]:
        """Sync a single file.

        Args:
            path: Path to file to sync
            new: Whether this is a new file

        Returns:
            Tuple of (entity, checksum) or (None, None) if sync fails
        """
        try:
            logger.debug(
                f"Syncing file path={path} is_new={new} is_markdown={self.file_service.is_markdown(path)}"
            )

            if self.file_service.is_markdown(path):
                entity, checksum = await self.sync_markdown_file(path, new)
            else:
                entity, checksum = await self.sync_regular_file(path, new)

            if entity is not None:
                await self.search_service.index_entity(entity)

                logger.debug(
                    f"File sync completed, path={path}, entity_id={entity.id}, checksum={checksum[:8]}"
                )
            return entity, checksum

        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to sync file: path={path}, error={str(e)}")
            return None, None

    async def sync_markdown_file(self, path: str, new: bool = True) -> Tuple[Optional[Entity], str]:
        """Sync a markdown file with full processing.

        Args:
            path: Path to markdown file
            new: Whether this is a new file

        Returns:
            Tuple of (entity, checksum)
        """
        # Parse markdown first to get any existing permalink
        logger.debug(f"Parsing markdown file, path: {path}, new: {new}")

        file_path = self.entity_parser.base_path / path
        file_content = await self._read_file_async(file_path)
        file_contains_frontmatter = has_frontmatter(file_content)

        # entity markdown will always contain front matter, so it can be used up create/update the entity
        entity_markdown = await self.entity_parser.parse_file(path)

        # if the file contains frontmatter, resolve a permalink
        if file_contains_frontmatter:
            # Resolve permalink - this handles all the cases including conflicts
            permalink = await self.entity_service.resolve_permalink(path, markdown=entity_markdown)

            # If permalink changed, update the file
            if permalink != entity_markdown.frontmatter.permalink:
                logger.info(
                    f"Updating permalink for path: {path}, old_permalink: {entity_markdown.frontmatter.permalink}, new_permalink: {permalink}"
                )

                entity_markdown.frontmatter.metadata["permalink"] = permalink
                await self.file_service.update_frontmatter(path, {"permalink": permalink})

        # if the file is new, create an entity
        if new:
            # Create entity with final permalink
            logger.debug(f"Creating new entity from markdown, path={path}")
            await self.entity_service.create_entity_from_markdown(Path(path), entity_markdown)

        # otherwise we need to update the entity and observations
        else:
            logger.debug(f"Updating entity from markdown, path={path}")
            await self.entity_service.update_entity_and_observations(Path(path), entity_markdown)

        # Update relations and search index
        entity = await self.entity_service.update_entity_relations(path, entity_markdown)

        # After updating relations, we need to compute the checksum again
        # This is necessary for files with wikilinks to ensure consistent checksums
        # after relation processing is complete
        final_checksum = await self._compute_checksum_async(path)

        # set checksum
        await self.entity_repository.update(entity.id, {"checksum": final_checksum})

        logger.debug(
            f"Markdown sync completed: path={path}, entity_id={entity.id}, "
            f"observation_count={len(entity.observations)}, relation_count={len(entity.relations)}, "
            f"checksum={final_checksum[:8]}"
        )

        # Return the final checksum to ensure everything is consistent
        return entity, final_checksum

    async def sync_regular_file(self, path: str, new: bool = True) -> Tuple[Optional[Entity], str]:
        """Sync a non-markdown file with basic tracking.

        Args:
            path: Path to file
            new: Whether this is a new file

        Returns:
            Tuple of (entity, checksum)
        """
        checksum = await self._compute_checksum_async(path)
        if new:
            # Generate permalink from path
            await self.entity_service.resolve_permalink(path)

            # get file timestamps
            file_stats = self.file_service.file_stats(path)
            created = datetime.fromtimestamp(file_stats.st_ctime).astimezone()
            modified = datetime.fromtimestamp(file_stats.st_mtime).astimezone()

            # get mime type
            content_type = self.file_service.content_type(path)

            file_path = Path(path)
            try:
                entity = await self.entity_repository.add(
                    Entity(
                        entity_type="file",
                        file_path=path,
                        checksum=checksum,
                        title=file_path.name,
                        created_at=created,
                        updated_at=modified,
                        content_type=content_type,
                    )
                )
                return entity, checksum
            except IntegrityError as e:
                # Handle race condition where entity was created by another process
                if "UNIQUE constraint failed: entity.file_path" in str(e):
                    logger.info(
                        f"Entity already exists for file_path={path}, updating instead of creating"
                    )
                    # Treat as update instead of create
                    entity = await self.entity_repository.get_by_file_path(path)
                    if entity is None:  # pragma: no cover
                        logger.error(f"Entity not found after constraint violation, path={path}")
                        raise ValueError(f"Entity not found after constraint violation: {path}")

                    updated = await self.entity_repository.update(
                        entity.id, {"file_path": path, "checksum": checksum}
                    )

                    if updated is None:  # pragma: no cover
                        logger.error(f"Failed to update entity, entity_id={entity.id}, path={path}")
                        raise ValueError(f"Failed to update entity with ID {entity.id}")

                    return updated, checksum
                else:
                    # Re-raise if it's a different integrity error
                    raise
        else:
            entity = await self.entity_repository.get_by_file_path(path)
            if entity is None:  # pragma: no cover
                logger.error(f"Entity not found for existing file, path={path}")
                raise ValueError(f"Entity not found for existing file: {path}")

            updated = await self.entity_repository.update(
                entity.id, {"file_path": path, "checksum": checksum}
            )

            if updated is None:  # pragma: no cover
                logger.error(f"Failed to update entity, entity_id={entity.id}, path={path}")
                raise ValueError(f"Failed to update entity with ID {entity.id}")

            return updated, checksum

    async def handle_delete(self, file_path: str):
        """Handle complete entity deletion including search index cleanup."""

        # First get entity to get permalink before deletion
        entity = await self.entity_repository.get_by_file_path(file_path)
        if entity:
            logger.info(
                f"Deleting entity with file_path={file_path}, entity_id={entity.id}, permalink={entity.permalink}"
            )

            # Delete from db (this cascades to observations/relations)
            await self.entity_service.delete_entity_by_file_path(file_path)

            # Clean up search index
            permalinks = (
                [entity.permalink]
                + [o.permalink for o in entity.observations]
                + [r.permalink for r in entity.relations]
            )

            logger.debug(
                f"Cleaning up search index for entity_id={entity.id}, file_path={file_path}, "
                f"index_entries={len(permalinks)}"
            )

            for permalink in permalinks:
                if permalink:
                    await self.search_service.delete_by_permalink(permalink)
                else:
                    await self.search_service.delete_by_entity_id(entity.id)

    async def handle_move(self, old_path, new_path):
        logger.debug("Moving entity", old_path=old_path, new_path=new_path)

        entity = await self.entity_repository.get_by_file_path(old_path)
        if entity:
            # Check if destination path is already occupied by another entity
            existing_at_destination = await self.entity_repository.get_by_file_path(new_path)
            if existing_at_destination and existing_at_destination.id != entity.id:
                # Handle the conflict - this could be a file swap or replacement scenario
                logger.warning(
                    f"File path conflict detected during move: "
                    f"entity_id={entity.id} trying to move from '{old_path}' to '{new_path}', "
                    f"but entity_id={existing_at_destination.id} already occupies '{new_path}'"
                )

                # Check if this is a file swap (the destination entity is being moved to our old path)
                # This would indicate a simultaneous move operation
                old_path_after_swap = await self.entity_repository.get_by_file_path(old_path)
                if old_path_after_swap and old_path_after_swap.id == existing_at_destination.id:
                    logger.info(f"Detected file swap between '{old_path}' and '{new_path}'")
                    # This is a swap scenario - both moves should succeed
                    # We'll allow this to proceed since the other file has moved out
                else:
                    # This is a conflict where the destination is occupied
                    raise ValueError(
                        f"Cannot move entity from '{old_path}' to '{new_path}': "
                        f"destination path is already occupied by another file. "
                        f"This may be caused by: "
                        f"1. Conflicting file names with different character encodings, "
                        f"2. Case sensitivity differences (e.g., 'Finance/' vs 'finance/'), "
                        f"3. Character conflicts between hyphens in filenames and generated permalinks, "
                        f"4. Files with similar names containing special characters. "
                        f"Try renaming one of the conflicting files to resolve this issue."
                    )

            # Update file_path in all cases
            updates = {"file_path": new_path}

            # If configured, also update permalink to match new path
            if self.app_config.update_permalinks_on_move and self.file_service.is_markdown(
                new_path
            ):
                # generate new permalink value
                new_permalink = await self.entity_service.resolve_permalink(new_path)

                # write to file and get new checksum
                new_checksum = await self.file_service.update_frontmatter(
                    new_path, {"permalink": new_permalink}
                )

                updates["permalink"] = new_permalink
                updates["checksum"] = new_checksum

                logger.info(
                    f"Updating permalink on move,old_permalink={entity.permalink}"
                    f"new_permalink={new_permalink}"
                    f"new_checksum={new_checksum}"
                )

            try:
                updated = await self.entity_repository.update(entity.id, updates)
            except Exception as e:
                # Catch any database integrity errors and provide helpful context
                if "UNIQUE constraint failed" in str(e):
                    logger.error(
                        f"Database constraint violation during move: "
                        f"entity_id={entity.id}, old_path='{old_path}', new_path='{new_path}'"
                    )
                    raise ValueError(
                        f"Cannot complete move from '{old_path}' to '{new_path}': "
                        f"a database constraint was violated. This usually indicates "
                        f"a file path or permalink conflict. Please check for: "
                        f"1. Duplicate file names, "
                        f"2. Case sensitivity issues (e.g., 'File.md' vs 'file.md'), "
                        f"3. Character encoding conflicts in file names."
                    ) from e
                else:
                    # Re-raise other exceptions as-is
                    raise

            if updated is None:  # pragma: no cover
                logger.error(
                    "Failed to update entity path"
                    f"entity_id={entity.id}"
                    f"old_path={old_path}"
                    f"new_path={new_path}"
                )
                raise ValueError(f"Failed to update entity path for ID {entity.id}")

            logger.debug(
                "Entity path updated"
                f"entity_id={entity.id} "
                f"permalink={entity.permalink} "
                f"old_path={old_path} "
                f"new_path={new_path} "
            )

            # update search index
            await self.search_service.index_entity(updated)

    async def resolve_relations(self, entity_id: int | None = None):
        """Try to resolve unresolved relations.

        Args:
            entity_id: If provided, only resolve relations for this specific entity.
                      Otherwise, resolve all unresolved relations in the database.
        """

        if entity_id:
            # Only get unresolved relations for the specific entity
            unresolved_relations = (
                await self.relation_repository.find_unresolved_relations_for_entity(entity_id)
            )
            logger.info(
                f"Resolving forward references for entity {entity_id}",
                count=len(unresolved_relations),
            )
        else:
            # Get all unresolved relations (original behavior)
            unresolved_relations = await self.relation_repository.find_unresolved_relations()
            logger.info("Resolving all forward references", count=len(unresolved_relations))

        for relation in unresolved_relations:
            logger.trace(
                "Attempting to resolve relation "
                f"relation_id={relation.id} "
                f"from_id={relation.from_id} "
                f"to_name={relation.to_name}"
            )

            resolved_entity = await self.entity_service.link_resolver.resolve_link(relation.to_name)

            # ignore reference to self
            if resolved_entity and resolved_entity.id != relation.from_id:
                logger.debug(
                    "Resolved forward reference "
                    f"relation_id={relation.id} "
                    f"from_id={relation.from_id} "
                    f"to_name={relation.to_name} "
                    f"resolved_id={resolved_entity.id} "
                    f"resolved_title={resolved_entity.title}",
                )
                try:
                    await self.relation_repository.update(
                        relation.id,
                        {
                            "to_id": resolved_entity.id,
                            "to_name": resolved_entity.title,
                        },
                    )
                except IntegrityError:  # pragma: no cover
                    logger.debug(
                        "Ignoring duplicate relation "
                        f"relation_id={relation.id} "
                        f"from_id={relation.from_id} "
                        f"to_name={relation.to_name}"
                    )

                # update search index
                await self.search_service.index_entity(resolved_entity)

    async def scan_directory(self, directory: Path) -> ScanResult:
        """
        Scan directory for markdown files and their checksums.

        Args:
            directory: Directory to scan

        Returns:
            ScanResult containing found files and any errors
        """
        start_time = time.time()

        logger.debug(f"Scanning directory {directory}")
        result = ScanResult()

        for root, dirnames, filenames in os.walk(str(directory)):
            # Skip dot directories in-place
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            for filename in filenames:
                # Skip dot files
                if filename.startswith("."):
                    continue

                path = Path(root) / filename
                rel_path = path.relative_to(directory).as_posix()
                checksum = await self._compute_checksum_async(rel_path)
                result.files[rel_path] = checksum
                result.checksums[checksum] = rel_path

                logger.trace(f"Found file, path={rel_path}, checksum={checksum}")

        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug(
            f"{directory} scan completed "
            f"directory={str(directory)} "
            f"files_found={len(result.files)} "
            f"duration_ms={duration_ms}"
        )

        return result
