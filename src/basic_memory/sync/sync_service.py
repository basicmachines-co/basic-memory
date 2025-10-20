"""Service for syncing files between filesystem and database."""

import asyncio
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Set, Tuple

import logfire
from loguru import logger
from sqlalchemy.exc import IntegrityError

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.file_utils import has_frontmatter
from basic_memory.ignore_utils import load_bmignore_patterns, should_ignore_path
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.models import Entity, Project
from basic_memory.repository import EntityRepository, RelationRepository, ObservationRepository
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.services import EntityService, FileService
from basic_memory.services.exceptions import SyncFatalError
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService

# Circuit breaker configuration
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class FileFailureInfo:
    """Track failure information for a file that repeatedly fails to sync.

    Attributes:
        count: Number of consecutive failures
        first_failure: Timestamp of first failure in current sequence
        last_failure: Timestamp of most recent failure
        last_error: Error message from most recent failure
        last_checksum: Checksum of file when it last failed (for detecting file changes)
    """

    count: int
    first_failure: datetime
    last_failure: datetime
    last_error: str
    last_checksum: str


@dataclass
class SkippedFile:
    """Information about a file that was skipped due to repeated failures.

    Attributes:
        path: File path relative to project root
        reason: Error message from last failure
        failure_count: Number of consecutive failures
        first_failed: Timestamp of first failure
    """

    path: str
    reason: str
    failure_count: int
    first_failed: datetime


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
        skipped_files: Files that were skipped due to repeated failures
    """

    # We keep paths as strings in sets/dicts for easier serialization
    new: Set[str] = field(default_factory=set)
    modified: Set[str] = field(default_factory=set)
    deleted: Set[str] = field(default_factory=set)
    moves: Dict[str, str] = field(default_factory=dict)  # old_path -> new_path
    checksums: Dict[str, str] = field(default_factory=dict)  # path -> checksum
    skipped_files: List[SkippedFile] = field(default_factory=list)

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
        # Load ignore patterns once at initialization for performance
        self._ignore_patterns = load_bmignore_patterns()
        # Circuit breaker: track file failures to prevent infinite retry loops
        # Use OrderedDict for LRU behavior with bounded size to prevent unbounded memory growth
        self._file_failures: OrderedDict[str, FileFailureInfo] = OrderedDict()
        self._max_tracked_failures = 100  # Limit failure cache size

    async def _should_skip_file(self, path: str) -> bool:
        """Check if file should be skipped due to repeated failures.

        Computes current file checksum and compares with last failed checksum.
        If checksums differ, file has changed and we should retry.

        Args:
            path: File path to check

        Returns:
            True if file should be skipped, False otherwise
        """
        if path not in self._file_failures:
            return False

        failure_info = self._file_failures[path]

        # Check if failure count exceeds threshold
        if failure_info.count < MAX_CONSECUTIVE_FAILURES:
            return False

        # Compute current checksum to see if file changed
        try:
            current_checksum = await self.file_service.compute_checksum(path)

            # If checksum changed, file was modified - reset and retry
            if current_checksum != failure_info.last_checksum:
                logger.info(
                    f"File {path} changed since last failure (checksum differs), "
                    f"resetting failure count and retrying"
                )
                del self._file_failures[path]
                return False
        except Exception as e:
            # If we can't compute checksum, log but still skip to avoid infinite loops
            logger.warning(f"Failed to compute checksum for {path}: {e}")

        # File unchanged and exceeded threshold - skip it
        return True

    async def _record_failure(self, path: str, error: str) -> None:
        """Record a file sync failure for circuit breaker tracking.

        Uses LRU cache with bounded size to prevent unbounded memory growth.

        Args:
            path: File path that failed
            error: Error message from the failure
        """
        now = datetime.now()

        # Compute checksum for failure tracking
        try:
            checksum = await self.file_service.compute_checksum(path)
        except Exception:
            # If checksum fails, use empty string (better than crashing)
            checksum = ""

        if path in self._file_failures:
            # Update existing failure record and move to end (most recently used)
            failure_info = self._file_failures.pop(path)
            failure_info.count += 1
            failure_info.last_failure = now
            failure_info.last_error = error
            failure_info.last_checksum = checksum
            self._file_failures[path] = failure_info

            logger.warning(
                f"File sync failed (attempt {failure_info.count}/{MAX_CONSECUTIVE_FAILURES}): "
                f"path={path}, error={error}"
            )

            # Log when threshold is reached
            if failure_info.count >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    f"File {path} has failed {MAX_CONSECUTIVE_FAILURES} times and will be skipped. "
                    f"First failure: {failure_info.first_failure}, Last error: {error}"
                )
        else:
            # Create new failure record
            self._file_failures[path] = FileFailureInfo(
                count=1,
                first_failure=now,
                last_failure=now,
                last_error=error,
                last_checksum=checksum,
            )
            logger.debug(f"Recording first failure for {path}: {error}")

            # Enforce cache size limit - remove oldest entry if over limit
            if len(self._file_failures) > self._max_tracked_failures:
                removed_path, removed_info = self._file_failures.popitem(last=False)
                logger.debug(
                    f"Evicting oldest failure record from cache: path={removed_path}, "
                    f"failures={removed_info.count}"
                )

    def _clear_failure(self, path: str) -> None:
        """Clear failure tracking for a file after successful sync.

        Args:
            path: File path that successfully synced
        """
        if path in self._file_failures:
            logger.info(f"Clearing failure history for {path} after successful sync")
            del self._file_failures[path]

    @logfire.instrument()
    async def sync(self, directory: Path, project_name: Optional[str] = None) -> SyncReport:
        """Sync all files with database."""

        start_time = time.time()
        logger.info(f"Sync operation started for directory: {directory}")

        # initial paths from db to sync
        # path -> checksum
        report = await self.scan(directory)

        # order of sync matters to resolve relations effectively
        logger.info(
            f"Sync changes detected: new_files={len(report.new)}, modified_files={len(report.modified)}, "
            + f"deleted_files={len(report.deleted)}, moved_files={len(report.moves)}"
        )

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

        # deleted next
        for path in report.deleted:
            await self.handle_delete(path)

        # then new and modified
        for path in report.new:
            entity, _ = await self.sync_file(path, new=True)

            # Track if file was skipped
            if entity is None and await self._should_skip_file(path):
                failure_info = self._file_failures[path]
                report.skipped_files.append(
                    SkippedFile(
                        path=path,
                        reason=failure_info.last_error,
                        failure_count=failure_info.count,
                        first_failed=failure_info.first_failure,
                    )
                )

        for path in report.modified:
            entity, _ = await self.sync_file(path, new=False)

            # Track if file was skipped
            if entity is None and await self._should_skip_file(path):
                failure_info = self._file_failures[path]
                report.skipped_files.append(
                    SkippedFile(
                        path=path,
                        reason=failure_info.last_error,
                        failure_count=failure_info.count,
                        first_failed=failure_info.first_failure,
                    )
                )

        await self.resolve_relations()

        duration_ms = int((time.time() - start_time) * 1000)

        # Log summary with skipped files if any
        if report.skipped_files:
            logger.warning(
                f"Sync completed with {len(report.skipped_files)} skipped files: "
                f"directory={directory}, total_changes={report.total}, "
                f"skipped={len(report.skipped_files)}, duration_ms={duration_ms}"
            )
            for skipped in report.skipped_files:
                logger.warning(
                    f"Skipped file: path={skipped.path}, "
                    f"failures={skipped.failure_count}, reason={skipped.reason}"
                )
        else:
            logger.info(
                f"Sync operation completed: directory={directory}, "
                f"total_changes={report.total}, duration_ms={duration_ms}"
            )

        return report

    @logfire.instrument()
    async def scan(self, directory):
        """Scan directory for changes using streaming architecture with mtime-based comparison.

        This scan processes files one at a time using indexed lookups instead of
        loading all entities upfront. Only files with different mtime/size need checksums
        computed, dramatically reducing I/O and memory usage on large projects.

        Architecture:
        - Stream files and query DB incrementally with indexed lookups
        - Process files immediately, no accumulation in memory
        """

        report = SyncReport()

        # Track files we've seen and their checksums for move detection
        scanned_paths: Set[str] = set()
        changed_checksums: Dict[str, str] = {}

        # Stream filesystem and process each file immediately with indexed DB lookup
        logger.debug("Starting streaming mtime-based scan with indexed lookups")
        async for file_path_str, stat_info in self.scan_directory(directory):
            rel_path = Path(file_path_str).relative_to(directory).as_posix()
            scanned_paths.add(rel_path)

            # Indexed lookup - single file query (not full table scan)
            db_entity = await self.entity_repository.get_by_file_path(rel_path)

            if db_entity is None:
                # New file - need checksum for move detection
                checksum = await self.file_service.compute_checksum(rel_path)
                report.new.add(rel_path)
                changed_checksums[rel_path] = checksum
                logger.trace(f"New file detected: {rel_path}")
                continue

            # File exists in DB - check if mtime/size changed
            db_mtime = db_entity.mtime
            db_size = db_entity.size
            fs_mtime = stat_info.st_mtime
            fs_size = stat_info.st_size

            # Compare mtime and size (like rsync/rclone)
            # Allow small epsilon for float comparison (0.01s = 10ms)
            mtime_changed = db_mtime is None or abs(fs_mtime - db_mtime) > 0.01
            size_changed = db_size is None or fs_size != db_size

            if mtime_changed or size_changed:
                # File modified - compute checksum
                checksum = await self.file_service.compute_checksum(rel_path)
                db_checksum = db_entity.checksum

                # Only mark as modified if checksum actually differs
                # (handles cases where mtime changed but content didn't, e.g., git operations)
                if checksum != db_checksum:
                    report.modified.add(rel_path)
                    changed_checksums[rel_path] = checksum
                    logger.trace(
                        f"Modified file detected: {rel_path}, "
                        f"mtime_changed={mtime_changed}, size_changed={size_changed}"
                    )
            else:
                # File unchanged - no checksum needed
                logger.trace(f"File unchanged (mtime/size match): {rel_path}")

        # Detect deletions and moves
        # Use optimized query for just file paths (not full entities)
        db_file_paths = await self.entity_repository.get_all_file_paths()
        logger.debug(f"Found {len(db_file_paths)} db paths for deletion/move detection")

        for db_path in db_file_paths:
            if db_path not in scanned_paths:
                # File in DB but not on filesystem - deleted or moved
                # Need to check if checksum appears in new/modified files (indicates move)
                # For move detection, we need the checksum, so do a single entity lookup
                db_entity = await self.entity_repository.get_by_file_path(db_path)
                if db_entity is None:
                    # Entity was deleted between get_all_file_paths() and now (race condition)
                    continue

                db_checksum = db_entity.checksum

                # Check if this checksum appears in new/modified files (indicates move)
                moved_to_path = None
                for new_path, new_checksum in changed_checksums.items():
                    if new_checksum == db_checksum:
                        moved_to_path = new_path
                        break

                if moved_to_path:
                    # File was moved
                    report.moves[db_path] = moved_to_path
                    # Remove from new files if present
                    if moved_to_path in report.new:
                        report.new.remove(moved_to_path)
                    logger.trace(f"Move detected: {db_path} -> {moved_to_path}")
                else:
                    # File was deleted
                    report.deleted.add(db_path)
                    logger.trace(f"Deleted file detected: {db_path}")

        # Store checksums for files that need syncing
        report.checksums = changed_checksums

        logger.info(
            f"Completed streaming scan for directory {directory}, "
            f"found {report.total} changes (new={len(report.new)}, "
            f"modified={len(report.modified)}, deleted={len(report.deleted)}, "
            f"moves={len(report.moves)})"
        )
        return report

    async def sync_file(
        self, path: str, new: bool = True
    ) -> Tuple[Optional[Entity], Optional[str]]:
        """Sync a single file with circuit breaker protection.

        Args:
            path: Path to file to sync
            new: Whether this is a new file

        Returns:
            Tuple of (entity, checksum) or (None, None) if sync fails or file is skipped
        """
        # Check if file should be skipped due to repeated failures
        if await self._should_skip_file(path):
            logger.warning(f"Skipping file due to repeated failures: {path}")
            return None, None

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

                # Clear failure tracking on successful sync
                self._clear_failure(path)

                logger.debug(
                    f"File sync completed, path={path}, entity_id={entity.id}, checksum={checksum[:8]}"
                )
            return entity, checksum

        except Exception as e:
            # Check if this is a fatal error (or caused by one)
            # Fatal errors like project deletion should terminate sync immediately
            if isinstance(e, SyncFatalError) or isinstance(e.__cause__, SyncFatalError):
                logger.error(f"Fatal sync error encountered, terminating sync: path={path}")
                raise

            # Otherwise treat as recoverable file-level error
            error_msg = str(e)
            logger.error(f"Failed to sync file: path={path}, error={error_msg}")

            # Record failure for circuit breaker
            await self._record_failure(path, error_msg)

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

        file_content = await self.file_service.read_file_content(path)
        file_contains_frontmatter = has_frontmatter(file_content)

        # Get file timestamps for tracking modification times
        file_stats = self.file_service.file_stats(path)
        created = datetime.fromtimestamp(file_stats.st_ctime).astimezone()
        modified = datetime.fromtimestamp(file_stats.st_mtime).astimezone()

        # entity markdown will always contain front matter, so it can be used up create/update the entity
        entity_markdown = await self.entity_parser.parse_file(path)

        # if the file contains frontmatter, resolve a permalink (unless disabled)
        if file_contains_frontmatter and not self.app_config.disable_permalinks:
            # Resolve permalink - skip conflict checks during bulk sync for performance
            permalink = await self.entity_service.resolve_permalink(
                path, markdown=entity_markdown, skip_conflict_check=True
            )

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
        final_checksum = await self.file_service.compute_checksum(path)

        # Update checksum, timestamps, and file metadata from file system
        # Store mtime/size for efficient change detection in future scans
        # This ensures temporal ordering in search and recent activity uses actual file modification times
        await self.entity_repository.update(
            entity.id,
            {
                "checksum": final_checksum,
                "created_at": created,
                "updated_at": modified,
                "mtime": file_stats.st_mtime,
                "size": file_stats.st_size,
            },
        )

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
        checksum = await self.file_service.compute_checksum(path)
        if new:
            # Generate permalink from path - skip conflict checks during bulk sync
            await self.entity_service.resolve_permalink(path, skip_conflict_check=True)

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
                        mtime=file_stats.st_mtime,
                        size=file_stats.st_size,
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

                    # Re-get file stats since we're in update path
                    file_stats_for_update = self.file_service.file_stats(path)
                    updated = await self.entity_repository.update(
                        entity.id,
                        {
                            "file_path": path,
                            "checksum": checksum,
                            "mtime": file_stats_for_update.st_mtime,
                            "size": file_stats_for_update.st_size,
                        },
                    )

                    if updated is None:  # pragma: no cover
                        logger.error(f"Failed to update entity, entity_id={entity.id}, path={path}")
                        raise ValueError(f"Failed to update entity with ID {entity.id}")

                    return updated, checksum
                else:
                    # Re-raise if it's a different integrity error
                    raise
        else:
            # Get file timestamps for updating modification time
            file_stats = self.file_service.file_stats(path)
            modified = datetime.fromtimestamp(file_stats.st_mtime).astimezone()

            entity = await self.entity_repository.get_by_file_path(path)
            if entity is None:  # pragma: no cover
                logger.error(f"Entity not found for existing file, path={path}")
                raise ValueError(f"Entity not found for existing file: {path}")

            # Update checksum, modification time, and file metadata from file system
            # Store mtime/size for efficient change detection in future scans
            updated = await self.entity_repository.update(
                entity.id,
                {
                    "file_path": path,
                    "checksum": checksum,
                    "updated_at": modified,
                    "mtime": file_stats.st_mtime,
                    "size": file_stats.st_size,
                },
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
            if (
                self.app_config.update_permalinks_on_move
                and not self.app_config.disable_permalinks
                and self.file_service.is_markdown(new_path)
            ):
                # generate new permalink value - skip conflict checks during bulk sync
                new_permalink = await self.entity_service.resolve_permalink(
                    new_path, skip_conflict_check=True
                )

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

    async def scan_directory(self, directory: Path) -> AsyncIterator[Tuple[str, os.stat_result]]:
        """Stream files from directory using scandir() with cached stat info.

        This method uses os.scandir() instead of os.walk() to leverage cached stat
        information from directory entries. This reduces network I/O by 50% on network
        filesystems like TigrisFS by avoiding redundant stat() calls.

        Args:
            directory: Directory to scan

        Yields:
            Tuples of (absolute_file_path, stat_info) for each file
        """

        def _sync_scandir(dir_path: Path):
            """Synchronous scandir implementation for thread pool execution."""
            try:
                entries = os.scandir(dir_path)
            except PermissionError:
                logger.warning(f"Permission denied scanning directory: {dir_path}")
                return [], []  # Return empty results and subdirs tuple

            results = []
            subdirs = []

            for entry in entries:
                entry_path = Path(entry.path)

                # Check ignore patterns
                if should_ignore_path(entry_path, directory, self._ignore_patterns):
                    logger.trace(
                        f"Ignoring path per .bmignore: {entry_path.relative_to(directory)}"
                    )
                    continue

                if entry.is_dir(follow_symlinks=False):
                    # Collect subdirectories to recurse into
                    subdirs.append(entry_path)
                elif entry.is_file(follow_symlinks=False):
                    # Get cached stat info (no extra syscall!)
                    stat_info = entry.stat(follow_symlinks=False)
                    results.append((entry.path, stat_info))

            return results, subdirs

        # Run scandir in default executor to avoid blocking event loop
        # os.scandir() is synchronous, so we use run_in_executor with None (default executor)
        loop = asyncio.get_event_loop()
        results, subdirs = await loop.run_in_executor(None, _sync_scandir, directory)

        # Yield files from current directory
        for file_path, stat_info in results:
            yield (file_path, stat_info)

        # Recurse into subdirectories
        for subdir in subdirs:
            async for result in self.scan_directory(subdir):
                yield result


async def get_sync_service(project: Project) -> SyncService:  # pragma: no cover
    """Get sync service instance with all dependencies."""

    app_config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
    )

    project_path = Path(project.path)
    entity_parser = EntityParser(project_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(project_path, markdown_processor)

    # Initialize repositories
    entity_repository = EntityRepository(session_maker, project_id=project.id)
    observation_repository = ObservationRepository(session_maker, project_id=project.id)
    relation_repository = RelationRepository(session_maker, project_id=project.id)
    search_repository = SearchRepository(session_maker, project_id=project.id)

    # Initialize services
    search_service = SearchService(search_repository, entity_repository, file_service)
    link_resolver = LinkResolver(entity_repository, search_service)

    # Initialize services
    entity_service = EntityService(
        entity_parser,
        entity_repository,
        observation_repository,
        relation_repository,
        file_service,
        link_resolver,
    )

    # Create sync service
    sync_service = SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_service=file_service,
    )

    return sync_service
