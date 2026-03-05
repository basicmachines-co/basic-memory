"""Typed client for graph intelligence API operations."""

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_get, call_post
from basic_memory.schemas.graph_intelligence import (
    GraphHealthResponse,
    GraphImpactRequest,
    GraphImpactResponse,
    GraphLineageRequest,
    GraphLineageResponse,
    GraphReindexRequest,
    GraphReindexResponse,
)


class GraphClient:
    """Typed client for graph intelligence operations."""

    def __init__(self, http_client: AsyncClient, project_id: str):
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/graph"

    async def lineage(self, request: GraphLineageRequest) -> GraphLineageResponse:
        response = await call_post(
            self.http_client,
            f"{self._base_path}/lineage",
            json=request.model_dump(mode="json"),
        )
        return GraphLineageResponse.model_validate(response.json())

    async def impact(self, request: GraphImpactRequest) -> GraphImpactResponse:
        response = await call_post(
            self.http_client,
            f"{self._base_path}/impact",
            json=request.model_dump(mode="json"),
        )
        return GraphImpactResponse.model_validate(response.json())

    async def health(
        self, scope: str | None = None, timeframe: str | None = None
    ) -> GraphHealthResponse:
        params: dict[str, str] = {}
        if scope is not None:
            params["scope"] = scope
        if timeframe is not None:
            params["timeframe"] = timeframe
        response = await call_get(
            self.http_client,
            f"{self._base_path}/health",
            params=params,
        )
        return GraphHealthResponse.model_validate(response.json())

    async def reindex(self, request: GraphReindexRequest) -> GraphReindexResponse:
        response = await call_post(
            self.http_client,
            f"{self._base_path}/reindex",
            json=request.model_dump(mode="json"),
        )
        return GraphReindexResponse.model_validate(response.json())
