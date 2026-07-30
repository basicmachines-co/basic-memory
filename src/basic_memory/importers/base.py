"""Base import service for Basic Memory."""

import logging
from abc import abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, TypeVar

from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.markdown.schemas import EntityMarkdown
from basic_memory.read_cache import ReadCacheInvalidator, invalidate_cache
from basic_memory.schemas.importer import ImportResult
from basic_memory.utils import build_canonical_permalink, generate_permalink

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.services.file_service import FileService

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=ImportResult)


@dataclass(frozen=True, slots=True)
class ImportReadCache:
    """Bind an importer's file writes to one project cache generation."""

    backend: ReadCacheInvalidator
    project_id: str


class Importer[T: ImportResult]:
    """Base class for all import services.

    All file operations are delegated to FileService, which can be overridden
    in cloud environments to use S3 or other storage backends.
    """

    def __init__(
        self,
        base_path: Path,
        markdown_processor: MarkdownProcessor,
        file_service: "FileService",
        project_name: Optional[str] = None,
        *,
        read_cache: ImportReadCache | None = None,
    ):
        """Initialize the import service.

        Args:
            base_path: Base path for the project.
            markdown_processor: MarkdownProcessor instance for markdown serialization.
            file_service: FileService instance for all file operations.
        """
        self.base_path = base_path.resolve()  # Get absolute path
        self.markdown_processor = markdown_processor
        self.file_service = file_service
        self.project_name = project_name
        self.project_permalink = generate_permalink(project_name) if project_name else None
        self.read_cache = read_cache

    @abstractmethod
    async def import_data(self, source_data, destination_folder: str, **kwargs: Any) -> T:
        """Import data from source file to destination folder.

        Args:
            source_path: Path to the source file.
            destination_folder: Destination folder within the project.
            **kwargs: Additional keyword arguments for specific import types.

        Returns:
            ImportResult containing statistics and status of the import.
        """
        pass  # pragma: no cover

    async def write_entity(self, entity: EntityMarkdown, file_path: str | Path) -> str:
        """Write entity to file using FileService.

        This method serializes the entity to markdown and writes it using
        FileService, which handles directory creation and storage backend
        abstraction (local filesystem vs cloud storage).

        Args:
            entity: EntityMarkdown instance to write.
            file_path: Relative path to write the entity to. FileService handles base_path.

        Returns:
            Checksum of written file.
        """
        content = self.markdown_processor.to_markdown_string(entity)

        # Trigger: one imported file can become visible before the remaining batch completes.
        # Why: cached file-first resources must not retain the overwritten bytes until the
        #      import's final failure-safe invalidation.
        # Outcome: advance the project generation after every attempted file write.
        invalidation_scope = (
            invalidate_cache(self.read_cache.backend, self.read_cache.project_id)
            if self.read_cache is not None
            else nullcontext()
        )
        async with invalidation_scope:
            # FileService.write_file handles directory creation and returns checksum
            return await self.file_service.write_file(file_path, content)

    def canonical_permalink(self, path: str) -> str:
        """Build a canonical permalink for imported content."""
        include_project = True
        # Trigger: importer has app config with permalink prefixing flag
        # Why: imported notes should align with canonical permalink format
        # Outcome: include project prefix when enabled
        if self.file_service.app_config is not None:
            include_project = self.file_service.app_config.permalinks_include_project

        return build_canonical_permalink(
            self.project_permalink,
            path,
            include_project=include_project,
        )

    def build_import_paths(self, path: str) -> tuple[str, str]:
        """Return (permalink, file_path) for an imported entity."""
        permalink = self.canonical_permalink(path)
        return permalink, f"{path}.md"

    async def ensure_folder_exists(self, folder: str) -> None:
        """Ensure folder exists using FileService.

        For cloud storage (S3), this is essentially a no-op since S3 doesn't
        have actual folders - they're just key prefixes.

        Args:
            folder: Relative folder path within the project. FileService handles base_path.
        """
        await self.file_service.ensure_directory(folder)

    @abstractmethod
    def handle_error(
        self, message: str, error: Optional[Exception] = None
    ) -> T:  # pragma: no cover
        """Handle errors during import.

        Args:
            message: Error message.
            error: Optional exception that caused the error.

        Returns:
            ImportResult with error information.
        """
        pass
