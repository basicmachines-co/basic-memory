"""Tests for notes as MCP resources (memory://{project}/{path*})."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ResourceError, ToolError

import basic_memory.mcp.resources.notes as notes_module
from basic_memory.mcp.resources.notes import NOTE_TEMPLATE, note_resource
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import write_note


async def _read(uri: str) -> str:
    # A real client session: resources/read through the server injects a live
    # Context, exactly as production does (mcp.read_resource alone would not).
    async with Client(mcp) as session:
        contents = await session.read_resource(uri)
    text = getattr(contents[0], "text", None)
    assert isinstance(text, str)
    return text


@pytest.mark.asyncio
async def test_note_template_is_registered() -> None:
    templates = {str(template.uri_template) for template in await mcp.list_resource_templates()}

    assert NOTE_TEMPLATE in templates


@pytest.mark.asyncio
async def test_note_reads_as_raw_markdown(app, test_project) -> None:
    await write_note(
        title="Resource Read Test",
        directory="specs",
        content="# Resource Read Test\n\n- [design] notes are resources #mcp\n",
        project=test_project.name,
    )

    text = await _read(f"memory://{test_project.permalink}/specs/resource-read-test")

    assert text.startswith("---\n")  # the raw file, frontmatter included
    assert "- [design] notes are resources #mcp" in text


@pytest.mark.asyncio
async def test_unknown_note_and_unknown_project_raise_resource_errors(app, test_project) -> None:
    with pytest.raises(ResourceError, match="No note 'test-project/nope/missing'"):
        await note_resource(project=test_project.name, path="nope/missing")
    # An unknown first segment falls back to the default project (unprefixed
    # permalinks) and reports the full identifier as missing there.
    with pytest.raises(ResourceError, match="No note"):
        await note_resource(project="no-such-project-anywhere", path="anything")


@pytest.mark.asyncio
async def test_unprefixed_permalink_reads_in_default_project(app, test_project) -> None:
    # With permalinks_include_project=False (or legacy notes) the URI's first
    # segment is a directory, not a project; routing must fall back to the
    # active/default project with the whole path as the identifier.
    await write_note(
        title="Roadmap",
        directory="docs",
        content="# Roadmap\n\nUnprefixed permalink read.\n",
        project=test_project.name,
    )

    text = await _read("memory://docs/roadmap")

    assert "Unprefixed permalink read." in text


@pytest.mark.asyncio
async def test_non_404_failures_keep_their_cause(
    app, test_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_resolve(client, project_external_id, identifier):
        raise ToolError("Authentication required: You need to authenticate to access 'x'")

    monkeypatch.setattr(notes_module, "resolve_entity_id", failing_resolve)
    with pytest.raises(ResourceError, match="Authentication required"):
        await note_resource(project=test_project.name, path="anything")


@pytest.mark.asyncio
async def test_routing_errors_surface_their_cause(
    app, test_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def constrained(client, identifier, project, context):
        raise ValueError("Project is constrained to 'other'")

    monkeypatch.setattr(notes_module, "resolve_project_and_path", constrained)
    with pytest.raises(ResourceError, match="constrained"):
        await note_resource(project=test_project.name, path="anything")


@pytest.mark.asyncio
async def test_man_namespace_stays_the_manual(app) -> None:
    # Which template a server matches first is not guaranteed, so the notes
    # handler must answer memory://man/... exactly as the manual would.
    direct = await note_resource(project="man", path="search-notes(3)")
    served = await _read("memory://man/search-notes(3)")

    assert direct.startswith("---\ntitle: search-notes(3)\n")
    assert served == direct


@pytest.mark.asyncio
async def test_project_info_template_still_answers_info_uris(app, test_project) -> None:
    # The three-segment info URI overlaps the notes template; pin that reading it
    # through a real session yields project info rather than a missing-note error.
    content = await _read(f"memory://local/{test_project.permalink}/info")

    assert test_project.name in content


@pytest.mark.asyncio
async def test_info_shaped_uris_delegate_to_project_info(app, test_project) -> None:
    # Insurance for the other tie outcome: if this template ever wins the
    # {ws}/{proj}/info shape, the reader still gets project info.
    direct = await note_resource(project="local", path=f"{test_project.permalink}/info")

    assert test_project.name in direct


@pytest.mark.asyncio
async def test_note_actually_named_info_still_reads(app, test_project) -> None:
    await write_note(
        title="Info",
        directory="sub",
        content="# Info\n\nA note that happens to be called info.\n",
        project=test_project.name,
    )

    # Direct: the delegation routes through project_info, which falls back to
    # the note when no such workspace/project pair exists.
    direct = await note_resource(project=test_project.name, path="sub/info")
    # Served: whichever template wins the 3-segment /info shape, the canonical
    # extensionless permalink reads — and so does the file path.
    served = await _read(f"memory://{test_project.permalink}/sub/info")
    served_md = await _read(f"memory://{test_project.permalink}/sub/info.md")

    assert "A note that happens to be called info." in direct
    assert "A note that happens to be called info." in served
    assert "A note that happens to be called info." in served_md


@pytest.mark.asyncio
async def test_info_uri_that_is_neither_project_nor_note_reports_the_route(
    app, test_project
) -> None:
    with pytest.raises(ResourceError):
        await notes_module.project_info(workspace="nowhere", project="also-nowhere")


@pytest.mark.asyncio
async def test_binary_content_is_steered_to_read_content(
    app, test_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    await write_note(
        title="Binary Decoy",
        directory="specs",
        content="# Binary Decoy\n",
        project=test_project.name,
    )

    async def fake_call_get(client, url):
        return SimpleNamespace(headers={"content-type": "image/png"}, text="")

    monkeypatch.setattr(notes_module, "call_get", fake_call_get)

    with pytest.raises(ResourceError, match="use the read_content tool"):
        await note_resource(project=test_project.name, path="specs/binary-decoy")
