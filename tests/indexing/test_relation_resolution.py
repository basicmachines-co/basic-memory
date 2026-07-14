"""Tests for portable relation resolution orchestration."""

from dataclasses import FrozenInstanceError, dataclass
from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy.exc import IntegrityError
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
    resolve_relations_until_stable,
)
from basic_memory.indexing.models import IndexFileJobStatus
from basic_memory.models import Entity


class StubUnresolvedRelationCounter:
    """Returns scripted unresolved relation counts, in call order."""

    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self.calls = 0

    async def count_unresolved_relations(self) -> int:
        index = min(self.calls, len(self._counts) - 1)
        self.calls += 1
        return self._counts[index]


class StubRelationResolutionPass:
    """Returns scripted affected entity sets, in pass order."""

    def __init__(self, affected_per_pass: list[set[int]]) -> None:
        self._affected_per_pass = affected_per_pass
        self.calls = 0

    async def resolve_relations(self) -> set[int]:
        index = min(self.calls, len(self._affected_per_pass) - 1)
        self.calls += 1
        return self._affected_per_pass[index]


class StubRelationResolutionRuntime:
    """Relation-resolution runtime with scripted counters and pass results."""

    def __init__(self, counts: list[int], affected_per_pass: list[set[int]]) -> None:
        self.counter = StubUnresolvedRelationCounter(counts)
        self.resolver = StubRelationResolutionPass(affected_per_pass)

    async def count_unresolved_relations(self) -> int:
        return await self.counter.count_unresolved_relations()

    async def resolve_relations(self) -> set[int]:
        return await self.resolver.resolve_relations()


class FakeSession:
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
        *,
        fail_update_ids: set[int] | None = None,
    ) -> None:
        self._unresolved_per_call = unresolved_per_call
        self._fail_update_ids = fail_update_ids or set()
        self.calls = 0
        self.updates: list[tuple[int, dict[str, object]]] = []
        self.deletes: list[int] = []

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

    async def update(
        self,
        session: AsyncSession,
        entity_id: int,
        entity_data: dict[str, object],
    ) -> object | None:
        assert isinstance(session, FakeSession)
        if entity_id in self._fail_update_ids:
            raise IntegrityError("update relation", {}, Exception("duplicate relation"))
        self.updates.append((entity_id, entity_data))
        return object()

    async def delete(self, session: AsyncSession, entity_id: int) -> bool:
        assert isinstance(session, FakeSession)
        self.deletes.append(entity_id)
        return True


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

    async def index_entity(self, entity: Entity) -> None:
        self.indexed_entities.append(entity)


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
    assert runtime.counter.calls == 2
    assert runtime.resolver.calls == 2

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
    assert skipped_runtime.counter.calls == 0
    assert skipped_runtime.resolver.calls == 0


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
    counter = StubUnresolvedRelationCounter([3, 1])
    resolver = StubRelationResolutionPass([{10, 11}, set()])

    result = await resolve_relations_until_stable(
        resolver=resolver,
        unresolved_counter=counter,
    )

    assert result == ResolveRelationsResult(
        unresolved_before=3,
        remaining=1,
        passes=2,
        affected_entities=2,
    )
    assert result.resolved == 2
    assert resolver.calls == 2
    assert counter.calls == 2


@pytest.mark.asyncio
async def test_stops_immediately_when_no_relations_resolve() -> None:
    counter = StubUnresolvedRelationCounter([1, 1])
    resolver = StubRelationResolutionPass([set()])

    result = await resolve_relations_until_stable(
        resolver=resolver,
        unresolved_counter=counter,
    )

    assert result.passes == 1
    assert result.resolved == 0
    assert result.remaining == 1
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_resolution_loop_is_bounded_by_max_passes() -> None:
    counter = StubUnresolvedRelationCounter([2, 0])
    resolver = StubRelationResolutionPass([{1}])

    result = await resolve_relations_until_stable(
        resolver=resolver,
        unresolved_counter=counter,
        max_passes=3,
    )

    assert result.passes == 3
    assert result.remaining == 0
    assert resolver.calls == 3


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
        entity_indexer,
    )

    result = await resolve_project_relations(runtime)

    assert result == ResolveRelationsResult(
        unresolved_before=3,
        remaining=1,
        passes=2,
        affected_entities=2,
    )
    assert result.resolved == 2
    assert repo.updates == [
        (1, {"to_id": 20, "to_name": "Target A"}),
        (2, {"to_id": 21, "to_name": "Target B"}),
    ]
    assert entity_indexer.indexed_entities == [
        FakeResolvedEntity(id=10, title="Source A"),
        FakeResolvedEntity(id=11, title="Source B"),
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
    assert repo.updates == []
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
    assert len(repo.updates) == 3
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_repository_runtime_deletes_duplicate_unresolved_relation() -> None:
    relation = FakeRelation(id=1, from_id=10, to_name="Target A")
    repo = StubRelationRepository([[relation]], fail_update_ids={1})
    entity_indexer = StubEntityIndexer()
    runtime = build_repository_runtime(
        repo,
        StubLinkResolver({"Target A": FakeResolvedEntity(id=20, title="Target A")}),
        entity_indexer,
    )

    affected = await runtime.resolve_relations()

    assert affected == {10}
    assert repo.deletes == [1]
    assert entity_indexer.indexed_entities == [FakeResolvedEntity(id=10, title="Source A")]
