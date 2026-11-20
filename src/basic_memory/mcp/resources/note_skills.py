"""Notes served as MCP skills.

A note with `type: skill` in a project the user trusts is served as a single-file
skill: its accepted markdown, byte for byte, is the skill's `SKILL.md`. The note's
frontmatter must carry the Agent Skills `name` and `description`.

Each skill lives under its project, `skill://<project>/<name>/SKILL.md`, so a note
skill never takes the URI of a packaged skill (`skill://memory-notes/SKILL.md`) or
of a same-named skill in another project. A client that wants "the daily-note
skill if one exists" matches on the skill's `name`, which every entry carries.

Trust is per project and off by default (`ProjectEntry.serve_skills`): an agent
can write any note, so turning notes into instructions other agents follow is the
user's call, not the note author's.
"""

import os
from collections import Counter
from dataclasses import dataclass
from typing import override

from fastmcp.skills import Skill
from fastmcp.skills.provider import SkillCatalogProvider
from loguru import logger
from pydantic import ValidationError

from basic_memory.config import ConfigManager
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.mcp.project_context import get_project_client
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import MAX_PAGE_SIZE
from basic_memory.schemas.search import SearchItemType, SearchQuery
from basic_memory.utils import generate_permalink

SKILL_NOTE_TYPE = "skill"


@dataclass(frozen=True, slots=True)
class SkillNote:
    """A `type: skill` note: its title, for log lines, and its accepted markdown."""

    title: str
    content: str


def skill_projects() -> list[str]:
    """The configured projects whose skill notes the user has chosen to serve."""
    config = ConfigManager().config
    trusted = [name for name, entry in config.projects.items() if entry.serve_skills]
    # Trigger: BASIC_MEMORY_MCP_PROJECT pins this server to one project.
    # Why: routing sends every project argument to the pinned project, so reading
    #   any other trusted project would return the pinned project's notes.
    # Outcome: serve the pinned project's skills only, and only if it is trusted.
    constrained = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained:
        pinned = generate_permalink(constrained)
        return [name for name in trusted if generate_permalink(name) == pinned]
    return trusted


async def project_skill_notes(project: str, *, page_size: int = MAX_PAGE_SIZE) -> list[SkillNote]:
    """Every `type: skill` note in one project, with its accepted markdown."""
    async with get_project_client(project) as (client, active_project):
        # Trust was granted to `project`; never serve notes from wherever routing
        # landed instead, under that project's skill URIs.
        if generate_permalink(active_project.name) != generate_permalink(project):
            raise ValueError(
                f"Skill notes for {project!r} resolved to project {active_project.name!r}"
            )
        search = SearchClient(client, active_project.external_id)
        query = SearchQuery(
            note_types=[SKILL_NOTE_TYPE], entity_types=[SearchItemType.ENTITY]
        ).model_dump()
        hits = []
        page = 1
        while True:
            response = await search.search(query, page=page, page_size=page_size)
            hits.extend(response.results)
            if not response.has_more:
                break
            page += 1

        titles = {hit.external_id: hit.title for hit in hits if hit.external_id}
        contents = await KnowledgeClient(client, active_project.external_id).accepted_note_contents(
            list(titles)
        )
    return [
        SkillNote(title=titles[external_id], content=content)
        for external_id, content in contents.items()
    ]


def note_skill(project: str, note: SkillNote) -> Skill | None:
    """The skill a note defines, or None when its frontmatter is not a valid skill.

    One malformed note must not hide the project's other skills, so it is logged
    with the reason and left out.
    """
    try:
        return Skill.from_files({"SKILL.md": note.content})
    except (ValidationError, ValueError) as error:
        logger.warning(
            "Not serving skill note {!r} in project {!r}: {}", note.title, project, error
        )
        return None


def project_skills(project: str, notes: list[SkillNote]) -> dict[str, Skill]:
    """One project's valid skill notes, keyed by `<project>/<name>`."""
    skills = [skill for note in notes if (skill := note_skill(project, note)) is not None]
    # Trigger: two notes in one project declare the same skill name.
    # Why: both would claim one URI and neither is clearly the intended one.
    # Outcome: both are left out until the user renames one.
    counts = Counter(skill.name for skill in skills)
    for name, count in counts.items():
        if count > 1:
            logger.warning(
                "Not serving skill {!r} in project {!r}: {} notes declare it",
                name,
                project,
                count,
            )
    prefix = generate_permalink(project)
    return {f"{prefix}/{skill.name}": skill for skill in skills if counts[skill.name] == 1}


class NoteSkillsProvider(SkillCatalogProvider):
    """Serve trusted projects' skill notes, re-read on every MCP request.

    fastmcp calls `load_skills()` once per request, so a note edit shows up in the
    next `skills/list` with its new digest. A project whose read fails takes only
    this provider's skills down for that request; the packaged skills keep serving.
    """

    @override
    async def load_skills(self) -> dict[str, Skill]:
        catalog: dict[str, Skill] = {}
        for project in skill_projects():
            catalog.update(project_skills(project, await project_skill_notes(project)))
        return catalog


note_skills_provider = NoteSkillsProvider()
mcp.add_provider(note_skills_provider)
