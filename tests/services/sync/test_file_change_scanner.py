"""Test file sync service."""
import pytest
from pathlib import Path
from typing import AsyncGenerator

from basic_memory.repository import DocumentRepository, EntityRepository
from basic_memory.services import FileChangeScanner
from basic_memory.services.sync.utils import FileState
from basic_memory.utils.file_utils import compute_checksum
from basic_memory.models import Document, Entity


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Create temp directory for test files."""
    return tmp_path


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_scan_empty_directory(
    file_change_scanner: FileChangeScanner,
    temp_dir: Path
):
    """Test scanning empty directory."""
    result = await file_change_scanner.scan_directory(temp_dir)
    assert len(result.files) == 0
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_scan_with_mixed_files(
    file_change_scanner: FileChangeScanner,
    temp_dir: Path
):
    """Test scanning directory with markdown and non-markdown files."""
    # Create test files
    await create_test_file(temp_dir / "doc.md", "markdown")
    await create_test_file(temp_dir / "text.txt", "not markdown")
    await create_test_file(temp_dir / "notes/deep.md", "nested markdown")

    result = await file_change_scanner.scan_directory(temp_dir)
    assert len(result.files) == 2
    assert "doc.md" in result.files
    assert "notes/deep.md" in result.files
    assert len(result.errors) == 0

    # Verify FileState objects
    assert isinstance(result.files["doc.md"], FileState)
    assert result.files["doc.md"].path == "doc.md"
    assert result.files["doc.md"].checksum is not None


@pytest.mark.asyncio
async def test_scan_with_unreadable_file(
    file_change_scanner: FileChangeScanner,
    temp_dir: Path
):
    """Test scanning directory with an unreadable file."""
    # Create a file we'll make unreadable
    bad_file = temp_dir / "bad.md"
    await create_test_file(bad_file)
    bad_file.chmod(0o000)  # Remove all permissions

    result = await file_change_scanner.scan_directory(temp_dir)
    assert len(result.files) == 0
    assert len(result.errors) == 1
    assert "bad.md" in result.errors


@pytest.mark.asyncio
async def test_detect_new_files(
    file_change_scanner: FileChangeScanner,
    temp_dir: Path,
    document_repository: DocumentRepository
):
    """Test detection of new files."""
    # Create new file
    await create_test_file(temp_dir / "new.md")
    
    # Empty DB state
    db_records = await file_change_scanner.get_db_state([])
    
    changes = await file_change_scanner.find_changes(
        directory=temp_dir,
        db_records=db_records
    )
    
    assert len(changes.new) == 1
    assert "new.md" in changes.new


@pytest.mark.asyncio
async def test_detect_modified_file(
    file_change_scanner: FileChangeScanner,
    temp_dir: Path
):
    """Test detection of modified files."""
    path = "test.md"
    content = "original"
    await create_test_file(temp_dir / path, content)
    
    # Create DB state with original checksum
    original_checksum = await compute_checksum(content)
    db_records = {
        path: FileState(path=path, checksum=original_checksum)
    }

    # Modify file
    await create_test_file(temp_dir / path, "modified")

    changes = await file_change_scanner.find_changes(
        directory=temp_dir,
        db_records=db_records
    )

    assert len(changes.modified) == 1
    assert path in changes.modified


@pytest.mark.asyncio
async def test_detect_deleted_files(
    file_change_scanner: FileChangeScanner,
    temp_dir: Path
):
    """Test detection of deleted files."""
    path = "deleted.md"
    
    # Create DB state with file that doesn't exist
    db_records = {
        path: FileState(path=path, checksum="any-checksum")
    }

    changes = await file_change_scanner.find_changes(
        directory=temp_dir,
        db_records=db_records
    )

    assert len(changes.deleted) == 1
    assert path in changes.deleted


@pytest.mark.asyncio
async def test_get_db_state_documents(
    file_change_scanner: FileChangeScanner
):
    """Test converting document records to file states."""
    doc = Document(
        path_id="test.md",
        file_path="test.md",
        checksum="test-checksum"
    )
    
    db_records = await file_change_scanner.get_db_state([doc])
    
    assert len(db_records) == 1
    assert "test.md" in db_records
    assert db_records["test.md"].checksum == "test-checksum"


@pytest.mark.asyncio
async def test_get_db_state_entities(
    file_change_scanner: FileChangeScanner
):
    """Test converting entity records to file states."""
    entity = Entity(
        path_id="concept/test",
        file_path="concept/test.md",
        checksum="test-checksum"
    )
    
    db_records = await file_change_scanner.get_db_state([entity])
    
    assert len(db_records) == 1
    assert "concept/test" in db_records
    assert db_records["concept/test"].checksum == "test-checksum"


@pytest.mark.asyncio
async def test_get_db_state_handles_missing_file_path(
    file_change_scanner: FileChangeScanner
):
    """Test that get_db_state uses path_id when file_path is None."""
    doc = Document(
        path_id="test.md",
        file_path=None,
        checksum="test-checksum"
    )
    
    db_records = await file_change_scanner.get_db_state([doc])
    
    assert len(db_records) == 1
    assert "test.md" in db_records


@pytest.mark.asyncio
async def test_get_db_state_skips_missing_checksum(
    file_change_scanner: FileChangeScanner
):
    """Test that get_db_state skips records with missing checksums."""
    doc = Document(
        path_id="test.md",
        file_path="test.md",
        checksum=None
    )
    
    db_records = await file_change_scanner.get_db_state([doc])
    
    assert len(db_records) == 0


@pytest.mark.asyncio
async def test_empty_directory(
    file_change_scanner: FileChangeScanner,
    temp_dir: Path
):
    """Test handling empty/nonexistent directory."""
    nonexistent = temp_dir / "nonexistent"
    
    changes = await file_change_scanner.find_changes(
        directory=nonexistent,
        db_records={}
    )
    
    assert changes.total_changes == 0
    assert not changes.new
    assert not changes.modified
    assert not changes.deleted