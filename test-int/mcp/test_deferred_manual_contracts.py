"""Public MCP contracts with real deferred writes, files, indexes and embeddings.

The ordinary suite indexes inline for speed and simple assertions. These tests
use production scheduling and a real queue barrier to distinguish acceptance
from materialization without replacing application code or guessing sleep times.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.client.transports import FastMCPTransport

from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.index import note_content_materialization
from basic_memory.index.local_schedulers import drain_background_tasks
from basic_memory.index.note_content_materialization import drain_pending_materializations
from basic_memory.models import Project

pytestmark = [pytest.mark.asyncio, pytest.mark.real_embedder]

type McpSession = AbstractAsyncContextManager[Client[FastMCPTransport]]


async def settle_background_work() -> None:
    """Wait for actual queue completion, including relation-resolution follow-ups."""
    async with asyncio.timeout(90):
        await drain_pending_materializations()
        await drain_background_tasks()


@asynccontextmanager
async def held_materialization() -> AsyncIterator[None]:
    """Occupy the single real worker before a request enqueues its file write."""
    release = asyncio.Event()
    # One worker makes FIFO queue ordering a deterministic acceptance boundary.
    # This is ordinary queued work; no writer, indexer or scheduler is replaced.
    note_content_materialization._materialization_pool.submit(release.wait(), workers=1, key=(0, 0))
    try:
        yield
    finally:
        release.set()
        await settle_background_work()


@asynccontextmanager
async def production_client(
    server: FastMCP, config: BasicMemoryConfig, config_manager: ConfigManager
) -> AsyncIterator[Client[FastMCPTransport]]:
    config.env = "dev"
    config.materialization_workers = 1
    config_manager.save_config(config)
    # is_test_env also consults pytest's per-phase environment variable. Enter
    # this context inside the test call so pytest cannot restore it before use.
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("BASIC_MEMORY_ENV", "dev")
        environment.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert not config.is_test_env
        try:
            async with Client(server) as client:
                yield client
        finally:
            await settle_background_work()
            await note_content_materialization._materialization_pool.aclose()
            await note_content_materialization._vector_sync_pool.aclose()


@pytest.fixture
def deferred_mcp(
    mcp_server: FastMCP,
    app: FastAPI,
    app_config: BasicMemoryConfig,
    config_manager: ConfigManager,
) -> McpSession:
    """Keep real API dependencies while selecting production's deferred lifecycle."""
    return production_client(mcp_server, app_config, config_manager)


@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_write_edit_move_report_acceptance_before_materialization(
    deferred_mcp: McpSession, test_project: Project, output_format: str
) -> None:
    root = Path(test_project.path)
    path = "plans/launch.md"
    content = "# Launch\n\n- [goal] ship\n- relates_to [[Other Plan]]\n"
    async with deferred_mcp as client:
        await client.call_tool(
            "bm_write",
            {
                "identifier": "plans/other.md",
                "content": "# Other Plan",
                "project": test_project.name,
            },
        )
        await settle_background_work()

        # --- Acceptance is readable while the portable file is still queued ---
        async with held_materialization():
            created = await client.call_tool(
                "bm_write",
                {
                    "identifier": path,
                    "content": content,
                    "project": test_project.name,
                    "output_format": output_format,
                },
            )
            assert not (root / path).exists()
            immediate = await client.call_tool(
                "bm_read",
                {"identifier": path, "project": test_project.name, "output_format": "json"},
            )
            assert isinstance(immediate.data, dict)
            assert "[goal] ship" in immediate.data["content"]
        materialized = (root / path).read_bytes()

        async with held_materialization():
            edited = await client.call_tool(
                "bm_edit",
                {
                    "identifier": path,
                    "command": "append",
                    "content": "\n- [decision] freeze\n",
                    "project": test_project.name,
                    "output_format": output_format,
                },
            )
            assert (root / path).read_bytes() == materialized
            immediate = await client.call_tool(
                "bm_read",
                {"identifier": path, "project": test_project.name, "output_format": "json"},
            )
            assert isinstance(immediate.data, dict)
            assert "[decision] freeze" in immediate.data["content"]

        async with held_materialization():
            moved = await client.call_tool(
                "bm_move",
                {
                    "identifier": path,
                    "destination": "archive/",
                    "project": test_project.name,
                    "output_format": output_format,
                },
            )
            assert (root / path).is_file()
            assert not (root / "archive/launch.md").exists()
            immediate = await client.call_tool(
                "bm_read",
                {
                    "identifier": "archive/launch.md",
                    "project": test_project.name,
                    "output_format": "json",
                },
            )
            assert isinstance(immediate.data, dict)
            assert "[decision] freeze" in immediate.data["content"]

        # --- Accepted results never pretend the derived graph was counted ---
        for result in (created, edited, moved):
            if output_format == "json":
                assert isinstance(result.data, dict)
                assert {"observations", "relations", "unresolved_relations"}.isdisjoint(result.data)
                assert result.data["checksum"]
            else:
                assert isinstance(result.data, str)
                assert "observations:" not in result.data
                assert "relations:" not in result.data
                assert "checksum:" in result.data

        assert not (root / path).exists()
        assert "[decision] freeze" in (root / "archive/launch.md").read_text(encoding="utf-8")
        context = await client.call_tool(
            "bm_context", {"identifier": "archive/launch.md", "project": test_project.name}
        )
        assert isinstance(context.data, dict)
        (note,) = context.data["results"]
        assert len(note["observations"]) == 2
        assert any(row["type"] == "relation" for row in note["related_results"])


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
@pytest.mark.parametrize("lines", [0, 1])
async def test_real_semantic_candidates_are_filtered_without_losing_pagination(
    deferred_mcp: McpSession, test_project: Project, mode: str, lines: int
) -> None:
    async with deferred_mcp as client:
        for path, content in (
            ("plans/launch.md", "# Launch\n\nRollback is manual."),
            ("plans/history.md", "# History\n\nThe previous deployment was quiet."),
        ):
            await client.call_tool(
                "bm_write", {"identifier": path, "content": content, "project": test_project.name}
            )
        await settle_background_work()
        arguments = {
            "query": "zebra",
            "mode": mode,
            "items": ["note"],
            "min_similarity": 0.0,
            "page_size": 1,
            "project": test_project.name,
        }
        ranked = await client.call_tool("bm_search", {**arguments, "output_format": "json"})
        assert isinstance(ranked.data, dict)
        assert len(ranked.data["results"]) == 1
        assert ranked.data["has_more"] is True

        first = await client.call_tool(
            "bm_search", {**arguments, "lines": lines, "output_format": "json"}
        )
        assert isinstance(first.data, dict)
        assert first.data["results"] == []
        assert first.data["has_more"] is True
        assert first.data["total_is_exact"] is False
        text = await client.call_tool("bm_search", {**arguments, "lines": lines})
        assert isinstance(text.data, str)
        assert "on this page" in text.data
        assert "Call bm_search(" in text.data and "page=2" in text.data
        second = await client.call_tool(
            "bm_search", {**arguments, "lines": lines, "page": 2, "output_format": "json"}
        )
        assert isinstance(second.data, dict)
        assert second.data["results"] == []
        assert second.data["has_more"] is False

        matched = await client.call_tool(
            "bm_search",
            {
                **arguments,
                "query": "rollback",
                "lines": lines,
                "page_size": 10,
                "output_format": "json",
            },
        )
        assert isinstance(matched.data, dict)
        assert [row["file_path"] for row in matched.data["results"]] == ["plans/launch.md"]
        assert all(row["match_count"] > 0 and row["windows"] for row in matched.data["results"])

        no_match = await client.call_tool(
            "bm_search",
            {
                "query": "zebra",
                "identifier": "plans/launch.md",
                "project": test_project.name,
                "output_format": "json",
            },
        )
        assert isinstance(no_match.data, dict)
        assert no_match.data["match_count"] == 0 and no_match.data["windows"] == []
        text = await client.call_tool(
            "bm_search",
            {"query": "zebra", "identifier": "plans/launch.md", "project": test_project.name},
        )
        assert isinstance(text.data, str) and "No matching lines." in text.data


