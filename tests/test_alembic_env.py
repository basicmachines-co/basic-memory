"""Regression tests for Alembic env async migration helpers."""

import importlib.util
import uuid
from contextlib import nullcontext
from pathlib import Path

import pytest


class FakeAlembicConfig:
    """Minimal config object used while importing env.py under test."""

    def __init__(self):
        self.options = {"sqlalchemy.url": "sqlite:///:memory:"}
        self.attributes = {}
        self.config_file_name = None
        self.config_ini_section = "alembic"

    def get_main_option(self, name: str) -> str | None:
        return self.options.get(name)

    def set_main_option(self, name: str, value: str) -> None:
        self.options[name] = value

    def get_section(self, name: str, default=None):
        return default or {}


class FakeCoroutine:
    """Track whether the migration coroutine gets closed on failure."""

    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


def load_alembic_env_module(monkeypatch, tmp_path):
    """Import env.py with a fake Alembic context and isolated HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    from alembic import context as alembic_context

    fake_config = FakeAlembicConfig()
    monkeypatch.setattr(alembic_context, "config", fake_config, raising=False)
    monkeypatch.setattr(alembic_context, "configure", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(alembic_context, "begin_transaction", lambda: nullcontext(), raising=False)
    monkeypatch.setattr(alembic_context, "run_migrations", lambda: None, raising=False)
    monkeypatch.setattr(alembic_context, "is_offline_mode", lambda: True, raising=False)

    env_path = Path(__file__).resolve().parents[1] / "src/basic_memory/alembic/env.py"
    module_name = f"test_alembic_env_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, env_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asyncio_run_failure_closes_migration_coroutine(monkeypatch, tmp_path):
    """The running-loop fallback should not leak an un-awaited coroutine."""
    env_module = load_alembic_env_module(monkeypatch, tmp_path)
    fake_coro = FakeCoroutine()

    monkeypatch.setattr(env_module, "run_async_migrations", lambda connectable: fake_coro)

    def raising_asyncio_run(coro):
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    monkeypatch.setattr(env_module.asyncio, "run", raising_asyncio_run)

    with pytest.raises(RuntimeError, match="running event loop"):
        env_module._run_async_migrations_with_asyncio_run(object())

    assert fake_coro.closed is True


def test_running_loop_error_uses_thread_fallback(monkeypatch, tmp_path):
    """Async-engine helper should switch to the thread fallback for running-loop errors."""
    env_module = load_alembic_env_module(monkeypatch, tmp_path)
    connectable = object()
    fallback_calls: list[object] = []

    def raising_run(connectable):
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    def record_fallback(target):
        fallback_calls.append(target)

    monkeypatch.setattr(env_module, "_run_async_migrations_with_asyncio_run", raising_run)
    monkeypatch.setattr(env_module, "_run_async_migrations_in_thread", record_fallback)

    env_module._run_async_engine_migrations(connectable)

    assert fallback_calls == [connectable]


def test_non_loop_runtime_error_is_re_raised(monkeypatch, tmp_path):
    """Unexpected RuntimeError values should not be swallowed by the fallback path."""
    env_module = load_alembic_env_module(monkeypatch, tmp_path)

    def raising_run(connectable):
        raise RuntimeError("different runtime failure")

    monkeypatch.setattr(env_module, "_run_async_migrations_with_asyncio_run", raising_run)

    with pytest.raises(RuntimeError, match="different runtime failure"):
        env_module._run_async_engine_migrations(object())
