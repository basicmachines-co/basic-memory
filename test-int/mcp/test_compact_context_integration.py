"""Graph discovery can omit prose and retain usable note identities."""

import json

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_compact_context_preserves_graph_navigation(mcp_server, app, test_project):
    body = "Long context body. " * 500 + "\n- [fact] " + "Detailed observation. " * 100
    async with Client(mcp_server) as client:
        for title, content in [
            ("Context Target", "Target body"),
            ("Context Source", body + "\n- relates_to [[Context Target]]"),
        ]:
            await client.call_tool(
                "write_note",
                {
                    "title": title,
                    "content": content,
                    "directory": "context",
                    "project": test_project.name,
                },
            )
        arguments = {
            "url": "context/context-source",
            "project": test_project.name,
            "depth": 2,
        }
        full_result = await client.call_tool("build_context", arguments)
        compact_result = await client.call_tool("build_context", {**arguments, "compact": True})
        full = json.loads(full_result.content[0].text)
        compact = json.loads(compact_result.content[0].text)
        assert full["results"]
        assert full["results"][0]["observations"]
        assert any(row["type"] == "relation" for row in full["results"][0]["related_results"])
        # Request-time timestamps vary between calls; graph and page data must not.
        for field in ("generated_at", "timeframe"):
            full["metadata"].pop(field, None)
            compact["metadata"].pop(field, None)
        for result in full["results"]:
            result["primary_result"].pop("content", None)
            for item in [*result["observations"], *result["related_results"]]:
                item.pop("content", None)
        assert compact == full
        assert len(compact_result.content[0].text) < len(full_result.content[0].text) / 2
        selected = compact["results"][0]["primary_result"]
        read = await client.call_tool(
            "read_note", {"identifier": selected["external_id"], "project": test_project.name}
        )
        assert body in read.content[0].text
        text_result = await client.call_tool(
            "build_context", {**arguments, "compact": True, "output_format": "text"}
        )
        assert "Context Source" in text_result.content[0].text
        assert "Long context body" not in text_result.content[0].text
        assert "### Observations" not in text_result.content[0].text
