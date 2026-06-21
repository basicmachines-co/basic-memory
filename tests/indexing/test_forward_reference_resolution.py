"""Tests for portable forward-reference resolution planning."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, dataclass
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.forward_reference_resolution as forward_resolution_module
from basic_memory.indexing.forward_reference_resolution import (
    ForwardReferenceLinkResolver,
    ForwardReferenceResolutionPlan,
    ForwardReferenceResolutionRun,
    ForwardReferenceUpdate,
    RepositoryForwardReferenceEntityRefreshRuntime,
    RepositoryForwardReferenceRelationSource,
    RepositoryForwardReferenceResolutionRuntime,
    collect_forward_reference_link_texts,
    plan_forward_reference_resolution,
    run_forward_reference_entity_refresh,
    run_forward_reference_resolution,
)
from basic_memory.models import Entity


@dataclass(frozen=True, slots=True)
class StubUnresolvedRelation:
    id: int
    from_id: int
    to_name: str | None


class RecordingForwardReferenceRuntime:
    def __init__(self, resolved_targets: dict[str, int | None]) -> None:
        self.resolved_targets = resolved_targets
        self.resolve_calls: list[tuple[str, ...]] = []
        self.applied_updates: tuple[ForwardReferenceUpdate, ...] = ()

    async def resolve_forward_reference_link_texts(
        self,
        link_texts: Sequence[str],
    ) -> dict[str, int | None]:
        self.resolve_calls.append(tuple(link_texts))
        return self.resolved_targets

    async def apply_forward_reference_updates(
        self,
        updates: Sequence[ForwardReferenceUpdate],
    ) -> None:
        self.applied_updates = tuple(updates)


@dataclass(slots=True)
class RecordingForwardReferenceLinkResolver:
    resolved_targets: dict[str, int | None]
    calls: list[tuple[str, ...]]

    async def resolve_link_texts(
        self,
        link_texts: Sequence[str],
    ) -> dict[str, int | None]:
        self.calls.append(tuple(link_texts))
        return self.resolved_targets


class RecordingForwardReferenceEntityRefreshRuntime:
    def __init__(self, outcomes: dict[int, bool | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[int] = []

    async def refresh_forward_reference_entity(self, entity_id: int) -> bool:
        self.calls.append(entity_id)
        outcome = self.outcomes[entity_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingForwardReferenceEntityRepository:
    def __init__(self, entity: Entity | None) -> None:
        self.entity = entity
        self.calls: list[tuple[object, int]] = []

    async def find_by_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> Entity | None:
        self.calls.append((session, entity_id))
        return self.entity


class RecordingForwardReferenceEntityIndexer:
    def __init__(self) -> None:
        self.entities: list[Entity] = []

    async def index_entity(self, entity: Entity) -> None:
        self.entities.append(entity)


class FakeForwardReferenceScalarResult:
    """Minimal scalar result stand-in for repository runtime tests."""

    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeForwardReferenceResult:
    """Minimal SQLAlchemy result stand-in for repository runtime tests."""

    def __init__(self, *, scalar_values: list[object] | None = None) -> None:
        self.scalar_values = scalar_values or []

    def scalars(self) -> FakeForwardReferenceScalarResult:
        return FakeForwardReferenceScalarResult(self.scalar_values)


class FakeForwardReferenceSession:
    """Record relation update statements issued by the repository runtime."""

    def __init__(self, results: list[FakeForwardReferenceResult] | None = None) -> None:
        self.results = results or []
        self.statements: list[object] = []

    async def execute(self, statement: object) -> FakeForwardReferenceResult:
        self.statements.append(statement)
        if self.results:
            return self.results.pop(0)
        return FakeForwardReferenceResult()


def test_collect_forward_reference_link_texts_dedupes_in_first_seen_order() -> None:
    relations = [
        StubUnresolvedRelation(id=1, from_id=10, to_name="Target"),
        StubUnresolvedRelation(id=2, from_id=11, to_name="Other"),
        StubUnresolvedRelation(id=3, from_id=12, to_name="Target"),
        StubUnresolvedRelation(id=4, from_id=13, to_name=None),
        StubUnresolvedRelation(id=5, from_id=14, to_name=""),
    ]

    assert collect_forward_reference_link_texts(relations) == ("Target", "Other")


def test_plan_forward_reference_resolution_filters_only_exact_safe_updates() -> None:
    relations = [
        StubUnresolvedRelation(id=1, from_id=10, to_name="Target"),
        StubUnresolvedRelation(id=2, from_id=11, to_name="Missing"),
        StubUnresolvedRelation(id=3, from_id=12, to_name="Self"),
        StubUnresolvedRelation(id=4, from_id=13, to_name=None),
        StubUnresolvedRelation(id=5, from_id=14, to_name="Target"),
    ]

    plan = plan_forward_reference_resolution(
        relations,
        {
            "Target": 99,
            "Missing": None,
            "Self": 12,
        },
    )

    assert plan == ForwardReferenceResolutionPlan(
        unresolved_before=5,
        link_texts=("Target", "Missing", "Self"),
        updates=(
            ForwardReferenceUpdate(
                relation_id=1,
                source_entity_id=10,
                target_entity_id=99,
                link_text="Target",
            ),
            ForwardReferenceUpdate(
                relation_id=5,
                source_entity_id=14,
                target_entity_id=99,
                link_text="Target",
            ),
        ),
        entity_ids_to_refresh=frozenset({99}),
    )
    assert plan.resolved_count == 2
    assert plan.remaining_count == 3
    assert plan.has_updates is True


def test_forward_reference_resolution_plan_is_immutable() -> None:
    plan = plan_forward_reference_resolution(
        [StubUnresolvedRelation(id=1, from_id=10, to_name="Target")],
        {"Target": 20},
    )

    with pytest.raises(FrozenInstanceError):
        setattr(plan, "updates", ())


@pytest.mark.asyncio
async def test_run_forward_reference_resolution_applies_updates_once() -> None:
    runtime = RecordingForwardReferenceRuntime({"Target": 20, "Missing": None})
    relations = (
        StubUnresolvedRelation(id=1, from_id=10, to_name="Target"),
        StubUnresolvedRelation(id=2, from_id=11, to_name="Missing"),
    )

    result = await run_forward_reference_resolution(runtime, relations)

    assert result == ForwardReferenceResolutionRun(
        plan=ForwardReferenceResolutionPlan(
            unresolved_before=2,
            link_texts=("Target", "Missing"),
            updates=(
                ForwardReferenceUpdate(
                    relation_id=1,
                    source_entity_id=10,
                    target_entity_id=20,
                    link_text="Target",
                ),
            ),
            entity_ids_to_refresh=frozenset({20}),
        ),
        resolved_link_text_count=1,
    )
    assert result.unresolved_before == 2
    assert result.resolved_count == 1
    assert result.remaining_count == 1
    assert result.entity_ids_to_refresh == frozenset({20})
    assert runtime.resolve_calls == [("Target", "Missing")]
    assert runtime.applied_updates == result.plan.updates


@pytest.mark.asyncio
async def test_run_forward_reference_resolution_skips_apply_without_updates() -> None:
    runtime = RecordingForwardReferenceRuntime({"Missing": None})

    result = await run_forward_reference_resolution(
        runtime,
        (StubUnresolvedRelation(id=1, from_id=10, to_name="Missing"),),
    )

    assert result.resolved_count == 0
    assert result.remaining_count == 1
    assert result.entity_ids_to_refresh == frozenset()
    assert runtime.resolve_calls == [("Missing",)]
    assert runtime.applied_updates == ()


@pytest.mark.asyncio
async def test_repository_forward_reference_runtime_uses_link_resolver() -> None:
    calls: list[tuple[str, ...]] = []
    link_resolver: ForwardReferenceLinkResolver = RecordingForwardReferenceLinkResolver(
        resolved_targets={"Target": 20, "Missing": None},
        calls=calls,
    )

    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        link_resolver=link_resolver,
    )

    result = await runtime.resolve_forward_reference_link_texts(("Target", "Missing"))

    assert result == {"Target": 20, "Missing": None}
    assert calls == [("Target", "Missing")]


@pytest.mark.asyncio
async def test_repository_forward_reference_relation_source_lists_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    unresolved = [
        StubUnresolvedRelation(id=1, from_id=10, to_name="Target"),
        StubUnresolvedRelation(id=2, from_id=11, to_name="Other"),
    ]
    session = FakeForwardReferenceSession(
        results=[FakeForwardReferenceResult(scalar_values=list(unresolved))]
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeForwardReferenceSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(forward_resolution_module.db, "scoped_session", fake_scoped_session)

    source = RepositoryForwardReferenceRelationSource(
        session_maker=session_maker,
        project_id=7,
    )

    result = await source.list_unresolved_forward_references()

    assert result == tuple(unresolved)
    assert len(session.statements) == 1
    assert "WHERE relation.project_id = :project_id_1" in str(session.statements[0])
    assert "relation.to_id IS NULL" in str(session.statements[0])


@pytest.mark.asyncio
async def test_repository_forward_reference_runtime_applies_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeForwardReferenceSession()
    link_resolver: ForwardReferenceLinkResolver = RecordingForwardReferenceLinkResolver(
        resolved_targets={},
        calls=[],
    )

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeForwardReferenceSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(forward_resolution_module.db, "scoped_session", fake_scoped_session)

    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=session_maker,
        link_resolver=link_resolver,
    )

    await runtime.apply_forward_reference_updates(
        (
            ForwardReferenceUpdate(
                relation_id=1,
                source_entity_id=10,
                target_entity_id=20,
                link_text="Target",
            ),
            ForwardReferenceUpdate(
                relation_id=2,
                source_entity_id=11,
                target_entity_id=21,
                link_text="Other",
            ),
        )
    )

    assert len(session.statements) == 1
    statement_text = str(session.statements[0])
    assert "UPDATE relation" in statement_text
    assert "relation.id IN" in statement_text


@pytest.mark.asyncio
async def test_repository_forward_reference_runtime_skips_empty_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link_resolver: ForwardReferenceLinkResolver = RecordingForwardReferenceLinkResolver(
        resolved_targets={},
        calls=[],
    )

    @asynccontextmanager
    async def fake_scoped_session(
        _scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeForwardReferenceSession]:
        raise AssertionError("empty updates should not open a session")
        yield FakeForwardReferenceSession()

    monkeypatch.setattr(forward_resolution_module.db, "scoped_session", fake_scoped_session)

    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        link_resolver=link_resolver,
    )

    await runtime.apply_forward_reference_updates(())


@pytest.mark.asyncio
async def test_repository_forward_reference_entity_refresh_indexes_existing_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = cast(AsyncSession, object())
    entity = cast(Entity, SimpleNamespace(id=20))
    entity_repository = RecordingForwardReferenceEntityRepository(entity)
    entity_indexer = RecordingForwardReferenceEntityIndexer()

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(forward_resolution_module.db, "scoped_session", fake_scoped_session)

    runtime = RepositoryForwardReferenceEntityRefreshRuntime(
        session_maker=session_maker,
        entity_repository=entity_repository,
        entity_indexer=entity_indexer,
    )

    refreshed = await runtime.refresh_forward_reference_entity(20)

    assert refreshed is True
    assert entity_repository.calls == [(session, 20)]
    assert entity_indexer.entities == [entity]


@pytest.mark.asyncio
async def test_repository_forward_reference_entity_refresh_reports_missing_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = cast(AsyncSession, object())
    entity_repository = RecordingForwardReferenceEntityRepository(None)
    entity_indexer = RecordingForwardReferenceEntityIndexer()

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(forward_resolution_module.db, "scoped_session", fake_scoped_session)

    runtime = RepositoryForwardReferenceEntityRefreshRuntime(
        session_maker=session_maker,
        entity_repository=entity_repository,
        entity_indexer=entity_indexer,
    )

    refreshed = await runtime.refresh_forward_reference_entity(20)

    assert refreshed is False
    assert entity_repository.calls == [(session, 20)]
    assert entity_indexer.entities == []


@pytest.mark.asyncio
async def test_run_forward_reference_entity_refresh_collects_success_missing_and_failures() -> None:
    error = RuntimeError("index failed")
    runtime = RecordingForwardReferenceEntityRefreshRuntime(
        {
            10: True,
            20: False,
            30: error,
        }
    )

    result = await run_forward_reference_entity_refresh(runtime, (10, 20, 30))

    assert runtime.calls == [10, 20, 30]
    assert result.successful_entity_ids == frozenset({10})
    assert result.missing_entity_ids == frozenset({20})
    assert result.failed_entity_ids == frozenset({30})
    assert len(result.failures) == 1
    assert result.failures[0].entity_id == 30
    assert result.failures[0].error is error


@pytest.mark.asyncio
async def test_run_forward_reference_resolution_skips_resolution_without_link_texts() -> None:
    runtime = RecordingForwardReferenceRuntime({})

    result = await run_forward_reference_resolution(
        runtime,
        (StubUnresolvedRelation(id=1, from_id=10, to_name=None),),
    )

    assert result.resolved_link_text_count == 0
    assert result.link_texts == ()
    assert runtime.resolve_calls == []
    assert runtime.applied_updates == ()
