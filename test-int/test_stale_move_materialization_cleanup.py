"""A move whose materialization goes stale must still clean up the path it left.

Accepted writes reach storage through deferred materialization jobs, one per
write. A move's job is the only one that knows the path the note left; it
writes the note at its new path and then deletes the old file. When another
write to the same note is accepted before the move's job runs (an undo that
moves a note back and then restores its text, an agent that edits and then
moves), the move's job finds the note changed and exits as stale. The later
job writes the new path, but nothing deletes the old file: it stays in storage
with old content, and a later move back onto that path is refused as a conflict
(basic-memory-cloud, development, 2026-10-23).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from basic_memory.index.note_content_materialization import LocalNoteContentMaterializationProvider
from basic_memory.models import Project
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeNoteContentResponsePayload,
)


class HeldMaterializations:
    """Hold accepted writes, then materialize them in acceptance order.

    The test seam: local test mode materializes inline, so a later write could
    never be accepted before an earlier write's job ran. Holding the jobs and
    releasing them in order reproduces what the deferred queue does in
    production; each release runs the real provider.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.holding = False
        self.held: list[
            tuple[
                LocalNoteContentMaterializationProvider,
                RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
            ]
        ] = []
        original = LocalNoteContentMaterializationProvider.materialize_write_change
        self._original = original

        async def materialize_write_change(provider, accepted):
            if self.holding:
                self.held.append((provider, accepted))
                return accepted
            return await original(provider, accepted)

        monkeypatch.setattr(
            LocalNoteContentMaterializationProvider,
            "materialize_write_change",
            materialize_write_change,
        )

    async def release(self) -> None:
        self.holding = False
        held, self.held = self.held, []
        for provider, accepted in held:
            await self._original(provider, accepted)


@pytest.mark.asyncio
async def test_a_move_whose_materialization_goes_stale_still_removes_the_old_file(
    app: FastAPI,
    client: AsyncClient,
    test_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = HeldMaterializations(monkeypatch)
    project_url = f"/v2/projects/{test_project.external_id}"
    root = Path(test_project.path)

    # --- A note that lives in archive/, its file written ---
    created = await client.post(
        f"{project_url}/knowledge/entities",
        json={"title": "Edit Then Move", "directory": "archive", "content": "Agent text.\n"},
    )
    assert created.status_code == 202, created.text
    note_id = created.json()["external_id"]
    archived = root / "archive/Edit Then Move.md"
    assert archived.exists()

    # --- Move it back, then restore its text, before either job runs ---
    jobs.holding = True
    moved = await client.put(
        f"{project_url}/knowledge/entities/{note_id}/move",
        json={"destination_path": "targets/Edit Then Move.md"},
    )
    assert moved.status_code == 202, moved.text
    restored = await client.put(
        f"{project_url}/knowledge/entities/{note_id}",
        json={"title": "Edit Then Move", "directory": "targets", "content": "Original text.\n"},
    )
    assert restored.status_code == 202, restored.text
    assert len(jobs.held) == 2

    # --- The jobs run in acceptance order: the move's finds the note changed ---
    await jobs.release()

    current = root / "targets/Edit Then Move.md"
    assert "Original text." in current.read_text()
    # The path the note left must not keep its old file.
    assert not archived.exists(), "the moved-away file was left in storage"

    # --- Moving onto that path again writes the note's current text there ---
    moved_again = await client.put(
        f"{project_url}/knowledge/entities/{note_id}/move",
        json={"destination_path": "archive/Edit Then Move.md"},
    )
    assert moved_again.status_code == 202, moved_again.text
    assert "Original text." in archived.read_text()
    assert not current.exists()
