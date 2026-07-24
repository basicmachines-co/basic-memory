"""Regression tests for semantic search repository startup composition."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository import search_repository as search_repository_module


@pytest.mark.asyncio
async def test_migrations_initialize_search_through_repository_factory(
    monkeypatch, tmp_path
) -> None:
    """Startup must load configured vector extensions through the repository factory."""
    config = BasicMemoryConfig(
        env="test",
        database_backend=DatabaseBackend.POSTGRES,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        semantic_search_enabled=True,
        semantic_vector_index="milvus",
    )
    session_maker = object()
    repository = SimpleNamespace(init_search_index=AsyncMock())
    factory_calls: list[dict[str, object]] = []

    def fake_create_search_repository(**kwargs):
        factory_calls.append(kwargs)
        return repository

    monkeypatch.setattr(db, "_session_maker", session_maker)
    monkeypatch.setattr(db.command, "upgrade", lambda *_args: None)
    monkeypatch.setattr(
        search_repository_module,
        "create_search_repository",
        fake_create_search_repository,
    )

    await db.run_migrations(config, db.DatabaseType.POSTGRES)

    assert factory_calls == [
        {
            "session_maker": session_maker,
            "project_id": 1,
            "app_config": config,
            "database_backend": DatabaseBackend.POSTGRES,
        }
    ]
    repository.init_search_index.assert_awaited_once()
