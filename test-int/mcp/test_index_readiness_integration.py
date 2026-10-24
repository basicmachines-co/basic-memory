"""A real first index pass changes MCP guidance into trustworthy retrieval."""

from pathlib import Path

import pytest

from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.mcp.tools.projects import bm_project
from basic_memory.mcp.tools.ranked_search import bm_search
from basic_memory.schemas.project_readiness import ProjectIndexPhase


@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_first_index_makes_empty_reads_trustworthy(client, test_project, output_format):
    note = Path(test_project.path) / "unindexed.md"
    note.write_text("# Previously Invisible\n\nUniqueReadinessToken\n", encoding="utf-8")
    projects = ProjectClient(client)
    before = await projects.get_status(test_project.external_id)
    assert before.readiness.phase is ProjectIndexPhase.NEVER_INDEXED

    search = await bm_search(
        project=test_project.name, query="UniqueReadinessToken", output_format=output_format
    )
    activity = await bm_project(
        command="recent", project=test_project.name, output_format=output_format
    )
    for result in (search, activity):
        if output_format == "text":
            assert isinstance(result, str) and result.startswith("# Project Index Required")
        else:
            assert isinstance(result, dict) and result["results"] == []
            assert "Project Index Required" in result["guidance"]

    await projects.index(test_project.external_id, run_in_background=False)
    after = await projects.get_status(test_project.external_id)
    assert after.readiness.phase is not ProjectIndexPhase.NEVER_INDEXED
    found = await bm_search(
        project=test_project.name, query="UniqueReadinessToken", output_format="json"
    )
    assert isinstance(found, dict) and found["results"]
    miss = await bm_search(
        project=test_project.name, query="MissingReadinessToken", output_format="json"
    )
    assert isinstance(miss, dict) and miss["results"] == []
    # The note has no observations: filtering them is now an honest empty activity page.
    activity = await bm_project(
        command="recent", project=test_project.name, items=["observation"], output_format="json"
    )
    assert isinstance(activity, dict) and activity["results"] == []
