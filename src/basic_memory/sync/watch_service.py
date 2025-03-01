"""Watch service for Basic Memory."""

import dataclasses
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from loguru import logger
from pydantic import BaseModel
from rich.console import Console
from watchfiles import awatch
from watchfiles.main import FileChange, Change

from basic_memory.config import ProjectConfig
from basic_memory.services.file_service import FileService
from basic_memory.sync.sync_service import SyncService


class WatchEvent(BaseModel):
    timestamp: datetime
    path: str
    action: str  # new, delete, etc
    status: str  # success, error
    checksum: Optional[str]
    error: Optional[str] = None


class WatchServiceState(BaseModel):
    # Service status
    running: bool = False
    start_time: datetime = dataclasses.field(default_factory=datetime.now)
    pid: int = dataclasses.field(default_factory=os.getpid)

    # Stats
    error_count: int = 0
    last_error: Optional[datetime] = None
    last_scan: Optional[datetime] = None

    # File counts
    synced_files: int = 0

    # Recent activity
    recent_events: List[WatchEvent] = dataclasses.field(default_factory=list)

    def add_event(
        self,
        path: str,
        action: str,
        status: str,
        checksum: Optional[str] = None,
        error: Optional[str] = None,
    ) -> WatchEvent:
        event = WatchEvent(
            timestamp=datetime.now(),
            path=path,
            action=action,
            status=status,
            checksum=checksum,
            error=error,
        )
        self.recent_events.insert(0, event)
        self.recent_events = self.recent_events[:100]  # Keep last 100
        return event

    def record_error(self, error: str):
        self.error_count += 1
        self.add_event(path="", action="sync", status="error", error=error)
        self.last_error = datetime.now()


class WatchService:
    def __init__(self, sync_service: SyncService, file_service: FileService, config: ProjectConfig):
        self.sync_service = sync_service
        self.file_service = file_service
        self.config = config
        self.state = WatchServiceState()
        self.status_path = config.home / ".basic-memory" / "watch-status.json"
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.console = Console()

    async def run(self):  # pragma: no cover
        """Watch for file changes and sync them"""
        logger.info("Watching for sync changes")
        self.state.running = True
        self.state.start_time = datetime.now()
        await self.write_status()
        try:
            async for changes in awatch(
                self.config.home,
                debounce=self.config.sync_delay,
                watch_filter=self.filter_changes,
                recursive=True,
            ):
                await self.handle_changes(self.config.home, changes)

        except Exception as e:
            self.state.record_error(str(e))
            await self.write_status()
            raise
        finally:
            self.state.running = False
            await self.write_status()

    def filter_changes(self, change: Change, path: str) -> bool:
        """Filter to only watch non-hidden files and directories.

        Returns:
            True if the file should be watched, False if it should be ignored
        """
        # Skip if path is invalid
        try:
            relative_path = Path(path).relative_to(self.config.home)
        except ValueError:
            return False

        # Skip hidden directories and files
        path_parts = relative_path.parts
        for part in path_parts:
            if part.startswith("."):
                return False

        return True

    async def write_status(self):
        """Write current state to status file"""
        self.status_path.write_text(WatchServiceState.model_dump_json(self.state, indent=2))

    async def handle_changes(self, directory: Path, changes: Set[FileChange]):
        """Process a batch of file changes"""
        logger.debug(f"handling {len(changes)} changes in directory: {directory} ...")

        # Group changes by type
        adds = []
        deletes = []
        modifies = []

        for change, path in changes:
            # convert to relative path
            relative_path = str(Path(path).relative_to(directory))
            if change == Change.added:
                adds.append(relative_path)
            elif change == Change.deleted:
                deletes.append(relative_path)
            elif change == Change.modified:
                modifies.append(relative_path)

        # Track processed files to avoid duplicates
        processed = set()

        # First handle potential moves
        for added_path in adds:
            if added_path in processed:
                continue  # pragma: no cover

            for deleted_path in deletes:
                if deleted_path in processed:
                    continue  # pragma: no cover

                if added_path != deleted_path:
                    # Compare checksums to detect moves
                    try:
                        added_checksum = await self.file_service.compute_checksum(added_path)
                        deleted_entity = await self.sync_service.entity_repository.get_by_file_path(
                            deleted_path
                        )

                        if deleted_entity and deleted_entity.checksum == added_checksum:
                            await self.sync_service.handle_move(deleted_path, added_path)
                            self.state.add_event(
                                path=f"{deleted_path} -> {added_path}",
                                action="moved",
                                status="success",
                            )
                            self.console.print(
                                f"[blue]→[/blue] Moved: {deleted_path} → {added_path}"
                            )
                            processed.add(added_path)
                            processed.add(deleted_path)
                            break
                    except Exception as e:  # pragma: no cover
                        logger.warning(f"Error checking for move: {e}")

        # Handle remaining changes
        for path in deletes:
            if path not in processed:
                await self.sync_service.handle_delete(path)
                self.state.add_event(path=path, action="deleted", status="success")
                self.console.print(f"[red]✕[/red] Deleted: {path}")
                processed.add(path)

        for path in adds:
            if path not in processed:
                _, checksum = await self.sync_service.sync_file(path, new=True)
                if checksum:
                    self.state.add_event(
                        path=path, action="new", status="success", checksum=checksum
                    )
                    self.console.print(f"[green]✓[/green] Added: {path}")
                    processed.add(path)
                else:
                    self.console.print(f"[orange]?[/orange] Error syncing: {path}")

        for path in modifies:
            if path not in processed:
                _, checksum = await self.sync_service.sync_file(path, new=False)
                self.state.add_event(
                    path=path, action="modified", status="success", checksum=checksum
                )
                self.console.print(f"[yellow]✎[/yellow] Modified: {path}")
                processed.add(path)

        # Add a divider if we processed any files
        if processed:
            self.console.print("─" * 80, style="dim")

        self.state.last_scan = datetime.now()
        self.state.synced_files += len(processed)
        await self.write_status()
