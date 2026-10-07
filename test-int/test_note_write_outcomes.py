"""Typed write outcomes preserve canonical content across the HTTP boundary."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import basic_memory.indexing.accepted_note_mutation_runner as mutation_runner
from basic_memory.config import BasicMemoryConfig
from basic_memory import db
from basic_memory.models import Entity, NoteContent, Project, RelationSearchRefresh
from basic_memory.repository import EntityRepository
from basic_memory.services.note_content_writes import (
    AcceptedNoteChange,
    NoteContentMutationKind,
    NoteContentMutationService,
    NoteContentMutationServiceError,
)


async def test_write_outcomes_preserve_identity_and_content(
    client: AsyncClient,
    test_project: Project,
) -> None:
    endpoint = f"/v2/projects/{test_project.external_id}/knowledge/write"
    note = {"title": "Typed Write", "directory": "notes", "content": "Original"}
    created = await client.post(endpoint, json={"note": note})
    assert created.status_code == 200
    assert created.json()["kind"] == "created"
    entity_id = created.json()["entity"]["external_id"]
    path = Path(test_project.path) / "notes/Typed Write.md"
    original = path.read_bytes()

    exists = await client.post(endpoint, json={"note": {**note, "content": "Refused"}})
    assert exists.json() == {"kind": "already_exists", "file_path": "notes/Typed Write.md"}
    assert path.read_bytes() == original

    updated = await client.post(
        endpoint, json={"note": {**note, "content": "Replacement"}, "overwrite": True}
    )
    assert updated.json()["kind"] == "updated"
    assert updated.json()["entity"]["external_id"] == entity_id
    assert "Replacement" in path.read_text()


async def test_write_rejection_keeps_validation_detail(
    client: AsyncClient,
    test_project: Project,
) -> None:
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/write",
        json={
            "note": {
                "title": "Invalid",
                "directory": "notes",
                "content": "{}",
                "content_type": "application/json",
            }
        },
    )
    assert response.status_code == 415
    assert response.json()["detail"] == (
        "Only markdown note writes are supported by the note-content path."
    )
    assert not (Path(test_project.path) / "notes/Invalid.md").exists()


async def test_write_rejects_invalid_frontmatter_before_selecting_identity(
    client: AsyncClient,
    test_project: Project,
) -> None:
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/write",
        json={
            "note": {
                "title": "Invalid",
                "directory": "notes",
                "content": "---\npermalink: [broken\n---\nBody",
            },
            "overwrite": True,
        },
    )
    assert response.status_code == 400
    assert "Invalid YAML" in response.json()["detail"]
    assert not (Path(test_project.path) / "notes/Invalid.md").exists()


@pytest.mark.parametrize("overwrite", [False, True])
async def test_write_preserves_runtime_operation_overrides(
    client: AsyncClient,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
    overwrite: bool,
) -> None:
    endpoint = f"/v2/projects/{test_project.external_id}/knowledge/write"
    note = {"title": "Runtime Policy", "directory": "notes", "content": "Original"}
    if overwrite:
        response = await client.post(endpoint, json={"note": note})
        assert response.json()["kind"] == "created"
    operation = "update_note" if overwrite else "create_note"
    override = AsyncMock(side_effect=NoteContentMutationServiceError(429, "Runtime write limit"))
    monkeypatch.setattr(NoteContentMutationService, operation, override)
    response = await client.post(endpoint, json={"note": note, "overwrite": overwrite})
    assert response.status_code == 429
    assert response.json() == {"detail": "Runtime write limit"}
    override.assert_awaited_once()


@pytest.mark.parametrize("overwrite", [False, True])
async def test_write_hook_failure_rolls_back_canonical_acceptance(
    client: AsyncClient,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
    overwrite: bool,
) -> None:
    endpoint = f"/v2/projects/{test_project.external_id}/knowledge/write"
    note = {"title": "Hook", "directory": "notes", "content": "Original"}
    path = Path(test_project.path) / "notes/Hook.md"
    created = await client.post(endpoint, json={"note": note}) if overwrite else None
    if created is not None:
        assert created.json()["kind"] == "created"
    original = path.read_bytes() if overwrite else None

    async def reject_hook(
        self: NoteContentMutationService,
        session: AsyncSession,
        *,
        project_external_id: str,
        change: AcceptedNoteChange,
        mutation_kind: NoteContentMutationKind,
        source: str,
    ) -> None:
        assert session.in_transaction()
        assert project_external_id == str(test_project.external_id)
        assert mutation_kind == ("update" if overwrite else "create")
        assert source == "api"
        raise RuntimeError("Runtime marker failed")

    monkeypatch.setattr(NoteContentMutationService, "on_accepted_mutation", reject_hook)
    with pytest.raises(RuntimeError, match="Runtime marker failed"):
        await client.post(
            endpoint, json={"note": {**note, "content": "Refused"}, "overwrite": overwrite}
        )
    if created is not None:
        assert path.read_bytes() == original
        result = await client.get(
            f"/v2/projects/{test_project.external_id}/knowledge/entities/"
            f"{created.json()['entity']['external_id']}"
        )
        assert "Original" in result.json()["content"]
        assert "Refused" not in result.json()["content"]
    else:
        assert not path.exists()
        monkeypatch.undo()
        retried = await client.post(endpoint, json={"note": note})
        assert retried.json()["kind"] == "created"


# --- Conditional overwrite (expected_checksum) ---


async def test_expected_checksum_replaces_only_the_revision_the_caller_read(
    client: AsyncClient,
    test_project: Project,
) -> None:
    endpoint = f"/v2/projects/{test_project.external_id}/knowledge/write"
    note = {"title": "Conditional", "directory": "notes", "content": "Revision A"}
    created = await client.post(endpoint, json={"note": note})
    revision_a = created.json()["entity"]["db_checksum"]
    assert revision_a
    path = Path(test_project.path) / "notes/Conditional.md"

    updated = await client.post(
        endpoint,
        json={
            "note": {**note, "content": "Revision B"},
            "overwrite": True,
            "expected_checksum": revision_a,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["kind"] == "updated"
    revision_b = updated.json()["entity"]["db_checksum"]
    assert revision_b != revision_a
    after_b = path.read_bytes()
    assert b"Revision B" in after_b

    # A writer still holding revision A must not replace revision B.
    stale = await client.post(
        endpoint,
        json={
            "note": {**note, "content": "Stale replacement"},
            "overwrite": True,
            "expected_checksum": revision_a,
        },
    )
    assert stale.status_code == 200
    assert stale.json() == {
        "kind": "revision_conflict",
        "file_path": "notes/Conditional.md",
        "db_checksum": revision_b,
    }
    assert path.read_bytes() == after_b


async def test_expected_checksum_never_creates_a_missing_note(
    client: AsyncClient,
    test_project: Project,
) -> None:
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/write",
        json={
            "note": {"title": "Deleted Since", "directory": "notes", "content": "Resurrected"},
            "overwrite": True,
            "expected_checksum": "a" * 64,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "kind": "revision_conflict",
        "file_path": "notes/Deleted Since.md",
        "db_checksum": None,
    }
    assert not (Path(test_project.path) / "notes/Deleted Since.md").exists()


async def test_expected_checksum_requires_overwrite(
    client: AsyncClient,
    test_project: Project,
) -> None:
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/write",
        json={
            "note": {"title": "Create Only", "directory": "notes", "content": "Body"},
            "expected_checksum": "a" * 64,
        },
    )
    assert response.status_code == 422
    assert "expected_checksum requires overwrite=True" in response.text
    assert not (Path(test_project.path) / "notes/Create Only.md").exists()


async def test_expected_checksum_refuses_a_note_moved_after_the_path_lookup(
    client: AsyncClient,
    test_project: Project,
    app_config: BasicMemoryConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The shipped default: a move keeps the permalink, and so the note's checksum.
    monkeypatch.setattr(app_config, "update_permalinks_on_move", False)
    endpoint = f"/v2/projects/{test_project.external_id}/knowledge/write"
    note = {"title": "Moving Target", "directory": "notes", "content": "Keep me"}
    created = await client.post(endpoint, json={"note": note})
    entity = created.json()["entity"]
    entity_url = (
        f"/v2/projects/{test_project.external_id}/knowledge/entities/{entity['external_id']}"
    )
    original_update = NoteContentMutationService.update_note
    moved_checksum: str | None = None

    async def move_before_update(self: NoteContentMutationService, **kwargs: Any):
        # Another writer moves the note after write_note resolved its path but
        # before the guarded replacement takes the note lock.
        nonlocal moved_checksum
        if moved_checksum is None:
            # The real move route, so the file moves on disk as well as in the DB.
            moved = await client.put(
                f"{entity_url}/move", json={"destination_path": "archive/Moving Target.md"}
            )
            assert moved.status_code == 202, moved.text
            moved_checksum = (await client.get(entity_url)).json()["db_checksum"]
        return await original_update(self, **kwargs)

    monkeypatch.setattr(NoteContentMutationService, "update_note", move_before_update)
    response = await client.post(
        endpoint,
        json={
            "note": {**note, "content": "Stale replacement"},
            "overwrite": True,
            "expected_checksum": entity["db_checksum"],
        },
    )

    # The move kept the Markdown, so only the path precondition can catch it.
    assert moved_checksum == entity["db_checksum"]
    assert response.json() == {
        "kind": "revision_conflict",
        "file_path": "notes/Moving Target.md",
        "db_checksum": entity["db_checksum"],
    }
    current = (await client.get(entity_url)).json()
    assert current["file_path"] == "archive/Moving Target.md"
    assert "Keep me" in current["content"]
    assert "Stale replacement" not in current["content"]


@pytest.mark.parametrize("conditional", [True, False], ids=["conditional", "unconditional"])
async def test_a_note_deleted_under_the_update_lock_is_a_revision_conflict_when_pinned(
    client: AsyncClient,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
    conditional: bool,
) -> None:
    endpoint = f"/v2/projects/{test_project.external_id}/knowledge/write"
    note = {"title": "Vanishing", "directory": "notes", "content": "Original"}
    created = await client.post(endpoint, json={"note": note})
    checksum = created.json()["entity"]["db_checksum"]

    # A delete commits after the update runner loads the entity but before it holds
    # the content lock, so the accepted content row is gone once the lock is taken.
    async def content_deleted(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mutation_runner, "load_accepted_note_content", content_deleted)
    request: dict[str, object] = {"note": {**note, "content": "Replacement"}, "overwrite": True}
    if conditional:
        request["expected_checksum"] = checksum
    response = await client.post(endpoint, json=request)

    if conditional:
        assert response.status_code == 200
        assert response.json() == {
            "kind": "revision_conflict",
            "file_path": "notes/Vanishing.md",
            "db_checksum": None,
        }
    else:
        # Without a pinned revision the missing content stays Core's backfill refusal.
        assert response.status_code == 409
        assert "Note content is not available" in response.json()["detail"]


# --- Relation publication marker ---


async def _publication_state(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    file_path: str,
) -> tuple[list[int | None], str | None, str | None]:
    """Return the note's marker generations, its stored checksum, and the gate's view of it."""
    async with db.scoped_session(session_maker) as session:
        entity = await session.scalar(
            select(Entity).where(Entity.project_id == project.id, Entity.file_path == file_path)
        )
        assert entity is not None
        markers = list(
            await session.scalars(
                select(RelationSearchRefresh.publication_generation)
                .where(RelationSearchRefresh.entity_id == entity.id)
                .order_by(RelationSearchRefresh.id)
            )
        )
        rows = await EntityRepository(project_id=project.id).get_by_file_paths(
            session, [file_path]
        )
    return markers, entity.checksum, rows[0][1]


async def test_published_write_leaves_no_pending_marker(
    client: AsyncClient,
    test_project: Project,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """A write whose graph is published leaves nothing pending, so its checksum stays visible."""
    _, session_maker = engine_factory
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/write",
        json={"note": {"title": "Published", "directory": "notes", "content": "- [a] b"}},
    )
    assert response.json()["kind"] == "created"

    markers, stored_checksum, gate_checksum = await _publication_state(
        session_maker, test_project, "notes/Published.md"
    )
    assert all(generation is None for generation in markers)
    assert stored_checksum is not None
    assert gate_checksum == stored_checksum


async def test_accepted_generation_is_pending_before_publication_starts(
    client: AsyncClient,
    test_project: Project,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The accept transaction owns the marker, so a publish that never starts still repairs."""
    _, session_maker = engine_factory
    observed: list[tuple[list[int | None], str | None, int | None]] = []

    async def fail_before_publishing(
        self: NoteContentMutationService, publication: Any
    ) -> None:
        markers, _, gate_checksum = await _publication_state(
            session_maker, test_project, "notes/Unpublished.md"
        )
        async with db.scoped_session(session_maker) as session:
            db_version = await session.scalar(
                select(NoteContent.db_version).where(
                    NoteContent.entity_id == publication.entity_id
                )
            )
        observed.append((markers, gate_checksum, db_version))
        raise OSError("graph publication unavailable")

    monkeypatch.setattr(
        NoteContentMutationService, "_publish_relation_generation", fail_before_publishing
    )
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/write",
        json={"note": {"title": "Unpublished", "directory": "notes", "content": "- [a] b"}},
    )
    # Graph publication is post-commit work; its failure never refuses the accepted note.
    assert response.json()["kind"] == "created"

    [(markers, gate_checksum, db_version)] = observed
    assert db_version is not None
    assert markers == [db_version]
    # The pending marker masks the checksum, so change detection re-reads and republishes.
    assert gate_checksum is None
