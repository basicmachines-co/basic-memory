"""A person the host vouches for may unlock a locked note; nobody else may.

The resolver stands in for a hosted runtime (Cloud's web app) that authenticated a
person. Without it every caller is an agent, and the lock stays one-way.
"""

import functools
from dataclasses import dataclass, replace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from basic_memory.deps.services import get_note_content_mutation_service
from basic_memory.markdown.note_lock import (
    AGENT_WRITER,
    NoteLockAuthority,
    PersonInWebApp,
    note_is_locked,
)
from basic_memory.services.note_content_writes import (
    NoteContentMutationActorContext,
    NoteContentMutationKind,
    NoteContentMutationService,
)

LOCKED_CONTENT = "---\nlocked: true\n---\n\nKeep this exact content.\n"


@dataclass
class LockAuthorityResolver:
    """Resolve every mutation with whichever lock authority the test sets."""

    lock_authority: NoteLockAuthority = AGENT_WRITER
    author: str | None = None

    def resolve_mutation_actor(
        self,
        *,
        mutation_kind: NoteContentMutationKind,
        requested: NoteContentMutationActorContext,
    ) -> NoteContentMutationActorContext:
        return replace(requested, lock_authority=self.lock_authority, author=self.author)


@pytest.fixture
def lock_resolver(app: FastAPI) -> LockAuthorityResolver:
    resolver = LockAuthorityResolver()

    @functools.wraps(get_note_content_mutation_service)
    async def resolved_service(**dependencies: Any) -> NoteContentMutationService:
        service = await get_note_content_mutation_service(**dependencies)
        service.actor_resolver = resolver
        return service

    app.dependency_overrides[get_note_content_mutation_service] = resolved_service
    return resolver


async def _create_locked(client: AsyncClient, v2_project_url: str) -> str:
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Runbook", "directory": "protected", "content": LOCKED_CONTENT},
        params={"fast": False},
    )
    assert response.status_code == 202, response.text
    return f"{v2_project_url}/knowledge/entities/{response.json()['external_id']}"


async def _stored(client: AsyncClient, note_url: str) -> str:
    response = await client.get(note_url)
    assert response.status_code == 200, response.text
    content = response.json()["content"]
    assert isinstance(content, str)
    return content


async def _put(client: AsyncClient, note_url: str, content: str):
    return await client.put(
        note_url,
        json={"title": "Runbook", "directory": "protected", "content": content},
        params={"fast": False},
    )


def _without_lock(content: str) -> str:
    assert "locked: true\n" in content
    return content.replace("locked: true\n", "")


@pytest.mark.asyncio
async def test_person_can_unlock_and_then_edit(
    client: AsyncClient, v2_project_url: str, lock_resolver: LockAuthorityResolver
) -> None:
    lock_resolver.lock_authority = PersonInWebApp()
    lock_resolver.author = "Alice"
    note_url = await _create_locked(client, v2_project_url)
    locked = await _stored(client, note_url)

    response = await _put(client, note_url, _without_lock(locked))

    assert response.status_code == 202, response.text
    unlocked = await _stored(client, note_url)
    assert not note_is_locked(unlocked)
    assert "Keep this exact content." in unlocked

    edited = await _put(client, note_url, unlocked.replace("Keep this", "Now edit this"))
    assert edited.status_code == 202, edited.text
    assert "Now edit this exact content." in await _stored(client, note_url)


@pytest.mark.asyncio
async def test_person_can_unlock_by_setting_locked_false(
    client: AsyncClient, v2_project_url: str, lock_resolver: LockAuthorityResolver
) -> None:
    lock_resolver.lock_authority = PersonInWebApp()
    note_url = await _create_locked(client, v2_project_url)
    locked = await _stored(client, note_url)

    response = await _put(client, note_url, locked.replace("locked: true", "locked: false"))

    assert response.status_code == 202, response.text
    assert not note_is_locked(await _stored(client, note_url))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda c: _without_lock(c).replace("Keep", "Changed"), id="unlock-and-edit"),
        pytest.param(lambda c: c.replace("Keep", "Changed"), id="edit-while-locked"),
        pytest.param(
            lambda c: _without_lock(c).replace("---\n\n", "status: draft\n---\n\n", 1),
            id="unlock-and-add-frontmatter",
        ),
    ],
)
async def test_person_cannot_unlock_and_change_anything_else(
    client: AsyncClient,
    v2_project_url: str,
    lock_resolver: LockAuthorityResolver,
    change: Any,
) -> None:
    lock_resolver.lock_authority = PersonInWebApp()
    note_url = await _create_locked(client, v2_project_url)
    locked = await _stored(client, note_url)

    response = await _put(client, note_url, change(locked))

    assert response.status_code == 423, response.text
    assert await _stored(client, note_url) == locked


@pytest.mark.asyncio
async def test_agent_cannot_unlock(
    client: AsyncClient, v2_project_url: str, lock_resolver: LockAuthorityResolver
) -> None:
    note_url = await _create_locked(client, v2_project_url)
    locked = await _stored(client, note_url)

    response = await _put(client, note_url, _without_lock(locked))

    assert response.status_code == 423, response.text
    assert await _stored(client, note_url) == locked


@pytest.mark.asyncio
async def test_person_cannot_unlock_through_edit_or_delete(
    client: AsyncClient, v2_project_url: str, lock_resolver: LockAuthorityResolver
) -> None:
    lock_resolver.lock_authority = PersonInWebApp()
    note_url = await _create_locked(client, v2_project_url)
    locked = await _stored(client, note_url)

    edit = await client.patch(
        note_url,
        json={"operation": "find_replace", "find_text": "locked: true", "content": "locked: false"},
    )
    delete = await client.delete(note_url)

    assert edit.status_code == 423, edit.text
    assert delete.status_code == 423, delete.text
    assert await _stored(client, note_url) == locked
