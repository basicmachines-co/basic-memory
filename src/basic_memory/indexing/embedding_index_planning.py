"""Portable embedding index planning."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingIndexTarget:
    """One entity version that may need embedding indexing."""

    entity_id: int
    entity_checksum: str


@dataclass(frozen=True, slots=True)
class EmbeddingIndexPlan:
    """The entity set handed to vector sync code."""

    total_targets: int
    entity_ids: tuple[int, ...]
    fingerprint: str

    @property
    def unique_entities(self) -> int:
        """Number of unique entities in this plan."""
        return len(self.entity_ids)


class EmbeddingIndexPlanner:
    """Prepare embedding job inputs without duplicating source-hash logic."""

    def plan(self, targets: Sequence[EmbeddingIndexTarget]) -> EmbeddingIndexPlan:
        """Dedupe entity ids and fingerprint the queued entity versions."""
        entity_ids = tuple(sorted({target.entity_id for target in targets}))
        return EmbeddingIndexPlan(
            total_targets=len(targets),
            entity_ids=entity_ids,
            fingerprint=self.fingerprint(targets),
        )

    def fingerprint(self, targets: Sequence[EmbeddingIndexTarget]) -> str:
        """Return a stable key for one batch of queued entity versions."""
        material = "|".join(
            f"{target.entity_id}:{target.entity_checksum}"
            for target in sorted(targets, key=lambda item: (item.entity_id, item.entity_checksum))
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
