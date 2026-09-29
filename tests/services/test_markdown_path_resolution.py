"""Path targets resolve to one exact file, relative to the note that carries them."""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models import Entity


async def _add(entity_repository, session_maker, test_project, file_path: str, title: str):
    now = datetime.now(timezone.utc)
    entity = Entity(
        title=title,
        note_type="note",
        content_type="text/markdown",
        file_path=file_path,
        permalink=file_path[:-3].lower().replace(" ", "-"),
        created_at=now,
        updated_at=now,
        project_id=test_project.id,
    )
    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(session, entity)
    return entity


@pytest.mark.asyncio
async def test_rooted_path_resolves_only_exact_file(
    link_resolver, entity_repository, test_project, session_maker
):
    entity = await _add(entity_repository, session_maker, test_project, "notes/Guide.md", "Guide")
    resolved = await link_resolver.resolve_link("/notes/Guide.md")
    assert resolved is not None
    assert resolved.id == entity.id
    assert await link_resolver.resolve_link("/guide") is None
    assert await link_resolver.resolve_link("/notes/guide.md") is None


@pytest.mark.asyncio
async def test_relative_paths_resolve_from_the_source_note(
    link_resolver, entity_repository, test_project, session_maker
):
    guide = await _add(entity_repository, session_maker, test_project, "notes/Guide.md", "Guide")
    shared = await _add(entity_repository, session_maker, test_project, "Shared.md", "Shared")

    sibling = await link_resolver.resolve_link("./Guide.md", source_path="notes/Source.md")
    parent = await link_resolver.resolve_link("../Shared.md", source_path="notes/Source.md")
    assert sibling is not None and sibling.id == guide.id
    assert parent is not None and parent.id == shared.id
    # The same spelling from another folder names another file, or none.
    assert await link_resolver.resolve_link("./Guide.md", source_path="other/Source.md") is None
    # Without a source, only the root is a base: `./` resolves there, `../` cannot.
    root_shared = await link_resolver.resolve_link("./Shared.md")
    assert root_shared is not None and root_shared.id == shared.id
    assert await link_resolver.resolve_link("../Shared.md") is None
    # Climbing past the project root names nothing.
    assert (
        await link_resolver.resolve_link("../../Shared.md", source_path="notes/Source.md") is None
    )


@pytest.mark.asyncio
async def test_wikilinks_spelled_as_paths_follow_the_same_rule(
    link_resolver, entity_repository, test_project, session_maker
):
    shared = await _add(entity_repository, session_maker, test_project, "Shared.md", "Shared")

    resolved = await link_resolver.resolve_link(
        "[[../Shared.md|the shared note]]", source_path="notes/Source.md"
    )
    assert resolved is not None and resolved.id == shared.id
    # A path never falls through to the title or permalink the file also answers to.
    assert await link_resolver.resolve_link("./shared", source_path="notes/Source.md") is None
    by_entity = await link_resolver.resolve_entity("../Shared.md", source_path="notes/Source.md")
    assert by_entity is not None and by_entity.id == shared.id