@pytest.mark.parametrize(("compact", "max_tokens"), [(False, 1), (True, 20)])
async def test_deferred_notes_have_honest_graph_pages_read_budgets_and_schema_guidance(
    deferred_mcp: McpSession,
    test_project: Project,
    compact: bool,
    max_tokens: int,
) -> None:
    first_line = "A long opening paragraph " * 30
    async with deferred_mcp as client:
        await client.call_tool(
            "bm_write",
            {
                "identifier": "plans/other.md",
                "content": "# Other Plan\n- [goal] support",
                "note_type": "plan",
                "project": test_project.name,
            },
        )
        await client.call_tool(
            "bm_write",
            {
                "identifier": "plans/launch.md",
                "note_type": "plan",
                "content": f"\n\n{first_line}\n\nLast paragraph.\n\n- [goal] ship\n"
                "- relates_to [[Other Plan]]",
                "project": test_project.name,
            },
        )
        await settle_background_work()

        paths: set[str] = set()
        for page in (1, 2):
            graph = await client.call_tool(
                "bm_context",
                {
                    "identifier": "plans/*",
                    "page": page,
                    "page_size": 1,
                    "compact": compact,
                    "project": test_project.name,
                },
            )
            assert isinstance(graph.data, dict)
            (note,) = graph.data["results"]
            assert note["primary_result"]["type"] == "entity"
            paths.add(note["primary_result"]["file_path"])
            assert graph.data["has_more"] is (page == 1)
            if not compact:
                assert note["observations"]
        assert paths == {"plans/launch.md", "plans/other.md"}

        start_line = 1
        contents: list[str] = []
        for page in range(10):
            arguments = {
                "identifier": "plans/launch.md",
                "max_tokens": max_tokens,
                "project": test_project.name,
                "output_format": "json",
            }
            # The first call exercises the default body slice after frontmatter.
            if page:
                arguments["start_line"] = start_line
            read = await client.call_tool("bm_read", arguments)
            assert isinstance(read.data, dict)
            assert read.data["content"].strip()
            contents.append(read.data["content"])
            if not read.data["has_more"]:
                break
            assert read.data["next_start_line"] > read.data["start_line"]
            start_line = read.data["next_start_line"]
        else:
            pytest.fail("The read continuation did not reach the end of the note")
        assert first_line in contents[0]
        assert "Last paragraph." in "\n".join(contents)

        with pytest.raises(ToolError) as refused:
            await client.call_tool(
                "bm_schema", {"command": "validate", "project": test_project.name}
            )
        guidance = str(refused.value)
        assert "bm_schema(command='infer'" in guidance
        assert "schema_infer(" not in guidance and "schema_validate" not in guidance
