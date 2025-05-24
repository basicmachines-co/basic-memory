from basic_memory.config import get_project_config
from basic_memory.mcp.tools.utils import call_delete


from basic_memory.mcp.server import mcp
from basic_memory.mcp.async_client import client
from basic_memory.schemas import DeleteEntitiesResponse


@mcp.tool(description="Delete a note by title or permalink")
async def delete_note(identifier: str) -> bool:
    """Delete a note from the knowledge base.

    Args:
        identifier: Note title or permalink

    Returns:
        True if note was deleted, False otherwise

    Examples:
        # Delete by title
        delete_note("Meeting Notes: Project Planning")

        # Delete by permalink
        delete_note("notes/project-planning")
    """
    project_url = get_project_config().project_url

    response = await call_delete(client, f"{project_url}/knowledge/entities/{identifier}")
    result = DeleteEntitiesResponse.model_validate(response.json())
    return result.deleted
