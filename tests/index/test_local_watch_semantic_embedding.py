"""End-to-end semantic embedding through the local watcher (#1016).

Marked ``semantic`` because it runs the real fastembed + sqlite-vec stack. The
watcher must produce vector chunks for files it indexes, not just full-text and
relation rows, so externally edited notes are semantically searchable without a
manual reindex.
"""

from pathlib import Path

import pytest
from sqlalchemy import text
from watchfiles import Change

from basic_memory import config as config_module
from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.index.watch_service import WatchService


def _enable_semantic(app_config: BasicMemoryConfig, config_manager: ConfigManager) -> None:
    """Persist semantic search so the watcher's search service embeds for real."""
    pytest.importorskip("sqlite_vec")
    pytest.importorskip("fastembed")
    app_config.semantic_search_enabled = True
    config_manager.save_config(app_config)
    # The watcher resolves config through ConfigManager().config; clear the cache
    # so the persisted semantic flag is observed on the next read.
    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None


@pytest.mark.semantic
@pytest.mark.asyncio
async def test_local_watcher_embeds_indexed_file(
    app_config: BasicMemoryConfig,
    config_manager: ConfigManager,
    project_repository,
    session_maker,
    test_project,
    project_config,
) -> None:
    """A file indexed by the watcher should produce semantic vector chunks."""
    _enable_semantic(app_config, config_manager)

    note_path = Path(project_config.home) / "semantic" / "note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "---\ntitle: Semantic Note\ntype: note\n---\n\n"
        "# Semantic Note\n\nThe gravitational pull of the moon drives ocean tides.\n",
        encoding="utf-8",
    )

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(
            index_embeddings=app_config.semantic_search_enabled,
        ),
    )

    await watch_service.handle_changes(test_project, {(Change.added, str(note_path))})

    assert watch_service.state.indexed_files == 1

    async with db.scoped_session(session_maker) as session:
        chunk_count = (
            await session.execute(
                text("SELECT count(*) FROM search_vector_chunks WHERE project_id = :pid"),
                {"pid": test_project.id},
            )
        ).scalar_one()

    # Without the #1016 fix the watcher indexes FTS + relations but writes no
    # vector chunks, so this count would be zero.
    assert chunk_count > 0
