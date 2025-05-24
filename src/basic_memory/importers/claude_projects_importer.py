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
            base_path = self.base_path
            if destination_folder:
                base_path = self.ensure_folder_exists(destination_folder)

            projects = source_data

            # Process each project
            docs_imported = 0
            prompts_imported = 0

            for project in projects:
                project_dir = clean_filename(project["name"])

                # Create project directories
                docs_dir = base_path / project_dir / "docs"
                docs_dir.mkdir(parents=True, exist_ok=True)

                # Import prompt template if it exists
                if prompt_entity := self._format_prompt_markdown(project):
                    file_path = base_path / f"{prompt_entity.frontmatter.metadata['permalink']}.md"
                    await self.write_entity(prompt_entity, file_path)
                    prompts_imported += 1

                # Import project documents
                for doc in project.get("docs", []):
                    entity = self._format_project_markdown(project, doc)
                    file_path = base_path / f"{entity.frontmatter.metadata['permalink']}.md"
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
            return self.handle_error("Failed to import Claude projects", e)  # pyright: ignore [reportReturnType]

    def _format_project_markdown(
        self, project: Dict[str, Any], doc: Dict[str, Any]
    ) -> EntityMarkdown:
        """Format a project document as a Basic Memory entity.

        Args:
            project: Project data.
            doc: Document data.

        Returns:
            EntityMarkdown instance representing the document.
        """
        # Extract timestamps
        created_at = doc.get("created_at") or project["created_at"]
        modified_at = project["updated_at"]

        # Generate clean names for organization
        project_dir = clean_filename(project["name"])
        doc_file = clean_filename(doc["filename"])

        # Create entity
        entity = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "type": "project_doc",
                    "title": doc["filename"],
                    "created": created_at,
                    "modified": modified_at,
                    "permalink": f"{project_dir}/docs/{doc_file}",
                    "project_name": project["name"],
                    "project_uuid": project["uuid"],
                    "doc_uuid": doc["uuid"],
                }
            ),
            content=doc["content"],
        )

        return entity

    def _format_prompt_markdown(self, project: Dict[str, Any]) -> Optional[EntityMarkdown]:
        """Format project prompt template as a Basic Memory entity.

        Args:
            project: Project data.

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

        # Create entity
        entity = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "type": "prompt_template",
                    "title": f"Prompt Template: {project['name']}",
                    "created": created_at,
                    "modified": modified_at,
                    "permalink": f"{project_dir}/prompt-template",
                    "project_name": project["name"],
                    "project_uuid": project["uuid"],
                }
            ),
            content=f"# Prompt Template: {project['name']}\n\n{project['prompt_template']}",
        )

        return entity
