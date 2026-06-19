"""Portable embedding index planning."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EmbeddingIndexTarget:
    """One entity version that may need embedding indexing."""

    entity_id: int
    entity_checksum: str


@dataclass(frozen=True, slots=True)
class EmbeddingIndexJobRequest:
    """Queue-neutral request shape for indexing embeddings for one entity."""

    tenant_id: UUID
    project_id: int
    entity_id: int
    entity_checksum: str | None = None

    def dedupe_key(self) -> str:
        """Return the logical single-entity embedding queue identity."""
        checksum_key = self.entity_checksum or "latest"
        return (
            f"index-embeddings:{self.tenant_id}:{self.project_id}:{self.entity_id}:{checksum_key}"
        )

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the single-entity embedding job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
            }
        )
        return routing_headers


@dataclass(frozen=True, slots=True)
class EmbeddingIndexBatchJobRequest:
    """Queue-neutral request shape for indexing embeddings for entity versions."""

    tenant_id: UUID
    project_id: int
    project_path: str
    entities: tuple[EmbeddingIndexTarget, ...] = ()
    workflow_id: UUID | None = None

    def dedupe_key(self) -> str:
        """Return the logical batch embedding queue identity."""
        fingerprint = EmbeddingIndexPlanner().fingerprint(self.entities)
        return f"index-embeddings-batch:{self.tenant_id}:{self.project_id}:{fingerprint}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the batch embedding job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
                "project_path": self.project_path,
            }
        )
        if self.workflow_id is not None:
            routing_headers["workflow_id"] = str(self.workflow_id)
        return routing_headers


@dataclass(frozen=True, slots=True)
class EmbeddingIndexBatchJobContext:
    """Indexed entity versions that may need batched embedding jobs."""

    tenant_id: UUID
    project_id: int
    project_path: str
    index_embeddings: bool
    targets: tuple[EmbeddingIndexTarget, ...]
    batch_size: int
    workflow_id: UUID | None = None


class EmbeddingIndexStatus(StrEnum):
    """Normal outcomes for one semantic-embedding indexing job."""

    processed = "processed"
    noop = "noop"


@dataclass(frozen=True, slots=True)
class EmbeddingIndexResult:
    """Summary of one embedding indexing job."""

    entity_id: int
    status: EmbeddingIndexStatus
    reason: str


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


class EmbeddingIndexBatchSummary(Protocol):
    """Vector sync counts produced by the concrete search backend."""

    entities_synced: int
    entities_skipped: int
    entities_failed: int
    entities_deferred: int


@dataclass(frozen=True, slots=True)
class EmbeddingIndexBatchResult:
    """Summary of a batch embedding index operation."""

    total_entities: int
    unique_entities: int
    synced_entities: int
    skipped_entities: int
    failed_entities: int
    deferred_entities: int
    reason: str

    @classmethod
    def no_entities(cls) -> "EmbeddingIndexBatchResult":
        """Return the result for an empty batch that does no backend work."""
        return cls(
            total_entities=0,
            unique_entities=0,
            synced_entities=0,
            skipped_entities=0,
            failed_entities=0,
            deferred_entities=0,
            reason="no entities",
        )


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


def plan_embedding_index_batch_jobs(
    context: EmbeddingIndexBatchJobContext,
) -> tuple[EmbeddingIndexBatchJobRequest, ...]:
    """Plan queue-neutral batch embedding jobs after a file-index batch."""
    if not context.index_embeddings:
        return ()
    if not context.targets:
        return ()
    if context.batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    return tuple(
        EmbeddingIndexBatchJobRequest(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            project_path=context.project_path,
            workflow_id=context.workflow_id,
            entities=context.targets[index : index + context.batch_size],
        )
        for index in range(0, len(context.targets), context.batch_size)
    )


def summarize_embedding_index_batch_result(
    plan: EmbeddingIndexPlan,
    batch_result: EmbeddingIndexBatchSummary,
) -> EmbeddingIndexBatchResult:
    """Combine a deduped embedding plan with backend vector sync counts."""
    return EmbeddingIndexBatchResult(
        total_entities=plan.total_targets,
        unique_entities=plan.unique_entities,
        synced_entities=batch_result.entities_synced,
        skipped_entities=batch_result.entities_skipped,
        failed_entities=batch_result.entities_failed,
        deferred_entities=batch_result.entities_deferred,
        reason=f"entity embedding batch indexed: {plan.unique_entities} entities",
    )
