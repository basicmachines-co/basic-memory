"""Schema for project info response."""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from pydantic import Field, BaseModel

from basic_memory.utils import generate_permalink


class ProjectStatistics(BaseModel):
    """Statistics about the current project."""

    # Basic counts
    total_entities: int = Field(description="Total number of entities in the knowledge base")
    total_observations: int = Field(description="Total number of observations across all entities")
    total_relations: int = Field(description="Total number of relations between entities")
    total_unresolved_relations: int = Field(
        description="Number of relations with unresolved targets"
    )

    # Entity counts by type
    entity_types: Dict[str, int] = Field(
        description="Count of entities by type (e.g., note, conversation)"
    )

    # Observation counts by category
    observation_categories: Dict[str, int] = Field(
        description="Count of observations by category (e.g., tech, decision)"
    )

    # Relation counts by type
    relation_types: Dict[str, int] = Field(
        description="Count of relations by type (e.g., implements, relates_to)"
    )

    # Graph metrics
    most_connected_entities: List[Dict[str, Any]] = Field(
        description="Entities with the most relations, including their titles and permalinks"
    )
    isolated_entities: int = Field(description="Number of entities with no relations")


class ActivityMetrics(BaseModel):
    """Activity metrics for the current project."""

    # Recent activity
    recently_created: List[Dict[str, Any]] = Field(
        description="Recently created entities with timestamps"
    )
    recently_updated: List[Dict[str, Any]] = Field(
        description="Recently updated entities with timestamps"
    )

    # Growth over time (last 6 months)
    monthly_growth: Dict[str, Dict[str, int]] = Field(
        description="Monthly growth statistics for entities, observations, and relations"
    )


class SystemStatus(BaseModel):
    """System status information."""

    # Version information
    version: str = Field(description="Basic Memory version")

    # Database status
    database_path: str = Field(description="Path to the SQLite database")
    database_size: str = Field(description="Size of the database in human-readable format")

    # Watch service status
    watch_status: Optional[Dict[str, Any]] = Field(
        default=None, description="Watch service status information (if running)"
    )

    # System information
    timestamp: datetime = Field(description="Timestamp when the information was collected")


class ProjectInfoResponse(BaseModel):
    """Response for the project_info tool."""

    # Project configuration
    project_name: str = Field(description="Name of the current project")
    project_path: str = Field(description="Path to the current project files")
    available_projects: Dict[str, Dict[str, Any]] = Field(
        description="Map of configured project names to detailed project information"
    )
    default_project: str = Field(description="Name of the default project")

    # Statistics
    statistics: ProjectStatistics = Field(description="Statistics about the knowledge base")

    # Activity metrics
    activity: ActivityMetrics = Field(description="Activity and growth metrics")

    # System status
    system: SystemStatus = Field(description="System and service status information")


class ProjectInfoRequest(BaseModel):
    """Request model for switching projects."""

    name: str = Field(..., description="Name of the project to switch to")
    path: str = Field(..., description="Path to the project directory")
    set_default: bool = Field(..., description="Set the project as the default")


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
    start_time: datetime = datetime.now()  # Use directly with Pydantic model
    pid: int = os.getpid()  # Use directly with Pydantic model

    # Stats
    error_count: int = 0
    last_error: Optional[datetime] = None
    last_scan: Optional[datetime] = None

    # File counts
    synced_files: int = 0

    # Recent activity
    recent_events: List[WatchEvent] = []  # Use directly with Pydantic model

    def add_event(
        self,
        path: str,
        action: str,
        status: str,
        checksum: Optional[str] = None,
        error: Optional[str] = None,
    ) -> WatchEvent:  # pragma: no cover
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

    def record_error(self, error: str):  # pragma: no cover
        self.error_count += 1
        self.add_event(path="", action="sync", status="error", error=error)
        self.last_error = datetime.now()


class ProjectWatchStatus(BaseModel):
    """Project with its watch status."""

    name: str = Field(..., description="Name of the project")
    path: str = Field(..., description="Path to the project")
    watch_status: Optional[WatchServiceState] = Field(
        None, description="Watch status information for the project"
    )


class ProjectItem(BaseModel):
    """Simple representation of a project."""

    id: int
    name: str
    path: str
    is_default: bool = False

    @property
    def permalink(self) -> str:  # pragma: no cover
        return generate_permalink(self.name)

    @property
    def home(self) -> Path:  # pragma: no cover
        return Path(self.path).expanduser()

    @property
    def project_url(self) -> str:  # pragma: no cover
        return f"/{generate_permalink(self.name)}"


class ProjectList(BaseModel):
    """Response model for listing projects."""

    projects: List[ProjectItem]
    default_project: str


class ProjectStatusResponse(BaseModel):
    """Response model for switching projects."""

    message: str = Field(..., description="Status message about the project switch")
    status: str = Field(..., description="Status of the switch (success or error)")
    default: bool = Field(..., description="True if the project was set as the default")
    old_project: Optional[ProjectItem] = Field(
        None, description="Information about the project being switched from"
    )
    new_project: Optional[ProjectItem] = Field(
        None, description="Information about the project being switched to"
    )
