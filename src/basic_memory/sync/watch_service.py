"""Watch service for Basic Memory."""

import dataclasses

from loguru import logger
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table
from watchfiles import awatch, Change
import os

from basic_memory.config import ProjectConfig
from basic_memory.sync.sync_service import SyncService
from basic_memory.services.file_service import FileService


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

    def generate_table(self) -> Table:
        """Generate status display table"""
        table = Table()

        # Add status row
        table.add_column("Status", style="cyan")
        table.add_column("Last Scan", style="cyan")
        table.add_column("Files", style="cyan")
        table.add_column("Errors", style="red")

        # Add main status row
        table.add_row(
            "✓ Running" if self.state.running else "✗ Stopped",
            self.state.last_scan.strftime("%H:%M:%S") if self.state.last_scan else "-",
            str(self.state.synced_files),
            f"{self.state.error_count} ({self.state.last_error.strftime('%H:%M:%S') if self.state.last_error else 'none'})",
        )

        if self.state.recent_events:
            # Add recent events
            table.add_section()
            table.add_row("Recent Events", "", "", "")

            for event in self.state.recent_events[:5]:  # Show last 5 events
                color = {
                    "new": "green",
                    "modified": "yellow",
                    "moved": "blue",
                    "deleted": "red",
                    "error": "red",
                }.get(event.action, "white")

                icon = {
                    "new": "✚",
                    "modified": "✎",
                    "moved": "→",
                    "deleted": "✖",
                    "error": "!",
                }.get(event.action, "*")

                table.add_row(
                    f"[{color}]{icon} {event.action}[/{color}]",
                    event.timestamp.strftime("%H:%M:%S"),
                    f"[{color}]{event.path}[/{color}]",
                    f"[dim]{event.checksum[:8] if event.checksum else ''}[/dim]",
                )

        return table

    async def run(self):
        """Watch for file changes and sync them"""
        self.state.running = True
        self.state.start_time = datetime.now()
        await self.write_status()

        with Live(self.generate_table(), refresh_per_second=4, console=self.console) as live:
            try:
                async for changes in awatch(
                    self.config.home,
                    watch_filter=self.filter_changes,
                    debounce=self.config.sync_delay,
                    recursive=True,
                ):
                    # Process changes
                    await self.handle_changes(self.config.home)  # pragma: no cover
                    # Update display
                    live.update(self.generate_table())  # pragma: no cover

            except Exception as e:  # pragma: no cover
                self.state.record_error(str(e))
                await self.write_status()
                raise
            finally:
                self.state.running = False
                await self.write_status()

    async def write_status(self):
        """Write current state to status file"""
        self.status_path.write_text(WatchServiceState.model_dump_json(self.state, indent=2))

    def filter_changes(self, change: Change, path: str) -> bool:
        """Filter to only watch markdown files"""
        return path.endswith(".md") and not Path(path).name.startswith(".")

    async def handle_changes(self, directory: Path):
        """Process a batch of file changes"""

        logger.debug(f"handling change in directory: {directory} ...")
        # Process changes with timeout
        report = await self.sync_service.sync(directory)
        self.state.last_scan = datetime.now()
        self.state.synced_files = report.total

        # Update stats
        for path in report.new:
            self.state.add_event(
                path=path, action="new", status="success", checksum=report.checksums[path]
            )
        for path in report.modified:
            self.state.add_event(
                path=path, action="modified", status="success", checksum=report.checksums[path]
            )
        for old_path, new_path in report.moves.items():
            self.state.add_event(
                path=f"{old_path} -> {new_path}",
                action="moved",
                status="success",
                checksum=report.checksums[new_path],
            )
        for path in report.deleted:
            self.state.add_event(path=path, action="deleted", status="success")

        await self.write_status()
