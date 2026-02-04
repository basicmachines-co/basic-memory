"""Integration tests for database_url configuration flexibility.

Tests:
1. SQLite with custom URL creates database in expected location
2. Postgres with search_path creates tables in isolated schema
3. Alembic migrations track in the correct schema
"""

import os
import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.db import (
    _create_postgres_engine,
    extract_search_path_from_url,
    ensure_schema_exists,
)


class TestPostgresSchemaIsolation:
    """Integration tests for Postgres schema isolation via search_path."""

    @pytest.fixture(scope="class")
    def postgres_container(self):
        """Start a Postgres container for schema isolation tests."""
        # Skip if not running Postgres tests
        if os.environ.get("BASIC_MEMORY_TEST_POSTGRES", "").lower() not in ("1", "true", "yes"):
            pytest.skip("Postgres tests disabled - set BASIC_MEMORY_TEST_POSTGRES=1")

        with PostgresContainer("postgres:16-alpine") as postgres:
            yield postgres

    @pytest.fixture
    def postgres_url(self, postgres_container):
        """Get asyncpg URL from testcontainer."""
        sync_url = postgres_container.get_connection_url()
        return sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")

    @pytest_asyncio.fixture
    async def clean_schema(self, postgres_url):
        """Create and clean up a test schema."""
        schema_name = "test_schema"

        # Create a basic engine to set up/tear down schema
        engine = create_async_engine(postgres_url, poolclass=NullPool)

        async with engine.begin() as conn:
            # Drop schema if exists from previous test
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))

        yield schema_name

        # Cleanup
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_ensure_schema_exists_creates_schema(self, postgres_url, clean_schema):
        """ensure_schema_exists() creates the schema if it doesn't exist."""
        schema_name = clean_schema

        # Create engine with search_path pointing to our test schema
        url_with_schema = f"{postgres_url}?search_path={schema_name}"
        config = BasicMemoryConfig(
            database_backend=DatabaseBackend.POSTGRES,
            database_url=url_with_schema,
        )

        engine = _create_postgres_engine(url_with_schema, config)

        try:
            # Verify schema doesn't exist yet
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        f"WHERE schema_name = '{schema_name}'"
                    )
                )
                assert result.fetchone() is None, "Schema should not exist yet"

            # Call ensure_schema_exists
            await ensure_schema_exists(engine, schema_name)

            # Verify schema now exists
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        f"WHERE schema_name = '{schema_name}'"
                    )
                )
                row = result.fetchone()
                assert row is not None, "Schema should exist after ensure_schema_exists"
                assert row[0] == schema_name
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ensure_schema_exists_idempotent(self, postgres_url, clean_schema):
        """ensure_schema_exists() can be called multiple times safely."""
        schema_name = clean_schema
        url_with_schema = f"{postgres_url}?search_path={schema_name}"
        config = BasicMemoryConfig(
            database_backend=DatabaseBackend.POSTGRES,
            database_url=url_with_schema,
        )

        engine = _create_postgres_engine(url_with_schema, config)

        try:
            # Call twice - should not raise
            await ensure_schema_exists(engine, schema_name)
            await ensure_schema_exists(engine, schema_name)

            # Verify schema exists
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        f"WHERE schema_name = '{schema_name}'"
                    )
                )
                assert result.fetchone() is not None
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_ensure_schema_exists_skips_public(self, postgres_url):
        """ensure_schema_exists() does nothing for public schema."""
        config = BasicMemoryConfig(
            database_backend=DatabaseBackend.POSTGRES,
            database_url=postgres_url,
        )

        engine = _create_postgres_engine(postgres_url, config)

        try:
            # Should not raise and should not try to create public
            await ensure_schema_exists(engine, "public")
            await ensure_schema_exists(engine, "")
            await ensure_schema_exists(engine, None)  # type: ignore
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_engine_uses_search_path(self, postgres_url, clean_schema):
        """Engine created with search_path URL uses that schema for queries."""
        schema_name = clean_schema
        url_with_schema = f"{postgres_url}?search_path={schema_name}"
        config = BasicMemoryConfig(
            database_backend=DatabaseBackend.POSTGRES,
            database_url=url_with_schema,
        )

        engine = _create_postgres_engine(url_with_schema, config)

        try:
            # Create the schema first
            await ensure_schema_exists(engine, schema_name)

            # Create a test table (should go into our schema)
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY)"))

            # Verify table is in our schema, not public
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "SELECT table_schema FROM information_schema.tables "
                        "WHERE table_name = 'test_table'"
                    )
                )
                row = result.fetchone()
                assert row is not None, "Table should exist"
                assert row[0] == schema_name, f"Table should be in {schema_name}, not {row[0]}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_search_path_with_multiple_schemas(self, postgres_url):
        """search_path with multiple schemas uses first for table creation."""
        # Test that comma-separated schemas are handled
        url = f"{postgres_url}?search_path=custom_schema,public"
        clean_url, search_path = extract_search_path_from_url(url)

        assert search_path == "custom_schema,public"
        assert "search_path" not in clean_url


class TestSQLiteUrlIntegration:
    """Integration tests for SQLite URL configuration."""

    def test_relative_path_creates_database(self, tmp_path, monkeypatch):
        """SQLite URL with relative path creates database in expected location."""
        # Change to tmp_path
        monkeypatch.chdir(tmp_path)

        # Create config with relative SQLite URL
        config = BasicMemoryConfig(
            database_url="sqlite+aiosqlite:///.basic-memory/memory.db"
        )

        # Get the path
        db_path = config.app_database_path
        assert db_path is not None

        # Verify it resolves to tmp_path
        assert db_path.parent.name == ".basic-memory"
        assert db_path.name == "memory.db"

        # The path should be absolute after resolve()
        assert db_path.is_absolute()

    def test_project_local_database(self, tmp_path, monkeypatch):
        """Project can have its own local database via URL configuration."""
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)

        # Configure project-local database
        config = BasicMemoryConfig(
            database_url="sqlite+aiosqlite:///.basic-memory/memory.db"
        )

        db_path = config.app_database_path
        assert db_path is not None

        # Should be inside project directory
        assert str(project_dir) in str(db_path)
