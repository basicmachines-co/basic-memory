"""A real first index pass changes MCP guidance into trustworthy retrieval."""

from pathlib import Path

import pytest

from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.mcp.tools import recent_activity, search_notes
from basic_memory.schemas.project_readiness import ProjectIndexPhase


@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_first_index_makes_empty_reads_trustworthy(client, test_project, output_format):
    note = Path(test_project.path) / "unindexed.md"
    note.write_text("# Previously Invisible\n\nUniqueReadinessToken\n", encoding="utf-8")
    projects = ProjectClient(client)
    before = await projects.get_status(test_project.external_id)
    assert before.readiness.phase is ProjectIndexPhase.NEVER_INDEXED

    search = await search_notes(
        project=test_project.name, query="UniqueReadinessToken", output_format=output_format
    )
    activity = await recent_activity(project=test_project.name, output_format=output_format)
    assert isinstance(search, str) and search.startswith("# Project Index Required")
    assert isinstance(activity, str) and activity.startswith("# Project Index Required")

    await projects.index(test_project.external_id, run_in_background=False)
    after = await projects.get_status(test_project.external_id)
    assert after.readiness.phase is not ProjectIndexPhase.NEVER_INDEXED
    found = await search_notes(
        project=test_project.name, query="UniqueReadinessToken", output_format="json"
    )
    assert isinstance(found, dict) and found["results"]
    miss = await search_notes(
        project=test_project.name, query="MissingReadinessToken", output_format="json"
    )
    assert isinstance(miss, dict) and miss["results"] == []
    # The note has no observations: filtering them is now an honest empty activity page.
    assert (
        await recent_activity(project=test_project.name, type="observation", output_format="json")
        == []
    )
