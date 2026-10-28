"""Memory JSON import service for Basic Memory."""

import logging
from typing import override, Any, Optional

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown, Observation, Relation
from basic_memory.importers.base import Importer
from basic_memory.schemas.importer import EntityImportResult
from basic_memory.schemas.memory_json import (
    MemoryJsonEntity,
    MemoryJsonRelation,
    parse_memory_json_records,
)

logger = logging.getLogger(__name__)


class MemoryJsonImporter(Importer[EntityImportResult]):
    """Service for importing memory.json format data."""

    @override
    def handle_error(  # pragma: no cover
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

    @override
    async def import_data(
        self, source_data: object, destination_folder: str = "", **kwargs: Any
    ) -> EntityImportResult:
        """Import entities and relations from a memory.json file.

        Args:
            source_data: Path to the memory.json file.
            destination_folder: Optional destination folder within the project.
            **kwargs: Additional keyword arguments.

        Returns:
            EntityImportResult containing statistics and status of the import.
        """
        # Input rejection stays outside the operational-failure handler so the
        # HTTP/CLI boundary can distinguish malformed uploads from storage faults.
        records = parse_memory_json_records(source_data)
        try:
            entity_relations: dict[str, list[Relation]] = {}
            entities: dict[str, MemoryJsonEntity] = {}

            # Ensure the destination folder exists if provided
            if destination_folder:  # pragma: no cover
                await self.ensure_folder_exists(destination_folder)

            for record in records:
                match record:
                    case MemoryJsonEntity():
                        entities[record.name] = record
                    case MemoryJsonRelation():
                        entity_relations.setdefault(record.source, []).append(
                            Relation(type=record.relation_type, target=record.target)
                        )

            # Second pass - create and write entities
            entities_created = 0
            for name, entity_data in entities.items():
                # Get entity type with fallback
                entity_type = entity_data.entity_type

                # Build permalink with optional destination folder prefix
                relative_path = (
                    f"{destination_folder}/{entity_type}/{name}"
                    if destination_folder
                    else f"{entity_type}/{name}"
                )
                permalink, file_path = self.build_import_paths(relative_path)

                # Ensure entity type directory exists using FileService with relative path
                entity_type_dir = (
                    f"{destination_folder}/{entity_type}" if destination_folder else entity_type
                )
                await self.ensure_folder_exists(entity_type_dir)

                # Get observations with fallback to empty list
                observations = entity_data.observations

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
                await self.write_entity(entity, file_path)
                entities_created += 1

            relations_count = sum(len(rels) for rels in entity_relations.values())

            return EntityImportResult(
                import_count={"entities": entities_created, "relations": relations_count},
                success=True,
                entities=entities_created,
                relations=relations_count,
                skipped_entities=0,
            )

        except Exception as e:  # pragma: no cover
            logger.exception("Failed to import memory.json")
            return self.handle_error("Failed to import memory.json", e)
