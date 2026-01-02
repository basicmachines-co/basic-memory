"""Tests for the base importer class."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from basic_memory.importers.base import Importer
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.markdown.schemas import EntityMarkdown
from basic_memory.schemas.importer import ImportResult
from basic_memory.services.file_service import FileService


# Create a concrete implementation of the abstract class for testing
class ConcreteTestImporter(Importer[ImportResult]):
    """Test implementation of Importer base class."""

    async def import_data(self, source_data, destination_folder: str, **kwargs):
        """Implement the abstract method for testing."""
        try:
            # Test implementation that returns success
            await self.ensure_folder_exists(destination_folder)
            return ImportResult(
                import_count={"files": 1},
                success=True,
                error_message=None,
            )
        except Exception as e:
            return self.handle_error("Test import failed", e)

    def handle_error(self, message: str, error=None) -> ImportResult:
        """Implement the abstract handle_error method."""
        import logging

        logger = logging.getLogger(__name__)

        error_message = f"{message}"
        if error:
            error_message += f": {str(error)}"

        logger.error(error_message)
        return ImportResult(
            import_count={},
            success=False,
            error_message=error_message,
        )


@pytest.fixture
def mock_markdown_processor():
    """Mock MarkdownProcessor for testing."""
    processor = MagicMock(spec=MarkdownProcessor)
    processor.to_markdown_string = MagicMock(return_value="# Test\n\nContent")
    return processor


@pytest.fixture
def mock_file_service():
    """Mock FileService for testing."""
    service = AsyncMock(spec=FileService)
    service.write_file = AsyncMock(return_value="abc123checksum")
    service.ensure_directory = AsyncMock()
    return service


@pytest.fixture
def test_importer(tmp_path, mock_markdown_processor, mock_file_service):
    """Create a ConcreteTestImporter instance for testing."""
    return ConcreteTestImporter(tmp_path, mock_markdown_processor, mock_file_service)


@pytest.mark.asyncio
async def test_import_data_success(test_importer, mock_file_service):
    """Test successful import_data implementation."""
    result = await test_importer.import_data({}, "test_folder")
    assert result.success
    assert result.import_count == {"files": 1}
    assert result.error_message is None

    # Verify file_service.ensure_directory was called with relative path
    mock_file_service.ensure_directory.assert_called_once_with("test_folder")


@pytest.mark.asyncio
async def test_write_entity(test_importer, mock_markdown_processor, mock_file_service, tmp_path):
    """Test write_entity method."""
    # Create test entity
    entity = EntityMarkdown(
        title="Test Entity",
        content="Test content",
        frontmatter={},
        observations=[],
        relations=[],
    )

    # Call write_entity
    file_path = tmp_path / "test_entity.md"
    checksum = await test_importer.write_entity(entity, file_path)

    # Verify markdown_processor.to_markdown_string was called
    mock_markdown_processor.to_markdown_string.assert_called_once_with(entity)
    # Verify file_service.write_file was called with serialized content
    mock_file_service.write_file.assert_called_once_with(file_path, "# Test\n\nContent")
    # Verify checksum is returned
    assert checksum == "abc123checksum"


@pytest.mark.asyncio
async def test_ensure_folder_exists(test_importer, mock_file_service):
    """Test ensure_folder_exists method."""
    # Test with simple folder - now passes relative path to FileService
    await test_importer.ensure_folder_exists("test_folder")
    mock_file_service.ensure_directory.assert_called_with("test_folder")

    # Test with nested folder - FileService handles base_path resolution
    await test_importer.ensure_folder_exists("nested/folder/path")
    mock_file_service.ensure_directory.assert_called_with("nested/folder/path")


@pytest.mark.asyncio
async def test_handle_error(test_importer):
    """Test handle_error method."""
    # Test with message only
    result = test_importer.handle_error("Test error message")
    assert not result.success
    assert result.error_message == "Test error message"
    assert result.import_count == {}

    # Test with message and exception
    test_exception = ValueError("Test exception")
    result = test_importer.handle_error("Error occurred", test_exception)
    assert not result.success
    assert "Error occurred" in result.error_message
    assert "Test exception" in result.error_message
    assert result.import_count == {}
