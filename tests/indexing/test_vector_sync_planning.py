"""Tests for portable vector-sync plan construction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.progress import VectorSyncProgress
from basic_memory.indexing.vector_sync_planning import (
    RepositoryVectorSyncEntitySource,
    VectorSyncBatchProgressCallback,
    plan_vector_sync_progress,
    run_vector_sync,
)

if TYPE_CHECKING:
    from loguru import Record


@contextmanager
def capture_logs() -> Iterator[list[Record]]:
    """Capture loguru records emitted while the block runs."""
    records: list[Record] = []
    sink_id = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        yield records
    finally:
        logger.remove(sink_id)


@dataclass(slots=True)
class BatchSummary:
    """Minimal batch result for exercising portable vector-sync execution."""

    entities_synced: int
    entities_failed: int = 0
    failed_entity_ids: list[int] = field(default_factory=list)
    embedding_jobs_total: int = 0
    embed_seconds_total: float = 0.0
    write_seconds_total: float = 0.0


@dataclass(slots=True)
class RecordingVectorSync:
    """Fake vector sync adapter that records chunk calls."""

    results: list[BatchSummary]
    progress_events: list[tuple[int, int, int]] = field(default_factory=list)
    calls: list[list[int]] = field(default_factory=list)

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
        progress_callback: VectorSyncBatchProgressCallback | None = None,
    ) -> BatchSummary:
        self.calls.append(list(entity_ids))
        if progress_callback is not None:
            for entity_id, index, total_count in self.progress_events:
                progress_callback(entity_id, index, total_count)
        return self.results.pop(0)


@dataclass(frozen=True, slots=True)
class SequencePerfCounter:
    """Stateful stand-in for ``vector_sync_perf_counter`` returning queued values."""

    values: list[float]

    def __call__(self) -> float:
        return self.values.pop(0)


@dataclass(slots=True)
class FakeRowResult:
    """Minimal SQLAlchemy row result for repository entity-source tests."""

    rows: list[tuple[int]]

    def all(self) -> list[tuple[int]]:
        return self.rows


@dataclass(slots=True)
class FakeEntitySourceSession:
    """Record repository SQL calls and return queued row results."""

    results: list[FakeRowResult]
    calls: list[tuple[str, dict[str, int]]] = field(default_factory=list)

    async def execute(self, statement: object, params: dict[str, int]) -> FakeRowResult:
        self.calls.append((str(statement), params))
        return self.results.pop(0)


@dataclass(slots=True)
class FakeEntitySourceSessionContext:
    """Async context manager returned by the fake session maker."""

    session: FakeEntitySourceSession

    async def __aenter__(self) -> FakeEntitySourceSession:
        return self.session

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        return None


@dataclass(slots=True)
class FakeEntitySourceSessionMaker:
    """Callable session maker for repository entity-source tests."""

    session: FakeEntitySourceSession

    def __call__(self) -> FakeEntitySourceSessionContext:
        return FakeEntitySourceSessionContext(self.session)


@pytest.mark.asyncio
async def test_repository_vector_sync_entity_source_lists_project_entities() -> None:
    session = FakeEntitySourceSession(results=[FakeRowResult([(3,), (5,)])])
    source = RepositoryVectorSyncEntitySource(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeEntitySourceSessionMaker(session)),
        project_id=7,
    )

    assert await source.list_project_entity_ids() == [3, 5]
    assert session.calls[0][1] == {"project_id": 7}
    assert "WHERE project_id = :project_id" in session.calls[0][0]
    assert "ORDER BY id" in session.calls[0][0]


@pytest.mark.asyncio
async def test_repository_vector_sync_entity_source_lists_markdown_entities() -> None:
    session = FakeEntitySourceSession(results=[FakeRowResult([(11,), (13,)])])
    source = RepositoryVectorSyncEntitySource(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeEntitySourceSessionMaker(session)),
        project_id=7,
    )

    assert await source.list_markdown_entity_ids() == [11, 13]
    assert session.calls[0][1] == {"project_id": 7}
    assert "content_type = 'text/markdown'" in session.calls[0][0]


@pytest.mark.asyncio
async def test_repository_vector_sync_entity_source_filters_markdown_entities() -> None:
    session = FakeEntitySourceSession(results=[FakeRowResult([(10,), (30,)])])
    source = RepositoryVectorSyncEntitySource(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeEntitySourceSessionMaker(session)),
        project_id=7,
    )

    assert await source.filter_markdown_entity_ids({30, 10, 20}) == {10, 30}
    assert session.calls[0][1] == {
        "project_id": 7,
        "entity_id_0": 10,
        "entity_id_1": 20,
        "entity_id_2": 30,
    }
    assert "id IN (:entity_id_0, :entity_id_1, :entity_id_2)" in session.calls[0][0]


@pytest.mark.asyncio
async def test_repository_vector_sync_entity_source_skips_empty_filter() -> None:
    session = FakeEntitySourceSession(results=[])
    source = RepositoryVectorSyncEntitySource(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeEntitySourceSessionMaker(session)),
        project_id=7,
    )

    assert await source.filter_markdown_entity_ids(set()) == set()
    assert session.calls == []


def test_vector_sync_plan_starts_new_progress_for_non_resume_phase() -> None:
    resume_progress = VectorSyncProgress(
        entity_ids=[11, 22],
        next_index=1,
        entities_synced=1,
        embedding_jobs_total=20,
    )

    planned = plan_vector_sync_progress(
        checkpoint_phase="relations_complete",
        candidate_entity_ids=[22, 33, 11, 44, 33],
        resume_progress=resume_progress,
    )

    assert planned is not resume_progress
    assert planned.entity_ids == [11, 22, 33, 44]
    assert planned.next_index == 0
    assert planned.entities_synced == 0
    assert planned.embedding_jobs_total == 0


def test_vector_sync_plan_reuses_resume_state_for_vector_resume_phase() -> None:
    resume_progress = VectorSyncProgress(
        entity_ids=[10, 20],
        next_index=1,
        entities_synced=1,
        entities_failed=0,
        embed_seconds_total=2.5,
        write_seconds_total=0.5,
        elapsed_seconds=3.0,
    )

    planned = plan_vector_sync_progress(
        checkpoint_phase="syncing_vectors",
        candidate_entity_ids=[20, 30],
        resume_progress=resume_progress,
    )

    assert planned is resume_progress
    assert planned.entity_ids == [10, 20, 30]
    assert planned.next_index == 1
    assert planned.entities_synced == 1
    assert planned.embed_seconds_total == 2.5


def test_vector_sync_plan_uses_resume_state_after_forward_refs_complete() -> None:
    resume_progress = VectorSyncProgress(entity_ids=[1])

    planned = plan_vector_sync_progress(
        checkpoint_phase="forward_refs_complete",
        candidate_entity_ids=[2, 1, 3],
        resume_progress=resume_progress,
    )

    assert planned is resume_progress
    assert planned.entity_ids == [1, 2, 3]


def test_vector_sync_plan_dedupes_candidates_for_empty_resume() -> None:
    planned = plan_vector_sync_progress(
        checkpoint_phase=None,
        candidate_entity_ids=[5, 5, 6],
        resume_progress=VectorSyncProgress(),
    )

    assert planned.entity_ids == [5, 6]
    assert planned.entities_total == 2


@pytest.mark.asyncio
async def test_run_vector_sync_resumes_from_chunk_boundary_and_reports_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_sync = RecordingVectorSync(
        results=[
            BatchSummary(
                entities_synced=25,
                entities_failed=1,
                failed_entity_ids=[125],
                embedding_jobs_total=50,
                embed_seconds_total=3.0,
                write_seconds_total=1.0,
            )
        ]
    )
    monkeypatch.setattr(
        "basic_memory.indexing.vector_sync_planning.vector_sync_perf_counter",
        SequencePerfCounter([20.0, 20.0, 23.0]),
    )

    with capture_logs() as records:
        resumed = await run_vector_sync(
            list(range(1, 126)),
            vector_sync=vector_sync,
            logger=logger,
            resume_progress=VectorSyncProgress(
                entity_ids=list(range(1, 126)),
                next_index=100,
                entities_synced=100,
                entities_failed=0,
                embedding_jobs_total=200,
                embed_seconds_total=10.0,
                write_seconds_total=2.0,
                elapsed_seconds=15.0,
            ),
            project_id=7,
        )

    assert vector_sync.calls == [list(range(101, 126))]
    assert resumed.next_index == 125
    assert resumed.entities_synced == 125
    assert resumed.entities_failed == 1
    assert resumed.failed_entity_ids == [125]
    assert resumed.embedding_jobs_total == 250
    assert resumed.embed_seconds_total == 13.0
    assert resumed.write_seconds_total == 3.0
    assert resumed.elapsed_seconds == 18.0
    errors = [record for record in records if record["level"].name == "ERROR"]
    infos = [record for record in records if record["level"].name == "INFO"]
    assert [(record["message"], record["extra"]) for record in errors] == [
        ("❌ [VECTOR] Failed to sync entity 125", {})
    ]
    # The completion log binds project_id as structured context.
    assert infos[-1]["extra"] == {"project_id": 7}


@pytest.mark.asyncio
async def test_run_vector_sync_returns_empty_progress_without_executor_call() -> None:
    vector_sync = RecordingVectorSync(results=[])

    with capture_logs() as records:
        progress = await run_vector_sync([], vector_sync=vector_sync, logger=logger)

    assert progress == VectorSyncProgress()
    assert vector_sync.calls == []
    # An empty candidate set returns before emitting any progress logs.
    assert records == []


@pytest.mark.asyncio
async def test_run_vector_sync_logs_periodic_batch_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_sync = RecordingVectorSync(
        results=[BatchSummary(entities_synced=3)],
        progress_events=[(1, 1, 3)],
    )
    monkeypatch.setattr(
        "basic_memory.indexing.vector_sync_planning.vector_sync_perf_counter",
        SequencePerfCounter([0.0, 0.0, 6.5, 8.0]),
    )

    with capture_logs() as records:
        await run_vector_sync(
            [1, 2, 3],
            vector_sync=vector_sync,
            logger=logger,
        )

    infos = [record for record in records if record["level"].name == "INFO"]
    assert infos[0]["message"] == (
        "🧠 [VECTOR] Progress: 1/3 entities (6.5s previous entity, 6.5s total, 0.2 entities/s)"
    )


@pytest.mark.asyncio
async def test_run_vector_sync_rejects_invalid_chunk_size() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        await run_vector_sync(
            [1],
            vector_sync=RecordingVectorSync(results=[]),
            logger=logger,
            chunk_size=0,
        )
