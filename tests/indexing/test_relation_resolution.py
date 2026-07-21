"""Tests for portable relation resolution orchestration."""

from collections.abc import Sequence
from dataclasses import FrozenInstanceError, dataclass
from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.relation_resolution import (
    IndexFileRelationResolutionContext,
    ProjectIndexRelationResolutionContext,
    RESOLVE_RELATIONS_DEBOUNCE_SECONDS,
    RepositoryRelationResolutionRuntime,
    ResolveRelationsJobRequest,
    ResolveRelationsResult,
    plan_index_file_relation_resolution,
    plan_project_index_completion_relation_resolution,
    resolve_project_index_completion_relations,
    resolve_project_relations,
)
from basic_memory.indexing.models import IndexFileJobStatus
from basic_memory.models import Entity
from basic_memory.repository.relation_repository import (
    ResolvedRelationWrite,
    ResolvedRelationWriteResult,
)


class StubRelationResolutionRuntime:
    """Relation-resolution runtime with scripted counters and pass results."""

    def __init__(self, counts: list[int], affected_per_pass: list[set[int]]) -> None:
        self._counts = counts
        self._affected_per_pass = affected_per_pass
        self.counter_calls = 0
        self.resolve_calls = 0

    async def count_unresolved_relations(self) -> int:
        index = min(self.counter_calls, len(self._counts) - 1)
        self.counter_calls += 1
        return self._counts[index]

    async def resolve_relations(self) -> set[int]:
        index = min(self.resolve_calls, len(self._affected_per_pass) - 1)
        self.resolve_calls += 1
        return self._affected_per_pass[index]


class FakeSession:
    created_count = 0

    def __init__(self) -> None:
        type(self).created_count += 1

    def get_bind(self) -> object:
        return type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


# Not frozen: UnresolvedRelation declares plain (writable) attribute members.
@dataclass(slots=True)
class FakeRelation:
    id: int
    from_id: int
    to_name: str
    relation_type: str = "related_to"


# Not frozen: ResolvedRelationTarget declares plain (writable) attribute members.
@dataclass(slots=True)
class FakeResolvedEntity:
    id: int
    title: str


