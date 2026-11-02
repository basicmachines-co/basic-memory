"""Tests for the distributed skills served over MCP (skill:// resources + SEP-2640)."""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.skills import SkillsClientExtension

from basic_memory.mcp.resources.skills import distributed_skills_root
from basic_memory.mcp.server import mcp


def _shipped_skill_names() -> set[str]:
    return {path.name for path in distributed_skills_root().iterdir() if path.is_dir()}


@pytest.mark.asyncio
async def test_skills_list_offers_every_shipped_skill() -> None:
    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        skills = await client.list_skills()

    listed = {str(skill.uri) for skill in skills}
    assert listed == {f"skill://{name}/SKILL.md" for name in _shipped_skill_names()}
    # Internal skills never ship, so they are never offered.
    assert "skill://memory-ci-capture/SKILL.md" not in listed
    for skill in skills:
        assert skill.frontmatter.root["description"]


@pytest.mark.asyncio
async def test_skill_get_returns_files_matching_the_shipped_bytes() -> None:
    root = distributed_skills_root()
    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        skill = await client.get_skill("skill://memory-notes/SKILL.md")

    assert skill.frontmatter.root["name"] == "memory-notes"
    assert [str(resource.uri) for resource in skill.resources] == ["skill://memory-notes/SKILL.md"]
    assert skill.resources[0].size == (root / "memory-notes" / "SKILL.md").stat().st_size


@pytest.mark.asyncio
async def test_clients_without_the_extension_read_skills_as_resources() -> None:
    expected = (distributed_skills_root() / "memory-notes" / "SKILL.md").read_text(encoding="utf-8")
    async with Client(mcp) as client:
        listed = {str(resource.uri) for resource in await client.list_resources()}
        contents = await client.read_resource("skill://memory-notes/SKILL.md")

    assert "skill://memory-notes/SKILL.md" in listed
    assert contents[0].text == expected  # pyright: ignore[reportAttributeAccessIssue]
