"""Service for file operations with checksum tracking."""

import mimetypes
from os import stat_result
from pathlib import Path
from typing import Tuple, Union, Dict, Any

from loguru import logger

from basic_memory import file_utils
from basic_memory.file_utils import FileError
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Entity as EntityModel
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.exceptions import FileOperationError


class FileService:
    """Service for handling file operations.

    All paths are handled as Path objects internally. Strings are converted to
    Path objects when passed in. Relative paths are assumed to be relative to
    base_path.

    Features:
    - Consistent file writing with checksums
    - Frontmatter management
    - Atomic operations
    - Error handling
    """

    def __init__(
        self,
        base_path: Path,
        markdown_processor: MarkdownProcessor,
    ):
        self.base_path = base_path.resolve()  # Get absolute path
        self.markdown_processor = markdown_processor

    def get_entity_path(self, entity: Union[EntityModel, EntitySchema]) -> Path:
        """Generate absolute filesystem path for entity.

        Args:
            entity: Entity model or schema with file_path attribute

        Returns:
            Absolute Path to the entity file
        """
        return self.base_path / entity.file_path

    async def read_entity_content(self, entity: EntityModel) -> str:
        """Get entity's content without frontmatter or structured sections.

        Used to index for search. Returns raw content without frontmatter,
        observations, or relations.

        Args:
            entity: Entity to read content for

        Returns:
            Raw content string without metadata sections
        """
        logger.debug(f"Reading entity with permalink: {entity.permalink}")

        file_path = self.get_entity_path(entity)
        markdown = await self.markdown_processor.read_file(file_path)
        return markdown.content or ""

    async def delete_entity_file(self, entity: EntityModel) -> None:
        """Delete entity file from filesystem.

        Args:
            entity: Entity model whose file should be deleted

        Raises:
            FileOperationError: If deletion fails
        """
        path = self.get_entity_path(entity)
        await self.delete_file(path)

    async def exists(self, path: Union[Path, str]) -> bool:
        """Check if file exists at the provided path.

        If path is relative, it is assumed to be relative to base_path.

        Args:
            path: Path to check (Path object or string)

        Returns:
            True if file exists, False otherwise

        Raises:
            FileOperationError: If check fails
        """
        try:
            path = Path(path)
            if path.is_absolute():
                return path.exists()
            else:
                return (self.base_path / path).exists()
        except Exception as e:
            logger.error(f"Failed to check file existence {path}: {e}")
            raise FileOperationError(f"Failed to check file existence: {e}")

    async def write_file(self, path: Union[Path, str], content: str) -> str:
        """Write content to file and return checksum.

        Handles both absolute and relative paths. Relative paths are resolved
        against base_path.

        Args:
            path: Where to write (Path object or string)
            content: Content to write

        Returns:
            Checksum of written content

        Raises:
            FileOperationError: If write fails
        """
        path = Path(path)
        full_path = path if path.is_absolute() else self.base_path / path

        try:
            # Ensure parent directory exists
            await file_utils.ensure_directory(full_path.parent)

            # Write content atomically
            await file_utils.write_file_atomic(full_path, content)

            # Compute and return checksum
            checksum = await file_utils.compute_checksum(content)
            logger.debug(f"wrote file: {full_path}, checksum: {checksum}")
            return checksum

        except Exception as e:
            logger.error(f"Failed to write file {full_path}: {e}")
            raise FileOperationError(f"Failed to write file: {e}")

    # TODO remove read_file
    async def read_file(self, path: Union[Path, str]) -> Tuple[str, str]:
        """Read file and compute checksum.

        Handles both absolute and relative paths. Relative paths are resolved
        against base_path.

        Args:
            path: Path to read (Path object or string)

        Returns:
            Tuple of (content, checksum)

        Raises:
            FileOperationError: If read fails
        """
        path = Path(path)
        full_path = path if path.is_absolute() else self.base_path / path

        try:
            content = full_path.read_text()
            checksum = await file_utils.compute_checksum(content)
            logger.debug(f"read file: {full_path}, checksum: {checksum}")
            return content, checksum

        except Exception as e:
            logger.error(f"Failed to read file {full_path}: {e}")
            raise FileOperationError(f"Failed to read file: {e}")

    async def delete_file(self, path: Union[Path, str]) -> None:
        """Delete file if it exists.

        Handles both absolute and relative paths. Relative paths are resolved
        against base_path.

        Args:
            path: Path to delete (Path object or string)
        """
        path = Path(path)
        full_path = path if path.is_absolute() else self.base_path / path
        full_path.unlink(missing_ok=True)

    async def update_frontmatter(self, path: Union[Path, str], updates: Dict[str, Any]) -> str:
        """
        Update frontmatter fields in a file while preserving all content.
        """

        path = Path(path)
        full_path = path if path.is_absolute() else self.base_path / path
        return await file_utils.update_frontmatter(full_path, updates)

    async def compute_checksum(self, path: Union[str, Path]) -> str:
        """Compute checksum for a file."""
        path = Path(path)
        full_path = path if path.is_absolute() else self.base_path / path
        try:
            if self.is_markdown(path):
                # read str
                content = full_path.read_text()
            else:
                # read bytes
                content = full_path.read_bytes()
            return await file_utils.compute_checksum(content)

        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to compute checksum for {path}: {e}")
            raise FileError(f"Failed to compute checksum for {path}: {e}")

    def file_stats(self, path: Union[Path, str]) -> stat_result:
        """
        Return file stats for a given path.
        :param path:
        :return:
        """
        path = Path(path)
        full_path = path if path.is_absolute() else self.base_path / path
        # get file timestamps
        return full_path.stat()

    def content_type(self, path: Union[Path, str]) -> str:
        """
        Return content_type for a given path.
        :param path:
        :return:
        """
        path = Path(path)
        full_path = path if path.is_absolute() else self.base_path / path
        # get file timestamps
        mime_type, _ = mimetypes.guess_type(full_path.name)
        content_type = mime_type or "text/plain"
        return content_type

    def is_markdown(self, path: Union[Path, str]) -> bool:
        """
        Return content_type for a given path.
        :param path:
        :return:
        """
        return self.content_type(path) == "text/markdown"
