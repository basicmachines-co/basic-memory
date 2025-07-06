"""Project management service for Basic Memory."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Sequence

from loguru import logger
from sqlalchemy import text

from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas import (
    ActivityMetrics,
    ProjectInfoResponse,
    ProjectStatistics,
    SystemStatus,
)
from basic_memory.config import WATCH_STATUS_JSON, ConfigManager, get_project_config, ProjectConfig
from basic_memory.utils import generate_permalink


class ProjectService:
    """Service for managing Basic Memory projects."""

    repository: ProjectRepository

    def __init__(self, repository: ProjectRepository):
        """Initialize the project service."""
        super().__init__()
        self.repository = repository

    @property
    def config_manager(self) -> ConfigManager:
        """Get a ConfigManager instance.

        Returns:
            Fresh ConfigManager instance for each access
        """
        return ConfigManager()

    @property
    def config(self) -> ProjectConfig:
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
    def default_project(self) -> str:
        """Get the name of the default project.

        Returns:
            The name of the default project
        """
        return self.config_manager.default_project

    @property
    def current_project(self) -> str:
        """Get the name of the currently active project.

        Returns:
            The name of the current project
        """
        return os.environ.get("BASIC_MEMORY_PROJECT", self.config_manager.default_project)

    async def list_projects(self) -> Sequence[Project]:
        return await self.repository.find_all()

    async def get_project(self, name: str) -> Optional[Project]:
        """Get the file path for a project by name or permalink."""
        return await self.repository.get_by_name(name) or await self.repository.get_by_permalink(
            name
        )

    async def add_project(self, name: str, path: str, set_default: bool = False) -> None:
        """Add a new project to the configuration and database.

        Args:
            name: The name of the project
            path: The file path to the project directory
            set_default: Whether to set this project as the default

        Raises:
            ValueError: If the project already exists
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for add_project")

        # Resolve to absolute path
        resolved_path = os.path.abspath(os.path.expanduser(path))

        # First add to config file (this will validate the project doesn't exist)
        project_config = self.config_manager.add_project(name, resolved_path)

        # Then add to database
        project_data = {
            "name": name,
            "path": resolved_path,
            "permalink": generate_permalink(project_config.name),
            "is_active": True,
            # Don't set is_default=False to avoid UNIQUE constraint issues
            # Let it default to NULL, only set to True when explicitly making default
        }
        created_project = await self.repository.create(project_data)

        # If this should be the default project, ensure only one default exists
        if set_default:
            await self.repository.set_as_default(created_project.id)
            self.config_manager.set_default_project(name)
            logger.info(f"Project '{name}' set as default")

        logger.info(f"Project '{name}' added at {resolved_path}")

    async def remove_project(self, name: str) -> None:
        """Remove a project from configuration and database.

        Args:
            name: The name of the project to remove

        Raises:
            ValueError: If the project doesn't exist or is the default project
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for remove_project")

        # First remove from config (this will validate the project exists and is not default)
        self.config_manager.remove_project(name)

        # Then remove from database
        project = await self.repository.get_by_name(name)
        if project:
            await self.repository.delete(project.id)

        logger.info(f"Project '{name}' removed from configuration and database")

    async def set_default_project(self, name: str) -> None:
        """Set the default project in configuration and database.

        Args:
            name: The name of the project to set as default

        Raises:
            ValueError: If the project doesn't exist
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for set_default_project")

        # First update config file (this will validate the project exists)
        self.config_manager.set_default_project(name)

        # Then update database
        project = await self.repository.get_by_name(name)
        if project:
            await self.repository.set_as_default(project.id)
        else:
            logger.error(f"Project '{name}' exists in config but not in database")

        logger.info(f"Project '{name}' set as default in configuration and database")

        # Refresh MCP session to pick up the new default project
        try:
            from basic_memory.mcp.project_session import session

            session.refresh_from_config()
        except ImportError:  # pragma: no cover
            # MCP components might not be available in all contexts (e.g., CLI-only usage)
            logger.debug("MCP session not available, skipping session refresh")

    async def _ensure_single_default_project(self) -> None:
        """Ensure only one project has is_default=True.

        This method validates the database state and fixes any issues where
        multiple projects might have is_default=True or no project is marked as default.
        """
        if not self.repository:
            raise ValueError(
                "Repository is required for _ensure_single_default_project"
            )  # pragma: no cover

        # Get all projects with is_default=True
        db_projects = await self.repository.find_all()
        default_projects = [p for p in db_projects if p.is_default is True]

        if len(default_projects) > 1:  # pragma: no cover
            # Multiple defaults found - fix by keeping the first one and clearing others
            # This is defensive code that should rarely execute due to business logic enforcement
            logger.warning(  # pragma: no cover
                f"Found {len(default_projects)} projects with is_default=True, fixing..."
            )
            keep_default = default_projects[0]  # pragma: no cover

            # Clear all defaults first, then set only the first one as default
            await self.repository.set_as_default(keep_default.id)  # pragma: no cover

            logger.info(
                f"Fixed default project conflicts, kept '{keep_default.name}' as default"
            )  # pragma: no cover

        elif len(default_projects) == 0:  # pragma: no cover
            # No default project - set the config default as default
            # This is defensive code for edge cases where no default exists
            config_default = self.config_manager.default_project  # pragma: no cover
            config_project = await self.repository.get_by_name(config_default)  # pragma: no cover
            if config_project:  # pragma: no cover
                await self.repository.set_as_default(config_project.id)  # pragma: no cover
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

        # Get all projects from database
        db_projects = await self.repository.get_active_projects()
        db_projects_by_permalink = {p.permalink: p for p in db_projects}

        # Get all projects from configuration and normalize names if needed
        config_projects = self.config_manager.projects.copy()
        updated_config = {}
        config_updated = False

        for name, path in config_projects.items():
            # Generate normalized name (what the database expects)
            normalized_name = generate_permalink(name)

            if normalized_name != name:
                logger.info(f"Normalizing project name in config: '{name}' -> '{normalized_name}'")
                config_updated = True

            updated_config[normalized_name] = path

        # Update the configuration if any changes were made
        if config_updated:
            config = self.config_manager.load_config()
            config.projects = updated_config
            self.config_manager.save_config(config)
            logger.info("Config updated with normalized project names")

        # Use the normalized config for further processing
        config_projects = updated_config

        # Add projects that exist in config but not in DB
        for name, path in config_projects.items():
            if name not in db_projects_by_permalink:
                logger.info(f"Adding project '{name}' to database")
                project_data = {
                    "name": name,
                    "path": path,
                    "permalink": generate_permalink(name),
                    "is_active": True,
                    # Don't set is_default here - let the enforcement logic handle it
                }
                await self.repository.create(project_data)

        # Add projects that exist in DB but not in config to config
        for name, project in db_projects_by_permalink.items():
            if name not in config_projects:
                logger.info(f"Adding project '{name}' to configuration")
                self.config_manager.add_project(name, project.path)

        # Ensure database default project state is consistent
        await self._ensure_single_default_project()

        # Make sure default project is synchronized between config and database
        db_default = await self.repository.get_default_project()
        config_default = self.config_manager.default_project

        if db_default and db_default.name != config_default:
            # Update config to match DB default
            logger.info(f"Updating default project in config to '{db_default.name}'")
            self.config_manager.set_default_project(db_default.name)
        elif not db_default and config_default:
            # Update DB to match config default (if the project exists)
            project = await self.repository.get_by_name(config_default)
            if project:
                logger.info(f"Updating default project in database to '{config_default}'")
                await self.repository.set_as_default(project.id)

        logger.info("Project synchronization complete")

        # Refresh MCP session to ensure it's in sync with current config
        try:
            from basic_memory.mcp.project_session import session

            session.refresh_from_config()
        except ImportError:
            # MCP components might not be available in all contexts
            logger.debug("MCP session not available, skipping session refresh")

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

        # Get project from database
        project = await self.repository.get_by_name(name)
        if not project:
            logger.error(f"Project '{name}' exists in config but not in database")
            return

        # Update path if provided
        if updated_path:
            resolved_path = os.path.abspath(os.path.expanduser(updated_path))

            # Update in config
            config = self.config_manager.load_config()
            config.projects[name] = resolved_path
            self.config_manager.save_config(config)

            # Update in database
            project.path = resolved_path
            await self.repository.update(project.id, project)

            logger.info(f"Updated path for project '{name}' to {resolved_path}")

        # Update active status if provided
        if is_active is not None:
            project.is_active = is_active
            await self.repository.update(project.id, project)
            logger.info(f"Set active status for project '{name}' to {is_active}")

        # If project was made inactive and it was the default, we need to pick a new default
        if is_active is False and project.is_default:
            # Find another active project
            active_projects = await self.repository.get_active_projects()
            if active_projects:
                new_default = active_projects[0]
                await self.repository.set_as_default(new_default.id)
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
        project_name = project_name or self.config.project
        # Get project path from configuration
        name, project_path = self.config_manager.get_project(project_name)
        if not name:  # pragma: no cover
            raise ValueError(f"Project '{project_name}' not found in configuration")

        assert project_path is not None
        project_permalink = generate_permalink(project_name)

        # Get project from database to get project_id
        db_project = await self.repository.get_by_permalink(project_permalink)
        if not db_project:  # pragma: no cover
            raise ValueError(f"Project '{project_name}' not found in database")

        # Get statistics for the specified project
        statistics = await self.get_statistics(db_project.id)

        # Get activity metrics for the specified project
        activity = await self.get_activity_metrics(db_project.id)

        # Get system status
        system = self.get_system_status()

        # Get enhanced project information from database
        db_projects = await self.repository.get_active_projects()
        db_projects_by_permalink = {p.permalink: p for p in db_projects}

        # Get default project info
        default_project = self.config_manager.default_project

        # Convert config projects to include database info
        enhanced_projects = {}
        for name, path in self.config_manager.projects.items():
            config_permalink = generate_permalink(name)
            db_project = db_projects_by_permalink.get(config_permalink)
            enhanced_projects[name] = {
                "path": path,
                "active": db_project.is_active if db_project else True,
                "id": db_project.id if db_project else None,
                "is_default": (name == default_project),
                "permalink": db_project.permalink if db_project else name.lower().replace(" ", "-"),
            }

        # Construct the response
        return ProjectInfoResponse(
            project_name=project_name,
            project_path=project_path,
            available_projects=enhanced_projects,
            default_project=default_project,
            statistics=statistics,
            activity=activity,
            system=system,
        )

    async def get_statistics(self, project_id: int) -> ProjectStatistics:
        """Get statistics about the specified project.

        Args:
            project_id: ID of the project to get statistics for (required).
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for get_statistics")

        # Get basic counts
        entity_count_result = await self.repository.execute_query(
            text("SELECT COUNT(*) FROM entity WHERE project_id = :project_id"),
            {"project_id": project_id},
        )
        total_entities = entity_count_result.scalar() or 0

        observation_count_result = await self.repository.execute_query(
            text(
                "SELECT COUNT(*) FROM observation o JOIN entity e ON o.entity_id = e.id WHERE e.project_id = :project_id"
            ),
            {"project_id": project_id},
        )
        total_observations = observation_count_result.scalar() or 0

        relation_count_result = await self.repository.execute_query(
            text(
                "SELECT COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE e.project_id = :project_id"
            ),
            {"project_id": project_id},
        )
        total_relations = relation_count_result.scalar() or 0

        unresolved_count_result = await self.repository.execute_query(
            text(
                "SELECT COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE r.to_id IS NULL AND e.project_id = :project_id"
            ),
            {"project_id": project_id},
        )
        total_unresolved = unresolved_count_result.scalar() or 0

        # Get entity counts by type
        entity_types_result = await self.repository.execute_query(
            text(
                "SELECT entity_type, COUNT(*) FROM entity WHERE project_id = :project_id GROUP BY entity_type"
            ),
            {"project_id": project_id},
        )
        entity_types = {row[0]: row[1] for row in entity_types_result.fetchall()}

        # Get observation counts by category
        category_result = await self.repository.execute_query(
            text(
                "SELECT o.category, COUNT(*) FROM observation o JOIN entity e ON o.entity_id = e.id WHERE e.project_id = :project_id GROUP BY o.category"
            ),
            {"project_id": project_id},
        )
        observation_categories = {row[0]: row[1] for row in category_result.fetchall()}

        # Get relation counts by type
        relation_types_result = await self.repository.execute_query(
            text(
                "SELECT r.relation_type, COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE e.project_id = :project_id GROUP BY r.relation_type"
            ),
            {"project_id": project_id},
        )
        relation_types = {row[0]: row[1] for row in relation_types_result.fetchall()}

        # Find most connected entities (most outgoing relations) - project filtered
        connected_result = await self.repository.execute_query(
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
            entity_types=entity_types,
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

        # Get recently created entities (project filtered)
        created_result = await self.repository.execute_query(
            text("""
            SELECT id, title, permalink, entity_type, created_at, file_path 
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
                "entity_type": row[3],
                "created_at": row[4],
                "file_path": row[5],
            }
            for row in created_result.fetchall()
        ]

        # Get recently updated entities (project filtered)
        updated_result = await self.repository.execute_query(
            text("""
            SELECT id, title, permalink, entity_type, updated_at, file_path 
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
                "entity_type": row[3],
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
        entity_growth_result = await self.repository.execute_query(
            text("""
            SELECT 
                strftime('%Y-%m', created_at) AS month,
                COUNT(*) AS count
            FROM entity
            WHERE created_at >= :six_months_ago AND project_id = :project_id
            GROUP BY month
            ORDER BY month
        """),
            {"six_months_ago": six_months_ago.isoformat(), "project_id": project_id},
        )
        entity_growth = {row[0]: row[1] for row in entity_growth_result.fetchall()}

        # Query for monthly observation creation (project filtered)
        observation_growth_result = await self.repository.execute_query(
            text("""
            SELECT 
                strftime('%Y-%m', entity.created_at) AS month,
                COUNT(*) AS count
            FROM observation
            INNER JOIN entity ON observation.entity_id = entity.id
            WHERE entity.created_at >= :six_months_ago AND entity.project_id = :project_id
            GROUP BY month
            ORDER BY month
        """),
            {"six_months_ago": six_months_ago.isoformat(), "project_id": project_id},
        )
        observation_growth = {row[0]: row[1] for row in observation_growth_result.fetchall()}

        # Query for monthly relation creation (project filtered)
        relation_growth_result = await self.repository.execute_query(
            text("""
            SELECT 
                strftime('%Y-%m', entity.created_at) AS month,
                COUNT(*) AS count
            FROM relation
            INNER JOIN entity ON relation.from_id = entity.id
            WHERE entity.created_at >= :six_months_ago AND entity.project_id = :project_id
            GROUP BY month
            ORDER BY month
        """),
            {"six_months_ago": six_months_ago.isoformat(), "project_id": project_id},
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

    def get_system_status(self) -> SystemStatus:
        """Get system status information."""
        import basic_memory

        # Get database information
        db_path = self.config_manager.config.database_path
        db_size = db_path.stat().st_size if db_path.exists() else 0
        db_size_readable = f"{db_size / (1024 * 1024):.2f} MB"

        # Get watch service status if available
        watch_status = None
        watch_status_path = Path.home() / ".basic-memory" / WATCH_STATUS_JSON
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
