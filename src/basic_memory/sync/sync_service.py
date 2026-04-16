"""Service for syncing files between filesystem and database."""

import asyncio
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import aiofiles.os

from loguru import logger
from sqlalchemy.exc import IntegrityError

from basic_memory import telemetry
from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.file_utils import ParseError, compute_checksum, remove_frontmatter
from basic_memory.indexing import (
    BatchIndexer,
    IndexFileMetadata,
    IndexInputFile,
    IndexProgress,
    SyncedMarkdownFile,
)
from basic_memory.indexing.batching import build_index_batches
from basic_memory.indexing.models import (
    IndexedEntity,
    IndexFileWriter,
    IndexFrontmatterUpdate,
    IndexFrontmatterWriteResult,
)
from basic_memory.ignore_utils import load_bmignore_patterns, should_ignore_path
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.models import Entity, Project
from basic_memory.repository import (
    EntityRepository,
    RelationRepository,
    ObservationRepository,
    ProjectRepository,
)
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.services import EntityService, FileService
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.services.exceptions import FileOperationError, SyncFatalError
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService

# Circuit breaker configuration
MAX_CONSECUTIVE_FAILURES = 3
SLOW_FILE_SYNC_WARNING_MS = 500


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
    scanned_paths: Set[str] = field(default_factory=set)
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


class _FileServiceIndexWriter(IndexFileWriter):
    """Adapt FileService frontmatter updates to the indexing writer protocol."""

    def __init__(self, file_service: FileService) -> None:
        self.file_service = file_service

    async def write_frontmatter(
        self, update: IndexFrontmatterUpdate
    ) -> IndexFrontmatterWriteResult:
        # Why: IndexFrontmatterWriteResult lives in indexing/models.py so the indexing
        # layer does not need to import FileService. This adapter keeps that boundary intact.
        result = await self.file_service.update_frontmatter_with_result(
            update.path, update.metadata
        )
        return IndexFrontmatterWriteResult(checksum=result.checksum, content=result.content)


