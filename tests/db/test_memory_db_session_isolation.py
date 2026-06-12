"""Regression test for issue #940: lost writes on the in-memory SQLite engine.

The in-memory SQLite URL (``sqlite+aiosqlite://``) used to fall back to
SQLAlchemy's StaticPool, which hands the same DBAPI connection to every
concurrently checked-out session. Concurrent asyncio tasks then share one
transaction scope: a rollback issued by one session — scoped_session's
exception handling, or the pool's reset-on-return at checkin — silently rolls
back another session's uncommitted writes. During sync this manifested as a
freshly inserted relation row vanishing without any error, which is how
``test_sync_entity_circular_relations`` failed on CI with
``len(entity_b.outgoing_relations) == 0``.

This test pins the isolation invariant directly: a session that rolls back in
one task must never destroy an uncommitted write of a session in another task.
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Base


class _SimulatedIndexingFailure(Exception):
    """Stand-in for any per-file error that _run_bounded swallows during sync."""


@pytest.mark.asyncio
async def test_concurrent_session_rollback_does_not_destroy_uncommitted_writes():
    """A rolled-back session in one task must not erase another task's writes."""
    async with db.engine_session_factory(
        db_path=Path("unused.db"), db_type=db.DatabaseType.MEMORY
    ) as (engine, session_maker):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed a project and entity so a relation row satisfies its FK constraints.
        async with db.scoped_session(session_maker) as session:
            await session.execute(
                text(
                    "INSERT INTO project (id, external_id, name, description, path, is_active,"
                    " is_default, created_at, updated_at, permalink) "
                    "VALUES (1, 'px', 'p', '', '/tmp', 1, 1, '2024-01-01', '2024-01-01', 'p')"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO entity (id, external_id, title, note_type, content_type,"
                    " project_id, file_path, created_at, updated_at) "
                    "VALUES (1, 'ex', 'E', 'note', 'text/markdown', 1, 'e.md',"
                    " '2024-01-01', '2024-01-01')"
                )
            )

        write_in_flight = asyncio.Event()

        async def writer() -> None:
            # Mirrors RelationRepository.add_all_ignore_duplicates: INSERT executed,
            # commit only happens at scoped_session exit several awaits later.
            async with db.scoped_session(session_maker) as session:
                await session.execute(
                    text(
                        "INSERT INTO relation (project_id, from_id, to_id, to_name,"
                        " relation_type) VALUES (1, 1, NULL, 'target', 'depends_on')"
                    )
                )
                write_in_flight.set()
                # Real DB roundtrips (not sleeps) keep this transaction open across
                # await points, exactly like the multi-statement sessions in sync.
                for _ in range(10):
                    await session.execute(text("SELECT 1"))

        async def failing_reader() -> None:
            # Mirrors any per-file failure during batch indexing: scoped_session
            # rolls back on the exception path while sibling tasks are mid-write.
            await write_in_flight.wait()
            with pytest.raises(_SimulatedIndexingFailure):
                async with db.scoped_session(session_maker) as session:
                    await session.execute(text("SELECT 1"))
                    raise _SimulatedIndexingFailure()

        await asyncio.gather(writer(), failing_reader())

        async with db.scoped_session(session_maker) as session:
            count = (await session.execute(text("SELECT count(*) FROM relation"))).scalar()

        assert count == 1, (
            "writer's committed INSERT was rolled back by a concurrent session — "
            "the in-memory engine is sharing one transaction scope across sessions"
        )
