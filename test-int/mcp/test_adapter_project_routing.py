"""Native adapters select a project before calling the explicit-routing tools."""

import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from mcp import types as mt


import implementation_mcp

implementation_server = implementation_mcp.implementation_server


async def seed_projects(
    client: Client[FastMCPTransport], default_project: str, other_path: str
) -> None:
    await client.call_tool("bm_project", {"command": "create", "name": "other", "path": other_path})
    for project, marker in ((default_project, "DefaultOnlyMarker"), ("other", "OtherOnlyMarker")):
        await client.call_tool(
            "bm_write",
            {
                "project": project,
                "title": "Routing Topic",
                "directory": "notes",
                "content": f"# Routing Topic\n\n{marker}",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_name", ["search_knowledge_base", "continue_conversation"])
@pytest.mark.parametrize("explicit_project", [False, True])
async def test_prompts_select_project_with_multiple_projects(
    implementation_server, app, test_project, tmp_path, prompt_name, explicit_project
):
    async with Client(implementation_server) as client:
        await seed_projects(
            client, test_project.name, str(tmp_path.parent / (tmp_path.name + "-other"))
        )
        arguments = {
            "query" if prompt_name == "search_knowledge_base" else "topic": "Routing Topic"
        }
        if explicit_project:
            arguments["project"] = "other"
        result = await client.get_prompt(prompt_name, arguments)
        text = "\n".join(
            message.content.text
            for message in result.messages
            if isinstance(message.content, mt.TextContent)
        )
        expected = "OtherOnlyMarker" if explicit_project else "DefaultOnlyMarker"
        excluded = "DefaultOnlyMarker" if explicit_project else "OtherOnlyMarker"
        assert expected in text
        assert excluded not in text