class StubRelationRepository:
    """Returns scripted ``find_unresolved_relations`` results, in call order."""

    def __init__(
        self,
        unresolved_per_call: list[list[FakeRelation]],
    ) -> None:
        self._unresolved_per_call = unresolved_per_call
        self.calls = 0
        self.write_batches: list[tuple[ResolvedRelationWrite, ...]] = []

    async def find_unresolved_relations(
        self,
        session: AsyncSession,
    ) -> list[FakeRelation]:
        assert isinstance(session, FakeSession)
        index = min(self.calls, len(self._unresolved_per_call) - 1)
        self.calls += 1
        return self._unresolved_per_call[index]

    async def find_unresolved_relations_for_entity(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> list[FakeRelation]:
        assert isinstance(session, FakeSession)
        return [
            relation
            for relation in await self.find_unresolved_relations(session)
            if relation.from_id == entity_id
        ]

    async def apply_resolved_targets(
        self,
        session: AsyncSession,
        writes: Sequence[ResolvedRelationWrite],
    ) -> ResolvedRelationWriteResult:
        assert isinstance(session, FakeSession)
        self.write_batches.append(tuple(writes))
        return ResolvedRelationWriteResult(
            affected_entity_ids=frozenset(write.from_id for write in writes),
            duplicate_relation_ids=(),
        )


class StubEntityRepository:
    def __init__(self) -> None:
        self.entities: dict[int, Entity] = {
            10: cast(Entity, FakeResolvedEntity(id=10, title="Source A")),
            11: cast(Entity, FakeResolvedEntity(id=11, title="Source B")),
        }

    async def find_by_id(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> Entity | None:
        assert isinstance(session, FakeSession)
        return self.entities.get(entity_id)

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> list[Entity]:
        assert isinstance(session, FakeSession)
        return [self.entities[entity_id] for entity_id in ids if entity_id in self.entities]


class StubLinkResolver:
    def __init__(self, targets: dict[str, FakeResolvedEntity]) -> None:
        self.targets = targets
        self.calls: list[tuple[str, bool]] = []

    async def resolve_link(
        self,
        link_text: str,
        *,
        strict: bool,
        session: AsyncSession,
    ) -> FakeResolvedEntity | None:
        assert isinstance(session, FakeSession)
        self.calls.append((link_text, strict))
        return self.targets.get(link_text)


class StubEntityIndexer:
    def __init__(self) -> None:
        self.indexed_entities: list[Entity] = []
        self.indexed_batches: list[tuple[Entity, ...]] = []

    async def index_entity(self, entity: Entity) -> None:
        self.indexed_entities.append(entity)

    async def index_entities(self, entities: Sequence[Entity]) -> None:
        self.indexed_batches.append(tuple(entities))
        self.indexed_entities.extend(entities)


def build_repository_runtime(
    relation_repository: StubRelationRepository,
    link_resolver: StubLinkResolver,
    entity_indexer: StubEntityIndexer,
) -> RepositoryRelationResolutionRuntime:
    return RepositoryRelationResolutionRuntime(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeSession),
        relation_repository=relation_repository,
        entity_repository=StubEntityRepository(),
        link_resolver=link_resolver,
        entity_indexer=entity_indexer,
    )


def test_resolve_relations_job_request_matches_project_queue_identity() -> None:
    request = ResolveRelationsJobRequest(
        project_id=7,
        project_path="main",
    )

    assert RESOLVE_RELATIONS_DEBOUNCE_SECONDS == 10
    assert request.dedupe_key() == "resolve-relations:7"
    assert request.routing_headers({"source": "test"}) == {
        "source": "test",
        "project_id": "7",
    }
    assert request.execute_after == timedelta(seconds=10)

    with pytest.raises(FrozenInstanceError):
        setattr(request, "project_path", "other")


def test_project_index_completion_relation_resolution_plan_requires_project_identity() -> None:
    assert plan_project_index_completion_relation_resolution(
        ProjectIndexRelationResolutionContext(
            project_id="7",
            project_path="main",
        )
    ) == ResolveRelationsJobRequest(
        project_id=7,
        project_path="main",
    )
    assert (
        plan_project_index_completion_relation_resolution(
            ProjectIndexRelationResolutionContext(
                project_id=None,
                project_path="main",
            )
        )
        is None
    )
    assert (
        plan_project_index_completion_relation_resolution(
            ProjectIndexRelationResolutionContext(
                project_id="7",
                project_path=None,
            )
        )
        is None
    )
    with pytest.raises(ValueError):
        plan_project_index_completion_relation_resolution(
            ProjectIndexRelationResolutionContext(
                project_id="not-an-int",
                project_path="main",
            )
        )


@pytest.mark.asyncio
async def test_project_index_completion_relation_resolution_runs_shared_pass() -> None:
    runtime = StubRelationResolutionRuntime([2, 0], [{10}, set()])

    result = await resolve_project_index_completion_relations(
        ProjectIndexRelationResolutionContext(
            project_id=7,
            project_path="main",
        ),
        runtime,
    )

    assert result == ResolveRelationsResult(
        unresolved_before=2,
        remaining=0,
        passes=2,
        affected_entities=1,
    )
    assert runtime.counter_calls == 2
    assert runtime.resolve_calls == 2

    skipped_runtime = StubRelationResolutionRuntime([2, 0], [{10}])
    assert (
        await resolve_project_index_completion_relations(
            ProjectIndexRelationResolutionContext(
                project_id=None,
                project_path="main",
            ),
            skipped_runtime,
        )
        is None
    )
    assert skipped_runtime.counter_calls == 0
    assert skipped_runtime.resolve_calls == 0


def test_index_file_relation_resolution_plan_requires_incremental_processed_file() -> None:
    assert plan_index_file_relation_resolution(
        IndexFileRelationResolutionContext(
            project_id=7,
            project_path="main",
            status=IndexFileJobStatus.processed,
        )
    ) == ResolveRelationsJobRequest(
        project_id=7,
        project_path="main",
    )
    assert (
        plan_index_file_relation_resolution(
            IndexFileRelationResolutionContext(
                project_id=7,
                project_path="main",
                status=IndexFileJobStatus.current,
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_resolves_until_a_stable_pass_changes_nothing() -> None:
    runtime = StubRelationResolutionRuntime([3, 1], [{10, 11}, set()])

    result = await resolve_project_relations(runtime)

    assert result == ResolveRelationsResult(
        unresolved_before=3,
        remaining=1,
        passes=2,
        affected_entities=2,
    )
    assert result.resolved == 2
    assert runtime.resolve_calls == 2
    assert runtime.counter_calls == 2


@pytest.mark.asyncio
async def test_stops_immediately_when_no_relations_resolve() -> None:
    runtime = StubRelationResolutionRuntime([1, 1], [set()])

    result = await resolve_project_relations(runtime)

    assert result.passes == 1
    assert result.resolved == 0
    assert result.remaining == 1
    assert runtime.resolve_calls == 1


@pytest.mark.asyncio
async def test_resolution_loop_is_bounded_by_max_passes() -> None:
    runtime = StubRelationResolutionRuntime([2, 0], [{1}])

    result = await resolve_project_relations(runtime, max_passes=3)

    assert result.passes == 3
    assert result.remaining == 0
    assert runtime.resolve_calls == 3


@pytest.mark.asyncio
async def test_project_relation_resolution_uses_repository_runtime_and_counts_remaining() -> None:
    repo = StubRelationRepository(
        [
            [
                FakeRelation(id=1, from_id=10, to_name="Target A"),
                FakeRelation(id=2, from_id=11, to_name="Target B"),
                FakeRelation(id=3, from_id=10, to_name="Still Missing"),
            ],
            [
                FakeRelation(id=1, from_id=10, to_name="Target A"),
                FakeRelation(id=2, from_id=11, to_name="Target B"),
            ],
            [],
            [FakeRelation(id=3, from_id=10, to_name="Still Missing")],
        ]
    )
    entity_indexer = StubEntityIndexer()
    runtime = build_repository_runtime(
        repo,
        StubLinkResolver(
            {
                "Target A": FakeResolvedEntity(id=20, title="Target A"),
                "Target B": FakeResolvedEntity(id=21, title="Target B"),
            }
        ),
        entity_indexer=entity_indexer,
    )

    result = await resolve_project_relations(runtime)

    assert result == ResolveRelationsResult(
        unresolved_before=3,
        remaining=1,
        passes=2,
        affected_entities=2,
    )
    assert result.resolved == 2
    assert repo.write_batches == [
        (
            ResolvedRelationWrite(
                relation_id=1,
                from_id=10,
                target_id=20,
                target_name="Target A",
                relation_type="related_to",
            ),
            ResolvedRelationWrite(
                relation_id=2,
                from_id=11,
                target_id=21,
                target_name="Target B",
                relation_type="related_to",
            ),
        ),
        (),
    ]
    assert entity_indexer.indexed_batches == [
        (
            FakeResolvedEntity(id=10, title="Source A"),
            FakeResolvedEntity(id=11, title="Source B"),
        ),
    ]


@pytest.mark.asyncio
async def test_project_relation_resolution_stops_when_nothing_resolves() -> None:
    repo = StubRelationRepository(
        [
            [FakeRelation(id=1, from_id=10, to_name="Still Missing")],
            [FakeRelation(id=1, from_id=10, to_name="Still Missing")],
            [FakeRelation(id=1, from_id=10, to_name="Still Missing")],
        ]
    )
    entity_indexer = StubEntityIndexer()
    runtime = build_repository_runtime(repo, StubLinkResolver({}), entity_indexer)

    result = await resolve_project_relations(runtime)

    assert result.passes == 1
    assert result.resolved == 0
    assert result.remaining == 1
    assert repo.write_batches == [()]
    assert entity_indexer.indexed_entities == []


@pytest.mark.asyncio
async def test_project_relation_resolution_respects_pass_limit() -> None:
    unresolved = [FakeRelation(id=1, from_id=10, to_name="Target A")]
    repo = StubRelationRepository(
        [
            [
                FakeRelation(id=1, from_id=10, to_name="Target A"),
                FakeRelation(id=2, from_id=11, to_name="Target B"),
            ],
            unresolved,
            unresolved,
            unresolved,
            [],
        ]
    )
    runtime = build_repository_runtime(
        repo,
        StubLinkResolver({"Target A": FakeResolvedEntity(id=20, title="Target A")}),
        StubEntityIndexer(),
    )

    result = await resolve_project_relations(runtime, max_passes=3)

    assert result.passes == 3
    assert len(repo.write_batches) == 3
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_repository_runtime_batches_resolution_and_entity_refresh_sessions() -> None:
    FakeSession.created_count = 0
    relations = [
        FakeRelation(id=1, from_id=10, to_name="Target A"),
        FakeRelation(id=2, from_id=11, to_name="Target B"),
    ]
    repo = StubRelationRepository([relations])
    entity_indexer = StubEntityIndexer()
    runtime = build_repository_runtime(
        relation_repository=repo,
        link_resolver=StubLinkResolver(
            {
                "Target A": FakeResolvedEntity(id=20, title="Target A"),
                "Target B": FakeResolvedEntity(id=21, title="Target B"),
            }
        ),
        entity_indexer=entity_indexer,
    )

    affected = await runtime.resolve_relations()

    assert affected == {10, 11}
    assert len(repo.write_batches) == 1
    assert entity_indexer.indexed_batches == [
        (
            FakeResolvedEntity(id=10, title="Source A"),
            FakeResolvedEntity(id=11, title="Source B"),
        )
    ]
    assert FakeSession.created_count == 2
