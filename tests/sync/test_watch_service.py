"""Tests for watch service."""

import json
from datetime import datetime
from pathlib import Path
import pytest
from watchfiles import Change

from basic_memory.config import ProjectConfig
from basic_memory.services.file_service import FileService
from basic_memory.sync.sync_service import SyncService
from basic_memory.sync.watch_service import WatchService, WatchServiceState, WatchEvent
from basic_memory.sync.utils import SyncReport


@pytest.fixture
def mock_sync_service(mocker):
    """Create mock sync service."""
    service = mocker.Mock(spec=SyncService)
    service.sync.return_value = SyncReport(
        new={"test.md"},
        modified={"modified.md"},
        deleted={"deleted.md"},
        moves={"old.md": "new.md"},
        checksums={"test.md": "abcd1234", "modified.md": "efgh5678", "new.md": "ijkl9012"},
    )
    return service


@pytest.fixture
def mock_file_service(mocker):
    """Create mock file service."""
    return mocker.Mock(spec=FileService)


@pytest.fixture
def watch_service(mock_sync_service, mock_file_service, test_config):
    """Create watch service instance."""
    return WatchService(mock_sync_service, mock_file_service, test_config)


def test_watch_service_init(watch_service, test_config):
    """Test watch service initialization."""
    assert watch_service.status_path.parent.exists()


def test_filter_changes(watch_service):
    """Test file change filtering."""
    assert watch_service.filter_changes(Change.added, "test.md")
    assert watch_service.filter_changes(Change.modified, "dir/test.md")
    assert not watch_service.filter_changes(Change.added, "test.txt")
    assert not watch_service.filter_changes(Change.added, ".hidden.md")


def test_state_add_event():
    """Test adding events to state."""
    state = WatchServiceState()
    event = state.add_event(path="test.md", action="new", status="success", checksum="abcd1234")

    assert len(state.recent_events) == 1
    assert state.recent_events[0] == event
    assert event.path == "test.md"
    assert event.action == "new"
    assert event.checksum == "abcd1234"

    # Test event limit
    for i in range(110):
        state.add_event(f"test{i}.md", "new", "success")
    assert len(state.recent_events) == 100


def test_state_record_error():
    """Test error recording in state."""
    state = WatchServiceState()
    state.record_error("test error")

    assert state.error_count == 1
    assert state.last_error is not None
    assert len(state.recent_events) == 1
    assert state.recent_events[0].action == "sync"
    assert state.recent_events[0].status == "error"
    assert state.recent_events[0].error == "test error"


@pytest.mark.asyncio
async def test_write_status(watch_service):
    """Test writing status file."""
    await watch_service.write_status()

    assert watch_service.status_path.exists()
    data = json.loads(watch_service.status_path.read_text())
    assert data["running"] == False
    assert data["error_count"] == 0


def test_generate_table(watch_service):
    """Test status table generation."""
    # Add some test events
    watch_service.state.add_event("test.md", "new", "success", "abcd1234")
    watch_service.state.add_event("modified.md", "modified", "success", "efgh5678")
    watch_service.state.record_error("test error")

    table = watch_service.generate_table()
    assert table is not None


@pytest.mark.asyncio
async def test_handle_changes(watch_service, mock_sync_service):
    """Test handling file changes."""
    await watch_service.handle_changes(watch_service.config.home)

    # Check sync service was called
    mock_sync_service.sync.assert_called_once_with(watch_service.config.home)

    # Check events were recorded
    events = watch_service.state.recent_events
    assert len(events) == 4  # new, modified, moved, deleted

    # Check specific events
    actions = [e.action for e in events]
    assert "new" in actions
    assert "modified" in actions
    assert "moved" in actions
    assert "deleted" in actions
