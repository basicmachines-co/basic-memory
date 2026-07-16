"""Typed client for directory API operations.

Encapsulates all /v2/projects/{project_id}/directory/* endpoints.
"""

from typing import Optional

from httpx import AsyncClient

from basic_memory.schemas.directory import DEFAULT_DIRECTORY_PAGE_SIZE, DirectoryListResponse

# call_* helpers live in basic_memory.mcp.tools.utils; importing that at module
# level executes the whole tools package (fastmcp + mcp SDK) during CLI startup,
# so each method defers the import to call time instead (#886).


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
        page: int = 1,
        page_size: int = DEFAULT_DIRECTORY_PAGE_SIZE,
    ) -> DirectoryListResponse:
        """List directory contents.

        Args:
            dir_name: Directory path to list (default: root)
            depth: How deep to traverse (default: 1)
            file_name_glob: Optional glob pattern to filter files
            page: One-indexed result page
            page_size: Number of nodes per page

        Returns:
            Bounded directory nodes with pagination metadata

        Raises:
            ToolError: If the request fails
        """
        from basic_memory.mcp.tools.utils import call_get

        params: dict = {
            "dir_name": dir_name,
            "depth": depth,
            "page": page,
            "page_size": page_size,
        }
        if file_name_glob:
            params["file_name_glob"] = file_name_glob

        response = await call_get(
            self.http_client,
            f"{self._base_path}/list",
            params=params,
        )
        return DirectoryListResponse.model_validate(response.json())
