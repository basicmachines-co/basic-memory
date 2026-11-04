"""Tests for the distributed skills served over MCP (skill:// resources + SEP-2640)."""

from __future__ import annotations

from pathlib import Path

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
    # Compare decoded bytes: read_text would turn a CRLF checkout (Windows) into LF,
    # while the served text keeps the file's line endings.
    skill_path = distributed_skills_root() / "memory-notes" / "SKILL.md"
    expected = skill_path.read_bytes().decode("utf-8")
    async with Client(mcp) as client:
        listed = {str(resource.uri) for resource in await client.list_resources()}
        contents = await client.read_resource("skill://memory-notes/SKILL.md")

    assert "skill://memory-notes/SKILL.md" in listed
    assert contents[0].text == expected  # pyright: ignore[reportAttributeAccessIssue]


def test_served_skills_match_the_repo_sources() -> None:
    """The served copies are the checked-in skills, not a stale install snapshot.

    An editable install copies `skills/` into the package at install time, so an
    edit there is not served until a reinstall. This fails loudly when they differ.
    """
    root = distributed_skills_root()
    repo_skills = Path(__file__).resolve().parents[2] / "skills"
    if not repo_skills.is_dir():  # pragma: no cover
        pytest.skip("run from an installed package, without the repository's skills/")

    served = [path for path in root.rglob("*") if path.is_file()]
    assert served
    stale = [
        str(path.relative_to(root))
        for path in served
        if path.read_bytes() != (repo_skills / path.relative_to(root)).read_bytes()
    ]
    assert stale == [], (
        "installed skills differ from skills/; run `uv sync --reinstall-package basic-memory`"
    )
