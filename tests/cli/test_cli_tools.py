"""Tests for the Basic Memory CLI tools.

These tests verify CLI tool functionality. Some tests that previously used
subprocess have been removed due to a pre-existing CLI architecture issue
where ASGI transport doesn't trigger FastAPI lifespan initialization.

The subprocess-based integration tests are kept in test_cli_integration.py
for future use when the CLI initialization issue is fixed.
"""

import pytest


def test_ensure_migrations_functionality(app_config, monkeypatch):
    """Test the database initialization functionality."""
    import basic_memory.services.initialization as init_mod

    calls = {"count": 0}

    async def fake_initialize_database(*args, **kwargs):
        calls["count"] += 1

    monkeypatch.setattr(init_mod, "initialize_database", fake_initialize_database)
    init_mod.ensure_initialization(app_config)
    assert calls["count"] == 1


def test_ensure_migrations_propagates_errors(app_config, monkeypatch):
    """Test that initialization errors propagate to caller."""
    import basic_memory.services.initialization as init_mod

    async def fake_initialize_database(*args, **kwargs):
        raise Exception("Test error")

    monkeypatch.setattr(init_mod, "initialize_database", fake_initialize_database)

    with pytest.raises(Exception, match="Test error"):
        init_mod.ensure_initialization(app_config)
