"""Portable planning for deferred forward-reference relation updates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from basic_memory.indexing.link_resolution import LinkText

type ForwardReferenceEntityId = int
type ForwardReferenceRelationId = int


class UnresolvedForwardReference(Protocol):
    """Minimal unresolved relation shape needed for exact target planning."""

    @property
    def id(self) -> ForwardReferenceRelationId:
        """Return the unresolved relation primary key."""

    @property
    def from_id(self) -> ForwardReferenceEntityId:
        """Return the source entity id for the unresolved relation."""

    @property
    def to_name(self) -> LinkText | None:
        """Return the unresolved target link text."""


@dataclass(frozen=True, slots=True)
class ForwardReferenceUpdate:
    """One unresolved relation that can be filled with an exact target entity."""

    relation_id: ForwardReferenceRelationId
    source_entity_id: ForwardReferenceEntityId
    target_entity_id: ForwardReferenceEntityId
    link_text: LinkText


@dataclass(frozen=True, slots=True)
class ForwardReferenceResolutionPlan:
    """Planned bulk updates and search refresh targets for forward references."""

    unresolved_before: int
    link_texts: tuple[LinkText, ...]
    updates: tuple[ForwardReferenceUpdate, ...]
    entity_ids_to_refresh: frozenset[ForwardReferenceEntityId]

    @property
    def resolved_count(self) -> int:
        """Return how many relation rows can be updated."""
        return len(self.updates)

    @property
    def remaining_count(self) -> int:
        """Return how many initially unresolved rows remain unresolved."""
        return max(0, self.unresolved_before - self.resolved_count)

    @property
    def has_updates(self) -> bool:
        """Return whether the executor has any relation rows to update."""
        return bool(self.updates)


class ForwardReferenceResolutionRuntime(Protocol):
    """Capability that resolves link text and applies exact relation updates."""

    async def resolve_forward_reference_link_texts(
        self,
        link_texts: Sequence[LinkText],
    ) -> Mapping[LinkText, ForwardReferenceEntityId | None]:
        """Resolve link texts to exact target entity ids."""

    async def apply_forward_reference_updates(
        self,
        updates: Sequence[ForwardReferenceUpdate],
    ) -> None:
        """Persist exact relation target updates."""


@dataclass(frozen=True, slots=True)
class ForwardReferenceResolutionRun:
    """Applied forward-reference updates and follow-up search refresh targets."""

    plan: ForwardReferenceResolutionPlan
    resolved_link_text_count: int

    @property
    def unresolved_before(self) -> int:
        """Return how many unresolved rows were considered."""
        return self.plan.unresolved_before

    @property
    def link_texts(self) -> tuple[LinkText, ...]:
        """Return unique link texts considered by the run."""
        return self.plan.link_texts

    @property
    def resolved_count(self) -> int:
        """Return how many relation rows were updated."""
        return self.plan.resolved_count

    @property
    def remaining_count(self) -> int:
        """Return how many initially unresolved rows remain unresolved."""
        return self.plan.remaining_count

    @property
    def entity_ids_to_refresh(self) -> frozenset[ForwardReferenceEntityId]:
        """Return exact target entity ids whose search rows should be refreshed."""
        return self.plan.entity_ids_to_refresh


def collect_forward_reference_link_texts(
    unresolved_relations: Sequence[UnresolvedForwardReference],
) -> tuple[LinkText, ...]:
    """Collect unique unresolved link texts in first-seen order."""
    link_texts: dict[LinkText, None] = {}
    for relation in unresolved_relations:
        if relation.to_name:
            link_texts.setdefault(relation.to_name, None)
    return tuple(link_texts)


def plan_forward_reference_resolution(
    unresolved_relations: Sequence[UnresolvedForwardReference],
    resolved_targets: Mapping[LinkText, ForwardReferenceEntityId | None],
) -> ForwardReferenceResolutionPlan:
    """Plan exact target updates for a batch of unresolved relation rows."""
    updates: list[ForwardReferenceUpdate] = []
    entity_ids_to_refresh: set[ForwardReferenceEntityId] = set()

    for relation in unresolved_relations:
        link_text = relation.to_name
        if not link_text:
            continue

        target_entity_id = resolved_targets.get(link_text)
        if target_entity_id is None or target_entity_id == relation.from_id:
            continue

        updates.append(
            ForwardReferenceUpdate(
                relation_id=relation.id,
                source_entity_id=relation.from_id,
                target_entity_id=target_entity_id,
                link_text=link_text,
            )
        )
        entity_ids_to_refresh.add(target_entity_id)

    return ForwardReferenceResolutionPlan(
        unresolved_before=len(unresolved_relations),
        link_texts=collect_forward_reference_link_texts(unresolved_relations),
        updates=tuple(updates),
        entity_ids_to_refresh=frozenset(entity_ids_to_refresh),
    )


async def run_forward_reference_resolution(
    runtime: ForwardReferenceResolutionRuntime,
    unresolved_relations: Sequence[UnresolvedForwardReference],
) -> ForwardReferenceResolutionRun:
    """Resolve link texts, apply exact relation updates, and return refresh targets."""
    link_texts = collect_forward_reference_link_texts(unresolved_relations)
    resolved_targets = (
        await runtime.resolve_forward_reference_link_texts(link_texts) if link_texts else {}
    )
    plan = plan_forward_reference_resolution(unresolved_relations, resolved_targets)
    if plan.has_updates:
        await runtime.apply_forward_reference_updates(plan.updates)

    return ForwardReferenceResolutionRun(
        plan=plan,
        resolved_link_text_count=sum(
            1 for link_text in link_texts if resolved_targets.get(link_text) is not None
        ),
    )
