"""Project-index route command dependency tests."""

from pathlib import Path

import pytest

from basic_memory.api.v2.routers.project_router import index_project
from basic_memory.config import ProjectConfig
from basic_memory.index.local_project import ProjectIndexRouteRequest


class RecordingProjectIndexCommand:
    def __init__(self) -> None:
        self.request: ProjectIndexRouteRequest | None = None

    async def index_project(self, request: ProjectIndexRouteRequest) -> dict[str, str]:
        self.request = request
        return {"status": "delegated"}


@pytest.mark.asyncio
async def test_project_index_route_delegates_to_command_dependency() -> None:
    """Project indexing routes should delegate branching to one overrideable command."""
    command = RecordingProjectIndexCommand()

    response = await index_project(
        project_index_command=command,
        project_config=ProjectConfig(name="moby-dick", home=Path("/tmp/moby-dick")),
        project_internal_id=5,
        force_full=True,
        run_in_background=False,
    )

    assert response == {"status": "delegated"}
    assert command.request is not None
    assert command.request.project_id == 5
    assert command.request.project_name == "moby-dick"
    assert command.request.force_full is True
    assert command.request.run_in_background is False
