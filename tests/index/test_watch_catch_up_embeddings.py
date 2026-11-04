"""A project's catch-up embedding must not hold live edits back (#1979).

The watcher catches a newly attached project up inside its batch consumer.
Embedding used to run inside that catch-up, so while the model loaded or a large
project embedded, no watch batch was handled for any project.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import override

import pytest

from basic_memory import db
from basic_memory.config import ConfigManager, ProjectEntry
from basic_memory.index.local_project import (
    LocalProjectIndexRuntime,
    LocalProjectIndexRuntimeFactory,
)
from basic_memory.index.local_schedulers import (
    BackgroundBatchVectorSync,
    BackgroundEmbeddingRuntimeFactory,
    drain_background_tasks,
)
from basic_memory.index.watch_service import FileChange, WatchService
from basic_memory.indexing.embedding_index_planning import EmbeddingBatchVectorSync
from basic_memory.models import Project
from basic_memory.read_cache import ReadCacheChange, ReadCacheInvalidationStatus
from basic_memory.repository import ProjectRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.runtime.vector_sync import VectorSyncBatchResult


@dataclass
class HeldVectorSync:
    """Embeds only after the test releases it, like a model that is still loading."""

    inner: EmbeddingBatchVectorSync
    release: asyncio.Event
    started: asyncio.Event
    synced: list[list[int]] = field(default_factory=list)

    async def sync_entity_vectors_batch(self, entity_ids: list[int]) -> VectorSyncBatchResult:
        self.started.set()
        await self.release.wait()
        self.synced.append(entity_ids)
        return await self.inner.sync_entity_vectors_batch(entity_ids)


@dataclass
class HeldEmbeddingRuntimeFactory:
    """The production runtime with its vector sync held until released."""

    release: asyncio.Event
    started: asyncio.Event = field(default_factory=asyncio.Event)
    held: list[HeldVectorSync] = field(default_factory=list)

    async def runtime_for_project(self, project: Project) -> LocalProjectIndexRuntime:
        runtime = await LocalProjectIndexRuntimeFactory().runtime_for_project(project)
        assert runtime.embedding_vector_sync is not None
        held = HeldVectorSync(runtime.embedding_vector_sync, self.release, self.started)
        self.held.append(held)
        return replace(runtime, embedding_vector_sync=held)


class SignallingWatchService(WatchService):
    """Signals once a batch naming the given file has been indexed."""

    def __init__(self, *args, signal_file: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.signal_file = signal_file
        self.handled = asyncio.Event()

    @override
    async def handle_changes(self, project: Project, changes: set[FileChange]) -> None:
        await super().handle_changes(project, changes)
        if any(Path(path).name == self.signal_file for _change, path in changes):
            self.handled.set()


async def _register_project(
    name: str,
    root: Path,
    config_manager: ConfigManager,
    project_repository: ProjectRepository,
    session_maker,
) -> Project:
    async with db.scoped_session(session_maker) as session:
        project = await project_repository.create(
            session,
            {"name": name, "path": str(root), "is_active": True, "is_default": False},
        )
    config = config_manager.config.model_copy(deep=True)
    config.projects[name] = ProjectEntry(path=str(root))
    config_manager.save_config(config)
    return project


@pytest.mark.asyncio
async def test_live_edit_is_indexed_while_catch_up_embedding_is_held(
    app_config, config_manager, project_repository, session_maker, test_project, tmp_path_factory
):
    root = tmp_path_factory.mktemp("held-embedding-root")
    (root / "before.md").write_text("# Before\n\n- [fact] unicorn\n", encoding="utf-8")
    added = await _register_project(
        "held-embedding", root, config_manager, project_repository, session_maker
    )
    release = asyncio.Event()
    runtime_factory = HeldEmbeddingRuntimeFactory(release)
    watcher = SignallingWatchService(
        app_config,
        project_repository,
        session_maker,
        initial_projects=[test_project],
        project_index_runtime_factory=BackgroundEmbeddingRuntimeFactory(
            runtime_factory, test_mode=False
        ),
        signal_file="live.md",
    )
    stop_event = asyncio.Event()
    cycle = asyncio.create_task(watcher._watch_projects_cycle([added], stop_event))
    try:
        # The catch-up indexed before.md and handed its vectors on; embedding is held.
        await asyncio.wait_for(runtime_factory.started.wait(), timeout=15)

        (root / "live.md").write_text("# Live\n\n- [fact] narwhal\n", encoding="utf-8")
        await asyncio.wait_for(watcher.handled.wait(), timeout=15)

        assert not release.is_set()
        async with db.scoped_session(session_maker) as session:
            repository = EntityRepository(added.id)
            before = await repository.get_by_file_path(session, "before.md")
            assert before is not None
            assert await repository.get_by_file_path(session, "live.md") is not None
    finally:
        release.set()
        stop_event.set()
        await asyncio.wait_for(cycle, timeout=5)
        await drain_background_tasks()

    # Released, the held catch-up embedding runs for the note it indexed.
    assert [before.id] in runtime_factory.held[0].synced


class RecordingVectorSync:
    def __init__(self) -> None:
        self.synced: list[list[int]] = []

    async def sync_entity_vectors_batch(self, entity_ids: list[int]) -> VectorSyncBatchResult:
        self.synced.append(entity_ids)
        return VectorSyncBatchResult(
            entities_total=len(entity_ids), entities_synced=len(entity_ids), entities_failed=0
        )


class RecordingReadCache:
    def __init__(self) -> None:
        self.changes: list[ReadCacheChange] = []

    async def invalidate(
        self, project_id: str, change: ReadCacheChange
    ) -> ReadCacheInvalidationStatus:
        self.changes.append(change)
        return ReadCacheInvalidationStatus.invalidated

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        return await self.invalidate(project_id, ReadCacheChange.whole_project())


@pytest.mark.asyncio
async def test_background_vector_sync_returns_before_embedding_and_retires_searches():
    vector_sync = RecordingVectorSync()
    read_cache = RecordingReadCache()
    background = BackgroundBatchVectorSync(
        vector_sync=vector_sync,
        project_external_id="project",
        read_cache=read_cache,
        test_mode=False,
    )

    result = await background.sync_entity_vectors_batch([3, 4])

    assert (result.entities_total, result.entities_synced) == (2, 0)
    assert vector_sync.synced == []
    await drain_background_tasks()
    assert vector_sync.synced == [[3, 4]]
    # Vectors change search results only, never a note's own response.
    assert read_cache.changes == [ReadCacheChange.search_index()]


@pytest.mark.asyncio
async def test_background_vector_sync_without_a_read_cache_still_embeds():
    vector_sync = RecordingVectorSync()
    background = BackgroundBatchVectorSync(
        vector_sync=vector_sync, project_external_id="project", read_cache=None, test_mode=False
    )

    await background.sync_entity_vectors_batch([7])
    await drain_background_tasks()

    assert vector_sync.synced == [[7]]


@dataclass
class NoEmbeddingRuntimeFactory:
    async def runtime_for_project(self, project: Project) -> LocalProjectIndexRuntime:
        runtime = await LocalProjectIndexRuntimeFactory().runtime_for_project(project)
        return replace(runtime, embedding_vector_sync=None)


@pytest.mark.asyncio
async def test_a_runtime_without_embeddings_is_left_alone(test_project):
    factory = BackgroundEmbeddingRuntimeFactory(NoEmbeddingRuntimeFactory(), test_mode=False)

    runtime = await factory.runtime_for_project(test_project)

    assert runtime.embedding_vector_sync is None
