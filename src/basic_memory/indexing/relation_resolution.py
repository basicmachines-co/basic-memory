"""Portable orchestration for bounded relation resolution passes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

type EntityId = int
type AffectedEntityIds = set[EntityId]
RESOLVE_RELATIONS_DEBOUNCE_SECONDS = 10


class RelationResolutionPass(Protocol):
    """Capability that performs one relation-resolution pass."""

    async def resolve_relations(self) -> AffectedEntityIds:
        """Resolve currently visible relations and return affected source entity IDs."""


class UnresolvedRelationCounter(Protocol):
    """Capability that counts currently unresolved relations."""

    async def count_unresolved_relations(self) -> int:
        """Return the current unresolved relation count."""


@dataclass(frozen=True, slots=True)
class ResolveRelationsJobRequest:
    """Queue-neutral request shape for resolving one project's forward references."""

    tenant_id: UUID
    project_id: int
    project_path: str
    debounce_seconds: int = RESOLVE_RELATIONS_DEBOUNCE_SECONDS

    @property
    def execute_after(self) -> timedelta:
        """Return the coalescing delay for relation-resolution work."""
        return timedelta(seconds=self.debounce_seconds)

    def dedupe_key(self) -> str:
        """Return the per-(tenant, project) relation-resolution queue identity."""
        return f"resolve-relations:{self.tenant_id}:{self.project_id}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the relation-resolution job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
            }
        )
        return routing_headers


@dataclass(frozen=True, slots=True)
class ResolveRelationsResult:
    """Outcome of one project-scoped resolution run."""

    unresolved_before: int
    remaining: int
    passes: int
    affected_entities: int

    @property
    def resolved(self) -> int:
        """Relations linked during this run (approximate under concurrent writes)."""
        return max(0, self.unresolved_before - self.remaining)


async def resolve_relations_until_stable(
    *,
    resolver: RelationResolutionPass,
    unresolved_counter: UnresolvedRelationCounter,
    max_passes: int = 3,
) -> ResolveRelationsResult:
    """Resolve all relations visible to the supplied capabilities.

    The loop deliberately runs one confirming pass after a productive pass. This
    lets queue workers catch writes that committed while the first pass was still
    running, while the pass cap keeps a noisy resolver from looping forever.
    """
    unresolved_before = await unresolved_counter.count_unresolved_relations()
    affected_entities: AffectedEntityIds = set()
    passes = 0

    while passes < max_passes:
        affected = await resolver.resolve_relations()
        passes += 1
        affected_entities |= affected

        if not affected:
            break

    remaining = await unresolved_counter.count_unresolved_relations()
    return ResolveRelationsResult(
        unresolved_before=unresolved_before,
        remaining=remaining,
        passes=passes,
        affected_entities=len(affected_entities),
    )
