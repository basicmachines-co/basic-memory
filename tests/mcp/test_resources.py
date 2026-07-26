import pytest

from basic_memory.mcp.prompts.ai_assistant_guide import ai_assistant_guide
from basic_memory.mcp.resources.project_info import project_info
from basic_memory.mcp.server import mcp


def test_ai_assistant_guide_exists():
    """The bundled guide should orient agents and teach progressive docs discovery."""
    guide = ai_assistant_guide()

    assert "# AI Assistant Guide" in guide
    assert "recent_activity" in guide
    assert "project_id" in guide
    assert "directory" in guide
    assert "https://docs.basicmemory.com/llms.txt" in guide
    assert "https://docs.basicmemory.com/raw/...md" in guide
    assert "https://docs.basicmemory.com/llms-full.txt" in guide
    assert "input schemas exposed by this MCP server are authoritative" in guide


@pytest.mark.asyncio
async def test_project_info_resource_is_registered():
    """The legacy project-info URI remains available to existing MCP clients."""
    assert project_info is not None
    templates = await mcp.list_resource_templates()

    assert "memory://{project}/info" in {str(template.uri_template) for template in templates}


@pytest.mark.asyncio
async def test_project_info_resource_reads_project(client, test_project):
    """The retained resource still resolves and returns project information."""
    info = await project_info(project=test_project.name)

    assert info.project_name == test_project.name
