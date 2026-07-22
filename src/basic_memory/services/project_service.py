"""Project management service for Basic Memory."""

import asyncio
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Sequence


from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
)
from basic_memory.repository.semantic_vector_index_factory import (
    resolve_semantic_vector_index_name,
)
from basic_memory.schemas import (
    ActivityMetrics,
    EmbeddingStatus,
    ProjectInfoResponse,
    ProjectStatistics,
    SystemStatus,
)
from basic_memory.config import (
    DatabaseBackend,
    WATCH_STATUS_JSON,
    ConfigManager,
    ProjectEntry,
    get_project_config,
    ProjectConfig,
)
from basic_memory.utils import generate_permalink

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.services.file_service import FileService


class ProjectService:
    """Service for managing Basic Memory projects."""

    repository: ProjectRepository

    def __init__(
        self,
        repository: ProjectRepository,
        session_maker: async_sessionmaker[AsyncSession],
        file_service: Optional["FileService"] = None,
    ):
        """Initialize the project service."""
        super().__init__()
        self.repository = repository
        self.session_maker = session_maker
        self.file_service = file_service

    @property
    def config_manager(self) -> ConfigManager:
        """Get a ConfigManager instance.

        Returns:
            Fresh ConfigManager instance for each access
        """
        return ConfigManager()

    @property
    def config(self) -> ProjectConfig:  # pragma: no cover
        """Get the current project configuration.

        Returns:
            Current project configuration
        """
        return get_project_config()

    @property
    def projects(self) -> Dict[str, str]:
        """Get all configured projects.

        Returns:
            Dict mapping project names to their file paths
        """
        return self.config_manager.projects

    @property
    def default_project(self) -> Optional[str]:
        """Get the name of the default project.

        Returns:
            The name of the default project, or None if not set
        """
        return self.config_manager.default_project

    async def get_default_project_name(self) -> str:
        """Get the default project name, falling back to the database.

        ConfigManager reads from the local config file, which doesn't exist
        in cloud mode. When it returns None, fall back to the is_default
        flag stored in the database.
        """
        default = self.config_manager.default_project
        if default is not None:
            return default
        async with db.scoped_session(self.session_maker) as session:
            db_default = await self.repository.get_default_project(session)
        if db_default is not None:
            return db_default.name
        raise ValueError("No default project configured")

    @property
    def current_project(self) -> Optional[str]:
        """Get the name of the currently active project.

        Returns:
            The name of the current project, or None if not set
        """
        return os.environ.get("BASIC_MEMORY_PROJECT", self.config_manager.default_project)

    async def list_projects(self) -> Sequence[Project]:
        """List all projects without loading entity relationships.

        Returns only basic project fields (name, path, etc.) without
        eager loading the entities relationship which could load thousands
        of entities for large knowledge bases.
        """
        async with db.scoped_session(self.session_maker) as session:
            return await self.repository.find_all(session, use_load_options=False)

    async def get_project(self, name: str) -> Optional[Project]:
        """Get the file path for a project by name or permalink."""
        async with db.scoped_session(self.session_maker) as session:
            return await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)

    def _check_nested_paths(self, path1: str, path2: str) -> bool:
        """Check if two paths are nested (one is a prefix of the other).

        Args:
            path1: First path to compare
            path2: Second path to compare

        Returns:
            True if one path is nested within the other, False otherwise

        Examples:
            _check_nested_paths("/foo", "/foo/bar")     # True (child under parent)
            _check_nested_paths("/foo/bar", "/foo")     # True (parent over child)
            _check_nested_paths("/foo", "/bar")         # False (siblings)
        """
        # Normalize paths to ensure proper comparison
        p1 = Path(path1).resolve()
        p2 = Path(path2).resolve()

        # Check if either path is a parent of the other
        try:
            # Check if p2 is under p1
            p2.relative_to(p1)
            return True
        except ValueError:
            # Not nested in this direction, check the other
            try:
                # Check if p1 is under p2
                p1.relative_to(p2)
                return True
            except ValueError:
                # Not nested in either direction
                return False

    async def add_project(self, name: str, path: str, set_default: bool = False) -> None:
        """Add a new project to the configuration and database.

        Args:
            name: The name of the project
            path: The file path to the project directory
            set_default: Whether to set this project as the default

        Raises:
            ValueError: If the project already exists or path collides with existing project
        """
        # If project_root is set, constrain all projects to that directory
        project_root = self.config_manager.config.project_root
        sanitized_name = None
        if project_root:
            base_path = Path(project_root)

            # In cloud mode (when project_root is set), ignore user's path completely
            # and use sanitized project name as the directory name
            # This ensures flat structure: /app/data/test-bisync instead of /app/data/documents/test bisync
            sanitized_name = generate_permalink(name)

            # Construct path using sanitized project name only
            resolved_path = (base_path / sanitized_name).resolve().as_posix()

            # Verify the resolved path is actually under project_root
            if not resolved_path.startswith(base_path.resolve().as_posix()):  # pragma: no cover
                raise ValueError(
                    f"BASIC_MEMORY_PROJECT_ROOT is set to {project_root}. "
                    f"All projects must be created under this directory. Invalid path: {path}"
                )  # pragma: no cover

        else:
            resolved_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

        async with db.scoped_session(self.session_maker) as session:
            existing_projects = await self.repository.find_all(session, use_load_options=False)
            if project_root:
                # Check for case-insensitive path collisions with existing projects
                for existing in existing_projects:
                    if (
                        existing.path.lower() == resolved_path.lower()
                        and existing.path != resolved_path
                    ):
                        raise ValueError(  # pragma: no cover
                            f"Path collision detected: '{resolved_path}' conflicts with existing project "
                            f"'{existing.name}' at '{existing.path}'. "
                            f"In cloud mode, paths are normalized to lowercase to prevent case-sensitivity issues."
                        )  # pragma: no cover

            # Check for nested paths with existing projects
            for existing in existing_projects:
                if self._check_nested_paths(resolved_path, existing.path):
                    # Determine which path is nested within which for appropriate error message
                    p_new = Path(resolved_path).resolve()
                    p_existing = Path(existing.path).resolve()

                    # Check if new path is nested under existing project
                    if p_new.is_relative_to(p_existing):
                        raise ValueError(
                            f"Cannot create project at '{resolved_path}': "
                            f"path is nested within existing project '{existing.name}' at '{existing.path}'. "
                            f"Projects cannot share directory trees."
                        )
                    else:
                        # Existing project is nested under new path
                        raise ValueError(
                            f"Cannot create project at '{resolved_path}': "
                            f"existing project '{existing.name}' at '{existing.path}' is nested within this path. "
                            f"Projects cannot share directory trees."
                        )

            # Ensure the project directory exists on disk.
            # Trigger: project_root not set means local filesystem mode (not S3/cloud)
            # Why: FileService (or future S3FileService) provides cloud-compatible directory creation;
            #      direct Path.mkdir() bypasses this abstraction
            # Outcome: directory exists before config/DB entries are written
            if not self.config_manager.config.project_root:
                if self.file_service is None:
                    raise ValueError(
                        "file_service is required for local project directory creation"
                    )
                await self.file_service.ensure_directory(Path(resolved_path))

            # First add to config file (this validates project uniqueness and keeps
            # config + database aligned for all backends).
            self.config_manager.add_project(name, resolved_path)

            # Then add to database
            project_data = {
                "name": name,
                "path": resolved_path,
                "permalink": sanitized_name,
                "is_active": True,
                # Don't set is_default=False to avoid UNIQUE constraint issues
                # Let it default to NULL, only set to True when explicitly making default
            }
            created_project = await self.repository.create(session, project_data)

            # If this should be the default project, ensure only one default exists
            if set_default:
                await self.repository.set_as_default(session, created_project.id)
                self.config_manager.set_default_project(name)
                logger.info(f"Project '{name}' set as default")
            else:
                config_default = self.config_manager.default_project
                if config_default is not None:
                    db_default_project = await self.repository.get_by_name(session, config_default)
                    if db_default_project is None:
                        # Trigger: config names a default project that has no database row
                        #      (the fresh-config wedge from issue #974).
                        # Why: synchronize_projects treats an existing database default as
                        #      authoritative, so a surviving default must win over promoting
                        #      the just-added project. set_default_project raises for names
                        #      absent from config — and reconciliation deletes such rows —
                        #      so a database default unknown to config cannot be repointed to.
                        # Outcome: config is repointed at a usable database default when one
                        #      exists; otherwise the added project becomes the default.
                        db_default = await self.repository.get_default_project(session)
                        if (
                            db_default is not None
                            and self.config_manager.get_project(db_default.name)[0] is not None
                        ):
                            self.config_manager.set_default_project(db_default.name)
                            logger.info(
                                "Repointed config default from missing '%s' at existing "
                                "database default '%s'",
                                config_default,
                                db_default.name,
                            )
                        else:
                            await self.repository.set_as_default(session, created_project.id)
                            self.config_manager.set_default_project(name)
                            logger.info(
                                "Promoted project '%s' to default because configured default '%s' "
                                "is missing from database",
                                name,
                                config_default,
                            )

        logger.info(f"Project '{name}' added at {resolved_path}")

    async def remove_project(self, name: str, delete_notes: bool = False) -> None:
        """Remove a project from configuration and database.

        Args:
            name: The name of the project to remove
            delete_notes: If True, delete the project directory from filesystem

        Raises:
            ValueError: If the project doesn't exist or is the default project
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for remove_project")

        async with db.scoped_session(self.session_maker) as session:
            # Get project from database first
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if not project:
                raise ValueError(f"Project '{name}' not found")  # pragma: no cover

            project_path = project.path

            # Check if project is default
            # In cloud mode: database is source of truth
            # In local mode: also check config file
            is_default = project.is_default
            if self.config_manager.config.database_backend != DatabaseBackend.POSTGRES:
                is_default = is_default or name == self.config_manager.config.default_project
            if is_default:
                raise ValueError(f"Cannot remove the default project '{name}'")  # pragma: no cover

            # Remove from config if it exists there (may not exist in cloud mode)
            try:
                self.config_manager.remove_project(name)
            except ValueError:  # pragma: no cover
                # Project not in config - that's OK in cloud mode, continue with database deletion
                logger.debug(  # pragma: no cover
                    f"Project '{name}' not found in config, removing from database only"
                )

            # Remove from database
            await self.repository.delete(session, project.id)

        logger.info(f"Project '{name}' removed from configuration and database")

        # Optionally delete the project directory
        if delete_notes and project_path:
            try:
                path_obj = Path(project_path)
                if path_obj.exists() and path_obj.is_dir():
                    await asyncio.to_thread(shutil.rmtree, project_path)
                    logger.info(f"Deleted project directory: {project_path}")
                else:
                    logger.warning(  # pragma: no cover
                        f"Project directory not found or not a directory: {project_path}"
                    )  # pragma: no cover
            except Exception as e:  # pragma: no cover
                logger.warning(  # pragma: no cover
                    f"Failed to delete project directory {project_path}: {e}"
                )

    async def set_default_project(self, name: str) -> None:
        """Set the default project in configuration and database.

        Args:
            name: The name of the project to set as default

        Raises:
            ValueError: If the project doesn't exist
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for set_default_project")

        async with db.scoped_session(self.session_maker) as session:
            # Look up project in database first to validate it exists
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if not project:
                raise ValueError(f"Project '{name}' not found")

            # Update database
            await self.repository.set_as_default(session, project.id)

        # Keep config and database default project in sync for all backends.
        self.config_manager.set_default_project(name)

        logger.info(f"Project '{name}' set as default in configuration and database")

    async def _ensure_single_default_project(self, session: AsyncSession) -> None:
        """Ensure only one project has is_default=True.

        This method validates the database state and fixes any issues where
        multiple projects might have is_default=True or no project is marked as default.
        """
        if not self.repository:
            raise ValueError(
                "Repository is required for _ensure_single_default_project"
            )  # pragma: no cover

        # Get all projects with is_default=True
        db_projects = await self.repository.find_all(session)
        default_projects = [p for p in db_projects if p.is_default is True]

        if len(default_projects) > 1:  # pragma: no cover
            # Multiple defaults found - fix by keeping the first one and clearing others
            # This is defensive code that should rarely execute due to business logic enforcement
            logger.warning(  # pragma: no cover
                f"Found {len(default_projects)} projects with is_default=True, fixing..."
            )
            keep_default = default_projects[0]  # pragma: no cover

            # Clear all defaults first, then set only the first one as default
            await self.repository.set_as_default(session, keep_default.id)  # pragma: no cover

            logger.info(
                f"Fixed default project conflicts, kept '{keep_default.name}' as default"
            )  # pragma: no cover

        elif len(default_projects) == 0:  # pragma: no cover
            # No default project - set the config default as default
            # This is defensive code for edge cases where no default exists
            config_default = self.config_manager.default_project  # pragma: no cover
            config_project = (
                await self.repository.get_by_name(session, config_default)
                if config_default
                else None
            )  # pragma: no cover
            if config_project:  # pragma: no cover
                await self.repository.set_as_default(session, config_project.id)  # pragma: no cover
                logger.info(
                    f"Set '{config_default}' as default project (was missing)"
                )  # pragma: no cover

    async def synchronize_projects(self) -> None:  # pragma: no cover
        """Synchronize projects between database and configuration.

        Ensures that all projects in the configuration file exist in the database
        and vice versa. This should be called during initialization to reconcile
        any differences between the two sources.
        """
        if not self.repository:
            raise ValueError("Repository is required for synchronize_projects")

        logger.info("Synchronizing projects between database and configuration")

        # Get all projects from configuration and normalize names if needed
        # Use .config property (not load_config()) so tests can patch ConfigManager.config
        config = self.config_manager.config
        updated_config: Dict[str, ProjectEntry] = {}
        config_updated = False

        for name, entry in config.projects.items():
            # Generate normalized name (what the database expects)
            normalized_name = generate_permalink(name)

            if normalized_name != name:
                logger.info(f"Normalizing project name in config: '{name}' -> '{normalized_name}'")
                config_updated = True

            updated_config[normalized_name] = entry

        # Update the configuration if any changes were made
        if config_updated:
            config.projects = updated_config
            self.config_manager.save_config(config)
            logger.info("Config updated with normalized project names")

        # Use the normalized config for further processing — keys are now project names
        config_project_names = updated_config

        async with db.scoped_session(self.session_maker) as session:
            # Get all projects from database
            db_projects = await self.repository.get_active_projects(session)
            db_projects_by_permalink = {p.permalink: p for p in db_projects}

            # Add projects that exist in config but not in DB
            for name, entry in config_project_names.items():
                if name not in db_projects_by_permalink:
                    logger.info(f"Adding project '{name}' to database")
                    project_data = {
                        "name": name,
                        "path": entry.path,
                        "permalink": generate_permalink(name),
                        "is_active": True,
                        # Don't set is_default here - let the enforcement logic handle it
                    }
                    await self.repository.create(session, project_data)

            # Remove projects that exist in DB but not in config
            # Config is the source of truth - if a project was deleted from config,
            # it should be deleted from DB too (fixes issue #193)
            for name, project in db_projects_by_permalink.items():
                if name not in config_project_names:
                    logger.info(
                        f"Removing project '{name}' from database (deleted from config, source of truth)"
                    )
                    await self.repository.delete(session, project.id)

            # Ensure database default project state is consistent
            await self._ensure_single_default_project(session)

            # Make sure default project is synchronized between config and database
            db_default = await self.repository.get_default_project(session)
            config_default = self.config_manager.default_project

            if db_default and db_default.name != config_default:
                # Update config to match DB default
                logger.info(f"Updating default project in config to '{db_default.name}'")
                self.config_manager.set_default_project(db_default.name)
            elif not db_default and config_default:
                # Update DB to match config default (if the project exists)
                project = await self.repository.get_by_name(session, config_default)
                if project:
                    logger.info(f"Updating default project in database to '{config_default}'")
                    await self.repository.set_as_default(session, project.id)

        logger.info("Project synchronization complete")

    async def move_project(self, name: str, new_path: str) -> None:
        """Move a project to a new location.

        Args:
            name: The name of the project to move
            new_path: The new absolute path for the project

        Raises:
            ValueError: If the project doesn't exist or repository isn't initialized
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for move_project")  # pragma: no cover

        # Resolve to absolute path
        resolved_path = Path(os.path.abspath(os.path.expanduser(new_path))).as_posix()

        # Validate project exists in config
        if name not in self.config_manager.projects:
            raise ValueError(f"Project '{name}' not found in configuration")

        # Create the new directory if it doesn't exist (skip in cloud mode where storage is S3)
        # Trigger: project_root not set means local filesystem mode
        # Why: FileService (or future S3FileService) provides cloud-compatible directory creation
        # Outcome: destination directory exists before config/DB are updated
        if not self.config_manager.config.project_root:
            if self.file_service is None:
                raise ValueError("file_service is required for local project directory creation")
            await self.file_service.ensure_directory(Path(resolved_path))

        # Update in configuration
        config = self.config_manager.load_config()
        old_path = config.projects[name].path
        config.projects[name].path = resolved_path
        self.config_manager.save_config(config)

        async with db.scoped_session(self.session_maker) as session:
            # Update in database using robust lookup
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if project:
                await self.repository.update_path(session, project.id, resolved_path)
                logger.info(f"Moved project '{name}' from {old_path} to {resolved_path}")
            else:
                logger.error(f"Project '{name}' exists in config but not in database")
                # Restore the old path in config since DB update failed
                config.projects[name].path = old_path
                self.config_manager.save_config(config)
                raise ValueError(f"Project '{name}' not found in database")

    async def update_project(  # pragma: no cover
        self, name: str, updated_path: Optional[str] = None, is_active: Optional[bool] = None
    ) -> None:
        """Update project information in both config and database.

        Args:
            name: The name of the project to update
            updated_path: Optional new path for the project
            is_active: Optional flag to set project active status

        Raises:
            ValueError: If project doesn't exist or repository isn't initialized
        """
        if not self.repository:
            raise ValueError("Repository is required for update_project")

        # Validate project exists in config
        if name not in self.config_manager.projects:
            raise ValueError(f"Project '{name}' not found in configuration")

        async with db.scoped_session(self.session_maker) as session:
            # Get project from database using robust lookup
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if not project:
                logger.error(f"Project '{name}' exists in config but not in database")
                return

            # Update path if provided
            if updated_path:
                resolved_path = Path(os.path.abspath(os.path.expanduser(updated_path))).as_posix()

                # Update in config
                config = self.config_manager.load_config()
                config.projects[name].path = resolved_path
                self.config_manager.save_config(config)

                # Update in database
                project.path = resolved_path
                await self.repository.update(session, project.id, project)

                logger.info(f"Updated path for project '{name}' to {resolved_path}")

            # Update active status if provided
            if is_active is not None:
                project.is_active = is_active
                await self.repository.update(session, project.id, project)
                logger.info(f"Set active status for project '{name}' to {is_active}")

            # If project was made inactive and it was the default, we need to pick a new default
            if is_active is False and project.is_default:
                # Find another active project
                active_projects = await self.repository.get_active_projects(session)
                if active_projects:
                    new_default = active_projects[0]
                    await self.repository.set_as_default(session, new_default.id)
                    self.config_manager.set_default_project(new_default.name)
                    logger.info(
                        f"Changed default project to '{new_default.name}' as '{name}' was deactivated"
                    )

    async def get_project_info(self, project_name: Optional[str] = None) -> ProjectInfoResponse:
        """Get comprehensive information about the specified Basic Memory project.

        Args:
            project_name: Name of the project to get info for. If None, uses the current config project.

        Returns:
            Comprehensive project information and statistics
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for get_project_info")

        # Use specified project or fall back to config project
        requested_project_name = project_name or self.config.project
        project_permalink = generate_permalink(requested_project_name)

        async with db.scoped_session(self.session_maker) as session:
            # Get project from database to get project_id
            db_project = await self.repository.get_by_permalink(session, project_permalink)
            if not db_project:  # pragma: no cover
                raise ValueError(f"Project '{requested_project_name}' not found in database")
            db_projects = await self.repository.get_active_projects(session)

        # Trigger: cloud-only projects may exist in DB but not in local config.
        # Why: cloud routing should not require local config entries for project info.
        # Outcome: prefer config path when available, otherwise use DB path.
        config_name, config_path = self.config_manager.get_project(db_project.name)
        if config_name and config_path:
            resolved_project_name = config_name
            resolved_project_path = config_path
        else:
            resolved_project_name = db_project.name
            resolved_project_path = db_project.path

        # Get statistics for the specified project
        statistics = await self.get_statistics(db_project.id)

        # Get activity metrics for the specified project
        activity = await self.get_activity_metrics(db_project.id)

        # Get embedding status for the specified project
        embedding_status = await self.get_embedding_status(db_project.id)

        # Get system status
        system = self.get_system_status()

        # Get enhanced project information from database
        db_projects_by_permalink = {p.permalink: p for p in db_projects}

        # Get default project info
        default_project = self.config_manager.default_project
        if default_project is None:
            for project in db_projects:
                if project.is_default:
                    default_project = project.name
                    break

        # Convert config projects to include database info
        enhanced_projects = {}
        for config_project_name, config_project_path in self.config_manager.projects.items():
            config_permalink = generate_permalink(config_project_name)
            config_db_project = db_projects_by_permalink.get(config_permalink)
            enhanced_projects[config_project_name] = {
                "path": config_project_path,
                "active": config_db_project.is_active if config_db_project else True,
                "id": config_db_project.id if config_db_project else None,
                "is_default": (config_project_name == default_project),
                "permalink": (
                    config_db_project.permalink
                    if config_db_project
                    else config_project_name.lower().replace(" ", "-")
                ),
            }

        # Include active DB projects that are not present in local config (cloud-only).
        for active_db_project in db_projects:
            if active_db_project.name in enhanced_projects:
                continue
            enhanced_projects[active_db_project.name] = {
                "path": active_db_project.path,
                "active": active_db_project.is_active,
                "id": active_db_project.id,
                "is_default": bool(active_db_project.is_default),
                "permalink": active_db_project.permalink,
            }

        # Construct the response
        return ProjectInfoResponse(
            project_name=resolved_project_name,
            project_path=resolved_project_path,
            available_projects=enhanced_projects,
            default_project=default_project,
            statistics=statistics,
            activity=activity,
            system=system,
            embedding_status=embedding_status,
        )

    async def get_statistics(self, project_id: int) -> ProjectStatistics:
        """Get statistics about the specified project.

        Args:
            project_id: ID of the project to get statistics for (required).
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for get_statistics")

        async with db.scoped_session(self.session_maker) as session:
            # Get basic counts
            entity_count_result = await self.repository.execute_query(
                session,
                text("SELECT COUNT(*) FROM entity WHERE project_id = :project_id"),
                {"project_id": project_id},
            )
            total_entities = entity_count_result.scalar() or 0

            observation_count_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM observation o JOIN entity e ON o.entity_id = e.id WHERE e.project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            total_observations = observation_count_result.scalar() or 0

            relation_count_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE e.project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            total_relations = relation_count_result.scalar() or 0

            unresolved_count_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE r.to_id IS NULL AND e.project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            total_unresolved = unresolved_count_result.scalar() or 0

            # Get entity counts by note type
            note_types_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT note_type, COUNT(*) FROM entity WHERE project_id = :project_id GROUP BY note_type"
                ),
                {"project_id": project_id},
            )
            note_types = {row[0]: row[1] for row in note_types_result.fetchall()}

            # Get observation counts by category
            category_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT o.category, COUNT(*) FROM observation o JOIN entity e ON o.entity_id = e.id WHERE e.project_id = :project_id GROUP BY o.category"
                ),
                {"project_id": project_id},
            )
            observation_categories = {row[0]: row[1] for row in category_result.fetchall()}

            # Get relation counts by type
            relation_types_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT r.relation_type, COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE e.project_id = :project_id GROUP BY r.relation_type"
                ),
                {"project_id": project_id},
            )
            relation_types = {row[0]: row[1] for row in relation_types_result.fetchall()}

            # Find most connected entities (most outgoing relations) - project filtered
            connected_result = await self.repository.execute_query(
                session,
                text("""
                SELECT e.id, e.title, e.permalink, COUNT(r.id) AS relation_count, e.file_path
                FROM entity e
                JOIN relation r ON e.id = r.from_id
                WHERE e.project_id = :project_id
                GROUP BY e.id
                ORDER BY relation_count DESC
                LIMIT 10
            """),
                {"project_id": project_id},
            )
            most_connected = [
                {
                    "id": row[0],
                    "title": row[1],
                    "permalink": row[2],
                    "relation_count": row[3],
                    "file_path": row[4],
                }
                for row in connected_result.fetchall()
            ]

            # Count isolated entities (no relations) - project filtered
            isolated_result = await self.repository.execute_query(
                session,
                text("""
                SELECT COUNT(e.id)
                FROM entity e
                LEFT JOIN relation r1 ON e.id = r1.from_id
                LEFT JOIN relation r2 ON e.id = r2.to_id
                WHERE e.project_id = :project_id AND r1.id IS NULL AND r2.id IS NULL
            """),
                {"project_id": project_id},
            )
            isolated_count = isolated_result.scalar() or 0

            return ProjectStatistics(
                total_entities=total_entities,
                total_observations=total_observations,
                total_relations=total_relations,
                total_unresolved_relations=total_unresolved,
                note_types=note_types,
                observation_categories=observation_categories,
                relation_types=relation_types,
                most_connected_entities=most_connected,
                isolated_entities=isolated_count,
            )

    async def get_activity_metrics(self, project_id: int) -> ActivityMetrics:
        """Get activity metrics for the specified project.

        Args:
            project_id: ID of the project to get activity metrics for (required).
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for get_activity_metrics")

        async with db.scoped_session(self.session_maker) as session:
            # Get recently created entities (project filtered)
            created_result = await self.repository.execute_query(
                session,
                text("""
                SELECT id, title, permalink, note_type, created_at, file_path
                FROM entity
                WHERE project_id = :project_id
                ORDER BY created_at DESC
                LIMIT 10
            """),
                {"project_id": project_id},
            )
            recently_created = [
                {
                    "id": row[0],
                    "title": row[1],
                    "permalink": row[2],
                    "note_type": row[3],
                    "created_at": row[4],
                    "file_path": row[5],
                }
                for row in created_result.fetchall()
            ]

            # Get recently updated entities (project filtered)
            updated_result = await self.repository.execute_query(
                session,
                text("""
                SELECT id, title, permalink, note_type, updated_at, file_path
                FROM entity
                WHERE project_id = :project_id
                ORDER BY updated_at DESC
                LIMIT 10
            """),
                {"project_id": project_id},
            )
            recently_updated = [
                {
                    "id": row[0],
                    "title": row[1],
                    "permalink": row[2],
                    "note_type": row[3],
                    "updated_at": row[4],
                    "file_path": row[5],
                }
                for row in updated_result.fetchall()
            ]

            # Get monthly growth over the last 6 months
            # Calculate the start of 6 months ago
            now = datetime.now()
            six_months_ago = datetime(
                now.year - (1 if now.month <= 6 else 0), ((now.month - 6) % 12) or 12, 1
            )

            # Query for monthly entity creation (project filtered)
            # Use different date formatting for SQLite vs Postgres
            from basic_memory.config import DatabaseBackend

            is_postgres = self.config_manager.config.database_backend == DatabaseBackend.POSTGRES
            date_format = (
                "to_char(created_at, 'YYYY-MM')" if is_postgres else "strftime('%Y-%m', created_at)"
            )

            # Postgres needs datetime objects, SQLite needs ISO strings
            six_months_param = six_months_ago if is_postgres else six_months_ago.isoformat()

            entity_growth_result = await self.repository.execute_query(
                session,
                text(f"""
                SELECT
                    {date_format} AS month,
                    COUNT(*) AS count
                FROM entity
                WHERE created_at >= :six_months_ago AND project_id = :project_id
                GROUP BY month
                ORDER BY month
            """),
                {"six_months_ago": six_months_param, "project_id": project_id},
            )
            entity_growth = {row[0]: row[1] for row in entity_growth_result.fetchall()}

            # Query for monthly observation creation (project filtered)
            date_format_entity = (
                "to_char(entity.created_at, 'YYYY-MM')"
                if is_postgres
                else "strftime('%Y-%m', entity.created_at)"
            )

            observation_growth_result = await self.repository.execute_query(
                session,
                text(f"""
                SELECT
                    {date_format_entity} AS month,
                    COUNT(*) AS count
                FROM observation
                INNER JOIN entity ON observation.entity_id = entity.id
                WHERE entity.created_at >= :six_months_ago AND entity.project_id = :project_id
                GROUP BY month
                ORDER BY month
            """),
                {"six_months_ago": six_months_param, "project_id": project_id},
            )
            observation_growth = {row[0]: row[1] for row in observation_growth_result.fetchall()}

            # Query for monthly relation creation (project filtered)
            relation_growth_result = await self.repository.execute_query(
                session,
                text(f"""
                SELECT
                    {date_format_entity} AS month,
                    COUNT(*) AS count
                FROM relation
                INNER JOIN entity ON relation.from_id = entity.id
                WHERE entity.created_at >= :six_months_ago AND entity.project_id = :project_id
                GROUP BY month
                ORDER BY month
            """),
                {"six_months_ago": six_months_param, "project_id": project_id},
            )
            relation_growth = {row[0]: row[1] for row in relation_growth_result.fetchall()}

            # Combine all monthly growth data
            monthly_growth = {}
            for month in set(
                list(entity_growth.keys())
                + list(observation_growth.keys())
                + list(relation_growth.keys())
            ):
                monthly_growth[month] = {
                    "entities": entity_growth.get(month, 0),
                    "observations": observation_growth.get(month, 0),
                    "relations": relation_growth.get(month, 0),
                    "total": (
                        entity_growth.get(month, 0)
                        + observation_growth.get(month, 0)
                        + relation_growth.get(month, 0)
                    ),
                }

            return ActivityMetrics(
                recently_created=recently_created,
                recently_updated=recently_updated,
                monthly_growth=monthly_growth,
            )

    async def get_embedding_status(self, project_id: int) -> EmbeddingStatus:
        """Get embedding/vector index status for the specified project.

        Reports config, counts, and whether a reindex is recommended.
        """
        config = self.config_manager.config
        semantic_enabled = config.semantic_search_enabled

        # When semantic search is disabled, return minimal status
        if not semantic_enabled:
            return EmbeddingStatus(semantic_search_enabled=False)

        provider = config.semantic_embedding_provider
        model = config.semantic_embedding_model
        dimensions = config.semantic_embedding_dimensions
        document_prefix_set = bool(config.semantic_embedding_document_prefix)
        query_prefix_set = bool(config.semantic_embedding_query_prefix)

        is_postgres = config.database_backend == DatabaseBackend.POSTGRES
        vector_index = resolve_semantic_vector_index_name(config, config.database_backend)
        embedding_identity = configured_embedding_provider_identity(config)

        # --- Check vector manifest existence ---
        # The SQL manifest is authoritative even when vector values live outside
        # the database, so status never probes backend-specific storage tables.
        if is_postgres:
            table_check_sql = text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'search_vector_chunks'"
            )
        else:
            table_check_sql = text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'search_vector_chunks'"
            )

        async with db.scoped_session(self.session_maker) as session:
            table_result = await self.repository.execute_query(session, table_check_sql, {})
            vector_tables_exist = (table_result.scalar() or 0) == 1

            if not vector_tables_exist:
                # Count distinct entities in search index for the recommendation message
                si_result = await self.repository.execute_query(
                    session,
                    text(
                        "SELECT COUNT(DISTINCT entity_id) FROM search_index "
                        "WHERE project_id = :project_id"
                    ),
                    {"project_id": project_id},
                )
                total_indexed_entities = si_result.scalar() or 0

                return EmbeddingStatus(
                    semantic_search_enabled=True,
                    embedding_provider=provider,
                    embedding_model=model,
                    embedding_dimensions=dimensions,
                    embedding_document_prefix_set=document_prefix_set,
                    embedding_query_prefix_set=query_prefix_set,
                    total_indexed_entities=total_indexed_entities,
                    vector_tables_exist=False,
                    reindex_recommended=True,
                    reindex_reason=(
                        "Vector manifest not initialized — run: bm reindex --embeddings"
                    ),
                )

            # --- Count queries (manifest exists) ---
            # Filter by entity existence to exclude stale rows from deleted entities
            # that remain in derived search tables (search_index, search_vector_chunks)
            entity_exists = (
                "AND entity_id IN (SELECT id FROM entity WHERE project_id = :project_id)"
            )

            si_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(DISTINCT entity_id) FROM search_index "
                    f"WHERE project_id = :project_id {entity_exists}"
                ),
                {"project_id": project_id},
            )
            total_indexed_entities = si_result.scalar() or 0

            manifest_params = {
                "project_id": project_id,
                "vector_index": vector_index,
                "embedding_identity": embedding_identity,
            }
            current_ready = (
                "vector_index = :vector_index "
                "AND embedding_model = :embedding_identity "
                "AND embedding_status = 'ready'"
            )

            chunks_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM search_vector_chunks "
                    f"WHERE project_id = :project_id {entity_exists}"
                ),
                {"project_id": project_id},
            )
            total_chunks = chunks_result.scalar() or 0

            entities_with_chunks_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(DISTINCT entity_id) FROM search_vector_chunks "
                    f"WHERE project_id = :project_id {entity_exists}"
                ),
                {"project_id": project_id},
            )
            total_entities_with_chunks = entities_with_chunks_result.scalar() or 0

            embeddings_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM search_vector_chunks "
                    f"WHERE project_id = :project_id AND {current_ready} {entity_exists}"
                ),
                manifest_params,
            )
            total_embeddings = embeddings_result.scalar() or 0

            orphaned_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM search_vector_chunks "
                    f"WHERE project_id = :project_id AND NOT ({current_ready}) {entity_exists}"
                ),
                manifest_params,
            )
            orphaned_chunks = orphaned_result.scalar() or 0

            # --- Reindex recommendation logic (priority order) ---
            reindex_recommended = False
            reindex_reason = None

            if total_indexed_entities > 0 and total_chunks == 0:
                reindex_recommended = True
                reindex_reason = "Embeddings have never been built — run: bm reindex --embeddings"
            elif orphaned_chunks > 0:
                reindex_recommended = True
                reindex_reason = (
                    f"{orphaned_chunks} chunks need vector indexing (pending or stale) "
                    "— run: bm reindex --embeddings"
                )
            elif total_indexed_entities > total_entities_with_chunks:
                missing = total_indexed_entities - total_entities_with_chunks
                reindex_recommended = True
                reindex_reason = (
                    f"{missing} entities missing embeddings — run: bm reindex --embeddings"
                )

            return EmbeddingStatus(
                semantic_search_enabled=True,
                embedding_provider=provider,
                embedding_model=model,
                embedding_dimensions=dimensions,
                embedding_document_prefix_set=document_prefix_set,
                embedding_query_prefix_set=query_prefix_set,
                total_indexed_entities=total_indexed_entities,
                total_entities_with_chunks=total_entities_with_chunks,
                total_chunks=total_chunks,
                total_embeddings=total_embeddings,
                orphaned_chunks=orphaned_chunks,
                vector_tables_exist=True,
                reindex_recommended=reindex_recommended,
                reindex_reason=reindex_reason,
            )

    def get_system_status(self) -> SystemStatus:
        """Get system status information."""
        import basic_memory

        # Get database information
        db_path = self.config_manager.config.database_path
        db_size = db_path.stat().st_size if db_path.exists() else 0
        db_size_readable = f"{db_size / (1024 * 1024):.2f} MB"

        # Get watch service status if available
        watch_status = None
        watch_status_path = self.config_manager.config.data_dir_path / WATCH_STATUS_JSON
        if watch_status_path.exists():
            try:
                watch_status = json.loads(watch_status_path.read_text(encoding="utf-8"))
            except Exception:  # pragma: no cover
                pass

        return SystemStatus(
            version=basic_memory.__version__,
            database_path=str(db_path),
            database_size=db_size_readable,
            watch_status=watch_status,
            timestamp=datetime.now(),
        )
