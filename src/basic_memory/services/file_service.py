"""Service for file operations with checksum tracking."""

import asyncio
import hashlib
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple, Union

import aiofiles

import yaml

from basic_memory import file_utils
from basic_memory.file_utils import FileError, FileMetadata, ParseError
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Entity as EntityModel
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.exceptions import FileOperationError
from basic_memory.utils import FilePath
from loguru import logger


class FileService:
    """Service for handling file operations with concurrency control.

    All paths are handled as Path objects internally. Strings are converted to
    Path objects when passed in. Relative paths are assumed to be relative to
    base_path.

    Features:
    - True async I/O with aiofiles (non-blocking)
    - Built-in concurrency limits (semaphore)
    - Consistent file writing with checksums
    - Frontmatter management
    - Atomic operations
    - Error handling
    """

    def __init__(
        self,
        base_path: Path,
        markdown_processor: MarkdownProcessor,
        max_concurrent_files: int = 10,
    ):
        self.base_path = base_path.resolve()  # Get absolute path
        self.markdown_processor = markdown_processor
        # Semaphore to limit concurrent file operations
        # Prevents OOM on large projects by processing files in batches
        self._file_semaphore = asyncio.Semaphore(max_concurrent_files)

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

    async def ensure_directory(self, path: FilePath) -> None:
        """Ensure directory exists, creating if necessary.

        Uses semaphore to control concurrency for directory creation operations.

        Args:
            path: Directory path to ensure (Path or string)

        Raises:
            FileOperationError: If directory creation fails
        """
        try:
            # Convert string to Path if needed
            path_obj = self.base_path / path if isinstance(path, str) else path
            full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

            # Use semaphore for concurrency control
            async with self._file_semaphore:
                # Run blocking mkdir in thread pool
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, lambda: full_path.mkdir(parents=True, exist_ok=True)
                )
        except Exception as e:  # pragma: no cover
            logger.error("Failed to create directory", path=str(path), error=str(e))
            raise FileOperationError(f"Failed to create directory {path}: {e}")

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
            await self.ensure_directory(full_path.parent)

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

    async def read_file_content(self, path: FilePath) -> str:
        """Read file content using true async I/O with aiofiles.

        Handles both absolute and relative paths. Relative paths are resolved
        against base_path.

        Args:
            path: Path to read (Path or string)

        Returns:
            File content as string

        Raises:
            FileOperationError: If read fails
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        try:
            logger.debug("Reading file content", operation="read_file_content", path=str(full_path))
            async with aiofiles.open(full_path, mode="r", encoding="utf-8") as f:
                content = await f.read()

            logger.debug(
                "File read completed",
                path=str(full_path),
                content_length=len(content),
            )
            return content

        except Exception as e:
            logger.exception("File read error", path=str(full_path), error=str(e))
            raise FileOperationError(f"Failed to read file: {e}")

    async def read_file_bytes(self, path: FilePath) -> bytes:
        """Read file content as bytes using true async I/O with aiofiles.

        This method reads files in binary mode, suitable for non-text files
        like images, PDFs, etc. For cloud compatibility with S3FileService.

        Args:
            path: Path to read (Path or string)

        Returns:
            File content as bytes

        Raises:
            FileOperationError: If read fails
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        try:
            logger.debug("Reading file bytes", operation="read_file_bytes", path=str(full_path))
            async with aiofiles.open(full_path, mode="rb") as f:
                content = await f.read()

            logger.debug(
                "File read completed",
                path=str(full_path),
                content_length=len(content),
            )
            return content

        except Exception as e:
            logger.exception("File read error", path=str(full_path), error=str(e))
            raise FileOperationError(f"Failed to read file: {e}")

    async def read_file(self, path: FilePath) -> Tuple[str, str]:
        """Read file and compute checksum using true async I/O.

        Uses aiofiles for non-blocking file reads.

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

            # Use aiofiles for non-blocking read
            async with aiofiles.open(full_path, mode="r", encoding="utf-8") as f:
                content = await f.read()

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

    async def move_file(self, source: FilePath, destination: FilePath) -> None:
        """Move/rename a file from source to destination.

        This method abstracts the underlying storage (filesystem vs cloud).
        Default implementation uses atomic filesystem rename, but cloud-backed
        implementations (e.g., S3) can override to copy+delete.

        Args:
            source: Source path (relative to base_path or absolute)
            destination: Destination path (relative to base_path or absolute)

        Raises:
            FileOperationError: If the move fails
        """
        # Convert strings to Paths and resolve relative paths against base_path
        src_obj = self.base_path / source if isinstance(source, str) else source
        dst_obj = self.base_path / destination if isinstance(destination, str) else destination
        src_full = src_obj if src_obj.is_absolute() else self.base_path / src_obj
        dst_full = dst_obj if dst_obj.is_absolute() else self.base_path / dst_obj

        try:
            # Ensure destination directory exists
            await self.ensure_directory(dst_full.parent)

            # Use semaphore for concurrency control and run blocking rename in executor
            async with self._file_semaphore:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: src_full.rename(dst_full))
        except Exception as e:
            logger.exception(
                "File move error",
                source=str(src_full),
                destination=str(dst_full),
                error=str(e),
            )
            raise FileOperationError(f"Failed to move file {source} -> {destination}: {e}")

    async def update_frontmatter(self, path: FilePath, updates: Dict[str, Any]) -> str:
        """Update frontmatter fields in a file while preserving all content.

        Only modifies the frontmatter section, leaving all content untouched.
        Creates frontmatter section if none exists.
        Returns checksum of updated file.

        Uses aiofiles for true async I/O (non-blocking).

        Args:
            path: Path to markdown file (Path or string)
            updates: Dict of frontmatter fields to update

        Returns:
            Checksum of updated file

        Raises:
            FileOperationError: If file operations fail
            ParseError: If frontmatter parsing fails
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        try:
            # Read current content using aiofiles
            async with aiofiles.open(full_path, mode="r", encoding="utf-8") as f:
                content = await f.read()

            # Parse current frontmatter with proper error handling for malformed YAML
            current_fm = {}
            if file_utils.has_frontmatter(content):
                try:
                    current_fm = file_utils.parse_frontmatter(content)
                    content = file_utils.remove_frontmatter(content)
                except (ParseError, yaml.YAMLError) as e:
                    # Log warning and treat as plain markdown without frontmatter
                    logger.warning(
                        f"Failed to parse YAML frontmatter in {full_path}: {e}. "
                        "Treating file as plain markdown without frontmatter."
                    )
                    # Keep full content, treat as having no frontmatter
                    current_fm = {}

            # Update frontmatter
            new_fm = {**current_fm, **updates}

            # Write new file with updated frontmatter
            yaml_fm = yaml.dump(new_fm, sort_keys=False, allow_unicode=True)
            final_content = f"---\n{yaml_fm}---\n\n{content.strip()}"

            logger.debug(
                "Updating frontmatter", path=str(full_path), update_keys=list(updates.keys())
            )

            await file_utils.write_file_atomic(full_path, final_content)
            return await file_utils.compute_checksum(final_content)

        except Exception as e:
            # Only log real errors (not YAML parsing, which is handled above)
            if not isinstance(e, (ParseError, yaml.YAMLError)):
                logger.error(
                    "Failed to update frontmatter",
                    path=str(full_path),
                    error=str(e),
                )
            raise FileOperationError(f"Failed to update frontmatter: {e}")

    async def compute_checksum(self, path: FilePath) -> str:
        """Compute checksum for a file using true async I/O.

        Uses aiofiles for non-blocking I/O with 64KB chunked reading.
        Semaphore limits concurrent file operations to prevent OOM.
        Memory usage is constant regardless of file size.

        Args:
            path: Path to the file (Path or string)

        Returns:
            SHA256 checksum hex string

        Raises:
            FileError: If checksum computation fails
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        # Semaphore controls concurrency - max N files processed at once
        async with self._file_semaphore:
            try:
                hasher = hashlib.sha256()
                chunk_size = 65536  # 64KB chunks

                # async I/O with aiofiles
                async with aiofiles.open(full_path, mode="rb") as f:
                    while chunk := await f.read(chunk_size):
                        hasher.update(chunk)

                return hasher.hexdigest()

            except Exception as e:  # pragma: no cover
                logger.error("Failed to compute checksum", path=str(full_path), error=str(e))
                raise FileError(f"Failed to compute checksum for {path}: {e}")

    async def get_file_metadata(self, path: FilePath) -> FileMetadata:
        """Return file metadata for a given path.

        This method is async to support cloud implementations (S3FileService)
        where file metadata requires async operations (head_object).

        Args:
            path: Path to the file (Path or string)

        Returns:
            FileMetadata with size, created_at, and modified_at
        """
        # Convert string to Path if needed
        path_obj = self.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.base_path / path_obj

        # Run blocking stat() in thread pool to maintain async compatibility
        loop = asyncio.get_event_loop()
        stat_result = await loop.run_in_executor(None, full_path.stat)

        return FileMetadata(
            size=stat_result.st_size,
            created_at=datetime.fromtimestamp(stat_result.st_ctime).astimezone(),
            modified_at=datetime.fromtimestamp(stat_result.st_mtime).astimezone(),
        )

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