class SyncService:
    """Syncs documents and knowledge files with database."""

    def __init__(
        self,
        app_config: BasicMemoryConfig,
        entity_service: EntityService,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        relation_repository: RelationRepository,
        project_repository: ProjectRepository,
        search_service: SearchService,
        file_service: FileService,
    ):
        self.app_config = app_config
        self.entity_service = entity_service
        self.entity_parser = entity_parser
        self.entity_repository = entity_repository
        self.relation_repository = relation_repository
        self.project_repository = project_repository
        self.search_service = search_service
        self.file_service = file_service
        # Load ignore patterns once at initialization for performance
        self._ignore_patterns = load_bmignore_patterns()
        # Circuit breaker: track file failures to prevent infinite retry loops
        # Use OrderedDict for LRU behavior with bounded size to prevent unbounded memory growth
        self._file_failures: OrderedDict[str, FileFailureInfo] = OrderedDict()
        self._max_tracked_failures = 100  # Limit failure cache size
        self.batch_indexer = BatchIndexer(
            app_config=app_config,
            entity_service=entity_service,
            entity_repository=entity_repository,
            relation_repository=relation_repository,
            search_service=search_service,
            file_writer=_FileServiceIndexWriter(file_service),
        )

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

    async def sync(
        self,
        directory: Path,
        project_name: Optional[str] = None,
        force_full: bool = False,
        sync_embeddings: bool = True,
        progress_callback: Callable[[IndexProgress], Awaitable[None]] | None = None,
    ) -> SyncReport:
        """Sync all files with database and update scan watermark.

        Args:
            directory: Directory to sync
            project_name: Optional project name
            force_full: If True, force a full scan bypassing watermark optimization
            sync_embeddings: If True, generate vectors for entities indexed during this sync
            progress_callback: Optional callback for typed indexing progress updates
        """

        start_time = time.time()
        sync_start_timestamp = time.time()  # Capture at start for watermark
        with telemetry.operation(
            "sync.project.run",
            project_name=project_name,
            force_full=force_full,
        ):
            logger.info(
                f"Sync operation started for directory: {directory} (force_full={force_full})"
            )

            # initial paths from db to sync
            # path -> checksum
            with telemetry.scope("sync.project.scan", force_full=force_full):
                report = await self.scan(directory, force_full=force_full)

            # order of sync matters to resolve relations effectively
            logger.info(
                f"Sync changes detected: new_files={len(report.new)}, modified_files={len(report.modified)}, "
                + f"deleted_files={len(report.deleted)}, moved_files={len(report.moves)}"
            )

            with telemetry.scope(
                "sync.project.apply_changes",
                new_count=len(report.new),
                modified_count=len(report.modified),
                deleted_count=len(report.deleted),
                move_count=len(report.moves),
            ):
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

                # Trigger: the caller requested a full reindex pass through the sync path.
                # Why: cloud-style "full" semantics should rebuild every current file-backed
                #      search row, not only the files that differ from the last watermark.
                # Outcome: progress reflects the whole project and unchanged files are
                #          re-indexed without inflating the change report itself.
                changed_paths = (
                    sorted(report.scanned_paths)
                    if force_full
                    else sorted(report.new | report.modified)
                )
                indexed_entities, skipped_files = await self._index_changed_files(
                    changed_paths,
                    report.checksums,
                    progress_callback=progress_callback,
                )
                report.skipped_files.extend(skipped_files)
                synced_entity_ids = [indexed.entity_id for indexed in indexed_entities]

            # Trigger: either the filesystem diff found changes, or the caller forced a
            # full reindex and we just reprocessed the current files.
            # Why: relation resolution should follow the file-processing work that just ran,
            #      not only the lightweight diff summary.
            # Outcome: full reindex can heal relation state even when the diff report is empty.
            if report.total > 0 or (force_full and indexed_entities):
                with telemetry.scope(
                    "sync.project.resolve_relations", relation_scope="all_pending"
                ):
                    synced_entity_ids.extend(await self.resolve_relations())
            else:
                logger.info("Skipping relation resolution - no file changes detected")

            # Batch-generate vector embeddings for all synced entities
            synced_entity_ids = list(dict.fromkeys(synced_entity_ids))
            if synced_entity_ids and sync_embeddings and self.app_config.semantic_search_enabled:
                try:
                    with telemetry.scope(
                        "sync.project.sync_embeddings",
                        entity_count=len(synced_entity_ids),
                    ):
                        logger.info(
                            f"Generating semantic embeddings for {len(synced_entity_ids)} entities..."
                        )
                        batch_result = await self.search_service.sync_entity_vectors_batch(
                            synced_entity_ids
                        )
                        logger.info(
                            f"Semantic embeddings complete: "
                            f"synced={batch_result.entities_synced}, "
                            f"failed={batch_result.entities_failed}"
                        )
                except SemanticDependenciesMissingError:
                    logger.warning(
                        "Semantic search dependencies missing — vector embeddings skipped. "
                        "Run 'bm reindex --embeddings' after resolving the dependency issue."
                    )

            # Update scan watermark after successful sync
            # Use the timestamp from sync start (not end) to ensure we catch files
            # created during the sync on the next iteration
            with telemetry.scope("sync.project.update_watermark"):
                current_file_count = await self._quick_count_files(directory)
                if self.entity_repository.project_id is not None:
                    project = await self.project_repository.find_by_id(
                        self.entity_repository.project_id
                    )
                    if project:
                        await self.project_repository.update(
                            project.id,
                            {
                                "last_scan_timestamp": sync_start_timestamp,
                                "last_file_count": current_file_count,
                            },
                        )
                        logger.debug(
                            f"Updated scan watermark: timestamp={sync_start_timestamp}, "
                            f"file_count={current_file_count}"
                        )

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

    async def _index_changed_files(
        self,
        changed_paths: list[str],
        checksums_by_path: dict[str, str],
        *,
        progress_callback: Callable[[IndexProgress], Awaitable[None]] | None = None,
    ) -> tuple[list[IndexedEntity], list[SkippedFile]]:
        """Load, batch, and index changed files without processing them serially."""
        if not changed_paths:
            if progress_callback is not None:
                await progress_callback(
                    IndexProgress(
                        files_total=0,
                        files_processed=0,
                        batches_total=0,
                        batches_completed=0,
                    )
                )
            return [], []

        started_at = time.monotonic()
        files_total = len(changed_paths)
        files_processed = 0
        batches_completed = 0
        skipped_files: list[SkippedFile] = []
        skipped_paths: set[str] = set()
        candidate_paths: list[str] = []

        # Trigger: a file exceeded the retry threshold in a previous sync.
        # Why: repeated retries on unchanged broken files waste the entire batch budget.
        # Outcome: skip it up front and still count it in progress.
        for path in changed_paths:
            if await self._should_skip_file(path):
                self._append_skipped_file(path, skipped_files, skipped_paths)
                files_processed += 1
            else:
                candidate_paths.append(path)

        (
            metadata_by_path,
            metadata_errors,
            missing_metadata_paths,
        ) = await self._load_index_file_metadata(
            candidate_paths,
            checksums_by_path,
        )
        files_processed += len(missing_metadata_paths)
        files_processed += len(metadata_errors)
        for path, error in metadata_errors:
            await self._record_index_failure(path, error, skipped_files, skipped_paths)

        batch_paths = sorted(metadata_by_path)
        batches = build_index_batches(
            batch_paths,
            metadata_by_path,
            max_files=self.app_config.index_batch_size,
            max_bytes=self.app_config.index_batch_max_bytes,
        )

        await self._emit_index_progress(
            progress_callback,
            files_total=files_total,
            files_processed=files_processed,
            batches_total=len(batches),
            batches_completed=0,
            current_batch_bytes=0,
            started_at=started_at,
        )

        indexed_entities: list[IndexedEntity] = []
        shared_permalink_by_path: dict[str, str | None] | None = None
        if any(
            metadata.content_type == "text/markdown"
            or (
                metadata.content_type is None
                and Path(metadata.path).suffix.lower() in {".md", ".markdown"}
            )
            for metadata in metadata_by_path.values()
        ):
            shared_permalink_by_path = {
                path: permalink
                for path, permalink in (
                    await self.entity_repository.get_file_path_to_permalink_map()
                ).items()
            }

        for batch in batches:
            loaded_files, load_errors = await self._load_index_batch_files(
                batch.paths, metadata_by_path
            )
            for path, error in load_errors:
                await self._record_index_failure(path, error, skipped_files, skipped_paths)

            if loaded_files:
                batch_result = await self.batch_indexer.index_files(
                    loaded_files,
                    max_concurrent=self.app_config.index_entity_max_concurrent,
                    parse_max_concurrent=self.app_config.index_parse_max_concurrent,
                    existing_permalink_by_path=shared_permalink_by_path,
                )
                indexed_entities.extend(batch_result.indexed)

                indexed_paths = {indexed.path for indexed in batch_result.indexed}
                for path in indexed_paths:
                    self._clear_failure(path)

                for path, error in batch_result.errors:
                    await self._record_index_failure(path, error, skipped_files, skipped_paths)

            files_processed += len(batch.paths)
            batches_completed += 1
            await self._emit_index_progress(
                progress_callback,
                files_total=files_total,
                files_processed=files_processed,
                batches_total=len(batches),
                batches_completed=batches_completed,
                current_batch_bytes=batch.total_bytes,
                started_at=started_at,
            )

        return indexed_entities, skipped_files

    async def _load_index_file_metadata(
        self,
        paths: list[str],
        checksums_by_path: dict[str, str],
    ) -> tuple[dict[str, IndexFileMetadata], list[tuple[str, str]], list[str]]:
        """Load typed metadata for batch planning before any file content is read."""
        if not paths:
            return {}, [], []

        semaphore = asyncio.Semaphore(self.app_config.sync_max_concurrent_files)
        metadata_by_path: dict[str, IndexFileMetadata] = {}
        errors: dict[str, str] = {}
        missing_paths: list[str] = []

        async def load(path: str) -> None:
            async with semaphore:
                try:
                    file_metadata = await self.file_service.get_file_metadata(path)
                    metadata_by_path[path] = IndexFileMetadata(
                        path=path,
                        size=file_metadata.size,
                        checksum=checksums_by_path.get(path),
                        content_type=self.file_service.content_type(path),
                        last_modified=file_metadata.modified_at,
                        created_at=file_metadata.created_at,
                    )
                except FileNotFoundError:
                    await self.handle_delete(path)
                    missing_paths.append(path)
                except Exception as exc:
                    errors[path] = str(exc)

        await asyncio.gather(*(load(path) for path in paths))
        return (
            metadata_by_path,
            [(path, errors[path]) for path in sorted(errors)],
            sorted(missing_paths),
        )

    async def _load_index_batch_files(
        self,
        paths: list[str],
        metadata_by_path: dict[str, IndexFileMetadata],
    ) -> tuple[dict[str, IndexInputFile], list[tuple[str, str]]]:
        """Read one batch of file contents into typed input objects."""
        if not paths:
            return {}, []

        semaphore = asyncio.Semaphore(self.app_config.sync_max_concurrent_files)
        files: dict[str, IndexInputFile] = {}
        errors: dict[str, str] = {}

        async def load(path: str) -> None:
            async with semaphore:
                metadata = metadata_by_path[path]
                try:
                    content = await self.file_service.read_file_bytes(path)
                    loaded_checksum = await compute_checksum(content)
                    files[path] = IndexInputFile(
                        path=metadata.path,
                        size=metadata.size,
                        checksum=loaded_checksum,
                        content_type=metadata.content_type,
                        last_modified=metadata.last_modified,
                        created_at=metadata.created_at,
                        content=content,
                    )
                except FileOperationError as exc:
                    # Trigger: FileService wraps binary read failures in FileOperationError.
                    # Why: the service contract should stay consistent for direct callers.
                    # Outcome: sync still treats wrapped missing-file reads as deletions.
                    if isinstance(exc.__cause__, FileNotFoundError):
                        await self.handle_delete(path)
                    else:
                        errors[path] = str(exc)
                except Exception as exc:
                    errors[path] = str(exc)

        await asyncio.gather(*(load(path) for path in paths))
        return files, [(path, errors[path]) for path in sorted(errors)]

    async def _record_index_failure(
        self,
        path: str,
        error: str,
        skipped_files: list[SkippedFile],
        skipped_paths: set[str],
    ) -> None:
        """Record a per-file batch failure and promote it to skipped when threshold is reached."""
        await self._record_failure(path, error)
        if await self._should_skip_file(path):
            self._append_skipped_file(path, skipped_files, skipped_paths)

    def _append_skipped_file(
        self,
        path: str,
        skipped_files: list[SkippedFile],
        skipped_paths: set[str],
    ) -> None:
        """Append one skipped file record once per sync run."""
        if path in skipped_paths or path not in self._file_failures:
            return

        failure_info = self._file_failures[path]
        skipped_files.append(
            SkippedFile(
                path=path,
                reason=failure_info.last_error,
                failure_count=failure_info.count,
                first_failed=failure_info.first_failure,
            )
        )
        skipped_paths.add(path)

    async def _emit_index_progress(
        self,
        progress_callback: Callable[[IndexProgress], Awaitable[None]] | None,
        *,
        files_total: int,
        files_processed: int,
        batches_total: int,
        batches_completed: int,
        current_batch_bytes: int,
        started_at: float,
    ) -> None:
        """Emit a typed indexing progress update when the caller requested one."""
        if progress_callback is None:
            return

        elapsed_seconds = max(time.monotonic() - started_at, 0.001)
        files_per_minute = files_processed / elapsed_seconds * 60 if files_processed else 0.0
        eta_seconds = None
        if files_processed and files_total > files_processed:
            files_per_second = files_processed / elapsed_seconds
            eta_seconds = (files_total - files_processed) / files_per_second

        await progress_callback(
            IndexProgress(
                files_total=files_total,
                files_processed=files_processed,
                batches_total=batches_total,
                batches_completed=batches_completed,
                current_batch_bytes=current_batch_bytes,
                files_per_minute=files_per_minute,
                eta_seconds=eta_seconds,
            )
        )

    async def scan(self, directory, force_full: bool = False):
        """Smart scan using watermark and file count for large project optimization.

        Uses scan watermark tracking to dramatically reduce scan time for large projects:
        - Tracks last_scan_timestamp and last_file_count in Project model
        - Uses `find -newermt` for incremental scanning (only changed files)
        - Falls back to full scan when deletions detected (file count decreased)

        Expected performance:
        - No changes: 225x faster (2s vs 450s for 1,460 files on TigrisFS)
        - Few changes: 84x faster (5s vs 420s)
        - Deletions: Full scan (rare, acceptable)

        Architecture:
        - Get current file count quickly (find | wc -l: 1.4s)
        - Compare with last_file_count to detect deletions
        - If no deletions: incremental scan with find -newermt (0.2s)
        - Process changed files with mtime-based comparison

        Args:
            directory: Directory to scan
            force_full: If True, bypass watermark optimization and force full scan
        """
        scan_start_time = time.time()

        report = SyncReport()

        # Get current project to check watermark
        if self.entity_repository.project_id is None:
            raise ValueError("Entity repository has no project_id set")

        project = await self.project_repository.find_by_id(self.entity_repository.project_id)
        if project is None:
            raise ValueError(f"Project not found: {self.entity_repository.project_id}")

        with telemetry.scope("sync.project.select_scan_strategy", force_full=force_full):
            # Step 1: Quick file count
            logger.debug("Counting files in directory")
            current_count = await self._quick_count_files(directory)
            logger.debug(f"Found {current_count} files in directory")

            # Step 2: Determine scan strategy based on watermark and file count
            if force_full:
                # User explicitly requested full scan → bypass watermark optimization
                scan_type = "full_forced"
                logger.info("Force full scan requested, bypassing watermark optimization")
                scan_coro = self._scan_directory_full(directory)

            elif project.last_file_count is None:
                # First sync ever → full scan
                scan_type = "full_initial"
                logger.info("First sync for this project, performing full scan")
                scan_coro = self._scan_directory_full(directory)

            elif current_count < project.last_file_count:
                # Files deleted → need full scan to detect which ones
                scan_type = "full_deletions"
                logger.info(
                    f"File count decreased ({project.last_file_count} → {current_count}), "
                    f"running full scan to detect deletions"
                )
                scan_coro = self._scan_directory_full(directory)

            elif project.last_scan_timestamp is not None:
                # Incremental scan: only files modified since last scan
                scan_type = "incremental"
                logger.debug(
                    f"Running incremental scan for files modified since {project.last_scan_timestamp}"
                )
                scan_coro = self._scan_directory_modified_since(
                    directory, project.last_scan_timestamp
                )

            else:
                # Fallback to full scan (no watermark available)
                scan_type = "full_fallback"
                logger.warning("No scan watermark available, falling back to full scan")
                scan_coro = self._scan_directory_full(directory)

        with telemetry.scope("sync.project.filesystem_scan", scan_type=scan_type):
            file_paths_to_scan = await scan_coro
            if scan_type == "incremental":
                logger.debug(
                    f"Incremental scan found {len(file_paths_to_scan)} potentially changed files"
                )

            # Step 3: Process each file with mtime-based comparison
            scanned_paths: Set[str] = set()
            changed_checksums: Dict[str, str] = {}

            logger.debug(f"Processing {len(file_paths_to_scan)} files with mtime-based comparison")

            for rel_path in file_paths_to_scan:
                scanned_paths.add(rel_path)

                # Get file stats
                abs_path = directory / rel_path
                if not abs_path.exists():
                    # File was deleted between scan and now (race condition)
                    continue

                stat_info = abs_path.stat()

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

            # Step 4: Detect moves (for both full and incremental scans)
            # Check if any "new" files are actually moves by matching checksums
            with telemetry.scope("sync.project.detect_moves", new_count=len(report.new)):
                for new_path in list(
                    report.new
                ):  # Use list() to allow modification during iteration
                    new_checksum = changed_checksums.get(new_path)
                    if not new_checksum:
                        continue

                    # Look for existing entity with same checksum but different path
                    # This could be a move or a copy
                    existing_entities = await self.entity_repository.find_by_checksum(new_checksum)

                    for candidate in existing_entities:
                        if candidate.file_path == new_path:
                            # Same path, skip (shouldn't happen for "new" files but be safe)
                            continue

                        # Check if the old path still exists on disk
                        old_path_abs = directory / candidate.file_path
                        if old_path_abs.exists():
                            # Original still exists → this is a copy, not a move
                            logger.trace(
                                f"File copy detected (not move): {candidate.file_path} copied to {new_path}"
                            )
                            continue

                        # Original doesn't exist → this is a move!
                        report.moves[candidate.file_path] = new_path
                        report.new.remove(new_path)
                        logger.trace(f"Move detected: {candidate.file_path} -> {new_path}")
                        break  # Only match first candidate

            # Step 5: Detect deletions (only for full scans)
            # Incremental scans can't reliably detect deletions since they only see modified files
            if scan_type in ("full_initial", "full_deletions", "full_fallback", "full_forced"):
                with telemetry.scope("sync.project.detect_deletions", scan_type=scan_type):
                    # Use optimized query for just file paths (not full entities)
                    db_file_paths = await self.entity_repository.get_all_file_paths()
                    logger.debug(f"Found {len(db_file_paths)} db paths for deletion detection")

                    for db_path in db_file_paths:
                        if db_path not in scanned_paths:
                            # File in DB but not on filesystem
                            # Check if it was already detected as a move
                            if db_path in report.moves:
                                # Already handled as a move, skip
                                continue

                            # File was deleted
                            report.deleted.add(db_path)
                            logger.trace(f"Deleted file detected: {db_path}")

            # Store checksums for files that need syncing
            report.checksums = changed_checksums
            report.scanned_paths = scanned_paths

            scan_duration_ms = int((time.time() - scan_start_time) * 1000)

            logger.info(
                f"Completed {scan_type} scan for directory {directory} in {scan_duration_ms}ms, "
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

        start_time = time.time()
        is_markdown = self.file_service.is_markdown(path)
        file_kind = "markdown" if is_markdown else "regular"

        try:
            logger.debug(f"Syncing file path={path} is_new={new} is_markdown={is_markdown}")

            if is_markdown:
                entity, checksum = await self.sync_markdown_file(path, new)
            else:
                entity, checksum = await self.sync_regular_file(path, new)

            if entity is not None:
                try:
                    await self.search_service.index_entity(entity)
                except SemanticDependenciesMissingError:
                    # Trigger: sqlite-vec or embedding provider unavailable
                    # Why: FTS indexing succeeded but vector embeddings cannot be generated.
                    #      Don't fail the entire sync — the entity is usable for text search.
                    # Outcome: entity returned successfully, warning logged for visibility.
                    logger.warning(
                        f"Semantic search dependencies missing — vector embeddings skipped "
                        f"for path={path}. Run 'bm reindex --embeddings' after resolving "
                        f"the dependency issue."
                    )

                # Clear failure tracking on successful sync
                self._clear_failure(path)

                logger.debug(
                    f"File sync completed, path={path}, entity_id={entity.id}, checksum={checksum[:8]}"
                )
            duration_ms = int((time.time() - start_time) * 1000)
            if duration_ms >= SLOW_FILE_SYNC_WARNING_MS:
                logger.warning(
                    f"Slow file sync detected: path={path}, file_kind={file_kind}, duration_ms={duration_ms}"
                )
            return entity, checksum

        except FileNotFoundError:
            # File exists in database but not on filesystem
            # This indicates a database/filesystem inconsistency - treat as deletion
            with telemetry.scope(
                "sync.file.failure",
                failure_type="file_not_found",
                path=path,
                file_kind=file_kind,
                is_new=new,
                is_fatal=False,
            ):
                logger.warning(
                    f"File not found during sync, treating as deletion: path={path}. "
                    "This may indicate a race condition or manual file deletion."
                )
                await self.handle_delete(path)
            return None, None

        except Exception as e:
            failure_type = type(e).__name__
            # Check if this is a fatal error (or caused by one)
            # Fatal errors like project deletion should terminate sync immediately
            if isinstance(e, SyncFatalError) or isinstance(
                e.__cause__, SyncFatalError
            ):  # pragma: no cover
                with telemetry.scope(
                    "sync.file.failure",
                    failure_type=failure_type,
                    path=path,
                    file_kind=file_kind,
                    is_new=new,
                    is_fatal=True,
                ):
                    logger.error(f"Fatal sync error encountered, terminating sync: path={path}")
                raise

            # Otherwise treat as recoverable file-level error
            error_msg = str(e)
            with telemetry.scope(
                "sync.file.failure",
                failure_type=failure_type,
                path=path,
                file_kind=file_kind,
                is_new=new,
                is_fatal=False,
            ):
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
        synced = await self.sync_one_markdown_file(path, new=new, index_search=False)
        return synced.entity, synced.checksum

    async def sync_one_markdown_file(
        self,
        path: str,
        *,
        new: bool = True,
        index_search: bool = True,
    ) -> SyncedMarkdownFile:
        """Sync one markdown file and return the final canonical file state.

        This method is the fail-fast single-file primitive for callers such as
        cloud workers. It does not swallow unexpected exceptions.
        """
        logger.debug(f"Parsing markdown file, path: {path}, new: {new}")

        try:
            initial_markdown_bytes = await self.file_service.read_file_bytes(path)
        except FileOperationError as exc:
            # Trigger: FileService wraps binary read failures in FileOperationError.
            # Why: sync_file() treats bare FileNotFoundError as a deletion race and cleans up the DB row.
            # Outcome: preserve that contract while still hashing the exact bytes we loaded.
            if isinstance(exc.__cause__, FileNotFoundError):
                raise exc.__cause__ from exc
            raise
        initial_markdown_content = initial_markdown_bytes.decode("utf-8")
        file_metadata = await self.file_service.get_file_metadata(path)
        initial_checksum = await compute_checksum(initial_markdown_bytes)
        indexed = await self.batch_indexer.index_markdown_file(
            IndexInputFile(
                path=path,
                size=file_metadata.size,
                checksum=initial_checksum,
                content_type=self.file_service.content_type(path),
                last_modified=file_metadata.modified_at,
                created_at=file_metadata.created_at,
                content=initial_markdown_bytes,
            ),
            new=new,
            index_search=False,
        )
        final_markdown_content = (
            indexed.markdown_content
            if indexed.markdown_content is not None
            else initial_markdown_content
        )
        file_metadata = await self.file_service.get_file_metadata(path)
        refreshed_entities = await self.entity_repository.find_by_ids([indexed.entity_id])
        if len(refreshed_entities) != 1:  # pragma: no cover
            raise ValueError(f"Failed to reload synced markdown entity for {path}")
        # Trigger: markdown sync may have rewritten frontmatter after the initial file metadata load.
        # Why: the batch indexer persisted checksum/path data from the pre-rewrite IndexInputFile.
        # Outcome: refresh size and mtime from the file as it actually exists on disk now.
        updated_entity = await self.entity_repository.update(
            refreshed_entities[0].id,
            {
                "checksum": indexed.checksum,
                "created_at": file_metadata.created_at,
                "updated_at": file_metadata.modified_at,
                "mtime": file_metadata.modified_at.timestamp(),
                "size": file_metadata.size,
            },
        )
        if updated_entity is None:  # pragma: no cover
            raise ValueError(f"Failed to update markdown entity metadata for {path}")

        if index_search:
            # Trigger: markdown may start with '---' as a thematic break or malformed
            #          frontmatter that the parser already treated as plain content.
            # Why: one-file sync should not fail after the entity upsert just because
            #      strict frontmatter stripping rejects that exact text shape.
            # Outcome: fall back to indexing the raw markdown content for these cases.
            try:
                search_content = remove_frontmatter(final_markdown_content)
            except ParseError:
                search_content = final_markdown_content
            await self.search_service.index_entity_data(
                updated_entity,
                content=search_content,
            )

        logger.debug(
            f"Markdown sync completed: path={path}, entity_id={updated_entity.id}, "
            f"observation_count={len(updated_entity.observations)}, "
            f"relation_count={len(updated_entity.relations)}, checksum={indexed.checksum[:8]}"
        )

        return SyncedMarkdownFile(
            entity=updated_entity,
            checksum=indexed.checksum,
            markdown_content=final_markdown_content,
            file_path=path,
            content_type=self.file_service.content_type(path),
            updated_at=file_metadata.modified_at,
            size=file_metadata.size,
        )

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
            file_metadata = await self.file_service.get_file_metadata(path)
            created = file_metadata.created_at
            modified = file_metadata.modified_at

            # get mime type
            content_type = self.file_service.content_type(path)

            file_path = Path(path)
            try:
                entity = await self.entity_repository.add(
                    Entity(
                        note_type="file",
                        file_path=path,
                        checksum=checksum,
                        title=file_path.name,
                        created_at=created,
                        updated_at=modified,
                        content_type=content_type,
                        mtime=file_metadata.modified_at.timestamp(),
                        size=file_metadata.size,
                    )
                )
                return entity, checksum
            except IntegrityError as e:
                # Handle race condition where entity was created by another process
                msg = str(e)
                if (
                    "UNIQUE constraint failed: entity.file_path" in msg
                    or "uix_entity_file_path_project" in msg
                    or "duplicate key value violates unique constraint" in msg
                    and "file_path" in msg
                ):
                    logger.info(
                        f"Entity already exists for file_path={path}, updating instead of creating"
                    )
                    # Treat as update instead of create
                    entity = await self.entity_repository.get_by_file_path(path)
                    if entity is None:  # pragma: no cover
                        logger.error(f"Entity not found after constraint violation, path={path}")
                        raise ValueError(f"Entity not found after constraint violation: {path}")

                    # Re-get file metadata since we're in update path
                    file_metadata_for_update = await self.file_service.get_file_metadata(path)
                    updated = await self.entity_repository.update(
                        entity.id,
                        {
                            "file_path": path,
                            "checksum": checksum,
                            "mtime": file_metadata_for_update.modified_at.timestamp(),
                            "size": file_metadata_for_update.size,
                        },
                    )

                    if updated is None:  # pragma: no cover
                        logger.error(f"Failed to update entity, entity_id={entity.id}, path={path}")
                        raise ValueError(f"Failed to update entity with ID {entity.id}")

                    return updated, checksum
                else:
                    # Re-raise if it's a different integrity error
                    raise  # pragma: no cover
        else:
            # Get file timestamps for updating modification time
            file_metadata = await self.file_service.get_file_metadata(path)
            modified = file_metadata.modified_at

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
                    "mtime": file_metadata.modified_at.timestamp(),
                    "size": file_metadata.size,
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

    async def resolve_relations(self, entity_id: int | None = None) -> set[int]:
        """Try to resolve unresolved relations.

        Args:
            entity_id: If provided, only resolve relations for this specific entity.
                      Otherwise, resolve all unresolved relations in the database.

        Returns:
            Set of source entity IDs whose outgoing relations changed.
        """
        affected_entity_ids: set[int] = set()

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
                    affected_entity_ids.add(relation.from_id)
                except IntegrityError:
                    with telemetry.scope(
                        "sync.relation.resolve_conflict",
                        relation_id=relation.id,
                        relation_type=relation.relation_type,
                    ):
                        # IntegrityError means a relation with this (from_id, to_id, relation_type)
                        # already exists. The UPDATE was rolled back, so our unresolved relation
                        # (to_id=NULL) still exists in the database. We delete it because:
                        # 1. It's redundant - a resolved version already captures this relationship
                        # 2. If we don't delete it, future syncs will try to resolve it again
                        #    and get the same IntegrityError
                        logger.debug(
                            "Deleting duplicate unresolved relation "
                            f"relation_id={relation.id} "
                            f"from_id={relation.from_id} "
                            f"to_name={relation.to_name} "
                            f"resolved_to_id={resolved_entity.id}"
                        )
                        try:
                            await self.relation_repository.delete(relation.id)
                        except Exception as e:
                            with telemetry.scope(
                                "sync.relation.cleanup_failure",
                                relation_id=relation.id,
                                relation_type=relation.relation_type,
                            ):
                                # Log but don't fail - the relation may have been deleted already
                                logger.debug(
                                    f"Could not delete duplicate relation {relation.id}: {e}"
                                )
                    affected_entity_ids.add(relation.from_id)

        for affected_entity_id in sorted(affected_entity_ids):
            source_entity = await self.entity_repository.find_by_id(affected_entity_id)
            if source_entity is not None:
                await self.search_service.index_entity(source_entity)

        return affected_entity_ids

    async def _quick_count_files(self, directory: Path) -> int:
        """Fast file count using find command.

        Uses subprocess to leverage OS-level file counting which is much faster
        than Python iteration, especially on network filesystems like TigrisFS.

        On Windows, subprocess is not supported with SelectorEventLoop (which we use
        to avoid aiosqlite cleanup issues), so we fall back to Python-based counting.

        Args:
            directory: Directory to count files in

        Returns:
            Number of files in directory (recursive)
        """
        # Windows with SelectorEventLoop doesn't support subprocess
        if sys.platform == "win32":
            count = 0
            async for _ in self.scan_directory(directory):
                count += 1
            return count

        process = await asyncio.create_subprocess_shell(
            f'find "{directory}" -type f | wc -l',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(
                f"FILE COUNT OPTIMIZATION FAILED: find command failed with exit code {process.returncode}, "
                f"error: {error_msg}. Falling back to manual count. "
                f"This will slow down watermark detection!"
            )
            # Fallback: count using scan_directory
            count = 0
            async for _ in self.scan_directory(directory):
                count += 1
            return count

        return int(stdout.strip())

    async def _scan_directory_modified_since(
        self, directory: Path, since_timestamp: float
    ) -> List[str]:
        """Use find -newermt for filesystem-level filtering of modified files.

        This is dramatically faster than scanning all files and comparing mtimes,
        especially on network filesystems like TigrisFS where stat operations are expensive.

        On Windows, subprocess is not supported with SelectorEventLoop (which we use
        to avoid aiosqlite cleanup issues), so we implement mtime filtering in Python.

        Args:
            directory: Directory to scan
            since_timestamp: Unix timestamp to find files newer than

        Returns:
            List of relative file paths modified since the timestamp (respects .bmignore)
        """
        # Windows with SelectorEventLoop doesn't support subprocess
        # Implement mtime filtering in Python to preserve watermark optimization
        if sys.platform == "win32":
            file_paths = []
            async for file_path_str, stat_info in self.scan_directory(directory):
                if stat_info.st_mtime > since_timestamp:
                    rel_path = Path(file_path_str).relative_to(directory).as_posix()
                    file_paths.append(rel_path)
            return file_paths

        # Convert timestamp to find-compatible format
        since_date = datetime.fromtimestamp(since_timestamp).strftime("%Y-%m-%d %H:%M:%S")

        process = await asyncio.create_subprocess_shell(
            f'find "{directory}" -type f -newermt "{since_date}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(
                f"SCAN OPTIMIZATION FAILED: find -newermt command failed with exit code {process.returncode}, "
                f"error: {error_msg}. Falling back to full scan. "
                f"This will cause slow syncs on large projects!"
            )
            # Fallback to full scan
            return await self._scan_directory_full(directory)

        # Convert absolute paths to relative and filter through ignore patterns
        file_paths = []
        for line in stdout.decode().splitlines():
            if line:
                try:
                    abs_path = Path(line)
                    rel_path = abs_path.relative_to(directory).as_posix()

                    # Apply ignore patterns (same as scan_directory)
                    if should_ignore_path(abs_path, directory, self._ignore_patterns):
                        logger.trace(f"Ignoring path per .bmignore: {rel_path}")
                        continue

                    file_paths.append(rel_path)
                except ValueError:
                    # Path is not relative to directory, skip it
                    logger.warning(f"Skipping file not under directory: {line}")
                    continue

        return file_paths

    async def _scan_directory_full(self, directory: Path) -> List[str]:
        """Full directory scan returning all file paths.

        Uses scan_directory() which respects .bmignore patterns.

        Args:
            directory: Directory to scan

        Returns:
            List of relative file paths (respects .bmignore)
        """
        file_paths = []
        async for file_path_str, _ in self.scan_directory(directory):
            rel_path = Path(file_path_str).relative_to(directory).as_posix()
            file_paths.append(rel_path)
        return file_paths

    async def scan_directory(self, directory: Path) -> AsyncIterator[Tuple[str, os.stat_result]]:
        """Stream files from directory using aiofiles.os.scandir() with cached stat info.

        This method uses aiofiles.os.scandir() to leverage async I/O and cached stat
        information from directory entries. This reduces network I/O by 50% on network
        filesystems like TigrisFS by avoiding redundant stat() calls.

        Args:
            directory: Directory to scan

        Yields:
            Tuples of (absolute_file_path, stat_info) for each file
        """
        try:
            entries = await aiofiles.os.scandir(directory)
        except PermissionError:
            logger.warning(f"Permission denied scanning directory: {directory}")
            return

        results = []
        subdirs = []

        for entry in entries:
            entry_path = Path(entry.path)

            # Check ignore patterns
            if should_ignore_path(entry_path, directory, self._ignore_patterns):
                logger.trace(f"Ignoring path per .bmignore: {entry_path.relative_to(directory)}")
                continue

            if entry.is_dir(follow_symlinks=False):
                # Collect subdirectories to recurse into
                subdirs.append(entry_path)
            elif entry.is_file(follow_symlinks=False):
                # Get cached stat info (no extra syscall!)
                stat_info = entry.stat(follow_symlinks=False)
                results.append((entry.path, stat_info))

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
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(project_path, markdown_processor, app_config=app_config)

    # Initialize repositories
    entity_repository = EntityRepository(session_maker, project_id=project.id)
    observation_repository = ObservationRepository(session_maker, project_id=project.id)
    relation_repository = RelationRepository(session_maker, project_id=project.id)
    search_repository = create_search_repository(session_maker, project_id=project.id)
    project_repository = ProjectRepository(session_maker)

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
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )

    return sync_service
