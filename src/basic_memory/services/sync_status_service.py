"""Simple sync status tracking service."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class SyncStatus(Enum):
    """Status of sync operations."""

    IDLE = "idle"
    SCANNING = "scanning"
    SYNCING = "syncing"
    COMPLETED = "completed"
    FAILED = "failed"
    WATCHING = "watching"


@dataclass
class ProjectSyncStatus:
    """Sync status for a single project."""

    project_name: str
    status: SyncStatus
    message: str = ""
    files_total: int = 0
    files_processed: int = 0
    error: Optional[str] = None


class SyncStatusTracker:
    """Global tracker for all sync operations."""

    def __init__(self):
        self._project_statuses: Dict[str, ProjectSyncStatus] = {}
        self._global_status: SyncStatus = SyncStatus.IDLE

    def start_project_sync(self, project_name: str, files_total: int = 0) -> None:
        """Start tracking sync for a project."""
        self._project_statuses[project_name] = ProjectSyncStatus(
            project_name=project_name,
            status=SyncStatus.SCANNING,
            message="Scanning files",
            files_total=files_total,
            files_processed=0,
        )
        self._update_global_status()

    def update_project_progress(  # pragma: no cover
        self,
        project_name: str,
        status: SyncStatus,
        message: str = "",
        files_processed: int = 0,
        files_total: Optional[int] = None,
    ) -> None:
        """Update progress for a project."""
        if project_name not in self._project_statuses:  # pragma: no cover
            return

        project_status = self._project_statuses[project_name]
        project_status.status = status
        project_status.message = message
        project_status.files_processed = files_processed

        if files_total is not None:
            project_status.files_total = files_total

        self._update_global_status()

    def complete_project_sync(self, project_name: str) -> None:
        """Mark project sync as completed."""
        if project_name in self._project_statuses:
            self._project_statuses[project_name].status = SyncStatus.COMPLETED
            self._project_statuses[project_name].message = "Sync completed"
            self._update_global_status()

    def fail_project_sync(self, project_name: str, error: str) -> None:
        """Mark project sync as failed."""
        if project_name in self._project_statuses:
            self._project_statuses[project_name].status = SyncStatus.FAILED
            self._project_statuses[project_name].error = error
            self._update_global_status()

    def start_project_watch(self, project_name: str) -> None:
        """Mark project as watching for changes (steady state after sync)."""
        if project_name in self._project_statuses:
            self._project_statuses[project_name].status = SyncStatus.WATCHING
            self._project_statuses[project_name].message = "Watching for changes"
            self._update_global_status()
        else:
            # Create new status if project isn't tracked yet
            self._project_statuses[project_name] = ProjectSyncStatus(
                project_name=project_name,
                status=SyncStatus.WATCHING,
                message="Watching for changes",
                files_total=0,
                files_processed=0,
            )
            self._update_global_status()

    def _update_global_status(self) -> None:
        """Update global status based on project statuses."""
        if not self._project_statuses:  # pragma: no cover
            self._global_status = SyncStatus.IDLE
            return

        statuses = [p.status for p in self._project_statuses.values()]

        if any(s == SyncStatus.FAILED for s in statuses):
            self._global_status = SyncStatus.FAILED
        elif any(s in (SyncStatus.SCANNING, SyncStatus.SYNCING) for s in statuses):
            self._global_status = SyncStatus.SYNCING
        elif all(s in (SyncStatus.COMPLETED, SyncStatus.WATCHING) for s in statuses):
            self._global_status = SyncStatus.COMPLETED
        else:
            self._global_status = SyncStatus.SYNCING

    @property
    def global_status(self) -> SyncStatus:
        """Get overall sync status."""
        return self._global_status

    @property
    def is_syncing(self) -> bool:
        """Check if any sync operation is in progress."""
        return self._global_status in (SyncStatus.SCANNING, SyncStatus.SYNCING)

    @property
    def is_ready(self) -> bool:  # pragma: no cover
        """Check if system is ready (no sync in progress)."""
        return self._global_status in (SyncStatus.IDLE, SyncStatus.COMPLETED)

    def is_project_ready(self, project_name: str) -> bool:
        """Check if a specific project is ready for operations.

        Args:
            project_name: Name of the project to check

        Returns:
            True if the project is ready (completed, watching, or not tracked),
            False if the project is syncing, scanning, or failed
        """
        project_status = self._project_statuses.get(project_name)
        if not project_status:
            # Project not tracked = ready (likely hasn't been synced yet)
            return True

        return project_status.status in (SyncStatus.COMPLETED, SyncStatus.WATCHING, SyncStatus.IDLE)

    def get_project_status(self, project_name: str) -> Optional[ProjectSyncStatus]:
        """Get status for a specific project."""
        return self._project_statuses.get(project_name)

    def get_all_projects(self) -> Dict[str, ProjectSyncStatus]:
        """Get all project statuses."""
        return self._project_statuses.copy()

    def get_summary(self) -> str:  # pragma: no cover
        """Get a user-friendly summary of sync status."""
        if self._global_status == SyncStatus.IDLE:
            return "✅ System ready"
        elif self._global_status == SyncStatus.COMPLETED:
            return "✅ All projects synced successfully"
        elif self._global_status == SyncStatus.FAILED:
            failed_projects = [
                p.project_name
                for p in self._project_statuses.values()
                if p.status == SyncStatus.FAILED
            ]
            return f"❌ Sync failed for: {', '.join(failed_projects)}"
        else:
            active_projects = [
                p.project_name
                for p in self._project_statuses.values()
                if p.status in (SyncStatus.SCANNING, SyncStatus.SYNCING)
            ]
            total_files = sum(p.files_total for p in self._project_statuses.values())
            processed_files = sum(p.files_processed for p in self._project_statuses.values())

            if total_files > 0:
                progress_pct = (processed_files / total_files) * 100
                return f"🔄 Syncing {len(active_projects)} projects ({processed_files}/{total_files} files, {progress_pct:.0f}%)"
            else:
                return f"🔄 Syncing {len(active_projects)} projects"

    def clear_completed(self) -> None:
        """Remove completed project statuses to clean up memory."""
        self._project_statuses = {
            name: status
            for name, status in self._project_statuses.items()
            if status.status != SyncStatus.COMPLETED
        }
        self._update_global_status()


# Global sync status tracker instance
sync_status_tracker = SyncStatusTracker()
