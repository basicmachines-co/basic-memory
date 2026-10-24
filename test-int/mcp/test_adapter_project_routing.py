"""Native adapters select a project before calling the explicit-routing tools."""

import json

import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from mcp import types as mt

from basic_memory.config import ConfigManager


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
    mcp_server, app, test_project, tmp_path, prompt_name, explicit_project
):
    async with Client(mcp_server) as client:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("id_form", ["search_hit", "relative_url", "qualified", "qualified_url"])
async def test_connector_fetch_routes_relative_and_qualified_ids(
    mcp_server, app, test_project, app_config, tmp_path, id_form
):
    app_config.permalinks_include_project = False
    ConfigManager().save_config(app_config)
    async with Client(
        mcp_server, client_info=mt.Implementation(name="openai-mcp", version="1.0.0")
    ) as client:
        await seed_projects(
            client, test_project.name, str(tmp_path.parent / (tmp_path.name + "-other"))
        )
        search = await client.call_tool("search", {"query": "Routing Topic"})
        search_items = json.loads(search.content[0].text)  # pyright: ignore[reportAttributeAccessIssue]
        hits = json.loads(search_items[0]["text"])
        identifier = hits["results"][0]["id"]
        assert identifier == "notes/routing-topic"
        if id_form in {"qualified", "qualified_url"}:
            identifier = f"other/{identifier}"
        if id_form in {"relative_url", "qualified_url"}:
            identifier = f"memory://{identifier}"
        fetched = await client.call_tool("fetch", {"id": identifier})
        fetched_items = json.loads(fetched.content[0].text)  # pyright: ignore[reportAttributeAccessIssue]
        document = json.loads(fetched_items[0]["text"])
        expected = "OtherOnlyMarker" if id_form.startswith("qualified") else "DefaultOnlyMarker"
        assert document["metadata"] == {"format": "markdown"}
        assert expected in document["text"]
