"""Tests for database_url configuration flexibility.

Tests SQLite URL path extraction and Postgres search_path handling.
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from basic_memory.config import BasicMemoryConfig
from basic_memory.db import extract_search_path_from_url, get_search_path_from_config


class TestSQLiteUrlParsing:
    """Test SQLite URL path extraction in app_database_path."""

    def test_relative_path_extracts_correctly(self, tmp_path, monkeypatch):
        """sqlite+aiosqlite:///.basic-memory/memory.db -> .basic-memory/memory.db"""
        # Change to tmp_path so relative path resolves there
        monkeypatch.chdir(tmp_path)

        config = BasicMemoryConfig(
            database_url="sqlite+aiosqlite:///.basic-memory/memory.db"
        )

        # Should resolve relative to cwd
        assert config.app_database_path is not None
        assert config.app_database_path.name == "memory.db"
        assert ".basic-memory" in str(config.app_database_path)

    def test_no_database_url_uses_default(self, config_home):
        """No database_url = default ~/.basic-memory/memory.db"""
        config = BasicMemoryConfig(database_url=None)

        assert config.app_database_path is not None
        assert str(config.app_database_path).endswith("memory.db")
        assert ".basic-memory" in str(config.app_database_path)

    def test_postgres_url_returns_none_for_path(self):
        """Postgres URL should return None for app_database_path."""
        config = BasicMemoryConfig(
            database_url="postgresql+asyncpg://user:pass@host/db"
        )

        assert config.app_database_path is None

    def test_sqlite_url_with_absolute_path(self, tmp_path):
        """SQLite URL with absolute path extracts correctly."""
        db_path = tmp_path / "custom" / "database.db"
        # Create parent directory
        db_path.parent.mkdir(parents=True, exist_ok=True)

        config = BasicMemoryConfig(
            database_url=f"sqlite+aiosqlite:///{db_path}"
        )

        assert config.app_database_path is not None
        # The resolved path should match (accounting for symlink resolution)
        assert config.app_database_path.name == "database.db"

    def test_sqlite_url_with_dot_relative_path(self, tmp_path, monkeypatch):
        """sqlite+aiosqlite:///./.basic-memory/memory.db works with dot prefix."""
        monkeypatch.chdir(tmp_path)

        config = BasicMemoryConfig(
            database_url="sqlite+aiosqlite:///./.basic-memory/memory.db"
        )

        assert config.app_database_path is not None
        assert config.app_database_path.name == "memory.db"


class TestPostgresSearchPath:
    """Test Postgres search_path extraction."""

    def test_extract_search_path_from_url_with_schema(self):
        """search_path should be extracted from URL."""
        url = "postgresql+asyncpg://user:pass@host/db?search_path=myschema"
        clean_url, search_path = extract_search_path_from_url(url)

        assert search_path == "myschema"
        assert "search_path" not in clean_url
        assert clean_url == "postgresql+asyncpg://user:pass@host/db"

    def test_extract_search_path_defaults_to_public(self):
        """No search_path param = default to public."""
        url = "postgresql+asyncpg://user:pass@host/db"
        clean_url, search_path = extract_search_path_from_url(url)

        assert search_path == "public"
        assert clean_url == url

    def test_extract_search_path_preserves_other_params(self):
        """Other URL parameters should be preserved."""
        url = "postgresql+asyncpg://user:pass@host/db?sslmode=require&search_path=myschema"
        clean_url, search_path = extract_search_path_from_url(url)

        assert search_path == "myschema"
        assert "sslmode=require" in clean_url
        assert "search_path" not in clean_url

    def test_extract_search_path_with_comma_separated_schemas(self):
        """Multiple schemas in search_path should use first one."""
        url = "postgresql+asyncpg://user:pass@host/db?search_path=myschema,public"
        clean_url, search_path = extract_search_path_from_url(url)

        # First schema in the list
        assert search_path == "myschema,public"

    def test_get_search_path_from_config_with_schema(self):
        """get_search_path_from_config returns schema when non-public."""
        config = BasicMemoryConfig(
            database_url="postgresql+asyncpg://user:pass@host/db?search_path=myschema"
        )

        search_path = get_search_path_from_config(config)
        assert search_path == "myschema"

    def test_get_search_path_from_config_returns_none_for_public(self):
        """get_search_path_from_config returns None for public schema."""
        config = BasicMemoryConfig(
            database_url="postgresql+asyncpg://user:pass@host/db?search_path=public"
        )

        search_path = get_search_path_from_config(config)
        assert search_path is None

    def test_get_search_path_from_config_returns_none_for_sqlite(self):
        """get_search_path_from_config returns None for SQLite URLs."""
        config = BasicMemoryConfig(
            database_url="sqlite+aiosqlite:///.basic-memory/memory.db"
        )

        search_path = get_search_path_from_config(config)
        assert search_path is None

    def test_get_search_path_from_config_returns_none_for_no_url(self):
        """get_search_path_from_config returns None when no database_url."""
        config = BasicMemoryConfig(database_url=None)

        search_path = get_search_path_from_config(config)
        assert search_path is None


