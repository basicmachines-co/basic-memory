"""An unknown project on a local-only install is reported as not found (#1632).

Unknown names default to cloud routing so cloud-only projects stay reachable, but
with no cloud credentials that route could only fail with a credentials error
that hid the real mistake.
"""

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import read_note, search_notes


@pytest.mark.asyncio
async def test_unknown_project_reports_not_found_instead_of_credentials(app, test_project):
    with pytest.raises(ToolError) as read_error:
        await read_note("anything", project="nope-nope")
    assert "not found" in str(read_error.value).lower()
    assert "credentials" not in str(read_error.value).lower()

    with pytest.raises(ToolError) as search_error:
        await search_notes(query="anything", project="nope-nope")
    assert "not found" in str(search_error.value).lower()
    assert "credentials" not in str(search_error.value).lower()
