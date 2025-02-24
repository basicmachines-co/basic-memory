"""Service for managing entities in the database."""

from pathlib import Path
from typing import Sequence, List, Optional, Tuple, Union

import frontmatter
from loguru import logger
from sqlalchemy.exc import IntegrityError

from basic_memory.markdown import EntityMarkdown
from basic_memory.markdown.utils import entity_model_from_markdown, schema_to_markdown
from basic_memory.models import Entity as EntityModel, Observation, Relation
from basic_memory.repository import ObservationRepository, RelationRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.base import Permalink
from basic_memory.services.exceptions import EntityNotFoundError, EntityCreationError
from basic_memory.services import FileService
from basic_memory.services import BaseService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.utils import generate_permalink


class EntityService(BaseService[EntityModel]):
    """Service for managing entities in the database."""

    def __init__(
        self,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        relation_repository: RelationRepository,
        file_service: FileService,
        link_resolver: LinkResolver,
    ):
        super().__init__(entity_repository)
        self.observation_repository = observation_repository
        self.relation_repository = relation_repository
        self.entity_parser = entity_parser
        self.file_service = file_service
        self.link_resolver = link_resolver

    async def resolve_permalink(
        self, file_path: Permalink | Path, markdown: Optional[EntityMarkdown] = None
    ) -> str:
        """Get or generate unique permalink for an entity.

        Priority:
        1. If markdown has permalink and it's not used by another file -> use as is
        2. If markdown has permalink but it's used by another file -> make unique
        3. For existing files, keep current permalink from db
        4. Generate new unique permalink from file path
        """
        # If markdown has explicit permalink, try to validate it
        if markdown and markdown.frontmatter.permalink:
            desired_permalink = markdown.frontmatter.permalink
            existing = await self.repository.get_by_permalink(desired_permalink)

            # If no conflict or it's our own file, use as is
            if not existing or existing.file_path == str(file_path):
                return desired_permalink

        # For existing files, try to find current permalink
        existing = await self.repository.get_by_file_path(str(file_path))
        if existing:
            return existing.permalink

        # New file - generate permalink
        if markdown and markdown.frontmatter.permalink:
            desired_permalink = markdown.frontmatter.permalink
        else:
            desired_permalink = generate_permalink(file_path)

        # Make unique if needed
        permalink = desired_permalink
        suffix = 1
        while await self.repository.get_by_permalink(permalink):
            permalink = f"{desired_permalink}-{suffix}"
            suffix += 1
            logger.debug(f"creating unique permalink: {permalink}")

        return permalink

    async def create_or_update_entity(self, schema: EntitySchema) -> Tuple[EntityModel, bool]:
        """Create new entity or update existing one.
        Returns: (entity, is_new) where is_new is True if a new entity was created
        """
        logger.debug(f"Creating or updating entity: {schema}")

        # Try to find existing entity using smart resolution
        existing = await self.link_resolver.resolve_link(schema.permalink)

        if existing:
            logger.debug(f"Found existing entity: {existing.permalink}")
            return await self.update_entity(existing, schema), False
        else:
            # Create new entity
            return await self.create_entity(schema), True

    async def create_entity(self, schema: EntitySchema) -> EntityModel:
        """Create a new entity and write to filesystem."""
        logger.debug(f"Creating entity: {schema.permalink}")

        # Get file path and ensure it's a Path object
        file_path = Path(schema.file_path)

        if await self.file_service.exists(file_path):
            raise EntityCreationError(
                f"file for entity {schema.folder}/{schema.title} already exists: {file_path}"
            )

        # Get unique permalink
        permalink = await self.resolve_permalink(schema.permalink or file_path)
        schema._permalink = permalink

        post = await schema_to_markdown(schema)

        # write file
        final_content = frontmatter.dumps(post, sort_keys=False)
        checksum = await self.file_service.write_file(file_path, final_content)

        # parse entity from file
        entity_markdown = await self.entity_parser.parse_file(file_path)

        # create entity
        await self.create_entity_from_markdown(file_path, entity_markdown)

        # add relations
        entity = await self.update_entity_relations(str(file_path), entity_markdown)

        # Set final checksum to mark complete
        return await self.repository.update(entity.id, {"checksum": checksum})

    async def update_entity(self, entity: EntityModel, schema: EntitySchema) -> EntityModel:
        """Update an entity's content and metadata."""
        logger.debug(f"Updating entity with permalink: {entity.permalink}")

        # Convert file path string to Path
        file_path = Path(entity.file_path)

        post = await schema_to_markdown(schema)

        # write file
        final_content = frontmatter.dumps(post)
        checksum = await self.file_service.write_file(file_path, final_content)

        # parse entity from file
        entity_markdown = await self.entity_parser.parse_file(file_path)

        # update entity in db
        entity = await self.update_entity_and_observations(file_path, entity_markdown)

        # add relations
        await self.update_entity_relations(str(file_path), entity_markdown)

        # Set final checksum to match file
        entity = await self.repository.update(entity.id, {"checksum": checksum})

        return entity

    async def delete_entity(self, permalink_or_id: str | int) -> bool:
        """Delete entity and its file."""
        logger.debug(f"Deleting entity: {permalink_or_id}")

        try:
            # Get entity first for file deletion
            if isinstance(permalink_or_id, str):
                entity = await self.get_by_permalink(permalink_or_id)
            else:
                entities = await self.get_entities_by_id([permalink_or_id])
                assert len(entities) == 1, f"Expected 1 entity, got {len(entities)}"
                entity = entities[0]

            # Delete file first
            await self.file_service.delete_entity_file(entity)

            # Delete from DB (this will cascade to observations/relations)
            return await self.repository.delete(entity.id)

        except EntityNotFoundError:
            logger.info(f"Entity not found: {permalink_or_id}")
            return True  # Already deleted

    async def get_by_permalink(self, permalink: str) -> EntityModel:
        """Get entity by type and name combination."""
        logger.debug(f"Getting entity by permalink: {permalink}")
        db_entity = await self.repository.get_by_permalink(permalink)
        if not db_entity:
            raise EntityNotFoundError(f"Entity not found: {permalink}")
        return db_entity

    async def get_entities_by_id(self, ids: List[int]) -> Sequence[EntityModel]:
        """Get specific entities and their relationships."""
        logger.debug(f"Getting entities: {ids}")
        return await self.repository.find_by_ids(ids)

    async def get_entities_by_permalinks(self, permalinks: List[str]) -> Sequence[EntityModel]:
        """Get specific nodes and their relationships."""
        logger.debug(f"Getting entities permalinks: {permalinks}")
        return await self.repository.find_by_permalinks(permalinks)

    async def delete_entity_by_file_path(self, file_path: Union[str, Path]) -> None:
        """Delete entity by file path."""
        await self.repository.delete_by_file_path(str(file_path))

    async def create_entity_from_markdown(
        self, file_path: Path, markdown: EntityMarkdown
    ) -> EntityModel:
        """Create entity and observations only.

        Creates the entity with null checksum to indicate sync not complete.
        Relations will be added in second pass.
        """
        logger.debug(f"Creating entity: {markdown.frontmatter.title}")
        model = entity_model_from_markdown(file_path, markdown)

        # Mark as incomplete because we still need to add relations
        model.checksum = None
        return await self.repository.add(model)

    async def update_entity_and_observations(
        self, file_path: Path, markdown: EntityMarkdown
    ) -> EntityModel:
        """Update entity fields and observations.

        Updates everything except relations and sets null checksum
        to indicate sync not complete.
        """
        logger.debug(f"Updating entity and observations: {file_path}")

        db_entity = await self.repository.get_by_file_path(str(file_path))

        # Clear observations for entity
        await self.observation_repository.delete_by_fields(entity_id=db_entity.id)

        # add new observations
        observations = [
            Observation(
                entity_id=db_entity.id,
                content=obs.content,
                category=obs.category,
                context=obs.context,
                tags=obs.tags,
            )
            for obs in markdown.observations
        ]
        await self.observation_repository.add_all(observations)

        # update values from markdown
        db_entity = entity_model_from_markdown(file_path, markdown, db_entity)

        # checksum value is None == not finished with sync
        db_entity.checksum = None

        # update entity
        return await self.repository.update(
            db_entity.id,
            db_entity,
        )

    async def update_entity_relations(
        self,
        path: str,
        markdown: EntityMarkdown,
    ) -> EntityModel:
        """Update relations for entity"""
        logger.debug(f"Updating relations for entity: {path}")

        db_entity = await self.repository.get_by_file_path(path)

        # Clear existing relations first
        await self.relation_repository.delete_outgoing_relations_from_entity(db_entity.id)

        # Process each relation
        for rel in markdown.relations:
            # Resolve the target permalink
            target_entity = await self.link_resolver.resolve_link(
                rel.target,
            )

            # if the target is found, store the id
            target_id = target_entity.id if target_entity else None
            # if the target is found, store the title, otherwise add the target for a "forward link"
            target_name = target_entity.title if target_entity else rel.target

            # Create the relation
            relation = Relation(
                from_id=db_entity.id,
                to_id=target_id,
                to_name=target_name,
                relation_type=rel.type,
                context=rel.context,
            )
            try:
                await self.relation_repository.add(relation)
            except IntegrityError:
                # Unique constraint violation - relation already exists
                logger.debug(
                    f"Skipping duplicate relation {rel.type} from {db_entity.permalink} target: {rel.target}, type: {rel.type}"
                )
                continue

        return await self.repository.get_by_file_path(path)