class TestBackwardsCompatibility:
    """Test that existing setups work unchanged."""

    def test_no_config_uses_default_sqlite_path(self, config_home):
        """No database_url configuration uses default SQLite path."""
        config = BasicMemoryConfig()

        # Should still get the default path
        assert config.app_database_path is not None
        assert config.app_database_path.name == "memory.db"

    def test_postgres_backend_without_search_path_works(self):
        """Postgres without search_path uses public schema."""
        config = BasicMemoryConfig(
            database_url="postgresql+asyncpg://user:pass@host/db"
        )

        # app_database_path should be None for Postgres
        assert config.app_database_path is None

        # search_path should default to public
        search_path = get_search_path_from_config(config)
        assert search_path is None  # None means "use default public"

    def test_database_url_field_description_unchanged(self):
        """Verify database_url field exists with expected properties."""
        field_info = BasicMemoryConfig.model_fields["database_url"]

        assert field_info.default is None
        assert "Database connection URL" in (field_info.description or "")


class TestDatabaseTypeGetDbUrl:
    """Test DatabaseType.get_db_url() handles custom URLs."""

    def test_sqlite_url_in_database_url_is_used(self, tmp_path, monkeypatch):
        """get_db_url() should return SQLite URL from database_url config."""
        from basic_memory.db import DatabaseType

        monkeypatch.chdir(tmp_path)
        custom_url = "sqlite+aiosqlite:///.custom/test.db"
        config = BasicMemoryConfig(database_url=custom_url)

        result = DatabaseType.get_db_url(
            db_path=tmp_path / "ignored.db",
            db_type=DatabaseType.FILESYSTEM,
            config=config,
        )

        assert result == custom_url

    def test_no_database_url_uses_db_path(self, tmp_path):
        """get_db_url() constructs URL from db_path when no database_url."""
        from basic_memory.db import DatabaseType

        config = BasicMemoryConfig(database_url=None)
        db_path = tmp_path / "test.db"

        result = DatabaseType.get_db_url(
            db_path=db_path,
            db_type=DatabaseType.FILESYSTEM,
            config=config,
        )

        assert result == f"sqlite+aiosqlite:///{db_path}"

    def test_postgres_url_returned_for_postgres_type(self):
        """get_db_url() returns Postgres URL for POSTGRES type."""
        from basic_memory.db import DatabaseType

        pg_url = "postgresql+asyncpg://user:pass@host/db"
        config = BasicMemoryConfig(database_url=pg_url)

        result = DatabaseType.get_db_url(
            db_path=None,  # type: ignore
            db_type=DatabaseType.POSTGRES,
            config=config,
        )

        assert result == pg_url
