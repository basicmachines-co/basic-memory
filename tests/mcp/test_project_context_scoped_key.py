"""Project-context regressions for project-scoped cloud credentials."""

from typing import Any, cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.project_context import resolve_project_and_path
from basic_memory.schemas.project_info import ProjectItem


class ContextState:
    """Minimal FastMCP state surface used by project routing."""

    def __init__(self) -> None:
        self.state: dict[str, object] = {}

    async def get_state(self, key: str) -> object | None:
        return self.state.get(key)

    async def set_state(self, key: str, value: object) -> None:
        self.state[key] = value


@pytest.mark.asyncio
async def test_read_route_falls_back_when_project_prefix_is_hidden_by_scope(
    config_manager,
    monkeypatch,
) -> None:
    """A note directory must not become a forbidden cross-project lookup."""
    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    active_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="ci-acceptance",
        path="/tmp/ci-acceptance",
        is_default=False,
    )
    await context.set_state("active_project", active_project.model_dump())

    async def reject_directory_as_project(*args: object, **kwargs: object) -> None:
        raise ToolError("This API key does not have access to this project")

    monkeypatch.setattr(
        "basic_memory.mcp.tools.utils.call_post",
        reject_directory_as_project,
    )

    resolved_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://acceptance/mcp/cloud-smoke",
        project="ci-acceptance",
        context=cast(Any, context),
    )

    assert resolved_project == active_project
    assert resolved_path == "ci-acceptance/acceptance/mcp/cloud-smoke"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_mutating_route_keeps_scope_rejection_fail_closed(
    config_manager,
    monkeypatch,
) -> None:
    """Strict mutation routing must not reinterpret a forbidden route as a path."""
    context = ContextState()
    active_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="ci-acceptance",
        path="/tmp/ci-acceptance",
        is_default=False,
    )
    await context.set_state("active_project", active_project.model_dump())

    async def reject_project_route(*args: object, **kwargs: object) -> None:
        raise ToolError("This API key does not have access to this project")

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", reject_project_route)

    with pytest.raises(ToolError, match="does not have access"):
        await resolve_project_and_path(
            client=cast(Any, None),
            identifier="memory://other-project/cloud-smoke",
            project="ci-acceptance",
            context=cast(Any, context),
            strict_project_routing=True,
        )


@pytest.mark.asyncio
async def test_read_route_requires_an_authorized_cached_project(
    config_manager,
    monkeypatch,
) -> None:
    """An access denial without an established project must still propagate."""
    context = ContextState()

    async def reject_project_route(*args: object, **kwargs: object) -> None:
        raise ToolError("This API key does not have access to this project")

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", reject_project_route)

    with pytest.raises(ToolError, match="does not have access"):
        await resolve_project_and_path(
            client=cast(Any, None),
            identifier="memory://other-project/cloud-smoke",
            project="ci-acceptance",
            context=cast(Any, context),
        )
