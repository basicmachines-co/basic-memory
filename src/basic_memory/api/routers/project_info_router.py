"""Router for statistics and system information."""

import json
from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import text

from basic_memory.config import config, config_manager
from basic_memory.deps import (
    ProjectInfoRepositoryDep,
)
from basic_memory.repository.project_info_repository import ProjectInfoRepository
from basic_memory.schemas import (
    ProjectInfoResponse,
    ProjectStatistics,
    ActivityMetrics,
    SystemStatus,
)
from basic_memory.sync.watch_service import WATCH_STATUS_JSON

router = APIRouter(prefix="/stats", tags=["statistics"])


@router.get("/project-info", response_model=ProjectInfoResponse)
async def get_project_info(
    repository: ProjectInfoRepositoryDep,
) -> ProjectInfoResponse:
    """Get comprehensive information about the current Basic Memory project."""
    # Get statistics
    statistics = await get_statistics(repository)

    # Get activity metrics
    activity = await get_activity_metrics(repository)

    # Get system status
    system = await get_system_status()

    # Get project configuration information
    project_name = config.project
    project_path = str(config.home)
    available_projects = config_manager.projects
    default_project = config_manager.default_project

    # Construct the response
    return ProjectInfoResponse(
        project_name=project_name,
        project_path=project_path,
        available_projects=available_projects,
        default_project=default_project,
        statistics=statistics,
        activity=activity,
        system=system,
    )


async def get_statistics(repository: ProjectInfoRepository) -> ProjectStatistics:
    """Get statistics about the current project."""
    # Get basic counts
    entity_count_result = await repository.execute_query(text("SELECT COUNT(*) FROM entity"))
    total_entities = entity_count_result.scalar() or 0

    observation_count_result = await repository.execute_query(
        text("SELECT COUNT(*) FROM observation")
    )
    total_observations = observation_count_result.scalar() or 0

    relation_count_result = await repository.execute_query(text("SELECT COUNT(*) FROM relation"))
    total_relations = relation_count_result.scalar() or 0

    unresolved_count_result = await repository.execute_query(
        text("SELECT COUNT(*) FROM relation WHERE to_id IS NULL")
    )
    total_unresolved = unresolved_count_result.scalar() or 0

    # Get entity counts by type
    entity_types_result = await repository.execute_query(
        text("SELECT entity_type, COUNT(*) FROM entity GROUP BY entity_type")
    )
    entity_types = {row[0]: row[1] for row in entity_types_result.fetchall()}

    # Get observation counts by category
    category_result = await repository.execute_query(
        text("SELECT category, COUNT(*) FROM observation GROUP BY category")
    )
    observation_categories = {row[0]: row[1] for row in category_result.fetchall()}

    # Get relation counts by type
    relation_types_result = await repository.execute_query(
        text("SELECT relation_type, COUNT(*) FROM relation GROUP BY relation_type")
    )
    relation_types = {row[0]: row[1] for row in relation_types_result.fetchall()}

    # Find most connected entities (most outgoing relations)
    connected_result = await repository.execute_query(
        text("""
        SELECT e.id, e.title, e.permalink, COUNT(r.id) AS relation_count
        FROM entity e
        JOIN relation r ON e.id = r.from_id
        GROUP BY e.id
        ORDER BY relation_count DESC
        LIMIT 10
    """)
    )
    most_connected = [
        {"id": row[0], "title": row[1], "permalink": row[2], "relation_count": row[3]}
        for row in connected_result.fetchall()
    ]

    # Count isolated entities (no relations)
    isolated_result = await repository.execute_query(
        text("""
        SELECT COUNT(e.id)
        FROM entity e
        LEFT JOIN relation r1 ON e.id = r1.from_id
        LEFT JOIN relation r2 ON e.id = r2.to_id
        WHERE r1.id IS NULL AND r2.id IS NULL
    """)
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


async def get_activity_metrics(repository: ProjectInfoRepository) -> ActivityMetrics:
    """Get activity metrics for the current project."""
    # Get recently created entities
    created_result = await repository.execute_query(
        text("""
        SELECT id, title, permalink, entity_type, created_at 
        FROM entity
        ORDER BY created_at DESC
        LIMIT 10
    """)
    )
    recently_created = [
        {
            "id": row[0],
            "title": row[1],
            "permalink": row[2],
            "entity_type": row[3],
            "created_at": row[4],
        }
        for row in created_result.fetchall()
    ]

    # Get recently updated entities
    updated_result = await repository.execute_query(
        text("""
        SELECT id, title, permalink, entity_type, updated_at 
        FROM entity
        ORDER BY updated_at DESC
        LIMIT 10
    """)
    )
    recently_updated = [
        {
            "id": row[0],
            "title": row[1],
            "permalink": row[2],
            "entity_type": row[3],
            "updated_at": row[4],
        }
        for row in updated_result.fetchall()
    ]

    # Get monthly growth over the last 6 months
    # Calculate the start of 6 months ago
    now = datetime.now()
    six_months_ago = datetime(
        now.year - (1 if now.month <= 6 else 0), ((now.month - 6) % 12) or 12, 1
    )

    # Query for monthly entity creation
    entity_growth_result = await repository.execute_query(
        text(f"""
        SELECT 
            strftime('%Y-%m', created_at) AS month,
            COUNT(*) AS count
        FROM entity
        WHERE created_at >= '{six_months_ago.isoformat()}'
        GROUP BY month
        ORDER BY month
    """)
    )
    entity_growth = {row[0]: row[1] for row in entity_growth_result.fetchall()}

    # Query for monthly observation creation
    observation_growth_result = await repository.execute_query(
        text(f"""
        SELECT 
            strftime('%Y-%m', created_at) AS month,
            COUNT(*) AS count
        FROM observation
        INNER JOIN entity ON observation.entity_id = entity.id
        WHERE entity.created_at >= '{six_months_ago.isoformat()}'
        GROUP BY month
        ORDER BY month
    """)
    )
    observation_growth = {row[0]: row[1] for row in observation_growth_result.fetchall()}

    # Query for monthly relation creation
    relation_growth_result = await repository.execute_query(
        text(f"""
        SELECT 
            strftime('%Y-%m', created_at) AS month,
            COUNT(*) AS count
        FROM relation
        INNER JOIN entity ON relation.from_id = entity.id
        WHERE entity.created_at >= '{six_months_ago.isoformat()}'
        GROUP BY month
        ORDER BY month
    """)
    )
    relation_growth = {row[0]: row[1] for row in relation_growth_result.fetchall()}

    # Combine all monthly growth data
    monthly_growth = {}
    for month in set(
        list(entity_growth.keys()) + list(observation_growth.keys()) + list(relation_growth.keys())
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


async def get_system_status() -> SystemStatus:
    """Get system status information."""
    import basic_memory

    # Get database information
    db_path = config.database_path
    db_size = db_path.stat().st_size if db_path.exists() else 0
    db_size_readable = f"{db_size / (1024 * 1024):.2f} MB"

    # Get watch service status if available
    watch_status = None
    watch_status_path = config.home / ".basic-memory" / WATCH_STATUS_JSON
    if watch_status_path.exists():
        try:
            watch_status = json.loads(watch_status_path.read_text())
        except Exception:  # pragma: no cover
            pass

    return SystemStatus(
        version=basic_memory.__version__,
        database_path=str(db_path),
        database_size=db_size_readable,
        watch_status=watch_status,
        timestamp=datetime.now(),
    )
