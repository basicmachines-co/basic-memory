"""Typed client for FCM API operations."""

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_post
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


class FCMClient:
    """Typed client for FCM operations."""

    def __init__(self, http_client: AsyncClient, project_id: str):
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/fcm"

    async def simulate(self, request: FCMSimulateRequest) -> FCMSimulateResponse:
        response = await call_post(
            self.http_client,
            f"{self._base_path}/simulate",
            json=request.model_dump(mode="json"),
        )
        return FCMSimulateResponse.model_validate(response.json())

    async def rank_actions(self, request: FCMRankActionsRequest) -> FCMRankActionsResponse:
        response = await call_post(
            self.http_client,
            f"{self._base_path}/rank-actions",
            json=request.model_dump(mode="json"),
        )
        return FCMRankActionsResponse.model_validate(response.json())

    async def import_model(self, request: FCMImportRequest) -> FCMImportResponse:
        response = await call_post(
            self.http_client,
            f"{self._base_path}/import",
            json=request.model_dump(mode="json"),
        )
        return FCMImportResponse.model_validate(response.json())

    async def export_model(self, request: FCMExportRequest) -> FCMExportResponse:
        response = await call_post(
            self.http_client,
            f"{self._base_path}/export",
            json=request.model_dump(mode="json"),
        )
        return FCMExportResponse.model_validate(response.json())
