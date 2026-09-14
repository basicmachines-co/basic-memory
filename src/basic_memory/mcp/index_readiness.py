"""Index-required guidance shared by MCP discovery tools."""

from httpx import AsyncClient

from basic_memory.schemas.project_info import ProjectItem
from basic_memory.schemas.project_readiness import ProjectIndexPhase
from basic_memory.utils import shell_command


async def project_index_required(client: AsyncClient, project: ProjectItem) -> str | None:
    """Distinguish an unsearched project from an honest empty result.

    Call only after an empty read: the status endpoint observes project files,
    so successful reads should not pay for an extra directory scan. Let status
    failures propagate rather than interpreting unknown readiness as an empty index.
    """
    # Match the tools' deferred client imports to keep CLI startup lightweight.
    from basic_memory.mcp.clients.project import ProjectClient

    status = await ProjectClient(client).get_status(project.external_id)
    readiness = status.readiness
    if readiness.phase is not ProjectIndexPhase.NEVER_INDEXED:
        return None

    # ProjectItem carries no routing mode. Label both remedies explicitly instead
    # of guessing from local config, which cannot identify a hosted factory route.
    local = readiness.describe(
        project.name, index_command=shell_command("bm", "project", "index", project.name)
    )
    cloud = readiness.describe(project.name, index_command=None)
    return (
        "# Project Index Required\n\n"
        f"Project '{project.name}' has never been indexed. You need to index it before "
        "an empty result can establish that no notes match.\n\n"
        f"- For a local project: {local}.\n"
        f"- For a cloud project: {cloud}; wait for server-side indexing before retrying."
    )
