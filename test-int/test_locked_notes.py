"""Real API and file regressions for the one-way locked-note contract."""

import json
from pathlib import Path

from httpx import AsyncClient
import pytest
import pytest_asyncio

from basic_memory.models import Project
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)

type LockedNote = tuple[str, Path, bytes]


@pytest_asyncio.fixture
async def locked_note(client: AsyncClient, test_project: Project) -> LockedNote:
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/entities",
        json={
            "title": "Runbook",
            "directory": "protected",
            "content": "---\nlocked: true\n---\n\nKeep this exact content.\n",
        },
    )
    assert response.status_code == 202, response.text
    note = response.json()
    path = Path(test_project.path) / note["file_path"]
    return (
        f"/v2/projects/{test_project.external_id}/knowledge/entities/{note['external_id']}",
        path,
        path.read_bytes(),
    )


async def assert_note_unchanged(client: AsyncClient, note: LockedNote) -> None:
    url, path, original = note
    assert path.read_bytes() == original
    response = await client.get(url)
    assert response.status_code == 200
    assert response.json()["content"] == original.decode("utf-8")
    assert response.json()["db_version"] == 1


@pytest.mark.asyncio
async def test_locked_note_rejects_append(client: AsyncClient, locked_note: LockedNote) -> None:
    response = await client.patch(
        locked_note[0], json={"operation": "append", "content": "Sneaky edit"}
    )
    assert response.status_code == 423, response.text
    assert "locked: true" in response.text
    await assert_note_unchanged(client, locked_note)


@pytest.mark.asyncio
async def test_directory_delete_still_accepts_offline_malformed_notes(
    client: AsyncClient, test_project: Project
) -> None:
    directory = Path(test_project.path) / "malformed"
    directory.mkdir()
    note_path = directory / "broken.md"
    note_path.write_text("---\ntitle: [unclosed\n---\n\nOriginal\n", encoding="utf-8")
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/delete-directory",
        json={"directory": "malformed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["successful_deletes"] == 1
    assert not note_path.exists()


@pytest.mark.asyncio
async def test_locked_note_rejects_metadata_unlock(
    client: AsyncClient, locked_note: LockedNote
) -> None:
    response = await client.patch(
        locked_note[0],
        json={"operation": "append", "content": "Sneaky edit", "metadata": {"locked": False}},
    )
    assert response.status_code == 423, response.text
    await assert_note_unchanged(client, locked_note)


@pytest.mark.asyncio
async def test_locked_note_rejects_frontmatter_find_replace(
    client: AsyncClient, locked_note: LockedNote
) -> None:
    response = await client.patch(
        locked_note[0],
        json={"operation": "find_replace", "find_text": "locked: true", "content": "locked: false"},
    )
    assert response.status_code == 423, response.text
    await assert_note_unchanged(client, locked_note)


@pytest.mark.asyncio
async def test_locked_note_rejects_replacement_body_unlock(
    client: AsyncClient, locked_note: LockedNote
) -> None:
    response = await client.put(
        locked_note[0],
        json={
            "title": "Runbook",
            "directory": "protected",
            "content": "---\nlocked: false\n---\n\nReplacement",
            "entity_metadata": {"locked": False},
        },
    )
    assert response.status_code == 423, response.text
    await assert_note_unchanged(client, locked_note)


@pytest.mark.asyncio
async def test_locked_note_rejects_replacement_omitting_lock(
    client: AsyncClient, locked_note: LockedNote
) -> None:
    response = await client.put(
        locked_note[0],
        json={"title": "Runbook", "directory": "protected", "content": "No frontmatter"},
    )
    assert response.status_code == 423, response.text
    await assert_note_unchanged(client, locked_note)


@pytest.mark.asyncio
async def test_locked_note_rejects_delete(client: AsyncClient, locked_note: LockedNote) -> None:
    response = await client.delete(locked_note[0])
    assert response.status_code == 423, response.text
    await assert_note_unchanged(client, locked_note)


@pytest.mark.asyncio
async def test_directory_delete_rejects_locked_note_before_deleting_siblings(
    client: AsyncClient, test_project: Project, locked_note: LockedNote
) -> None:
    base = f"/v2/projects/{test_project.external_id}/knowledge"
    sibling = await client.post(
        f"{base}/entities",
        json={"title": "Sibling", "directory": "protected", "content": "Keep sibling too"},
    )
    assert sibling.status_code == 202, sibling.text
    sibling_path = Path(test_project.path) / sibling.json()["file_path"]
    sibling_bytes = sibling_path.read_bytes()
    response = await client.post(f"{base}/delete-directory", json={"directory": "protected"})
    assert response.status_code == 423, response.text
    assert sibling_path.read_bytes() == sibling_bytes
    sibling_read = await client.get(f"{base}/entities/{sibling.json()['external_id']}")
    assert sibling_read.status_code == 200
    await assert_note_unchanged(client, locked_note)


@pytest.mark.asyncio
async def test_offline_edit_can_unlock_note(client: AsyncClient, locked_note: LockedNote) -> None:
    url, path, original = locked_note
    path.write_bytes(original.replace(b"locked: true", b"locked: false"))
    response = await client.patch(url, json={"operation": "append", "content": "Allowed now"})
    assert response.status_code == 202, response.text
    assert "Allowed now" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_can_lock_existing_note_but_cannot_unlock_it(
    client: AsyncClient, test_project: Project
) -> None:
    base = f"/v2/projects/{test_project.external_id}/knowledge/entities"
    created = await client.post(
        base, json={"title": "New lock", "directory": "", "content": "Original"}
    )
    assert created.status_code == 202, created.text
    url = f"{base}/{created.json()['external_id']}"
    locked = await client.patch(
        url, json={"operation": "append", "content": "Final edit", "metadata": {"locked": True}}
    )
    assert locked.status_code == 202, locked.text
    path = Path(test_project.path) / locked.json()["file_path"]
    original = path.read_bytes()
    unlock = await client.patch(
        url, json={"operation": "append", "content": "", "metadata": {"locked": False}}
    )
    assert unlock.status_code == 423, unlock.text
    assert path.read_bytes() == original
    reread = await client.get(url)
    assert reread.json()["content"] == original.decode("utf-8")
    assert reread.json()["db_version"] == locked.json()["db_version"]


@pytest.mark.asyncio
async def test_move_preserves_locked_note_protection(
    client: AsyncClient, locked_note: LockedNote
) -> None:
    url, original_path, _ = locked_note
    moved = await client.put(f"{url}/move", json={"destination_path": "archive/Runbook.md"})
    assert moved.status_code == 202, moved.text
    assert not original_path.exists()
    blocked = await client.patch(url, json={"operation": "append", "content": "Still forbidden"})
    assert blocked.status_code == 423, blocked.text
    read = await client.get(url)
    assert read.json()["content"] == moved.json()["content"]
    assert read.json()["db_version"] == moved.json()["db_version"]


@pytest.mark.asyncio
async def test_replacement_body_can_lock_an_unlocked_note(
    client: AsyncClient, test_project: Project
) -> None:
    base = f"/v2/projects/{test_project.external_id}/knowledge/entities"
    created = await client.post(
        base, json={"title": "Body lock", "directory": "", "content": "Original"}
    )
    assert created.status_code == 202, created.text
    url = f"{base}/{created.json()['external_id']}"
    locked = await client.put(
        url,
        json={"title": "Body lock", "directory": "", "content": "---\nlocked: true\n---\nFinal"},
    )
    assert locked.status_code == 202, locked.text
    blocked = await client.delete(url)
    assert blocked.status_code == 423, blocked.text
    read = await client.get(url)
    assert read.json()["content"] == locked.json()["content"]


@pytest.mark.asyncio
async def test_import_can_overwrite_locked_note_as_raw_file_write(
    client: AsyncClient, test_project: Project, locked_note: LockedNote
) -> None:
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/import/memory-json",
        data={"directory": "."},
        files={
            "file": (
                "memory.json",
                json.dumps(
                    {
                        "type": "entity",
                        "entityType": "protected",
                        "name": "Runbook",
                        "observations": ["Overwrite attempt"],
                    }
                ),
                "application/json",
            )
        },
    )
    assert response.status_code == 200, response.text
    content = locked_note[1].read_text(encoding="utf-8")
    assert "Overwrite attempt" in content
    assert "locked: true" not in content
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    read = await client.get(locked_note[0])
    assert read.status_code == 200, read.text
    assert "Overwrite attempt" in read.json()["content"]


