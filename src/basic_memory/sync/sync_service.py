"""Service for syncing files between filesystem and database."""

import os
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Set, Dict
from typing import Tuple

import logfire
from loguru import logger
from sqlalchemy.exc import IntegrityError

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
        entity_service: EntityService,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        relation_repository: RelationRepository,
        search_service: SearchService,
        file_service: FileService,
    ):
        self.entity_service = entity_service
        self.entity_parser = entity_parser
        self.entity_repository = entity_repository
        self.relation_repository = relation_repository
        self.search_service = search_service
        self.file_service = file_service

    async def sync(self, directory: Path) -> SyncReport:
        """Sync all files with database."""

        with logfire.span(f"sync {directory}", directory=directory):  # pyright: ignore [reportGeneralTypeIssues]
            # initial paths from db to sync
            # path -> checksum
            report = await self.scan(directory)

            # order of sync matters to resolve relations effectively

            # sync moves first
            for old_path, new_path in report.moves.items():
                # in the case where a file has been deleted and replaced by another file
                # it will show up in the move and modified lists, so handle it in modified
                if new_path in report.modified:
                    report.modified.remove(new_path)
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
            return report

    async def scan(self, directory):
        """Scan directory for changes compared to database state."""

        db_paths = await self.get_db_file_state()

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
        return {r.file_path: r.checksum or "" for r in db_records}

    async def sync_file(self, path: str, new: bool = True) -> Tuple[Entity, str]:
        """Sync a single file."""

        try:
            if self.file_service.is_markdown(path):
                entity, checksum = await self.sync_markdown_file(path, new)
            else:
                entity, checksum = await self.sync_regular_file(path, new)
            await self.search_service.index_entity(entity)
            return entity, checksum

        except Exception as e:  # pragma: no cover
            logger.exception(f"Failed to sync {path}: {e}")
            return None, None  # pyright: ignore

    async def sync_markdown_file(self, path: str, new: bool = True) -> Tuple[Entity, str]:
        """Sync a markdown file with full proces    sing."""

        # Parse markdown first to get any existing permalink
        entity_markdown = await self.entity_parser.parse_file(path)

        # Resolve permalink - this handles all the cases including conflicts
        permalink = await self.entity_service.resolve_permalink(path, markdown=entity_markdown)

        # If permalink changed, update the file
        if permalink != entity_markdown.frontmatter.permalink:
            logger.info(f"Updating permalink in {path}: {permalink}")
            entity_markdown.frontmatter.metadata["permalink"] = permalink
            checksum = await self.file_service.update_frontmatter(path, {"permalink": permalink})
        else:
            checksum = await self.file_service.compute_checksum(path)

        # if the file is new, create an entity
        if new:
            # Create entity with final permalink
            logger.debug(f"Creating new entity from markdown: {path}")
            await self.entity_service.create_entity_from_markdown(Path(path), entity_markdown)

        # otherwise we need to update the entity and observations
        else:
            logger.debug(f"Updating entity from markdown: {path}")
            await self.entity_service.update_entity_and_observations(Path(path), entity_markdown)

        # Update relations and search index
        entity = await self.entity_service.update_entity_relations(path, entity_markdown)

        # set checksum
        await self.entity_repository.update(entity.id, {"checksum": checksum})
        return entity, checksum

    async def sync_regular_file(self, path: str, new: bool = True) -> Tuple[Entity, str]:
        """Sync a non-markdown file with basic tracking."""

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
            assert entity is not None, "entity should not be None for existing file"
            updated = await self.entity_repository.update(
                entity.id, {"file_path": path, "checksum": checksum}
            )
            assert updated is not None, "entity should be updated"
            return updated, checksum

    async def handle_delete(self, file_path: str):
        """Handle complete entity deletion including search index cleanup."""

        # First get entity to get permalink before deletion
        entity = await self.entity_repository.get_by_file_path(file_path)
        if entity:
            logger.debug(f"Deleting entity and cleaning up search index: {file_path}")

            # Delete from db (this cascades to observations/relations)
            await self.entity_service.delete_entity_by_file_path(file_path)

            # Clean up search index
            permalinks = (
                [entity.permalink]
                + [o.permalink for o in entity.observations]
                + [r.permalink for r in entity.relations]
            )
            logger.debug(f"Deleting from search index: {permalinks}")
            for permalink in permalinks:
                if permalink:
                    await self.search_service.delete_by_permalink(permalink)
                else:
                    await self.search_service.delete_by_entity_id(entity.id)

    async def handle_move(self, old_path, new_path):
        logger.debug(f"Moving entity: {old_path} -> {new_path}")
        entity = await self.entity_repository.get_by_file_path(old_path)
        if entity:
            # Update file_path but keep the same permalink for link stability
            updated = await self.entity_repository.update(entity.id, {"file_path": new_path})
            assert updated is not None, "entity should be updated"
            # update search index
            await self.search_service.index_entity(updated)

    async def resolve_relations(self):
        """Try to resolve any unresolved relations"""

        unresolved_relations = await self.relation_repository.find_unresolved_relations()
        logger.debug(f"Attempting to resolve {len(unresolved_relations)} forward references")
        for relation in unresolved_relations:
            resolved_entity = await self.entity_service.link_resolver.resolve_link(relation.to_name)

            # ignore reference to self
            if resolved_entity and resolved_entity.id != relation.from_id:
                logger.debug(
                    f"Resolved forward reference: {relation.to_name} -> {resolved_entity.title}"
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
                    logger.debug(f"Ignoring duplicate relation {relation}")

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

        logger.debug(f"Scanning directory: {directory}")
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
                logger.debug(f"Found file: {rel_path} with checksum: {checksum}")

        return result
