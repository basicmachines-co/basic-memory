"""Typed client for schema API operations.

Encapsulates all /v2/projects/{project_id}/schema/* endpoints.
"""

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_post, call_get
from basic_memory.schemas.schema import (
    ValidationReport,
    InferenceReport,
    DriftReport,
)


class SchemaClient:
    """Typed client for schema operations.

    Centralizes:
    - API path construction for /v2/projects/{project_id}/schema/*
    - Response validation via Pydantic models
    - Consistent error handling through call_* utilities

    Usage:
        async with get_client() as http_client:
            client = SchemaClient(http_client, project_id)
            report = await client.validate(note_type="person")
    """

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize the schema client.

        Args:
            http_client: HTTPX AsyncClient for making requests
            project_id: Project external_id (UUID) for API calls
        """
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/schema"

    async def validate(
        self,
        *,
        note_type: str | None = None,
        identifier: str | None = None,
    ) -> ValidationReport:
        """Validate notes against their resolved schemas.

        Args:
            note_type: Optional note type to batch-validate
            identifier: Optional specific note to validate

        Returns:
            ValidationReport with per-note results

        Raises:
            ToolError: If the request fails
        """
        params: dict[str, str] = {}
        if note_type:
            params["note_type"] = note_type
        if identifier:
            params["identifier"] = identifier

        response = await call_post(
            self.http_client,
            f"{self._base_path}/validate",
            params=params,
        )
        return ValidationReport.model_validate(response.json())

    async def infer(
        self,
        note_type: str,
        *,
        threshold: float = 0.25,
    ) -> InferenceReport:
        """Infer a schema from existing notes of a given type.

        Args:
            note_type: The note type to analyze
            threshold: Minimum frequency for optional fields (0-1)

        Returns:
            InferenceReport with frequency data and suggested schema

        Raises:
            ToolError: If the request fails
        """
        response = await call_post(
            self.http_client,
            f"{self._base_path}/infer",
            params={"note_type": note_type, "threshold": threshold},
        )
        return InferenceReport.model_validate(response.json())

    async def diff(self, note_type: str) -> DriftReport:
        """Show drift between schema definition and actual usage.

        Args:
            note_type: The note type to check for drift

        Returns:
            DriftReport with detected differences

        Raises:
            ToolError: If the request fails
        """
        response = await call_get(
            self.http_client,
            f"{self._base_path}/diff/{note_type}",
        )
        return DriftReport.model_validate(response.json())