@pytest.mark.asyncio
async def test_api_directory_delete_observes_offline_lock_before_indexing(
    client: AsyncClient, test_project: Project
) -> None:
    base = f"/v2/projects/{test_project.external_id}/knowledge"
    created = await client.post(
        f"{base}/entities",
        json={"title": "Offline lock", "directory": "protected", "content": "Original"},
    )
    assert created.status_code == 202, created.text
    path = Path(test_project.path) / created.json()["file_path"]
    locked_bytes = path.read_bytes().replace(b"---\n", b"---\nlocked: true\n", 1)
    path.write_bytes(locked_bytes)
    sibling = await client.post(
        f"{base}/entities",
        json={"title": "Sibling", "directory": "protected", "content": "Keep sibling too"},
    )
    assert sibling.status_code == 202, sibling.text
    sibling_path = Path(test_project.path) / sibling.json()["file_path"]
    sibling_bytes = sibling_path.read_bytes()

    # No watcher or reindex: the API must inspect the local lock before deleting
    # any sibling, not accept partial deletion from an older unlocked DB snapshot.
    response = await client.post(f"{base}/delete-directory", json={"directory": "protected"})
    assert response.status_code == 423, response.text
    assert path.read_bytes() == locked_bytes
    assert sibling_path.read_bytes() == sibling_bytes
    assert (await client.get(f"{base}/entities/{created.json()['external_id']}")).status_code == 200
    assert (await client.get(f"{base}/entities/{sibling.json()['external_id']}")).status_code == 200


@pytest.mark.asyncio
async def test_raw_file_deletion_overrides_lock(
    client: AsyncClient, test_project: Project, locked_note: LockedNote
) -> None:
    locked_note[1].unlink()
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert not locked_note[1].exists()
    assert (await client.get(locked_note[0])).status_code == 404


@pytest.mark.asyncio
async def test_raw_directory_deletion_overrides_lock(
    client: AsyncClient, test_project: Project, locked_note: LockedNote
) -> None:
    directory = locked_note[1].parent
    locked_note[1].unlink()
    directory.rmdir()
    await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert not directory.exists()
    assert (await client.get(locked_note[0])).status_code == 404
