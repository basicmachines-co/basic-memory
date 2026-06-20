"""Tests for portable forward-reference resolution planning."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, dataclass
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.forward_reference_resolution as forward_resolution_module
from basic_memory.indexing.forward_reference_resolution import (
    ForwardReferenceResolutionPlan,
    ForwardReferenceResolutionRun,
    ForwardReferenceUpdate,
    RepositoryForwardReferenceResolutionRuntime,
    collect_forward_reference_link_texts,
    plan_forward_reference_resolution,
    run_forward_reference_resolution,
)


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


class FakeForwardReferenceSession:
    """Record relation update statements issued by the repository runtime."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)


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

    async def resolve_link_texts(link_texts: Sequence[str]) -> dict[str, int | None]:
        calls.append(tuple(link_texts))
        return {"Target": 20, "Missing": None}

    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        resolve_link_texts=resolve_link_texts,
    )

    result = await runtime.resolve_forward_reference_link_texts(("Target", "Missing"))

    assert result == {"Target": 20, "Missing": None}
    assert calls == [("Target", "Missing")]


@pytest.mark.asyncio
async def test_repository_forward_reference_runtime_applies_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = FakeForwardReferenceSession()

    async def resolve_link_texts(_link_texts: Sequence[str]) -> dict[str, int | None]:
        return {}

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeForwardReferenceSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(forward_resolution_module.db, "scoped_session", fake_scoped_session)

    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=session_maker,
        resolve_link_texts=resolve_link_texts,
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
    async def resolve_link_texts(_link_texts: Sequence[str]) -> dict[str, int | None]:
        return {}

    @asynccontextmanager
    async def fake_scoped_session(
        _scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[FakeForwardReferenceSession]:
        raise AssertionError("empty updates should not open a session")
        yield FakeForwardReferenceSession()

    monkeypatch.setattr(forward_resolution_module.db, "scoped_session", fake_scoped_session)

    runtime = RepositoryForwardReferenceResolutionRuntime(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        resolve_link_texts=resolve_link_texts,
    )

    await runtime.apply_forward_reference_updates(())


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
