"""Tests for portable relation resolution orchestration."""

from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError, dataclass
from datetime import timedelta
from typing import override, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.relation_resolution import (
    IndexFileRelationResolutionContext,
    ProjectIndexRelationResolutionContext,
    RELATION_RESOLUTION_WRITE_BATCH_SIZE,
    RESOLVE_RELATIONS_DEBOUNCE_SECONDS,
    RepositoryRelationResolutionRuntime,
    RelationSearchRefreshResult,
    ResolveRelationsJobRequest,
    ResolveRelationsResult,
    plan_index_file_relation_resolution,
    plan_project_index_completion_relation_resolution,
    plan_resolved_relation_write_batches,
    resolve_project_index_completion_relations,
    resolve_project_relations,
)
from basic_memory.indexing.models import IndexFileJobStatus
from basic_memory.models import Entity
from basic_memory.repository.relation_repository import (
    PendingRelationSearchRefresh,
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


@dataclass(frozen=True, slots=True)
class FakeRelation:
    id: int
    from_id: int
    to_name: str
    relation_type: str = "related_to"
    generation: int = 1


@dataclass(frozen=True, slots=True)
class FakeResolvedEntity:
    id: int
    title: str

    @property
    def external_id(self) -> str:
        return f"external-{self.id}"


class StubRelationRepository:
    """Returns scripted ``find_unresolved_relations`` results, in call order."""

    def __init__(
        self,
        unresolved_per_call: list[list[FakeRelation]],
    ) -> None:
        self._unresolved_per_call = unresolved_per_call
        self.calls = 0
        self.write_batches: list[tuple[ResolvedRelationWrite, ...]] = []
        self.pending_search_refreshes: list[PendingRelationSearchRefresh] = []
        self._next_refresh_id = 1

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
        affected_entity_ids = frozenset(write.from_id for write in writes)
        for entity_id in sorted(affected_entity_ids):
            self.pending_search_refreshes.append(
                PendingRelationSearchRefresh(
                    id=self._next_refresh_id,
                    entity_id=entity_id,
                )
            )
            self._next_refresh_id += 1
        return ResolvedRelationWriteResult(
            affected_entity_ids=affected_entity_ids,
            duplicate_relation_ids=(),
        )

    async def list_pending_search_refreshes(
        self,
        session: AsyncSession,
        *,
        entity_id: int | None = None,
    ) -> list[PendingRelationSearchRefresh]:
        assert isinstance(session, FakeSession)
        return [
            refresh
            for refresh in self.pending_search_refreshes
            if entity_id is None or refresh.entity_id == entity_id
        ]

    async def clear_pending_search_refreshes(
        self,
        session: AsyncSession,
        refresh_ids: Sequence[int],
    ) -> None:
        assert isinstance(session, FakeSession)
        completed_ids = set(refresh_ids)
        self.pending_search_refreshes = [
            refresh for refresh in self.pending_search_refreshes if refresh.id not in completed_ids
        ]


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


@dataclass(slots=True)
class FakeNoteContent:
    entity_id: int
    markdown_content: str
    file_write_status: str


class StubNoteContentRepository:
    def __init__(self, note_contents: Sequence[FakeNoteContent]) -> None:
        self.note_contents = {
            note_content.entity_id: note_content for note_content in note_contents
        }

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> list[FakeNoteContent]:
        assert isinstance(session, FakeSession)
        return [
            self.note_contents[entity_id] for entity_id in ids if entity_id in self.note_contents
        ]


class StubLinkResolver:
    def __init__(self, targets: dict[str, FakeResolvedEntity]) -> None:
        self.targets = targets
        self.calls: list[tuple[str, bool]] = []

    async def resolve_relation_targets(
        self,
        link_texts: Sequence[str],
        *,
        session: AsyncSession,
    ) -> Mapping[str, FakeResolvedEntity | None]:
        assert isinstance(session, FakeSession)
        self.calls.extend((link_text, True) for link_text in link_texts)
        return {link_text: self.targets.get(link_text) for link_text in link_texts}


class StubEntityIndexer:
    def __init__(self) -> None:
        self.indexed_entities: list[Entity] = []
        self.indexed_batches: list[tuple[Entity, ...]] = []
        self.indexed_content_batches: list[dict[int, str]] = []

    async def index_entity(self, entity: Entity) -> None:
        self.indexed_entities.append(entity)

    async def index_entities(
        self,
        entities: Sequence[Entity],
        *,
        content_by_entity_id: Mapping[int, str],
    ) -> RelationSearchRefreshResult:
        self.indexed_batches.append(tuple(entities))
        self.indexed_content_batches.append(dict(content_by_entity_id))
        self.indexed_entities.extend(entities)
        return RelationSearchRefreshResult()


class MissingFileEntityIndexer(StubEntityIndexer):
    """Model SearchService's disk fallback for entities without accepted content."""

    @override
    async def index_entities(
        self,
        entities: Sequence[Entity],
        *,
        content_by_entity_id: Mapping[int, str],
    ) -> RelationSearchRefreshResult:
        missing_entity_ids = [
            entity.id for entity in entities if entity.id not in content_by_entity_id
        ]
        if missing_entity_ids:
            return RelationSearchRefreshResult(
                missing_content_entity_ids=frozenset(missing_entity_ids)
            )
        return await super().index_entities(
            entities,
            content_by_entity_id=content_by_entity_id,
        )


def build_repository_runtime(
    relation_repository: StubRelationRepository,
    target_resolver: StubLinkResolver,
    entity_indexer: StubEntityIndexer,
    note_contents: Sequence[FakeNoteContent] = (),
) -> RepositoryRelationResolutionRuntime:
    return RepositoryRelationResolutionRuntime(
        session_maker=cast(async_sessionmaker[AsyncSession], FakeSession),
        relation_repository=relation_repository,
        entity_repository=StubEntityRepository(),
        note_content_repository=StubNoteContentRepository(note_contents),
        target_resolver=target_resolver,
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


def test_relation_write_batch_plan_keeps_collision_domains_together() -> None:
    assert plan_resolved_relation_write_batches([]) == ()
    with pytest.raises(ValueError, match="target size must be positive"):
        plan_resolved_relation_write_batches([], target_size=0)

    independent_writes = [
        ResolvedRelationWrite(
            relation_id=relation_id,
            from_id=relation_id,
            generation=1,
            original_target_name=f"Original {relation_id}",
            target_id=1_000 + relation_id,
            target_external_id=f"external-{1_000 + relation_id}",
            relation_type="related_to",
        )
        for relation_id in range(1, RELATION_RESOLUTION_WRITE_BATCH_SIZE)
    ]
    colliding_writes = [
        ResolvedRelationWrite(
            relation_id=RELATION_RESOLUTION_WRITE_BATCH_SIZE,
            from_id=999,
            generation=1,
            original_target_name="Alias A",
            target_id=2_000,
            target_external_id="external-2000",
            relation_type="related_to",
        ),
        ResolvedRelationWrite(
            relation_id=RELATION_RESOLUTION_WRITE_BATCH_SIZE + 1,
            from_id=999,
            generation=1,
            original_target_name="Alias B",
            target_id=2_001,
            target_external_id="external-2001",
            relation_type="related_to",
        ),
    ]

    batches = plan_resolved_relation_write_batches([*independent_writes, *colliding_writes])

    assert [len(batch.writes) for batch in batches] == [
        RELATION_RESOLUTION_WRITE_BATCH_SIZE - 1,
        2,
    ]
    assert {write.relation_id for write in batches[1].writes} == {
        RELATION_RESOLUTION_WRITE_BATCH_SIZE,
        RELATION_RESOLUTION_WRITE_BATCH_SIZE + 1,
    }


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
                generation=1,
                original_target_name="Target A",
                target_id=20,
                target_external_id="external-20",
                relation_type="related_to",
            ),
            ResolvedRelationWrite(
                relation_id=2,
                from_id=11,
                generation=1,
                original_target_name="Target B",
                target_id=21,
                target_external_id="external-21",
                relation_type="related_to",
            ),
        ),
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
    assert repo.write_batches == []
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
        target_resolver=StubLinkResolver(
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
    assert FakeSession.created_count == 4


@pytest.mark.asyncio
async def test_repository_runtime_commits_write_batches_and_resumes_after_interruption() -> None:
    relations = [
        FakeRelation(
            id=relation_id,
            from_id=10,
            to_name=f"Target {relation_id}",
            relation_type=f"related_to_{relation_id}",
        )
        for relation_id in range(1, RELATION_RESOLUTION_WRITE_BATCH_SIZE + 2)
    ]

    class InterruptingRelationRepository(StubRelationRepository):
        def __init__(self) -> None:
            super().__init__([relations])
            self.remaining = list(relations)
            self.apply_attempts = 0

        @override
        async def find_unresolved_relations(
            self,
            session: AsyncSession,
        ) -> list[FakeRelation]:
            assert isinstance(session, FakeSession)
            return list(self.remaining)

        @override
        async def apply_resolved_targets(
            self,
            session: AsyncSession,
            writes: Sequence[ResolvedRelationWrite],
        ) -> ResolvedRelationWriteResult:
            self.apply_attempts += 1
            if self.apply_attempts == 2:
                raise RuntimeError("worker interrupted between committed batches")

            result = await super().apply_resolved_targets(session, writes)
            completed_ids = {write.relation_id for write in writes}
            self.remaining = [
                relation for relation in self.remaining if relation.id not in completed_ids
            ]
            return result

    repository = InterruptingRelationRepository()
    link_resolver = StubLinkResolver(
        {
            relation.to_name: FakeResolvedEntity(
                id=1_000 + relation.id,
                title=relation.to_name,
            )
            for relation in relations
        }
    )
    runtime = build_repository_runtime(
        repository,
        link_resolver,
        StubEntityIndexer(),
    )

    with pytest.raises(RuntimeError, match="interrupted between committed batches"):
        await runtime.resolve_relations()

    assert len(repository.remaining) == 1
    assert len(repository.write_batches) == 1
    assert len(repository.write_batches[0]) == RELATION_RESOLUTION_WRITE_BATCH_SIZE

    link_resolver.calls.clear()
    assert await runtime.resolve_relations() == {10}

    assert link_resolver.calls == [(relations[-1].to_name, True)]
    assert repository.remaining == []
    assert [len(batch) for batch in repository.write_batches] == [
        RELATION_RESOLUTION_WRITE_BATCH_SIZE,
        1,
    ]


@pytest.mark.asyncio
async def test_repository_runtime_refreshes_pending_source_from_accepted_content() -> None:
    repo = StubRelationRepository([[FakeRelation(id=1, from_id=10, to_name="Target A")]])
    entity_indexer = MissingFileEntityIndexer()
    runtime = build_repository_runtime(
        relation_repository=repo,
        target_resolver=StubLinkResolver({"Target A": FakeResolvedEntity(id=20, title="Target A")}),
        entity_indexer=entity_indexer,
        note_contents=[
            FakeNoteContent(
                entity_id=10,
                markdown_content=(
                    "---\ntitle: Source A\n---\n\n# Source A\n\nAccepted before materialization."
                ),
                file_write_status="pending",
            )
        ],
    )

    affected = await runtime.resolve_relations()

    assert affected == {10}
    assert entity_indexer.indexed_content_batches == [
        {10: "# Source A\n\nAccepted before materialization."}
    ]


@pytest.mark.asyncio
async def test_repository_runtime_retires_missing_file_refresh_for_synced_source() -> None:
    repo = StubRelationRepository([[FakeRelation(id=1, from_id=10, to_name="Target A")]])
    runtime = build_repository_runtime(
        relation_repository=repo,
        target_resolver=StubLinkResolver({"Target A": FakeResolvedEntity(id=20, title="Target A")}),
        entity_indexer=MissingFileEntityIndexer(),
        note_contents=[
            FakeNoteContent(
                entity_id=10,
                markdown_content="# Source A\n\nAlready materialized.",
                file_write_status="synced",
            )
        ],
    )

    assert await runtime.resolve_relations() == {10}
    assert repo.pending_search_refreshes == []


@pytest.mark.asyncio
async def test_resolve_relations_skips_ambiguous_target_without_aborting_pass() -> None:
    """An ambiguous relation target is left unresolved; other relations still resolve (#1148).

    strict resolution now raises AmbiguousIdentifierError when a target title matches several
    notes. The repair pass must treat that as an unresolved forward reference rather than let the
    exception abort the whole batch and strand other resolvable relations.
    """

    class AmbiguousStubLinkResolver(StubLinkResolver):
        def __init__(
            self,
            targets: dict[str, FakeResolvedEntity],
            ambiguous: set[str],
        ) -> None:
            super().__init__(targets)
            self.ambiguous = ambiguous

        @override
        async def resolve_relation_targets(
            self,
            link_texts: Sequence[str],
            *,
            session: AsyncSession,
        ) -> Mapping[str, FakeResolvedEntity | None]:
            assert isinstance(session, FakeSession)
            self.calls.extend((link_text, True) for link_text in link_texts)
            return {
                link_text: None if link_text in self.ambiguous else self.targets.get(link_text)
                for link_text in link_texts
            }

    repo = StubRelationRepository(
        [
            [
                FakeRelation(id=1, from_id=10, to_name="Ambiguous Title"),
                FakeRelation(id=2, from_id=11, to_name="Clear Target"),
            ]
        ]
    )
    link_resolver = AmbiguousStubLinkResolver(
        targets={"Clear Target": FakeResolvedEntity(id=20, title="Clear Target")},
        ambiguous={"Ambiguous Title"},
    )
    entity_indexer = StubEntityIndexer()
    runtime = build_repository_runtime(repo, link_resolver, entity_indexer)

    # The pass completes (no exception propagates) ...
    affected = await runtime.resolve_relations()

    # ... resolving only the unambiguous relation; the ambiguous one stays a forward reference.
    assert affected == {11}
    assert len(repo.write_batches) == 1
    writes = repo.write_batches[0]
    assert [write.relation_id for write in writes] == [2]
    assert writes[0].target_id == 20
    assert ("Ambiguous Title", True) in link_resolver.calls
