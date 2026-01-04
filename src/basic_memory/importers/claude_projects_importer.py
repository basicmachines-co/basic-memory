"""Claude projects import service for Basic Memory."""

import logging
from typing import Any, Dict, Optional

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.importers.base import Importer
from basic_memory.schemas.importer import ProjectImportResult
from basic_memory.importers.utils import clean_filename

logger = logging.getLogger(__name__)


class ClaudeProjectsImporter(Importer[ProjectImportResult]):
    """Service for importing Claude projects."""

    def handle_error(  # pragma: no cover
        self, message: str, error: Optional[Exception] = None
    ) -> ProjectImportResult:
        """Return a failed ProjectImportResult with an error message."""
        error_msg = f"{message}: {error}" if error else message
        return ProjectImportResult(
            import_count={},
            success=False,
            error_message=error_msg,
            documents=0,
            prompts=0,
        )

    async def import_data(
        self, source_data, destination_folder: str, **kwargs: Any
    ) -> ProjectImportResult:
        """Import projects from Claude JSON export.

        Args:
            source_path: Path to the Claude projects.json file.
            destination_folder: Base folder for projects within the project.
            **kwargs: Additional keyword arguments.

        Returns:
            ProjectImportResult containing statistics and status of the import.
        """
        try:
            # Ensure the base folder exists
            if destination_folder:
                await self.ensure_folder_exists(destination_folder)

            projects = source_data

            # Process each project
            docs_imported = 0
            prompts_imported = 0

            for project in projects:
                project_dir = clean_filename(project["name"])

                # Create project directories using FileService with relative path
                docs_dir = (
                    f"{destination_folder}/{project_dir}/docs"
                    if destination_folder
                    else f"{project_dir}/docs"
                )
                await self.file_service.ensure_directory(docs_dir)

                # Import prompt template if it exists
                if prompt_entity := self._format_prompt_markdown(project, destination_folder):
                    # Write file using relative path - FileService handles base_path
                    file_path = f"{prompt_entity.frontmatter.metadata['permalink']}.md"
                    await self.write_entity(prompt_entity, file_path)
                    prompts_imported += 1

                # Import project documents
                for doc in project.get("docs", []):
                    entity = self._format_project_markdown(project, doc, destination_folder)
                    # Write file using relative path - FileService handles base_path
                    file_path = f"{entity.frontmatter.metadata['permalink']}.md"
                    await self.write_entity(entity, file_path)
                    docs_imported += 1

            return ProjectImportResult(
                import_count={"documents": docs_imported, "prompts": prompts_imported},
                success=True,
                documents=docs_imported,
                prompts=prompts_imported,
            )

        except Exception as e:  # pragma: no cover
            logger.exception("Failed to import Claude projects")
            return self.handle_error("Failed to import Claude projects", e)

    def _format_project_markdown(
        self, project: Dict[str, Any], doc: Dict[str, Any], destination_folder: str = ""
    ) -> EntityMarkdown:
        """Format a project document as a Basic Memory entity.

        Args:
            project: Project data.
            doc: Document data.
            destination_folder: Optional destination folder prefix.

        Returns:
            EntityMarkdown instance representing the document.
        """
        # Extract timestamps
        created_at = doc.get("created_at") or project["created_at"]
        modified_at = project["updated_at"]

        # Generate clean names for organization
        project_dir = clean_filename(project["name"])
        doc_file = clean_filename(doc["filename"])

        # Build permalink with optional destination folder prefix
        permalink = (
            f"{destination_folder}/{project_dir}/docs/{doc_file}"
            if destination_folder
            else f"{project_dir}/docs/{doc_file}"
        )

        # Create entity
        entity = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "type": "project_doc",
                    "title": doc["filename"],
                    "created": created_at,
                    "modified": modified_at,
                    "permalink": permalink,
                    "project_name": project["name"],
                    "project_uuid": project["uuid"],
                    "doc_uuid": doc["uuid"],
                }
            ),
            content=doc["content"],
        )

        return entity

    def _format_prompt_markdown(
        self, project: Dict[str, Any], destination_folder: str = ""
    ) -> Optional[EntityMarkdown]:
        """Format project prompt template as a Basic Memory entity.

        Args:
            project: Project data.
            destination_folder: Optional destination folder prefix.

        Returns:
            EntityMarkdown instance representing the prompt template, or None if
            no prompt template exists.
        """
        if not project.get("prompt_template"):
            return None

        # Extract timestamps
        created_at = project["created_at"]
        modified_at = project["updated_at"]

        # Generate clean project directory name
        project_dir = clean_filename(project["name"])

        # Build permalink with optional destination folder prefix
        permalink = (
            f"{destination_folder}/{project_dir}/prompt-template"
            if destination_folder
            else f"{project_dir}/prompt-template"
        )

        # Create entity
        entity = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "type": "prompt_template",
                    "title": f"Prompt Template: {project['name']}",
                    "created": created_at,
                    "modified": modified_at,
                    "permalink": permalink,
                    "project_name": project["name"],
                    "project_uuid": project["uuid"],
                }
            ),
            content=f"# Prompt Template: {project['name']}\n\n{project['prompt_template']}",
        )

        return entity
