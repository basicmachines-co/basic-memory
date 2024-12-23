"""Tests for file operations service."""

from pathlib import Path
from unittest.mock import patch

import pytest

from basic_memory.services.exceptions import FileOperationError
from basic_memory.services.file_service import FileService


@pytest.mark.asyncio
async def test_exists(tmp_path: Path, file_service: FileService):
    """Test file existence checking."""
    # Test path
    test_path = tmp_path / "test.md"
    
    # Should not exist initially
    assert not await file_service.exists(test_path)
    
    # Create file
    test_path.write_text("test content")
    assert await file_service.exists(test_path)
    
    # Delete file
    test_path.unlink()
    assert not await file_service.exists(test_path)


@pytest.mark.asyncio
async def test_exists_error_handling(tmp_path: Path, file_service: FileService):
    """Test error handling in exists() method."""
    test_path = tmp_path / "test.md"
    
    # Mock Path.exists to raise an error
    with patch.object(Path, 'exists') as mock_exists:
        mock_exists.side_effect = PermissionError("Access denied")
        
        with pytest.raises(FileOperationError) as exc_info:
            await file_service.exists(test_path)
        
        assert "Failed to check file existence" in str(exc_info.value)


@pytest.mark.asyncio
async def test_write_read_file(tmp_path: Path, file_service: FileService):
    """Test basic write/read operations with checksums."""
    test_path = tmp_path / "test.md"
    test_content = "test content\nwith multiple lines"

    # Write file and get checksum
    checksum = await file_service.write_file(test_path, test_content)
    assert test_path.exists()

    # Read back and verify content/checksum
    content, read_checksum = await file_service.read_file(test_path)
    assert content == test_content
    assert read_checksum == checksum


@pytest.mark.asyncio
async def test_write_creates_directories(tmp_path: Path, file_service: FileService):
    """Test directory creation on write."""
    test_path = tmp_path / "subdir" / "nested" / "test.md"
    test_content = "test content"

    # Write should create directories
    await file_service.write_file(test_path, test_content)
    assert test_path.exists()
    assert test_path.parent.is_dir()


@pytest.mark.asyncio
async def test_write_atomic(tmp_path: Path, file_service: FileService):
    """Test atomic write with no partial files."""
    test_path = tmp_path / "test.md"
    temp_path = test_path.with_suffix(".tmp")

    # Mock write_file_atomic to raise an error
    with patch("basic_memory.utils.file_utils.write_file_atomic") as mock_write:
        mock_write.side_effect = Exception("Write failed")

        # Attempt write that will fail
        with pytest.raises(FileOperationError):
            await file_service.write_file(test_path, "test content")

        # No partial files should exist
        assert not test_path.exists()
        assert not temp_path.exists()


@pytest.mark.asyncio
async def test_delete_file(tmp_path: Path, file_service: FileService):
    """Test file deletion."""
    test_path = tmp_path / "test.md"
    test_content = "test content"

    # Create then delete
    await file_service.write_file(test_path, test_content)
    assert test_path.exists()

    await file_service.delete_file(test_path)
    assert not test_path.exists()

    # Delete non-existent file should not error
    await file_service.delete_file(test_path)


@pytest.mark.asyncio
async def test_add_frontmatter(file_service: FileService):
    """Test frontmatter addition."""
    test_content = "# Test\nSome content"
    test_metadata = {"type": "test", "tags": ["one", "two"]}

    # Add frontmatter
    content_with_fm = await file_service.add_frontmatter(
        content=test_content, id=123, metadata=test_metadata
    )

    # Verify structure
    assert content_with_fm.startswith("---\n")
    assert "id: 123" in content_with_fm
    assert "type: test" in content_with_fm
    assert "created:" in content_with_fm
    assert "modified:" in content_with_fm
    assert "tags:" in content_with_fm
    assert test_content in content_with_fm


@pytest.mark.asyncio
async def test_checksum_consistency(tmp_path: Path, file_service: FileService):
    """Test checksum remains consistent."""
    test_path = tmp_path / "test.md"
    test_content = "test content\n" * 10

    # Get checksum from write
    checksum1 = await file_service.write_file(test_path, test_content)

    # Get checksum from read
    _, checksum2 = await file_service.read_file(test_path)

    # Write again and get new checksum
    checksum3 = await file_service.write_file(test_path, test_content)

    # All should match
    assert checksum1 == checksum2 == checksum3


@pytest.mark.asyncio
async def test_error_handling_missing_file(tmp_path: Path, file_service: FileService):
    """Test error handling for missing files."""
    test_path = tmp_path / "missing.md"

    with pytest.raises(FileOperationError):
        await file_service.read_file(test_path)


@pytest.mark.asyncio
async def test_error_handling_invalid_path(tmp_path: Path, file_service: FileService):
    """Test error handling for invalid paths."""
    # Try to write to a directory instead of file
    test_path = tmp_path / "test.md"
    test_path.mkdir()  # Create a directory instead of a file

    with pytest.raises(FileOperationError):
        await file_service.write_file(test_path, "test")


@pytest.mark.asyncio
async def test_frontmatter_invalid_metadata(file_service: FileService):
    """Test error handling for invalid frontmatter metadata."""

    # Create an object that can't be serialized to YAML
    class NonSerializable:
        def __getstate__(self):
            raise ValueError("Can't serialize me!")

    bad_metadata = {"bad": NonSerializable()}

    # Attempting to add frontmatter with non-serializable content
    with patch("basic_memory.utils.file_utils.add_frontmatter") as mock_add:
        mock_add.side_effect = FileOperationError("Failed to serialize metadata")
        with pytest.raises(FileOperationError):
            await file_service.add_frontmatter(content="content", id=123, metadata=bad_metadata)


@pytest.mark.asyncio
async def test_write_unicode_content(tmp_path: Path, file_service: FileService):
    """Test handling of unicode content."""
    test_path = tmp_path / "test.md"
    test_content = """
    # Test Unicode
    - Emoji: 🚀 ⭐️ 🔥
    - Chinese: 你好世界
    - Arabic: مرحبا بالعالم
    - Russian: Привет, мир
    """

    # Write and read back
    await file_service.write_file(test_path, test_content)
    content, _ = await file_service.read_file(test_path)

    assert content == test_content