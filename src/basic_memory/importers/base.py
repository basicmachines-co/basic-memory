"""Base import service for Basic Memory."""

import logging
from abc import abstractmethod
from pathlib import Path
from typing import Any, Optional, TypeVar

from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.markdown.schemas import EntityMarkdown
from basic_memory.schemas.importer import ImportResult

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=ImportResult)


class Importer[T: ImportResult]:
    """Base class for all import services."""

    def __init__(self, base_path: Path, markdown_processor: MarkdownProcessor):
        """Initialize the import service.

        Args:
            markdown_processor: MarkdownProcessor instance for writing markdown files.
        """
        self.base_path = base_path.resolve()  # Get absolute path
        self.markdown_processor = markdown_processor

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

    async def write_entity(self, entity: EntityMarkdown, file_path: Path) -> None:
        """Write entity to file using markdown processor.

        Args:
            entity: EntityMarkdown instance to write.
            file_path: Path to write the entity to.
        """
        await self.markdown_processor.write_file(file_path, entity)

    def ensure_folder_exists(self, folder: str) -> Path:
        """Ensure folder exists, create if it doesn't.

        Args:
            base_path: Base path of the project.
            folder: Folder name or path within the project.

        Returns:
            Path to the folder.
        """
        folder_path = self.base_path / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        return folder_path

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
