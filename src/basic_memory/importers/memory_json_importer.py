"""Memory JSON import service for Basic Memory."""

import logging
from typing import Any, Dict, List, Optional

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown, Observation, Relation
from basic_memory.importers.base import Importer
from basic_memory.schemas.importer import EntityImportResult

logger = logging.getLogger(__name__)


class MemoryJsonImporter(Importer[EntityImportResult]):
    """Service for importing memory.json format data."""

    def handle_error(
        self, message: str, error: Optional[Exception] = None
    ) -> EntityImportResult:
        """Return a failed EntityImportResult with an error message."""
        error_msg = f"{message}: {error}" if error else message
        return EntityImportResult(
            import_count={},
            success=False,
            error_message=error_msg,
            entities=0,
            relations=0,
            skipped_entities=0,
        )

    async def import_data(
        self, source_data, destination_folder: str = "", **kwargs: Any
    ) -> EntityImportResult:
        """Import entities and relations from a memory.json file.

        Args:
            source_data: Path to the memory.json file.
            destination_folder: Optional destination folder within the project.
            **kwargs: Additional keyword arguments.

        Returns:
            EntityImportResult containing statistics and status of the import.
        """
        try:
            # First pass - collect all relations by source entity
            entity_relations: Dict[str, List[Relation]] = {}
            entities: Dict[str, Dict[str, Any]] = {}
            skipped_entities: int = 0

            # Ensure the destination folder exists if provided
            if destination_folder:  # pragma: no cover
                await self.ensure_folder_exists(destination_folder)

            # First pass - collect entities and relations
            for line in source_data:
                data = line
                if data["type"] == "entity":
                    # Handle different possible name keys
                    entity_name = data.get("name") or data.get("entityName") or data.get("id")
                    if not entity_name:
                        logger.warning(f"Entity missing name field: {data}")  # pragma: no cover
                        skipped_entities += 1  # pragma: no cover
                        continue  # pragma: no cover
                    entities[entity_name] = data
                elif data["type"] == "relation":
                    # Store relation with its source entity
                    source = data.get("from") or data.get("from_id")
                    if source not in entity_relations:
                        entity_relations[source] = []
                    entity_relations[source].append(
                        Relation(
                            type=data.get("relationType") or data.get("relation_type"),
                            target=data.get("to") or data.get("to_id"),
                        )
                    )

            # Second pass - create and write entities
            entities_created = 0
            for name, entity_data in entities.items():
                # Get entity type with fallback
                entity_type = entity_data.get("entityType") or entity_data.get("type") or "entity"

                # Build permalink with optional destination folder prefix
                permalink = (
                    f"{destination_folder}/{entity_type}/{name}"
                    if destination_folder
                    else f"{entity_type}/{name}"
                )

                # Ensure entity type directory exists using FileService with relative path
                entity_type_dir = (
                    f"{destination_folder}/{entity_type}" if destination_folder else entity_type
                )
                await self.file_service.ensure_directory(entity_type_dir)

                # Get observations with fallback to empty list
                observations = entity_data.get("observations", [])

                entity = EntityMarkdown(
                    frontmatter=EntityFrontmatter(
                        metadata={
                            "type": entity_type,
                            "title": name,
                            "permalink": permalink,
                        }
                    ),
                    content=f"# {name}\n",
                    observations=[Observation(content=obs) for obs in observations],
                    relations=entity_relations.get(name, []),
                )

                # Write file using relative path - FileService handles base_path
                file_path = f"{entity.frontmatter.metadata['permalink']}.md"
                await self.write_entity(entity, file_path)
                entities_created += 1

            relations_count = sum(len(rels) for rels in entity_relations.values())

            return EntityImportResult(
                import_count={"entities": entities_created, "relations": relations_count},
                success=True,
                entities=entities_created,
                relations=relations_count,
                skipped_entities=skipped_entities,
            )

        except Exception as e:  # pragma: no cover
            logger.exception("Failed to import memory.json")
            return self.handle_error("Failed to import memory.json", e)
