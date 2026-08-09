"""Publish parsed relations under one accepted note-content generation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.models import IndexedRelation
from basic_memory.repository.relation_repository import (
    RELATION_GENERATION_WRITE_STATEMENT_SIZE,
    AcceptedRelationWrite,
    RelationGenerationWriteResult,
)


class RelationGenerationStore(Protocol):
    """Repository operations needed to publish one relation generation."""

    async def upsert_relation_generation(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[AcceptedRelationWrite],
    ) -> RelationGenerationWriteResult: ...

    async def cleanup_relation_generations(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        generation: int,
    ) -> RelationGenerationWriteResult: ...


@dataclass(frozen=True, slots=True)
class RelationGenerationPublisher:
    """Commit sorted relation chunks, then cleanup, under repeated source fences."""

    relation_repository: RelationGenerationStore
    session_maker: async_sessionmaker[AsyncSession]

    async def publish(
        self,
        *,
        entity_id: int,
        generation: int,
        relations: Sequence[IndexedRelation],
    ) -> bool:
        """Return whether every statement retained ownership of ``generation``."""
        relations_by_identity: dict[tuple[str, str], IndexedRelation] = {}
        for relation in relations:
            identity = relation.relation_type, relation.target_name
            relations_by_identity.setdefault(identity, relation)

        ordered_relations = [
            AcceptedRelationWrite(
                relation_type=relation.relation_type,
                target_name=relation.target_name,
                context=relation.context,
            )
            for _, relation in sorted(relations_by_identity.items())
        ]

        for relation_chunk in batched(
            ordered_relations,
            RELATION_GENERATION_WRITE_STATEMENT_SIZE,
        ):
            async with db.scoped_session(self.session_maker) as session:
                result = await self.relation_repository.upsert_relation_generation(
                    session,
                    entity_id=entity_id,
                    generation=generation,
                    relations=relation_chunk,
                )
            if not result.generation_is_current:
                return False

        # Cleanup is intentionally its own transaction. An empty desired set still
        # reaches this statement and removes every row older than the claimed generation.
        async with db.scoped_session(self.session_maker) as session:
            cleanup = await self.relation_repository.cleanup_relation_generations(
                session,
                entity_id=entity_id,
                generation=generation,
            )
        return cleanup.generation_is_current
