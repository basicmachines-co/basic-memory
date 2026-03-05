"""V2 router for graph intelligence endpoints."""

from fastapi import APIRouter, Query

from basic_memory.deps import (
    GraphIntelligenceServiceV2ExternalDep,
    ProjectExternalIdPathDep,
    TaskSchedulerDep,
)
from basic_memory.schemas.graph_intelligence import (
    GraphHealthResponse,
    GraphImpactRequest,
    GraphImpactResponse,
    GraphLineageRequest,
    GraphLineageResponse,
    GraphReindexRequest,
    GraphReindexResponse,
)

router = APIRouter(prefix="/graph", tags=["graph-v2"])


@router.post("/lineage", response_model=GraphLineageResponse)
async def graph_lineage(
    request: GraphLineageRequest,
    graph_service: GraphIntelligenceServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
) -> GraphLineageResponse:
    """Build lineage paths from a start node toward an optional goal."""
    _ = project_id
    return await graph_service.lineage(request)


@router.post("/impact", response_model=GraphImpactResponse)
async def graph_impact(
    request: GraphImpactRequest,
    graph_service: GraphIntelligenceServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
) -> GraphImpactResponse:
    """Compute impact radius from a target node."""
    _ = project_id
    return await graph_service.impact(request)


@router.get("/health", response_model=GraphHealthResponse)
async def graph_health(
    graph_service: GraphIntelligenceServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
    scope: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
) -> GraphHealthResponse:
    """Report graph quality metrics and issue candidates."""
    _ = project_id
    return await graph_service.health(scope=scope, timeframe=timeframe)


@router.post("/reindex", response_model=GraphReindexResponse)
async def graph_reindex(
    request: GraphReindexRequest,
    graph_service: GraphIntelligenceServiceV2ExternalDep,
    task_scheduler: TaskSchedulerDep,
    project_id: ProjectExternalIdPathDep,
) -> GraphReindexResponse:
    """Queue a graph reindex operation for the current project."""
    task_scheduler.schedule(
        "reindex_graph_project",
        project_id=project_id,
        mode=request.mode,
        reason=request.reason,
    )
    return await graph_service.start_reindex_job()
