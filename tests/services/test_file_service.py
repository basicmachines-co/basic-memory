"""Tests for file operations service."""

from pathlib import Path

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
async def test_exists_error_handling(tmp_path: Path, file_service: FileService, monkeypatch):
    """Test error handling in exists() method."""
    test_path = tmp_path / "test.md"

    def boom(*args, **kwargs):
        raise PermissionError("Access denied")

    monkeypatch.setattr(Path, "exists", boom)

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
async def test_write_atomic(tmp_path: Path, file_service: FileService, monkeypatch):
    """Test atomic write with no partial files."""
    test_path = tmp_path / "test.md"
    temp_path = test_path.with_suffix(".tmp")

    from basic_memory import file_utils

    async def fake_write_file_atomic(*args, **kwargs):
        raise Exception("Write failed")

    monkeypatch.setattr(file_utils, "write_file_atomic", fake_write_file_atomic)

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


@pytest.mark.asyncio
async def test_read_file_content(tmp_path: Path, file_service: FileService):
    """Test read_file_content returns just the content without checksum."""
    test_path = tmp_path / "test.md"
    test_content = "test content\nwith multiple lines"

    # Write file
    await file_service.write_file(test_path, test_content)

    # Read content only
    content = await file_service.read_file_content(test_path)
    assert content == test_content


@pytest.mark.asyncio
async def test_read_file_content_missing_file(tmp_path: Path, file_service: FileService):
    """Test read_file_content raises error for missing files."""
    test_path = tmp_path / "missing.md"

    # FileNotFoundError is preserved so callers can treat missing files specially (e.g. sync).
    with pytest.raises(FileNotFoundError):
        await file_service.read_file_content(test_path)


@pytest.mark.asyncio
async def test_read_file_content_raises_file_operation_error_for_directory(
    tmp_path: Path, file_service: FileService
):
    """read_file_content should wrap non-FileNotFound errors in FileOperationError."""
    dir_path = tmp_path / "not-a-file"
    dir_path.mkdir()

    with pytest.raises(FileOperationError) as exc_info:
        await file_service.read_file_content(dir_path)

    assert "Failed to read file" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_file_bytes(tmp_path: Path, file_service: FileService):
    """Test read_file_bytes for binary file reading."""
    test_path = tmp_path / "test.bin"
    # Create binary content with non-UTF8 bytes
    binary_content = b"\x00\x01\x02\x03\xff\xfe\xfd"

    # Write binary file directly
    test_path.write_bytes(binary_content)

    # Read back using read_file_bytes
    content = await file_service.read_file_bytes(test_path)
    assert content == binary_content


@pytest.mark.asyncio
async def test_read_file_bytes_image(tmp_path: Path, file_service: FileService):
    """Test read_file_bytes with image-like binary content."""
    test_path = tmp_path / "test.png"
    # PNG header signature
    png_header = b"\x89PNG\r\n\x1a\n"
    fake_image_content = png_header + b"\x00" * 100

    test_path.write_bytes(fake_image_content)

    content = await file_service.read_file_bytes(test_path)
    assert content == fake_image_content
    assert content.startswith(png_header)


@pytest.mark.asyncio
async def test_read_file_bytes_missing_file(tmp_path: Path, file_service: FileService):
    """Test read_file_bytes raises error for missing files."""
    test_path = tmp_path / "missing.bin"

    with pytest.raises(FileOperationError):
        await file_service.read_file_bytes(test_path)


@pytest.mark.asyncio
async def test_read_file_bytes_text_file(tmp_path: Path, file_service: FileService):
    """Test read_file_bytes can read text files as bytes."""
    test_path = tmp_path / "test.txt"
    text_content = "Hello, World!"

    test_path.write_text(text_content)

    content = await file_service.read_file_bytes(test_path)
    assert content == text_content.encode("utf-8")
