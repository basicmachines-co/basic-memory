"""Schema for project info response."""

from datetime import datetime
from typing import Dict, List, Optional, Any

from pydantic import Field, BaseModel


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
    available_projects: Dict[str, str] = Field(
        description="Map of configured project names to paths"
    )
    default_project: str = Field(description="Name of the default project")

    # Statistics
    statistics: ProjectStatistics = Field(description="Statistics about the knowledge base")

    # Activity metrics
    activity: ActivityMetrics = Field(description="Activity and growth metrics")

    # System status
    system: SystemStatus = Field(description="System and service status information")
