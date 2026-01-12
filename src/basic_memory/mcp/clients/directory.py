"""Typed client for directory API operations.

Encapsulates all /v2/projects/{project_id}/directory/* endpoints.
"""

from typing import Optional, Any

from httpx import AsyncClient

from basic_memory.mcp.tools.utils import call_get


class DirectoryClient:
    """Typed client for directory listing operations.

    Centralizes:
    - API path construction for /v2/projects/{project_id}/directory/*
    - Response validation
    - Consistent error handling through call_* utilities

    Usage:
        async with get_client() as http_client:
            client = DirectoryClient(http_client, project_id)
            nodes = await client.list("/", depth=2)
    """

    def __init__(self, http_client: AsyncClient, project_id: str):
        """Initialize the directory client.

        Args:
            http_client: HTTPX AsyncClient for making requests
            project_id: Project external_id (UUID) for API calls
        """
        self.http_client = http_client
        self.project_id = project_id
        self._base_path = f"/v2/projects/{project_id}/directory"

    async def list(
        self,
        dir_name: str = "/",
        *,
        depth: int = 1,
        file_name_glob: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List directory contents.

        Args:
            dir_name: Directory path to list (default: root)
            depth: How deep to traverse (default: 1)
            file_name_glob: Optional glob pattern to filter files

        Returns:
            List of directory nodes with their contents

        Raises:
            ToolError: If the request fails
        """
        params: dict = {
            "dir_name": dir_name,
            "depth": depth,
        }
        if file_name_glob:
            params["file_name_glob"] = file_name_glob

        response = await call_get(
            self.http_client,
            f"{self._base_path}/list",
            params=params,
        )
        return response.json()
