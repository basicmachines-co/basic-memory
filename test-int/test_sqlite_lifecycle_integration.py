"""Real file-backed SQLite contention and CLI-exit probes for #1430/#1322.

No platform simulation: Windows CI selects the production Windows engine path.
The CLI probe disables embeddings to isolate database lifecycle from ONNX work.
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from sqlalchemy import event, text

from basic_memory.db import DatabaseType, _create_sqlite_engine


@pytest.mark.asyncio
async def test_file_sqlite_concurrent_writes_commit_and_dispose(tmp_path: Path) -> None:
    engine = _create_sqlite_engine(
        DatabaseType.get_db_url(tmp_path / "contention.db", DatabaseType.FILESYSTEM),
        DatabaseType.FILESYSTEM,
    )
    connections = 0

    @event.listens_for(engine.sync_engine, "connect")
    def count_connection(connection: object, record: object) -> None:
        nonlocal connections
        connections += 1

    started = time.monotonic()
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
            assert (await connection.execute(text("PRAGMA busy_timeout"))).scalar() == 10000
            await connection.execute(text("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)"))
            await connection.execute(text("CREATE VIRTUAL TABLE notes_fts USING fts5(body)"))

        async def write_notes(writer: int) -> None:
            for index in range(20):
                note_id = writer * 20 + index
                async with engine.begin() as connection:
                    await connection.execute(
                        text("INSERT INTO notes VALUES (:id, :body)"),
                        {"id": note_id, "body": "searchable note"},
                    )
                    await connection.execute(
                        text("INSERT INTO notes_fts(rowid, body) VALUES (:id, :body)"),
                        {"id": note_id, "body": "searchable note"},
                    )
                    assert (
                        await connection.execute(
                            text("SELECT body FROM notes WHERE id = :id"), {"id": note_id}
                        )
                    ).scalar_one() == "searchable note"

        async with asyncio.timeout(90), asyncio.TaskGroup() as writers:
            for writer in range(32):
                writers.create_task(write_notes(writer))
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT count(*) FROM notes"))).scalar() == 640
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM notes_fts WHERE notes_fts MATCH 'searchable'")
                )
            ).scalar() == 640
            assert (await connection.execute(text("PRAGMA integrity_check"))).scalar() == "ok"
    finally:
        await asyncio.wait_for(engine.dispose(), 15)
        print(
            f"pool={type(engine.pool).__name__} connections={connections} "
            f"elapsed={time.monotonic() - started:.2f}s"
        )


def test_reindex_subprocess_exits_after_full_and_incremental_sqlite_runs(tmp_path: Path) -> None:
    project = tmp_path / "notes"
    project.mkdir()
    for index in range(15):
        (project / f"note-{index}.md").write_text(
            f"# Note {index}\n\n- [fact] searchable lifecycle probe\n", encoding="utf-8"
        )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "projects": {"probe": {"path": str(project), "mode": "local"}},
                "default_project": "probe",
                "semantic_search_enabled": False,
                "auto_update": False,
            }
        ),
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("BASIC_MEMORY_") and key != "PYTEST_CURRENT_TEST"
    }
    environment["BASIC_MEMORY_CONFIG_DIR"] = str(config_dir)
    environment["PYTHONUTF8"] = "1"
    # Native thread stacks survive in the CI artifact if the child wedges.
    entrypoint = (
        "import faulthandler; faulthandler.dump_traceback_later(60); "
        "from basic_memory.cli.main import app; app()"
    )
    for arguments in (["reindex", "--full"], ["reindex"]):
        try:
            result = subprocess.run(
                [sys.executable, "-c", entrypoint, *arguments],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
            )
        except subprocess.TimeoutExpired as error:
            pytest.fail(f"CLI did not exit: {arguments}\n{error.stdout}\n{error.stderr}")
        print(result.stdout)
        print(result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Reindex complete!" in result.stdout
