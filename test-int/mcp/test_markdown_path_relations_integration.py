"""Markdown links reach the project graph without changing authored content."""

from pathlib import Path

import pytest
from fastmcp import Client

from basic_memory import db
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.services.bulk_link_resolver import BulkLinkResolver


@pytest.mark.asyncio
async def test_markdown_paths_are_indexed_and_resolved_exactly(
    mcp_server, app, app_config, test_project, engine_factory
):
    body = "See [target](../targets/Guide.md#details) and [web](https://example.com).\n"
    async with Client(mcp_server) as client:
        for title, directory, content in [
            ("Guide", "targets", "# Guide\n\n## Details\nContent."),
            ("Source", "notes", body),
        ]:
            result = await client.call_tool(
                "write_note",
                {
                    "title": title,
                    "directory": directory,
                    "content": content,
                    "project": test_project.name,
                },
            )
            assert not result.is_error

    _, session_maker = engine_factory
    entities = EntityRepository(project_id=test_project.id)
    relations = RelationRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        target = await entities.get_by_file_path(session, "targets/Guide.md")
        assert target is not None
        edges = await relations.find_by_type(session, "links_to")
        assert [edge.to_name for edge in edges] == ["/targets/Guide.md"]
        assert edges[0].to_id == target.id
        resolved = await BulkLinkResolver(entities, app_config).resolve_relation_targets(
            [edges[0].to_name, "/Guide", "/targets/guide.md"], session=session
        )
        resolved_target = resolved[edges[0].to_name]
        assert resolved_target is not None
        assert resolved_target.id == target.id
        assert resolved["/Guide"] is None
        assert resolved["/targets/guide.md"] is None

    assert body.strip() in (Path(test_project.path) / "notes" / "Source.md").read_text()


@pytest.mark.asyncio
async def test_markdown_self_link_is_resolved_when_written(
    mcp_server, app, test_project, engine_factory
):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "title": "Self",
                "directory": "notes",
                "content": "[self](Self.md)",
                "project": test_project.name,
            },
        )
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        source = await EntityRepository(project_id=test_project.id).get_by_file_path(
            session, "notes/Self.md"
        )
        assert source is not None
        edges = await RelationRepository(project_id=test_project.id).find_by_type(
            session, "links_to"
        )
        assert len(edges) == 1
        assert edges[0].to_name == "/notes/Self.md"
        assert edges[0].to_id == source.id
