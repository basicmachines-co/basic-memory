"""Service for syncing files between filesystem and database."""

from pathlib import Path
from typing import Dict

from loguru import logger
from sqlalchemy.exc import IntegrityError

from basic_memory import file_utils
from basic_memory.markdown import EntityParser, EntityMarkdown
from basic_memory.repository import EntityRepository, RelationRepository
from basic_memory.services import EntityService
from basic_memory.services.search_service import SearchService
from basic_memory.sync import FileChangeScanner
from basic_memory.sync.utils import SyncReport


class SyncService:
    """Syncs documents and knowledge files with database.

    Implements two-pass sync strategy for knowledge files to handle relations:
    1. First pass creates/updates entities without relations
    2. Second pass processes relations after all entities exist
    """

    def __init__(
        self,
        scanner: FileChangeScanner,
        entity_service: EntityService,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        relation_repository: RelationRepository,
        search_service: SearchService,
    ):
        self.scanner = scanner
        self.entity_service = entity_service
        self.entity_parser = entity_parser
        self.entity_repository = entity_repository
        self.relation_repository = relation_repository
        self.search_service = search_service

    async def handle_entity_deletion(self, file_path: str):
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
                await self.search_service.delete_by_permalink(permalink)

    async def sync(self, directory: Path) -> SyncReport:
        """Sync knowledge files with database."""
        changes = await self.scanner.find_knowledge_changes(directory)
        logger.info(f"Found {changes.total_changes} knowledge changes")

        # Handle moves first
        for old_path, new_path in changes.moves.items():
            logger.debug(f"Moving entity: {old_path} -> {new_path}")
            entity = await self.entity_repository.get_by_file_path(old_path)
            if entity:
                # Update file_path but keep the same permalink for link stability
                updated = await self.entity_repository.update(
                    entity.id, {"file_path": new_path, "checksum": changes.checksums[new_path]}
                )
                # update search index
                if updated:
                    await self.search_service.index_entity(updated)

        # Handle deletions next
        # remove rows from db for files no longer present
        for path in changes.deleted:
            await self.handle_entity_deletion(path)

        # Parse files that need updating
        parsed_entities: Dict[str, EntityMarkdown] = {}

        for path in [*changes.new, *changes.modified]:
            entity_markdown = await self.entity_parser.parse_file(directory / path)
            parsed_entities[path] = entity_markdown

        # First pass: Create/update entities
        # entities will have a null checksum to indicate they are not complete
        for path, entity_markdown in parsed_entities.items():
            # Get unique permalink and update markdown if needed
            permalink = await self.entity_service.resolve_permalink(
                Path(path), markdown=entity_markdown
            )

            if permalink != entity_markdown.frontmatter.permalink:
                # Add/update permalink in frontmatter
                logger.info(f"Adding permalink '{permalink}' to file: {path}")

                # update markdown
                entity_markdown.frontmatter.metadata["permalink"] = permalink

                # update file frontmatter
                updated_checksum = await file_utils.update_frontmatter(
                    directory / path, {"permalink": permalink}
                )

                # Update checksum in changes report since file was modified
                changes.checksums[path] = updated_checksum

            # if the file is new, create an entity
            if path in changes.new:
                # Create entity with final permalink
                logger.debug(f"Creating new entity_markdown: {path}")
                await self.entity_service.create_entity_from_markdown(Path(path), entity_markdown)
            # otherwise we need to update the entity and observations
            else:
                logger.debug(f"Updating entity_markdown: {path}")
                await self.entity_service.update_entity_and_observations(
                    Path(path), entity_markdown
                )

        # Second pass
        for path, entity_markdown in parsed_entities.items():
            logger.debug(f"Updating relations for: {path}")

            # Process relations
            checksum = changes.checksums[path]
            entity = await self.entity_service.update_entity_relations(Path(path), entity_markdown)

            # add to search index
            await self.search_service.index_entity(entity)

            # Set final checksum to mark sync complete
            await self.entity_repository.update(entity.id, {"checksum": checksum})

        # Third pass: Try to resolve any forward references
        logger.debug("Attempting to resolve forward references")
        for relation in await self.relation_repository.find_unresolved_relations():
            target_entity = await self.entity_service.link_resolver.resolve_link(relation.to_name)
            # check we found a link that is not the source
            if target_entity and target_entity.id != relation.from_id:
                logger.debug(
                    f"Resolved forward reference: {relation.to_name} -> {target_entity.permalink}"
                )

                try:
                    await self.relation_repository.update(
                        relation.id,
                        {
                            "to_id": target_entity.id,
                            "to_name": target_entity.title,  # Update to actual title
                        },
                    )
                except IntegrityError as e:
                    logger.info(f"Ignoring duplicate relation {relation}")

                # update search index
                await self.search_service.index_entity(target_entity)

        return changes
