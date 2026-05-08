"""Regression tests for workspace-qualified permalink resolution."""

import pytest

from basic_memory.mcp.tools.delete_note import delete_note
from basic_memory.mcp.tools.edit_note import edit_note
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.write_note import write_note
from basic_memory.workspace_context import workspace_permalink_context


@pytest.mark.asyncio
async def test_link_resolver_strictly_finds_legacy_project_permalink_from_workspace_url(
    link_resolver,
    test_project,
    entity_service,
):
    """Workspace/project IDs should resolve legacy project-prefixed rows centrally."""
    await write_note(
        project=test_project.name,
        title="Resolver Legacy Workspace Note",
        directory="personal",
        content="Resolver legacy content",
    )

    legacy_permalink = f"{test_project.name}/personal/resolver-legacy-workspace-note"

    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        resolved = await link_resolver.resolve_link(
            f"personal/{legacy_permalink}",
            strict=True,
        )

    assert resolved is not None
    assert resolved.permalink == legacy_permalink


@pytest.mark.asyncio
async def test_edit_note_workspace_qualified_url_edits_legacy_project_permalink(
    app,
    test_project,
):
    """IDs returned from multi-project search should be editable even for legacy rows."""
    legacy_permalink = f"{test_project.name}/personal/edit-legacy-workspace-note"
    qualified_permalink = f"personal/{legacy_permalink}"

    await write_note(
        project=test_project.name,
        title="Edit Legacy Workspace Note",
        directory="personal",
        content="# Edit Legacy Workspace Note\n\nstatus: draft",
    )

    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        edit_result = await edit_note(
            identifier=f"memory://{qualified_permalink}",
            project=test_project.name,
            operation="find_replace",
            find_text="status: draft",
            content="status: done",
            output_format="json",
        )

    assert isinstance(edit_result, dict)
    assert edit_result["fileCreated"] is False
    assert edit_result["permalink"] == legacy_permalink

    read_result = await read_note(
        legacy_permalink,
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(read_result, dict)
    assert "status: done" in read_result["content"]


@pytest.mark.asyncio
async def test_delete_note_workspace_qualified_url_deletes_legacy_project_permalink(
    app,
    test_project,
):
    """IDs returned from multi-project search should be deletable even for legacy rows."""
    legacy_permalink = f"{test_project.name}/personal/delete-legacy-workspace-note"
    qualified_permalink = f"personal/{legacy_permalink}"

    await write_note(
        project=test_project.name,
        title="Delete Legacy Workspace Note",
        directory="personal",
        content="Delete legacy content",
    )

    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        delete_result = await delete_note(
            identifier=f"memory://{qualified_permalink}",
            project=test_project.name,
            output_format="json",
        )

    assert isinstance(delete_result, dict)
    assert delete_result["deleted"] is True
    assert delete_result["permalink"] == legacy_permalink
