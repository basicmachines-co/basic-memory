"""Migration service for handling background migrations and status tracking."""

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger

from basic_memory.config import BasicMemoryConfig


class MigrationStatus(Enum):
    """Status of migration operations."""

    NOT_NEEDED = "not_needed"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MigrationState:
    """Current state of migration operations."""

    status: MigrationStatus
    message: str
    progress: Optional[str] = None
    error: Optional[str] = None
    projects_migrated: int = 0
    projects_total: int = 0


class MigrationManager:
    """Manages background migration operations and status tracking."""

    def __init__(self):
        self._state = MigrationState(
            status=MigrationStatus.NOT_NEEDED, message="No migration required"
        )
        self._migration_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> MigrationState:
        """Get current migration state."""
        return self._state

    @property
    def is_ready(self) -> bool:
        """Check if the system is ready for normal operations."""
        return self._state.status in (MigrationStatus.NOT_NEEDED, MigrationStatus.COMPLETED)

    @property
    def status_message(self) -> str:
        """Get a user-friendly status message."""
        if self._state.status == MigrationStatus.IN_PROGRESS:
            progress = (
                f" ({self._state.projects_migrated}/{self._state.projects_total})"
                if self._state.projects_total > 0
                else ""
            )
            return f"🔄 File sync in progress{progress}: {self._state.message}. Use sync_status() tool for details."
        elif self._state.status == MigrationStatus.FAILED:
            return f"❌ File sync failed: {self._state.error or 'Unknown error'}. Use sync_status() tool for details."
        elif self._state.status == MigrationStatus.COMPLETED:
            return "✅ File sync completed successfully"
        else:
            return "✅ System ready"

    async def check_migration_needed(self, app_config: BasicMemoryConfig) -> bool:
        """Check if migration is needed without performing it."""
        from basic_memory import db
        from basic_memory.repository import ProjectRepository

        try:
            # Get database session
            _, session_maker = await db.get_or_create_db(
                db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
            )
            project_repository = ProjectRepository(session_maker)

            # Check for legacy projects
            legacy_projects = []
            for project_name, project_path in app_config.projects.items():
                legacy_dir = Path(project_path) / ".basic-memory"
                if legacy_dir.exists():
                    project = await project_repository.get_by_name(project_name)
                    if project:
                        legacy_projects.append(project)

            if legacy_projects:
                self._state = MigrationState(
                    status=MigrationStatus.PENDING,
                    message="Legacy projects detected",
                    projects_total=len(legacy_projects),
                )
                return True
            else:
                self._state = MigrationState(
                    status=MigrationStatus.NOT_NEEDED, message="No migration required"
                )
                return False

        except Exception as e:
            logger.error(f"Error checking migration status: {e}")
            self._state = MigrationState(
                status=MigrationStatus.FAILED, message="Migration check failed", error=str(e)
            )
            return False

    async def start_background_migration(self, app_config: BasicMemoryConfig) -> None:
        """Start migration in background if needed."""
        if not await self.check_migration_needed(app_config):
            return

        if self._migration_task and not self._migration_task.done():
            logger.info("Migration already in progress")
            return

        logger.info("Starting background migration")
        self._migration_task = asyncio.create_task(self._run_migration(app_config))

    async def _run_migration(self, app_config: BasicMemoryConfig) -> None:
        """Run the actual migration process."""
        try:
            self._state.status = MigrationStatus.IN_PROGRESS
            self._state.message = "Migrating legacy projects"

            # Import here to avoid circular imports
            from basic_memory.services.initialization import migrate_legacy_projects

            # Run the migration
            await migrate_legacy_projects(app_config)

            self._state = MigrationState(
                status=MigrationStatus.COMPLETED, message="Migration completed successfully"
            )
            logger.info("Background migration completed successfully")

        except Exception as e:
            logger.error(f"Background migration failed: {e}")
            self._state = MigrationState(
                status=MigrationStatus.FAILED, message="Migration failed", error=str(e)
            )

    async def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """Wait for migration to complete."""
        if self.is_ready:
            return True

        if not self._migration_task:
            return False

        try:
            await asyncio.wait_for(self._migration_task, timeout=timeout)
            return self.is_ready
        except asyncio.TimeoutError:
            return False

    def mark_completed(self, message: str = "Migration completed") -> None:
        """Mark migration as completed externally."""
        self._state = MigrationState(status=MigrationStatus.COMPLETED, message=message)


# Global migration manager instance
migration_manager = MigrationManager()
