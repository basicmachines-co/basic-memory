"""Tests for the base importer class."""

import pytest
from unittest.mock import AsyncMock

from basic_memory.importers.base import Importer
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.markdown.schemas import EntityMarkdown
from basic_memory.schemas.importer import ImportResult


# Create a concrete implementation of the abstract class for testing
class TestImporter(Importer[ImportResult]):
    """Test implementation of Importer base class."""

    async def import_data(self, source_data, destination_folder: str, **kwargs):
        """Implement the abstract method for testing."""
        try:
            # Test implementation that returns success
            self.ensure_folder_exists(destination_folder)
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
    processor = AsyncMock(spec=MarkdownProcessor)
    processor.write_file = AsyncMock()
    return processor


@pytest.fixture
def test_importer(tmp_path, mock_markdown_processor):
    """Create a TestImporter instance for testing."""
    return TestImporter(tmp_path, mock_markdown_processor)


@pytest.mark.asyncio
async def test_import_data_success(test_importer, tmp_path):
    """Test successful import_data implementation."""
    result = await test_importer.import_data({}, "test_folder")
    assert result.success
    assert result.import_count == {"files": 1}
    assert result.error_message is None

    # Verify folder was created
    folder_path = tmp_path / "test_folder"
    assert folder_path.exists()
    assert folder_path.is_dir()


@pytest.mark.asyncio
async def test_write_entity(test_importer, mock_markdown_processor, tmp_path):
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
    await test_importer.write_entity(entity, file_path)

    # Verify markdown processor was called with correct arguments
    mock_markdown_processor.write_file.assert_called_once_with(file_path, entity)


def test_ensure_folder_exists(test_importer, tmp_path):
    """Test ensure_folder_exists method."""
    # Test with simple folder
    folder_path = test_importer.ensure_folder_exists("test_folder")
    assert folder_path.exists()
    assert folder_path.is_dir()
    assert folder_path == tmp_path / "test_folder"

    # Test with nested folder
    nested_path = test_importer.ensure_folder_exists("nested/folder/path")
    assert nested_path.exists()
    assert nested_path.is_dir()
    assert nested_path == tmp_path / "nested" / "folder" / "path"

    # Test with existing folder (should not raise error)
    existing_path = test_importer.ensure_folder_exists("test_folder")
    assert existing_path.exists()
    assert existing_path.is_dir()


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
