"""V2 router for FCM simulation and interop endpoints."""

from fastapi import APIRouter

from basic_memory.deps import FCMServiceV2ExternalDep, ProjectExternalIdPathDep
from basic_memory.schemas.graph_intelligence import (
    FCMExportRequest,
    FCMExportResponse,
    FCMImportRequest,
    FCMImportResponse,
    FCMRankActionsRequest,
    FCMRankActionsResponse,
    FCMSimulateRequest,
    FCMSimulateResponse,
)

router = APIRouter(prefix="/fcm", tags=["fcm-v2"])


@router.post("/simulate", response_model=FCMSimulateResponse)
async def fcm_simulate(
    request: FCMSimulateRequest,
    fcm_service: FCMServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
) -> FCMSimulateResponse:
    """Run an FCM scenario simulation."""
    _ = project_id
    return await fcm_service.simulate(request)


@router.post("/rank-actions", response_model=FCMRankActionsResponse)
async def fcm_rank_actions(
    request: FCMRankActionsRequest,
    fcm_service: FCMServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
) -> FCMRankActionsResponse:
    """Rank action candidates toward a goal."""
    _ = project_id
    return await fcm_service.rank_actions(request)


@router.post("/import", response_model=FCMImportResponse)
async def fcm_import(
    request: FCMImportRequest,
    fcm_service: FCMServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
) -> FCMImportResponse:
    """Import an FCM model using a supported interchange format."""
    _ = project_id
    return await fcm_service.import_model(request)


@router.post("/export", response_model=FCMExportResponse)
async def fcm_export(
    request: FCMExportRequest,
    fcm_service: FCMServiceV2ExternalDep,
    project_id: ProjectExternalIdPathDep,
) -> FCMExportResponse:
    """Export an FCM model using a supported interchange format."""
    _ = project_id
    return await fcm_service.export_model(request)
