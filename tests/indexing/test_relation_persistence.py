"""Tests for generation-owned relation publication orchestration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import AsyncIterator, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.relation_persistence as relation_persistence_module
from basic_memory.indexing.models import IndexedRelation
from basic_memory.indexing.relation_persistence import RelationGenerationPublisher
from basic_memory.repository.relation_repository import (
    AcceptedRelationWrite,
    RelationGenerationWriteResult,
)


@dataclass(slots=True)
class RecordingRelationGenerationStore:
    """Record the statement sequence produced by the publisher."""

    generation_is_current: bool = True
    calls: list[tuple[str, int, tuple[AcceptedRelationWrite, ...]]] = field(default_factory=list)

    async def upsert_relation_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> RelationGenerationWriteResult:
        assert session is not None
        self.calls.append(("upsert", generation, tuple(relations)))
        return RelationGenerationWriteResult(generation_is_current=self.generation_is_current)

    async def cleanup_relation_generations(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult:
        assert session is not None
        self.calls.append(("cleanup", generation, ()))
        return RelationGenerationWriteResult(generation_is_current=self.generation_is_current)


@pytest.mark.asyncio
async def test_relation_generation_publisher_commits_sorted_chunks_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every bounded chunk and the final cleanup own a separate transaction."""
    sessions: list[AsyncSession] = []

    @asynccontextmanager
    async def fake_scoped_session(
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert session_maker is not None
        session = cast(AsyncSession, object())
        sessions.append(session)
        yield session

    monkeypatch.setattr(
        relation_persistence_module.db,
        "scoped_session",
        fake_scoped_session,
    )
    store = RecordingRelationGenerationStore()
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )
    relations = [
        IndexedRelation(
            relation_type="links_to",
            target_name=f"Target {index:03d}",
            context=None,
            target_id=42 if index == 0 else None,
        )
        for index in reversed(range(251))
    ]

    generation_is_current = await publisher.publish(
        entity_id=42,
        generation=7,
        relations=relations,
    )

    assert generation_is_current
    assert [call[0] for call in store.calls] == ["upsert", "upsert", "cleanup"]
    assert [len(call[2]) for call in store.calls] == [250, 1, 0]
    published_names = [
        relation.target_name
        for operation, _, relation_chunk in store.calls
        if operation == "upsert"
        for relation in relation_chunk
    ]
    assert published_names == sorted(published_names)
    published_targets = [
        relation.target_id
        for operation, _, relation_chunk in store.calls
        if operation == "upsert"
        for relation in relation_chunk
    ]
    assert published_targets.count(42) == 1
    assert len(sessions) == 3
    assert len({id(session) for session in sessions}) == 3


@pytest.mark.asyncio
async def test_relation_generation_publisher_stops_when_source_fence_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost source claim cannot continue into later chunks or cleanup."""
    transaction_count = 0

    @asynccontextmanager
    async def fake_scoped_session(
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        nonlocal transaction_count
        assert session_maker is not None
        transaction_count += 1
        yield cast(AsyncSession, object())

    monkeypatch.setattr(
        relation_persistence_module.db,
        "scoped_session",
        fake_scoped_session,
    )
    store = RecordingRelationGenerationStore(generation_is_current=False)
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    generation_is_current = await publisher.publish(
        entity_id=42,
        generation=6,
        relations=[IndexedRelation("links_to", "Target", None)],
    )

    assert not generation_is_current
    assert [call[0] for call in store.calls] == ["upsert"]
    assert transaction_count == 1


@pytest.mark.asyncio
async def test_relation_generation_publisher_rejects_non_self_pre_resolved_target() -> None:
    """Ordinary targets remain resolver-owned even when a caller supplies an ID."""
    store = RecordingRelationGenerationStore()
    publisher = RelationGenerationPublisher(
        relation_repository=store,
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
    )

    with pytest.raises(ValueError, match="Only the source entity"):
        await publisher.publish(
            entity_id=42,
            generation=7,
            relations=[IndexedRelation("links_to", "Target", None, target_id=99)],
        )

    assert store.calls == []
