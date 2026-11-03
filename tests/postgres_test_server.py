"""Locate the Postgres server a test session uses, one database per xdist worker.

CI provides a single Postgres service through BASIC_MEMORY_TEST_POSTGRES_URL. Every
test truncates and rebuilds the schema, so xdist workers sharing one database would
wipe each other's rows. Each worker therefore gets its own database on that server,
named after the configured database plus the worker id. Without xdist, the session
uses the configured database unchanged.

Without a configured URL, each session (and so each worker) starts its own
testcontainers server, which is already isolated.
"""

import functools
import os
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import Connection, URL, create_engine, text
from sqlalchemy.engine import make_url

from basic_memory import db
from basic_memory.models import Base
from basic_memory.models.search import (
    CREATE_POSTGRES_SEARCH_INDEX_FTS,
    CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_INDEX,
    CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_SCRIPT_NGRAMS_INDEX,
    CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_TABLE,
    CREATE_POSTGRES_SEARCH_INDEX_METADATA,
    CREATE_POSTGRES_SEARCH_INDEX_PERMALINK,
    CREATE_POSTGRES_SEARCH_INDEX_SCRIPT_NGRAMS_FTS,
    CREATE_POSTGRES_SEARCH_INDEX_TABLE,
    CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_INDEX,
    CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_LOCATION_INDEX,
    CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_TABLE,
)

SEARCH_SCHEMA = [
    CREATE_POSTGRES_SEARCH_INDEX_TABLE,
    CREATE_POSTGRES_SEARCH_INDEX_FTS,
    CREATE_POSTGRES_SEARCH_INDEX_SCRIPT_NGRAMS_FTS,
    CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_TABLE,
    CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_INDEX,
    CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_SCRIPT_NGRAMS_INDEX,
    CREATE_POSTGRES_SEARCH_INDEX_METADATA,
    CREATE_POSTGRES_SEARCH_INDEX_PERMALINK,
    CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_TABLE,
    CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_INDEX,
    CREATE_POSTGRES_SEARCH_VECTOR_CHUNKS_LOCATION_INDEX,
]


def configured_postgres_sync_url() -> str | None:
    """Return the psycopg2 URL of this session's database on the configured server."""
    configured_url = os.environ.get("BASIC_MEMORY_TEST_POSTGRES_URL") or os.environ.get(
        "POSTGRES_TEST_URL"
    )
    if not configured_url:
        return None

    sync_url = (
        configured_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        .replace("postgresql://", "postgresql+psycopg2://", 1)
        .replace("postgres://", "postgresql+psycopg2://", 1)
    )
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return sync_url
    return _fresh_worker_database(sync_url, worker)


# Fixtures resolve the URL for every test; the database is rebuilt once per session.
@functools.cache
def _fresh_worker_database(sync_url: str, worker: str) -> str:
    """Recreate this worker's database with the test schema and return its URL.

    The database is dropped first, so a session never inherits state a previous run
    left on a reused server (a schema a test created, a half-reset table).
    """
    server_url = make_url(sync_url)
    worker_url = server_url.set(database=f"{server_url.database}_{worker}")

    # CREATE DATABASE cannot run inside a transaction block. Callers only rewrite the
    # psycopg2 URL to asyncpg; these admin connections use psycopg (v3), the installed
    # sync driver.
    admin = create_engine(
        server_url.set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{worker_url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{worker_url.database}"'))
    finally:
        admin.dispose()

    # Why: the per-test reset only runs for tests that use the engine fixture. A worker's
    #      first test may instead start the MCP lifespan, whose migrations would build the
    #      Alembic schema (naive timestamps, extra foreign keys) that create_all never fixes.
    # Outcome: every worker starts from the same metadata schema, stamped at head.
    _build_test_schema(worker_url)

    return worker_url.render_as_string(hide_password=False)


def _build_test_schema(worker_url: URL) -> None:
    engine = create_engine(worker_url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
            for statement in SEARCH_SCHEMA:
                conn.execute(statement)
            _stamp_head(conn)
    finally:
        engine.dispose()


def _stamp_head(conn: Connection) -> None:
    alembic_dir = Path(db.__file__).parent / "alembic"
    head = ScriptDirectory(str(alembic_dir)).get_current_head()
    conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
    conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:head)"), {"head": head})
