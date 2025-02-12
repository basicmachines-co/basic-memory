"""Service for file operations with checksum tracking."""

from pathlib import Path
from typing import Tuple, Union

from loguru import logger

from basic_memory import file_utils
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
            content = path.read_text()
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
