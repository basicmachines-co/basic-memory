"""Accepted note writes stamp created_by/updated_by from the resolved actor.

The resolver stands in for a hosted runtime that authenticated the caller. The
local API has no resolver, so its writes must leave authorship keys alone.
"""

import functools
from dataclasses import dataclass, replace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from basic_memory.deps.services import get_note_content_mutation_service
from basic_memory.file_utils import parse_frontmatter
from basic_memory.services.note_content_writes import (
    NoteContentMutationActorContext,
    NoteContentMutationKind,
    NoteContentMutationService,
)


@dataclass
class AuthorResolver:
    """Resolve every mutation to whichever author the test sets next."""

    author: str | None = None

    def resolve_mutation_actor(
        self,
        *,
        mutation_kind: NoteContentMutationKind,
        requested: NoteContentMutationActorContext,
    ) -> NoteContentMutationActorContext:
        return replace(requested, author=self.author)


@pytest.fixture
def author_resolver(app: FastAPI) -> AuthorResolver:
    resolver = AuthorResolver()

    # FastAPI reads the override's parameters through __wrapped__, so the real
    # dependency's wiring still builds the service this override decorates.
    @functools.wraps(get_note_content_mutation_service)
    async def authored_service(**dependencies: Any) -> NoteContentMutationService:
        service = await get_note_content_mutation_service(**dependencies)
        service.actor_resolver = resolver
        return service

    app.dependency_overrides[get_note_content_mutation_service] = authored_service
    return resolver


async def _create(client: AsyncClient, v2_project_url: str, content: str) -> dict[str, Any]:
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Authored", "directory": "notes", "content": content},
        params={"fast": False},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _put(
    client: AsyncClient, v2_project_url: str, external_id: str, content: str
) -> dict[str, Any]:
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{external_id}",
        json={"title": "Authored", "directory": "notes", "content": content},
        params={"fast": False},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _patch(
    client: AsyncClient, v2_project_url: str, external_id: str, edit: dict[str, Any]
) -> dict[str, Any]:
    response = await client.patch(
        f"{v2_project_url}/knowledge/entities/{external_id}",
        json=edit,
        params={"fast": False},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _frontmatter(file_service, entity: dict[str, Any]) -> dict[str, Any]:
    content, _ = await file_service.read_file(entity["file_path"])
    return parse_frontmatter(content)


@pytest.mark.asyncio
async def test_create_stamps_author_over_submitted_values(
    client: AsyncClient, v2_project_url: str, file_service, author_resolver: AuthorResolver
) -> None:
    author_resolver.author = "Paul Hernandez via Nightly Backup"
    entity = await _create(
        client,
        v2_project_url,
        "---\ncreated_by: Forged\nupdated_by: Forged\n---\n\nBody",
    )

    frontmatter = await _frontmatter(file_service, entity)
    assert frontmatter["created_by"] == "Paul Hernandez via Nightly Backup"
    assert frontmatter["updated_by"] == "Paul Hernandez via Nightly Backup"
    assert entity["entity_metadata"]["created_by"] == "Paul Hernandez via Nightly Backup"


@pytest.mark.asyncio
async def test_replace_keeps_creator_and_restamps_updater(
    client: AsyncClient, v2_project_url: str, file_service, author_resolver: AuthorResolver
) -> None:
    author_resolver.author = "Alice"
    entity = await _create(client, v2_project_url, "Original")

    author_resolver.author = "Bob"
    updated = await _put(
        client,
        v2_project_url,
        entity["external_id"],
        "---\ncreated_by: Bob\nupdated_by: Mallory\n---\n\nReplaced",
    )

    frontmatter = await _frontmatter(file_service, updated)
    assert frontmatter["created_by"] == "Alice"
    assert frontmatter["updated_by"] == "Bob"
    assert updated["entity_metadata"]["updated_by"] == "Bob"


@pytest.mark.asyncio
async def test_same_author_save_does_not_rewrite_authorship_bytes(
    client: AsyncClient, v2_project_url: str, file_service, author_resolver: AuthorResolver
) -> None:
    author_resolver.author = "Alice"
    entity = await _create(client, v2_project_url, "Body")
    first, _ = await file_service.read_file(entity["file_path"])

    await _put(client, v2_project_url, entity["external_id"], first)

    second, _ = await file_service.read_file(entity["file_path"])
    assert second == first


@pytest.mark.asyncio
async def test_edit_cannot_forge_authorship(
    client: AsyncClient, v2_project_url: str, file_service, author_resolver: AuthorResolver
) -> None:
    author_resolver.author = "Alice"
    entity = await _create(client, v2_project_url, "Body")

    author_resolver.author = "Bob"
    await _patch(
        client,
        v2_project_url,
        entity["external_id"],
        {
            "operation": "find_replace",
            "find_text": "created_by: Alice",
            "content": "created_by: Eve",
        },
    )
    edited = await _patch(
        client,
        v2_project_url,
        entity["external_id"],
        {"operation": "append", "content": "", "metadata": {"updated_by": "Eve"}},
    )

    frontmatter = await _frontmatter(file_service, edited)
    assert frontmatter["created_by"] == "Alice"
    assert frontmatter["updated_by"] == "Bob"


@pytest.mark.asyncio
async def test_legacy_note_gains_no_forged_creator(
    client: AsyncClient, v2_project_url: str, file_service, author_resolver: AuthorResolver
) -> None:
    entity = await _create(client, v2_project_url, "Written before authorship existed")

    author_resolver.author = "Bob"
    updated = await _put(
        client,
        v2_project_url,
        entity["external_id"],
        "---\ncreated_by: Bob\n---\n\nReplaced",
    )

    frontmatter = await _frontmatter(file_service, updated)
    assert "created_by" not in frontmatter
    assert frontmatter["updated_by"] == "Bob"


@pytest.mark.asyncio
async def test_move_does_not_restamp(
    client: AsyncClient, v2_project_url: str, file_service, author_resolver: AuthorResolver
) -> None:
    author_resolver.author = "Alice"
    entity = await _create(client, v2_project_url, "Body")

    author_resolver.author = "Bob"
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{entity['external_id']}/move",
        json={"destination_path": "moved/Authored.md"},
    )
    assert response.status_code == 202, response.text

    frontmatter = await _frontmatter(file_service, response.json())
    assert frontmatter["updated_by"] == "Alice"


@pytest.mark.asyncio
async def test_local_writes_without_resolver_leave_authorship_alone(
    client: AsyncClient, v2_project_url: str, file_service
) -> None:
    entity = await _create(
        client,
        v2_project_url,
        "---\ncreated_by: Hand Written\n---\n\nBody",
    )

    frontmatter = await _frontmatter(file_service, entity)
    assert frontmatter["created_by"] == "Hand Written"
    assert "updated_by" not in frontmatter
