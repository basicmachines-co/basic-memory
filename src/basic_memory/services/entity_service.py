"""Service for managing entities in the database."""

from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import frontmatter
import yaml
from loguru import logger
from sqlalchemy.exc import IntegrityError

from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.file_utils import has_frontmatter, parse_frontmatter, remove_frontmatter
from basic_memory.markdown import EntityMarkdown
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.markdown.utils import entity_model_from_markdown, schema_to_markdown
from basic_memory.models import Entity as EntityModel
from basic_memory.models import Observation, Relation
from basic_memory.repository import ObservationRepository, RelationRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.base import Permalink
from basic_memory.services import BaseService, FileService
from basic_memory.services.exceptions import EntityCreationError, EntityNotFoundError
from basic_memory.services.link_resolver import LinkResolver
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
        logger.debug(
            f"Creating or updating entity: {schema.file_path}, permalink: {schema.permalink}"
        )

        # Try to find existing entity using smart resolution
        existing = await self.link_resolver.resolve_link(
            schema.file_path
        ) or await self.link_resolver.resolve_link(schema.permalink)

        if existing:
            logger.debug(f"Found existing entity: {existing.file_path}")
            return await self.update_entity(existing, schema), False
        else:
            # Create new entity
            return await self.create_entity(schema), True

    async def create_entity(self, schema: EntitySchema) -> EntityModel:
        """Create a new entity and write to filesystem."""
        logger.debug(f"Creating entity: {schema.title}")

        # Get file path and ensure it's a Path object
        file_path = Path(schema.file_path)

        if await self.file_service.exists(file_path):
            raise EntityCreationError(
                f"file for entity {schema.folder}/{schema.title} already exists: {file_path}"
            )

        # Parse content frontmatter to check for user-specified permalink
        content_markdown = None
        if schema.content and has_frontmatter(schema.content):
            content_frontmatter = parse_frontmatter(schema.content)
            if "permalink" in content_frontmatter:
                # Create a minimal EntityMarkdown object for permalink resolution
                from basic_memory.markdown.schemas import EntityFrontmatter

                frontmatter_metadata = {
                    "title": schema.title,
                    "type": schema.entity_type,
                    "permalink": content_frontmatter["permalink"],
                }
                frontmatter_obj = EntityFrontmatter(metadata=frontmatter_metadata)
                content_markdown = EntityMarkdown(
                    frontmatter=frontmatter_obj,
                    content="",  # content not needed for permalink resolution
                    observations=[],
                    relations=[],
                )

        # Get unique permalink (prioritizing content frontmatter)
        permalink = await self.resolve_permalink(file_path, content_markdown)
        schema._permalink = permalink

        post = await schema_to_markdown(schema)

        # write file
        final_content = frontmatter.dumps(post, sort_keys=False)
        checksum = await self.file_service.write_file(file_path, final_content)

        # parse entity from file
        entity_markdown = await self.entity_parser.parse_file(file_path)

        # create entity
        created = await self.create_entity_from_markdown(file_path, entity_markdown)

        # add relations
        entity = await self.update_entity_relations(created.file_path, entity_markdown)

        # Set final checksum to mark complete
        return await self.repository.update(entity.id, {"checksum": checksum})

    async def update_entity(self, entity: EntityModel, schema: EntitySchema) -> EntityModel:
        """Update an entity's content and metadata."""
        logger.debug(
            f"Updating entity with permalink: {entity.permalink} content-type: {schema.content_type}"
        )

        # Convert file path string to Path
        file_path = Path(entity.file_path)

        # Read existing frontmatter from the file if it exists
        existing_markdown = await self.entity_parser.parse_file(file_path)

        # Parse content frontmatter to check for user-specified permalink
        content_markdown = None
        if schema.content and has_frontmatter(schema.content):
            content_frontmatter = parse_frontmatter(schema.content)
            if "permalink" in content_frontmatter:
                # Create a minimal EntityMarkdown object for permalink resolution
                from basic_memory.markdown.schemas import EntityFrontmatter

                frontmatter_metadata = {
                    "title": schema.title,
                    "type": schema.entity_type,
                    "permalink": content_frontmatter["permalink"],
                }
                frontmatter_obj = EntityFrontmatter(metadata=frontmatter_metadata)
                content_markdown = EntityMarkdown(
                    frontmatter=frontmatter_obj,
                    content="",  # content not needed for permalink resolution
                    observations=[],
                    relations=[],
                )

        # Check if we need to update the permalink based on content frontmatter
        new_permalink = entity.permalink  # Default to existing
        if content_markdown and content_markdown.frontmatter.permalink:
            # Resolve permalink with the new content frontmatter
            resolved_permalink = await self.resolve_permalink(file_path, content_markdown)
            if resolved_permalink != entity.permalink:
                new_permalink = resolved_permalink
                # Update the schema to use the new permalink
                schema._permalink = new_permalink

        # Create post with new content from schema
        post = await schema_to_markdown(schema)

        # Merge new metadata with existing metadata
        existing_markdown.frontmatter.metadata.update(post.metadata)

        # Ensure the permalink in the metadata is the resolved one
        if new_permalink != entity.permalink:
            existing_markdown.frontmatter.metadata["permalink"] = new_permalink

        # Create a new post with merged metadata
        merged_post = frontmatter.Post(post.content, **existing_markdown.frontmatter.metadata)

        # write file
        final_content = frontmatter.dumps(merged_post, sort_keys=False)
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
                if len(entities) != 1:  # pragma: no cover
                    logger.error(
                        "Entity lookup error", entity_id=permalink_or_id, found_count=len(entities)
                    )
                    raise ValueError(
                        f"Expected 1 entity with ID {permalink_or_id}, got {len(entities)}"
                    )
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
        logger.debug(f"Creating entity: {markdown.frontmatter.title} file_path: {file_path}")
        model = entity_model_from_markdown(file_path, markdown)

        # Mark as incomplete because we still need to add relations
        model.checksum = None
        # Repository will set project_id automatically
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
                    f"Skipping duplicate relation {rel.type} from {db_entity.permalink} target: {rel.target}"
                )
                continue

        return await self.repository.get_by_file_path(path)

    async def edit_entity(
        self,
        identifier: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
    ) -> EntityModel:
        """Edit an existing entity's content using various operations.

        Args:
            identifier: Entity identifier (permalink, title, etc.)
            operation: The editing operation (append, prepend, find_replace, replace_section)
            content: The content to add or use for replacement
            section: For replace_section operation - the markdown header
            find_text: For find_replace operation - the text to find and replace
            expected_replacements: For find_replace operation - expected number of replacements (default: 1)

        Returns:
            The updated entity model

        Raises:
            EntityNotFoundError: If the entity cannot be found
            ValueError: If required parameters are missing for the operation or replacement count doesn't match expected
        """
        logger.debug(f"Editing entity: {identifier}, operation: {operation}")

        # Find the entity using the link resolver
        entity = await self.link_resolver.resolve_link(identifier)
        if not entity:
            raise EntityNotFoundError(f"Entity not found: {identifier}")

        # Read the current file content
        file_path = Path(entity.file_path)
        current_content, _ = await self.file_service.read_file(file_path)

        # Apply the edit operation
        new_content = self.apply_edit_operation(
            current_content, operation, content, section, find_text, expected_replacements
        )

        # Write the updated content back to the file
        checksum = await self.file_service.write_file(file_path, new_content)

        # Parse the updated file to get new observations/relations
        entity_markdown = await self.entity_parser.parse_file(file_path)

        # Update entity and its relationships
        entity = await self.update_entity_and_observations(file_path, entity_markdown)
        await self.update_entity_relations(str(file_path), entity_markdown)

        # Set final checksum to match file
        entity = await self.repository.update(entity.id, {"checksum": checksum})

        return entity

    def apply_edit_operation(
        self,
        current_content: str,
        operation: str,
        content: str,
        section: Optional[str] = None,
        find_text: Optional[str] = None,
        expected_replacements: int = 1,
    ) -> str:
        """Apply the specified edit operation to the current content."""

        if operation == "append":
            # Ensure proper spacing
            if current_content and not current_content.endswith("\n"):
                return current_content + "\n" + content
            return current_content + content  # pragma: no cover

        elif operation == "prepend":
            # Handle frontmatter-aware prepending
            return self._prepend_after_frontmatter(current_content, content)

        elif operation == "find_replace":
            if not find_text:
                raise ValueError("find_text is required for find_replace operation")
            if not find_text.strip():
                raise ValueError("find_text cannot be empty or whitespace only")

            # Count actual occurrences
            actual_count = current_content.count(find_text)

            # Validate count matches expected
            if actual_count != expected_replacements:
                if actual_count == 0:
                    raise ValueError(f"Text to replace not found: '{find_text}'")
                else:
                    raise ValueError(
                        f"Expected {expected_replacements} occurrences of '{find_text}', "
                        f"but found {actual_count}"
                    )

            return current_content.replace(find_text, content)

        elif operation == "replace_section":
            if not section:
                raise ValueError("section is required for replace_section operation")
            if not section.strip():
                raise ValueError("section cannot be empty or whitespace only")
            return self.replace_section_content(current_content, section, content)

        else:
            raise ValueError(f"Unsupported operation: {operation}")

    def replace_section_content(
        self, current_content: str, section_header: str, new_content: str
    ) -> str:
        """Replace content under a specific markdown section header.

        This method uses a simple, safe approach: when replacing a section, it only
        replaces the immediate content under that header until it encounters the next
        header of ANY level. This means:

        - Replacing "# Header" replaces content until "## Subsection" (preserves subsections)
        - Replacing "## Section" replaces content until "### Subsection" (preserves subsections)
        - More predictable and safer than trying to consume entire hierarchies

        Args:
            current_content: The current markdown content
            section_header: The section header to find and replace (e.g., "## Section Name")
            new_content: The new content to replace the section with

        Returns:
            The updated content with the section replaced

        Raises:
            ValueError: If multiple sections with the same header are found
        """
        # Normalize the section header (ensure it starts with #)
        if not section_header.startswith("#"):
            section_header = "## " + section_header

        # First pass: count matching sections to check for duplicates
        lines = current_content.split("\n")
        matching_sections = []

        for i, line in enumerate(lines):
            if line.strip() == section_header.strip():
                matching_sections.append(i)

        # Handle multiple sections error
        if len(matching_sections) > 1:
            raise ValueError(
                f"Multiple sections found with header '{section_header}'. "
                f"Section replacement requires unique headers."
            )

        # If no section found, append it
        if len(matching_sections) == 0:
            logger.info(f"Section '{section_header}' not found, appending to end of document")
            separator = "\n\n" if current_content and not current_content.endswith("\n\n") else ""
            return current_content + separator + section_header + "\n" + new_content

        # Replace the single matching section
        result_lines = []
        section_line_idx = matching_sections[0]

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check if this is our target section header
            if i == section_line_idx:
                # Add the section header and new content
                result_lines.append(line)
                result_lines.append(new_content)
                i += 1

                # Skip the original section content until next header or end
                while i < len(lines):
                    next_line = lines[i]
                    # Stop consuming when we hit any header (preserve subsections)
                    if next_line.startswith("#"):
                        # We found another header - continue processing from here
                        break
                    i += 1
                # Continue processing from the next header (don't increment i again)
                continue

            # Add all other lines (including subsequent sections)
            result_lines.append(line)
            i += 1

        return "\n".join(result_lines)

    def _prepend_after_frontmatter(self, current_content: str, content: str) -> str:
        """Prepend content after frontmatter, preserving frontmatter structure."""

        # Check if file has frontmatter
        if has_frontmatter(current_content):
            try:
                # Parse and separate frontmatter from body
                frontmatter_data = parse_frontmatter(current_content)
                body_content = remove_frontmatter(current_content)

                # Prepend content to the body
                if content and not content.endswith("\n"):
                    new_body = content + "\n" + body_content
                else:
                    new_body = content + body_content

                # Reconstruct file with frontmatter + prepended body
                yaml_fm = yaml.dump(frontmatter_data, sort_keys=False, allow_unicode=True)
                return f"---\n{yaml_fm}---\n\n{new_body.strip()}"

            except Exception as e:  # pragma: no cover
                logger.warning(
                    f"Failed to parse frontmatter during prepend: {e}"
                )  # pragma: no cover
                # Fall back to simple prepend if frontmatter parsing fails  # pragma: no cover

        # No frontmatter or parsing failed - do simple prepend  # pragma: no cover
        if content and not content.endswith("\n"):  # pragma: no cover
            return content + "\n" + current_content  # pragma: no cover
        return content + current_content  # pragma: no cover

    async def move_entity(
        self,
        identifier: str,
        destination_path: str,
        project_config: ProjectConfig,
        app_config: BasicMemoryConfig,
    ) -> EntityModel:
        """Move entity to new location with database consistency.

        Args:
            identifier: Entity identifier (title, permalink, or memory:// URL)
            destination_path: New path relative to project root
            project_config: Project configuration for file operations
            app_config: App configuration for permalink update settings

        Returns:
            Success message with move details

        Raises:
            EntityNotFoundError: If the entity cannot be found
            ValueError: If move operation fails due to validation or filesystem errors
        """
        logger.debug(f"Moving entity: {identifier} to {destination_path}")

        # 1. Resolve identifier to entity
        entity = await self.link_resolver.resolve_link(identifier)
        if not entity:
            raise EntityNotFoundError(f"Entity not found: {identifier}")

        current_path = entity.file_path
        old_permalink = entity.permalink

        # 2. Validate destination path format first
        if not destination_path or destination_path.startswith("/") or not destination_path.strip():
            raise ValueError(f"Invalid destination path: {destination_path}")

        # 3. Validate paths
        source_file = project_config.home / current_path
        destination_file = project_config.home / destination_path

        # Validate source exists
        if not source_file.exists():
            raise ValueError(f"Source file not found: {current_path}")

        # Check if destination already exists
        if destination_file.exists():
            raise ValueError(f"Destination already exists: {destination_path}")

        try:
            # 4. Create destination directory if needed
            destination_file.parent.mkdir(parents=True, exist_ok=True)

            # 5. Move physical file
            source_file.rename(destination_file)
            logger.info(f"Moved file: {current_path} -> {destination_path}")

            # 6. Prepare database updates
            updates = {"file_path": destination_path}

            # 7. Update permalink if configured
            if app_config.update_permalinks_on_move:
                # Generate new permalink from destination path
                new_permalink = await self.resolve_permalink(destination_path)

                # Update frontmatter with new permalink
                await self.file_service.update_frontmatter(
                    destination_path, {"permalink": new_permalink}
                )

                updates["permalink"] = new_permalink
                logger.info(f"Updated permalink: {old_permalink} -> {new_permalink}")

            # 8. Recalculate checksum
            new_checksum = await self.file_service.compute_checksum(destination_path)
            updates["checksum"] = new_checksum

            # 9. Update database
            updated_entity = await self.repository.update(entity.id, updates)
            if not updated_entity:
                raise ValueError(f"Failed to update entity in database: {entity.id}")

            return updated_entity

        except Exception as e:
            # Rollback: try to restore original file location if move succeeded
            if destination_file.exists() and not source_file.exists():
                try:
                    destination_file.rename(source_file)
                    logger.info(f"Rolled back file move: {destination_path} -> {current_path}")
                except Exception as rollback_error:  # pragma: no cover
                    logger.error(f"Failed to rollback file move: {rollback_error}")

            # Re-raise the original error with context
            raise ValueError(f"Move failed: {str(e)}") from e
