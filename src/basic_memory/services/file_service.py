"""Service for file operations with checksum tracking."""

import mimetypes
from os import stat_result
from pathlib import Path
from typing import Any, Dict, Tuple, Union

from basic_memory import file_utils
from basic_memory.file_utils import FileError
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Entity as EntityModel
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.exceptions import FileOperationError
from basic_memory.utils import FilePath
from loguru import logger


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
        logger.debug(f"Reading entity content, entity_id={entity.id}, permalink={entity.permalink}")

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

    async def exists(self, path: FilePath) -> bool:
        """Check if file exists at the provided path.

        If path is relative, it is assumed to be relative to base_path.

        Args:
            path: Path to check (Path or string)

        Returns:
            True if file exists, False otherwise

        Raises:
            FileOperationError: If check fails
        """
        try:
            # Convert string to Path if needed
            path_obj = self.base_path / path if isinstance(path, str) else path
            logger.debug(f"Checking file existence: path={path_obj}")
            if path_obj.is_absolute():
                return path_obj.exists()
            else:
                return (self.base_path / path_obj).exists()
        except Exception as e:
            logger.error("Failed to check file existence", path=str(path), error=str(e))
            raise FileOperationError(f"Failed to check file existence: {e}")

    async def write_file(self, path: FilePath, content: str) -> str:
        """Write content to file and return checksum.

        Handles both absolute and relative paths. Relative paths are resolved
        against base_path.

        Args:
            path: Where to write (Path or string)
            content: Content to write

        Returns:
            Checksum of written content

        Raises:
            FileOperationError: If write fails
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        try:
            # Ensure parent directory exists
            await file_utils.ensure_directory(full_path.parent)

            # Write content atomically
            logger.info(
                "Writing file: "
                f"path={path_obj}, "
                f"content_length={len(content)}, "
                f"is_markdown={full_path.suffix.lower() == '.md'}"
            )

            await file_utils.write_file_atomic(full_path, content)

            # Compute and return checksum
            checksum = await file_utils.compute_checksum(content)
            logger.debug(f"File write completed path={full_path}, {checksum=}")
            return checksum

        except Exception as e:
            logger.exception("File write error", path=str(full_path), error=str(e))
            raise FileOperationError(f"Failed to write file: {e}")

    # TODO remove read_file
    async def read_file(self, path: FilePath) -> Tuple[str, str]:
        """Read file and compute checksum.

        Handles both absolute and relative paths. Relative paths are resolved
        against base_path.

        Args:
            path: Path to read (Path or string)

        Returns:
            Tuple of (content, checksum)

        Raises:
            FileOperationError: If read fails
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        try:
            logger.debug("Reading file", operation="read_file", path=str(full_path))
            content = full_path.read_text(encoding="utf-8")
            checksum = await file_utils.compute_checksum(content)

            logger.debug(
                "File read completed",
                path=str(full_path),
                checksum=checksum,
                content_length=len(content),
            )
            return content, checksum

        except Exception as e:
            logger.exception("File read error", path=str(full_path), error=str(e))
            raise FileOperationError(f"Failed to read file: {e}")

    async def delete_file(self, path: FilePath) -> None:
        """Delete file if it exists.

        Handles both absolute and relative paths. Relative paths are resolved
        against base_path.

        Args:
            path: Path to delete (Path or string)
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj
        full_path.unlink(missing_ok=True)

    async def update_frontmatter(self, path: FilePath, updates: Dict[str, Any]) -> str:
        """
        Update frontmatter fields in a file while preserving all content.

        Args:
            path: Path to the file (Path or string)
            updates: Dictionary of frontmatter fields to update

        Returns:
            Checksum of updated file
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj
        return await file_utils.update_frontmatter(full_path, updates)

    async def compute_checksum(self, path: FilePath) -> str:
        """Compute checksum for a file.

        Args:
            path: Path to the file (Path or string)

        Returns:
            Checksum of the file content

        Raises:
            FileError: If checksum computation fails
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        try:
            if self.is_markdown(path):
                # read str
                content = full_path.read_text(encoding="utf-8")
            else:
                # read bytes
                content = full_path.read_bytes()
            return await file_utils.compute_checksum(content)

        except Exception as e:  # pragma: no cover
            logger.error("Failed to compute checksum", path=str(full_path), error=str(e))
            raise FileError(f"Failed to compute checksum for {path}: {e}")

    def file_stats(self, path: FilePath) -> stat_result:
        """Return file stats for a given path.

        Args:
            path: Path to the file (Path or string)

        Returns:
            File statistics
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj
        # get file timestamps
        return full_path.stat()

    def content_type(self, path: FilePath) -> str:
        """Return content_type for a given path.

        Args:
            path: Path to the file (Path or string)

        Returns:
            MIME type of the file
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj
        # get file timestamps
        mime_type, _ = mimetypes.guess_type(full_path.name)

        # .canvas files are json
        if full_path.suffix == ".canvas":
            mime_type = "application/json"

        content_type = mime_type or "text/plain"
        return content_type

    def is_markdown(self, path: FilePath) -> bool:
        """Check if a file is a markdown file.

        Args:
            path: Path to the file (Path or string)

        Returns:
            True if the file is a markdown file, False otherwise
        """
        return self.content_type(path) == "text/markdown"
