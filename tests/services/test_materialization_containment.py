"""Materialization and cleanup never inspect or remove another root's notes."""

import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from basic_memory.index.note_content_materialization import (
    InlineNoteFileDeleteEnqueuer,
    LocalNoteContentStorage,
)
from basic_memory.runtime.cleanup import RuntimeNoteFileDeleteJobRequest
from basic_memory.runtime.note_content import RuntimeNoteMaterializationJobRequest
from basic_memory.runtime.note_materialization import (
    plan_prepared_note_write,
    write_prepared_note_to_content_store,
)
from basic_memory.services.exceptions import FileOperationError
from basic_memory.services.file_service import FileService


@pytest.mark.skipif(os.name == "nt", reason="Requires unprivileged symlinks and hard links")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "materialize-reuse",
        "cleanup",
        "alias-source",
        "alias-live",
        "exists",
        "checksum",
        "metadata",
        "delete",
        "guarded-delete",
    ],
)
async def test_materialization_rejects_outside_storage_access(tmp_path: Path, operation: str):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    content = "# Accepted note\n"
    secret = outside / "note.md"
    secret.write_bytes(content.encode())
    checksum = sha256(secret.read_bytes()).hexdigest()
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    unsafe = "redirect/note.md"
    storage = LocalNoteContentStorage(FileService(root))

    with pytest.raises(FileOperationError, match="outside.*project"):
        match operation:
            case "materialize-reuse":
                prepared = plan_prepared_note_write(
                    request=RuntimeNoteMaterializationJobRequest(
                        project_id=1, entity_id=1, db_version=1, db_checksum=checksum
                    ),
                    file_path=unsafe,
                    markdown_content=content,
                    previous_file_checksum=None,
                    previous_sync_checksum=None,
                    attempted_at=datetime.now(UTC),
                )
                await write_prepared_note_to_content_store(storage, prepared)
            case "cleanup" | "alias-source" | "alias-live":
                if operation != "cleanup":
                    os.link(secret, root / "live.md")
                await InlineNoteFileDeleteEnqueuer(storage).enqueue_note_file_delete(
                    RuntimeNoteFileDeleteJobRequest(
                        project_id=1,
                        entity_id=1,
                        file_path="live.md" if operation == "alias-live" else unsafe,
                        file_checksum=checksum,
                        live_file_path=(
                            unsafe
                            if operation == "alias-live"
                            else "live.md"
                            if operation == "alias-source"
                            else None
                        ),
                    )
                )
            case "exists":
                await storage.exists(unsafe)
            case "checksum":
                await storage.compute_checksum(unsafe)
            case "metadata":
                await storage.get_file_metadata(unsafe)
            case "delete":
                await storage.delete_file(unsafe)
            case "guarded-delete":
                await storage.delete_file_if_matches(unsafe, expected_checksum=checksum)
            case _:
                raise AssertionError(operation)
    assert secret.read_bytes() == content.encode()
