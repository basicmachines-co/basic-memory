"""Tests for notes served as MCP skills (skill://<project>/<name>/SKILL.md)."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.skills import SkillsClientExtension

import basic_memory.mcp.resources.note_skills  # noqa: F401  # registers the provider
import basic_memory.mcp.resources.skills  # noqa: F401  # registers the packaged skills
from basic_memory.config import ConfigManager
from basic_memory.mcp.resources.note_skills import project_skill_notes
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import bm_write


def _serve_skills(config_manager: ConfigManager, project: str, *, enabled: bool = True) -> None:
    config = config_manager.config
    config.projects[project].serve_skills = enabled
    config_manager.save_config(config)


async def _write_skill(project: str, title: str, metadata: dict[str, object]) -> None:
    await bm_write(
        title=title,
        directory="skills",
        note_type="skill",
        metadata=metadata,
        content=f"# {title}\n\nStart with yesterday's open items.\n",
        project=project,
    )


async def _note_skill_uris(client: Client[Any]) -> set[str]:
    return {
        str(skill.uri)
        for skill in await client.list_skills()
        if str(skill.uri).startswith("skill://test-project/")
    }


@pytest.mark.asyncio
async def test_untrusted_project_serves_no_note_skills(app, test_project) -> None:
    await _write_skill(
        test_project.name,
        "Daily Note",
        {"name": "daily-note", "description": "Write today's daily note."},
    )

    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        assert await _note_skill_uris(client) == set()


@pytest.mark.asyncio
async def test_trusted_project_serves_skill_notes_byte_for_byte(
    app, test_project, config_manager
) -> None:
    _serve_skills(config_manager, test_project.name)
    await _write_skill(
        test_project.name,
        "Daily Note",
        {"name": "daily-note", "description": "Write today's daily note."},
    )
    uri = "skill://test-project/daily-note/SKILL.md"

    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        assert await _note_skill_uris(client) == {uri}
        skill = await client.get_skill(uri)
        contents = await client.read_resource(uri)

    text = getattr(contents[0], "text", None)
    assert isinstance(text, str)
    assert "Start with yesterday's open items." in text
    assert skill.frontmatter.root["name"] == "daily-note"
    # The advertised digest is the digest of the bytes a read returns.
    [resource] = skill.resources
    assert resource.digest == f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"
    # Packaged skills keep serving beside note skills.
    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        listed = {str(skill.uri) for skill in await client.list_skills()}
    assert "skill://memory-notes/SKILL.md" in listed


@pytest.mark.asyncio
async def test_invalid_and_duplicate_skill_notes_are_left_out(
    app, test_project, config_manager
) -> None:
    _serve_skills(config_manager, test_project.name)
    await _write_skill(
        test_project.name, "Valid", {"name": "valid-skill", "description": "A valid skill."}
    )
    await _write_skill(test_project.name, "No Name", {"description": "Missing its name."})
    await _write_skill(
        test_project.name, "Bad Name", {"name": "Bad Name", "description": "Not a skill name."}
    )
    await _write_skill(
        test_project.name, "Twin One", {"name": "twin", "description": "First twin."}
    )
    await _write_skill(
        test_project.name, "Twin Two", {"name": "twin", "description": "Second twin."}
    )

    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        assert await _note_skill_uris(client) == {"skill://test-project/valid-skill/SKILL.md"}


@pytest.mark.asyncio
async def test_turning_trust_off_stops_serving(app, test_project, config_manager) -> None:
    _serve_skills(config_manager, test_project.name)
    await _write_skill(
        test_project.name,
        "Daily Note",
        {"name": "daily-note", "description": "Write today's daily note."},
    )
    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        assert await _note_skill_uris(client)

    _serve_skills(config_manager, test_project.name, enabled=False)

    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        assert await _note_skill_uris(client) == set()


@pytest.mark.asyncio
async def test_skill_notes_are_read_across_every_search_page(app, test_project) -> None:
    await _write_skill(test_project.name, "First", {"name": "first", "description": "One."})
    await _write_skill(test_project.name, "Second", {"name": "second", "description": "Two."})

    notes = await project_skill_notes(test_project.name, page_size=1)

    assert sorted(note.title for note in notes) == ["First", "Second"]


@pytest.mark.asyncio
async def test_pinned_server_never_serves_an_untrusted_project_as_a_trusted_one(
    app, test_project, second_project, config_manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # test-project is trusted, second-project is not; the server is pinned to
    # second-project, so routing would hand second-project's notes to any read.
    _serve_skills(config_manager, test_project.name)
    await _write_skill(
        test_project.name, "Trusted", {"name": "trusted", "description": "Trusted skill."}
    )
    await _write_skill(
        second_project.name, "Untrusted", {"name": "untrusted", "description": "Not trusted."}
    )
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", second_project.name)

    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        listed = {str(skill.uri) for skill in await client.list_skills()}
    assert not any("trusted" in uri for uri in listed)

    # A direct read refuses rather than mislabel the pinned project's notes.
    with pytest.raises(ValueError, match="resolved to project 'second-project'"):
        await project_skill_notes(test_project.name)


@pytest.mark.asyncio
async def test_pinned_server_serves_its_own_trusted_project(
    app, test_project, second_project, config_manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_skills(config_manager, test_project.name)
    await _write_skill(
        test_project.name, "Trusted", {"name": "trusted", "description": "Trusted skill."}
    )
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        assert await _note_skill_uris(client) == {"skill://test-project/trusted/SKILL.md"}
