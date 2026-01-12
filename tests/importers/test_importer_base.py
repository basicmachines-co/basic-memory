"""Tests for the base importer class."""

import pytest

from basic_memory.importers.base import Importer
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
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
def test_importer(tmp_path):
    """Create a ConcreteTestImporter instance for testing."""
    entity_parser = EntityParser(base_path=tmp_path)
    markdown_processor = MarkdownProcessor(entity_parser=entity_parser)
    file_service = FileService(base_path=tmp_path, markdown_processor=markdown_processor)
    return ConcreteTestImporter(tmp_path, markdown_processor, file_service)


@pytest.mark.asyncio
async def test_import_data_success(test_importer):
    """Test successful import_data implementation."""
    result = await test_importer.import_data({}, "test_folder")
    assert result.success
    assert result.import_count == {"files": 1}
    assert result.error_message is None

    assert (test_importer.base_path / "test_folder").exists()


@pytest.mark.asyncio
async def test_write_entity(test_importer, tmp_path):
    """Test write_entity method."""
    # Create test entity
    entity = EntityMarkdown(
        frontmatter=EntityFrontmatter(metadata={"title": "Test Entity", "type": "note"}),
        content="Test content",
        observations=[],
        relations=[],
    )

    # Call write_entity
    file_path = tmp_path / "test_entity.md"
    checksum = await test_importer.write_entity(entity, file_path)

    assert file_path.exists()
    assert len(checksum) == 64  # sha256 hex digest
    assert file_path.read_text(encoding="utf-8").strip() != ""


@pytest.mark.asyncio
async def test_ensure_folder_exists(test_importer):
    """Test ensure_folder_exists method."""
    # Test with simple folder - now passes relative path to FileService
    await test_importer.ensure_folder_exists("test_folder")
    assert (test_importer.base_path / "test_folder").exists()

    # Test with nested folder - FileService handles base_path resolution
    await test_importer.ensure_folder_exists("nested/folder/path")
    assert (test_importer.base_path / "nested/folder/path").exists()


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
