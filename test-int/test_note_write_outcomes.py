"""Typed write outcomes preserve canonical content across the HTTP boundary."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.config import BasicMemoryConfig
from basic_memory.models import Project
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
