"""Tests for portable forward-reference resolution planning."""

from dataclasses import FrozenInstanceError, dataclass

import pytest

from basic_memory.indexing.forward_reference_resolution import (
    ForwardReferenceResolutionPlan,
    ForwardReferenceUpdate,
    collect_forward_reference_link_texts,
    plan_forward_reference_resolution,
)


@dataclass(frozen=True, slots=True)
class StubUnresolvedRelation:
    id: int
    from_id: int
    to_name: str | None


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
