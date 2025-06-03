"""Service for syncing files between filesystem and database."""

import os
import time
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

    async def sync(self, directory: Path) -> SyncReport:
        """Sync all files with database."""

        start_time = time.time()
        logger.info(f"Sync operation started for directory: {directory}")

        # initial paths from db to sync
        # path -> checksum
        report = await self.scan(directory)

        # Initialize progress tracking if requested
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
            await self.sync_file(path, new=True)

        for path in report.modified:
            await self.sync_file(path, new=False)

        await self.resolve_relations()

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
        file_content = file_path.read_text(encoding="utf-8")
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
        final_checksum = await self.file_service.compute_checksum(path)

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
        checksum = await self.file_service.compute_checksum(path)
        if new:
            # Generate permalink from path
            await self.entity_service.resolve_permalink(path)

            # get file timestamps
            file_stats = self.file_service.file_stats(path)
            created = datetime.fromtimestamp(file_stats.st_ctime)
            modified = datetime.fromtimestamp(file_stats.st_mtime)

            # get mime type
            content_type = self.file_service.content_type(path)

            file_path = Path(path)
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
            # Update file_path in all cases
            updates = {"file_path": new_path}

            # If configured, also update permalink to match new path
            if self.app_config.update_permalinks_on_move and self.file_service.is_markdown(new_path):
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

            updated = await self.entity_repository.update(entity.id, updates)

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

    async def resolve_relations(self):
        """Try to resolve any unresolved relations"""

        unresolved_relations = await self.relation_repository.find_unresolved_relations()

        logger.info("Resolving forward references", count=len(unresolved_relations))

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
                rel_path = str(path.relative_to(directory))
                checksum = await self.file_service.compute_checksum(rel_path)
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