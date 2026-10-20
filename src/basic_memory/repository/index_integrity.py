"""Read-only, project-scoped diagnostics for the eventually consistent search index."""

from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.models import Entity


@dataclass(frozen=True, slots=True)
class IndexIntegrityReport:
    """Observed counts and bounded examples, without changing indexed state."""

    entities: int
    search_rows: int
    entity_rows: int
    observation_rows: int
    relation_rows: int
    duplicate_keys: int
    duplicate_rows: int
    orphaned_rows: int
    # Informational only: entity timestamps fall back to file ctime/mtime or authored
    # frontmatter, where updated_at < created_at is a valid state (see the report
    # docstring). It never marks the index as corrupt.
    inverted_timestamps: int
    examples: tuple[str, ...]


_DUPLICATES = """
    SELECT type, id, COUNT(*) AS copies FROM search_index
    WHERE project_id = :project_id GROUP BY type, id HAVING COUNT(*) > 1
"""

# A relation with an unresolved target (to_id IS NULL) is valid: its source owns it.
# Match child IDs to their owner as well as project, so cross-project/stale rows
# cannot appear healthy merely because an unrelated row happens to have that ID.
_ORPHANS = """
    FROM search_index s WHERE s.project_id = :project_id AND (
        NOT EXISTS (SELECT 1 FROM entity e
            WHERE e.id = s.entity_id AND e.project_id = s.project_id)
        OR NOT EXISTS (SELECT 1 WHERE
            (s.type = 'entity' AND s.id = s.entity_id)
            OR (s.type = 'observation' AND EXISTS (
                SELECT 1 FROM observation o WHERE o.id = s.id
                AND o.entity_id = s.entity_id AND o.project_id = s.project_id))
            OR (s.type = 'relation' AND EXISTS (
                SELECT 1 FROM relation r WHERE r.id = s.id
                AND r.from_id = s.entity_id AND r.project_id = s.project_id))
        ) OR s.type IS NULL
    )
"""


async def inspect_index_integrity(
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
    *,
    sample_limit: int = 20,
) -> IndexIntegrityReport:
    """Inspect existing state on SQLite or PostgreSQL; never initialize or repair it.

    Indexing may run concurrently, so these are observations, not a permanent
    corruption verdict. Counts cover all rows; examples are bounded separately.
    Missing tables and database failures propagate instead of reporting success.

    ``inverted_timestamps`` is informational, not an anomaly: when frontmatter does
    not declare created/modified, the parser falls back to the file's ctime/mtime,
    and preserving mtimes while copying or restoring a vault legitimately produces
    updated_at < created_at. Callers must not treat it as index damage.
    """
    if sample_limit < 0:
        raise ValueError("sample_limit must be non-negative")
    params = {"project_id": project_id, "limit": sample_limit}
    async with session_maker() as session:
        counts = {
            row[0]: int(row[1])
            for row in (
                await session.execute(
                    text(
                        "SELECT type, COUNT(*) FROM search_index WHERE project_id = :project_id GROUP BY type"
                    ),
                    params,
                )
            ).all()
        }
        duplicates = (
            await session.execute(
                text(
                    f"SELECT COUNT(*), COALESCE(SUM(copies - 1), 0) FROM ({_DUPLICATES}) duplicates"
                ),
                params,
            )
        ).one()
        orphans = int(
            (await session.execute(text(f"SELECT COUNT(*) {_ORPHANS}"), params)).scalar_one()
        )
        entities = int(
            (
                await session.execute(
                    select(func.count()).select_from(Entity).where(Entity.project_id == project_id)
                )
            ).scalar_one()
        )
        inverted = select(Entity.id).where(
            Entity.project_id == project_id, Entity.updated_at < Entity.created_at
        )
        inverted_count = int(
            (
                await session.execute(select(func.count()).select_from(inverted.subquery()))
            ).scalar_one()
        )
        examples = [
            f"Duplicate {row.type} id={row.id}: {row.copies} copies"
            for row in (
                await session.execute(text(f"{_DUPLICATES} ORDER BY type, id LIMIT :limit"), params)
            ).all()
        ]
        remaining = sample_limit - len(examples)
        examples.extend(
            f"Orphan {row.type} id={row.id}, entity_id={row.entity_id}"
            for row in (
                await session.execute(
                    text(
                        f"SELECT s.type, s.id, s.entity_id {_ORPHANS} ORDER BY s.type, s.id LIMIT :limit"
                    ),
                    {**params, "limit": remaining},
                )
            ).all()
        )
        return IndexIntegrityReport(
            entities=entities,
            search_rows=sum(counts.values()),
            entity_rows=counts.get("entity", 0),
            observation_rows=counts.get("observation", 0),
            relation_rows=counts.get("relation", 0),
            duplicate_keys=int(duplicates[0]),
            duplicate_rows=int(duplicates[1]),
            orphaned_rows=orphans,
            inverted_timestamps=inverted_count,
            examples=tuple(examples),
        )
