from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest


def load_read_load_bench() -> ModuleType:
    script_path = Path(__file__).parents[1] / "benchmarks" / "scripts" / "read_load_bench.py"
    spec = importlib.util.spec_from_file_location("read_load_bench", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load benchmark script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


read_load_bench = load_read_load_bench()


@dataclass
class RecordingScalarResult:
    version: str

    def scalar_one(self) -> str:
        return self.version


@dataclass
class RecordingConnection:
    version: str
    query: str | None = None

    async def __aenter__(self) -> RecordingConnection:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def exec_driver_sql(self, query: str) -> RecordingScalarResult:
        self.query = query
        return RecordingScalarResult(self.version)


@dataclass
class RecordingEngine:
    connection: RecordingConnection
    disposed: bool = False

    def connect(self) -> RecordingConnection:
        return self.connection

    async def dispose(self) -> None:
        self.disposed = True


def test_benchmark_manifest_stays_beside_custom_output(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    output_path = tmp_path / "published" / "results.jsonl"

    assert (
        read_load_bench.benchmark_manifest_path(
            scratch=scratch,
            output_path=output_path,
        )
        == output_path.parent / "manifest.json"
    )
    assert (
        read_load_bench.benchmark_manifest_path(
            scratch=scratch,
            output_path=None,
        )
        == scratch / "manifest.json"
    )


@pytest.mark.asyncio
async def test_database_server_version_reports_sqlite_library() -> None:
    assert (
        await read_load_bench.database_server_version(backend="sqlite", database_url=None)
        == sqlite3.sqlite_version
    )


@pytest.mark.asyncio
async def test_database_server_version_queries_postgres_and_closes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = RecordingConnection(version="16.9")
    engine = RecordingEngine(connection=connection)
    observed_url: str | None = None

    def create_async_engine(database_url: str) -> RecordingEngine:
        nonlocal observed_url
        observed_url = database_url
        return engine

    monkeypatch.setattr(read_load_bench, "create_async_engine", create_async_engine)

    version = await read_load_bench.database_server_version(
        backend="postgres",
        database_url="postgresql+asyncpg://benchmark.test/readload",
    )

    assert version == "16.9"
    assert observed_url == "postgresql+asyncpg://benchmark.test/readload"
    assert connection.query == "SHOW server_version"
    assert engine.disposed


@pytest.mark.asyncio
async def test_database_server_version_requires_postgres_url() -> None:
    with pytest.raises(ValueError, match="Postgres provenance requires a database URL"):
        await read_load_bench.database_server_version(backend="postgres", database_url=None)


def test_basic_memory_runtime_uses_target_python_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_dir = tmp_path / "benchmark-checkout" / ".venv" / "bin"
    command_dir.mkdir(parents=True)
    command_path = command_dir / "basic-memory"
    command_path.touch()
    python_path = command_dir / "python"
    python_path.touch()

    module_path = tmp_path / "basic-memory-source" / "src" / "basic_memory" / "__init__.py"
    module_path.parent.mkdir(parents=True)
    module_path.touch()
    observed_commands: list[list[str]] = []

    def checked_output(command: list[str], *, env: dict[str, str] | None = None) -> str:
        observed_commands.append(command)
        assert env == {"BENCHMARK_TEST": "target"}
        return json.dumps(
            {
                "module_path": str(module_path),
                "python_version": "3.12.11",
            }
        )

    monkeypatch.setattr(read_load_bench, "checked_output", checked_output)

    runtime = read_load_bench.basic_memory_runtime(
        command_path,
        {"BENCHMARK_TEST": "target"},
    )

    assert runtime.module_path == module_path
    assert runtime.python_version == "3.12.11"
    assert observed_commands[0][0] == str(python_path)


@pytest.mark.parametrize("runtime_output", ["not json", "{}", "[]"])
def test_basic_memory_runtime_rejects_invalid_target_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_output: str,
) -> None:
    command_dir = tmp_path / "benchmark-checkout" / ".venv" / "bin"
    command_dir.mkdir(parents=True)
    command_path = command_dir / "basic-memory"
    command_path.touch()
    (command_dir / "python").touch()
    monkeypatch.setattr(read_load_bench, "checked_output", lambda *args, **kwargs: runtime_output)

    with pytest.raises((RuntimeError, TypeError), match="runtime provenance"):
        read_load_bench.basic_memory_runtime(command_path, {})


def test_isolated_env_removes_all_inherited_basic_memory_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited_keys = (
        "BASIC_MEMORY_PROJECT_ROOT",
        "BASIC_MEMORY_CLOUD_API_KEY",
        "BASIC_MEMORY_DATABASE_POOL_SIZE",
    )
    for key in inherited_keys:
        monkeypatch.setenv(key, "must-not-escape")
    monkeypatch.setenv("BASIC_MEMORY_AUTO_UPDATE", "true")
    monkeypatch.setenv("BENCHMARK_TEST", "preserved")

    env = read_load_bench.isolated_env(
        tmp_path / "config",
        redis_url="redis",
        redis_max_connections=64,
    )

    assert env["BENCHMARK_TEST"] == "preserved"
    assert env["BASIC_MEMORY_AUTO_UPDATE"] == "false"
    assert env["BASIC_MEMORY_REDIS_MAX_CONNECTIONS"] == "64"
    assert all(key not in env for key in inherited_keys)
    assert {key for key in env if key.startswith("BASIC_MEMORY_")} == {
        "BASIC_MEMORY_AUTO_UPDATE",
        "BASIC_MEMORY_CONFIG_DIR",
        "BASIC_MEMORY_LOG_LEVEL",
        "BASIC_MEMORY_REDIS_MAX_CONNECTIONS",
        "BASIC_MEMORY_REDIS_URL",
        "BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED",
    }


def benchmark_args(scratch: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "backend": "postgres",
        "bm_command": "basic-memory",
        "concurrency": "1",
        "database_url": None,
        "label": "test",
        "notes_per_size": 1,
        "output": None,
        "quiesce_seconds": 0.0,
        "reads": 1,
        "ready_timeout": 1.0,
        "redis_url": "redis://unreachable.test",
        "scratch": str(scratch),
        "seed": 1021,
        "seed_concurrency": 1,
        "sizes": "1024",
        "truncate": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@dataclass
class RecordingPostgresContainer:
    stopped: bool = False

    def stop(self) -> None:
        self.stopped = True

    def get_connection_url(
        self,
        host: str | None = None,
        driver: str | None = None,
    ) -> str:
        return "postgresql://benchmark:benchmark@localhost:5432/benchmark"


@dataclass
class FailingStartPostgresContainer(RecordingPostgresContainer):
    def start(self) -> object:
        raise RuntimeError("Postgres readiness failed")


def test_start_postgres_stops_container_when_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from testcontainers import postgres as postgres_module

    container = FailingStartPostgresContainer()
    monkeypatch.setattr(postgres_module, "PostgresContainer", lambda image: container)

    with pytest.raises(RuntimeError, match="Postgres readiness failed"):
        read_load_bench.start_postgres()

    assert container.stopped


@pytest.mark.asyncio
async def test_run_stops_postgres_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RecordingPostgresContainer()
    monkeypatch.setattr(read_load_bench, "start_postgres", lambda: container)

    async def fail_redis_provenance(redis_url: str) -> str:
        raise RuntimeError(f"Redis unavailable: {redis_url}")

    async def database_provenance(*, backend: str, database_url: str | None) -> str:
        assert backend == "postgres"
        assert database_url is not None
        return "16.9"

    monkeypatch.setattr(read_load_bench, "database_server_version", database_provenance)
    monkeypatch.setattr(read_load_bench, "redis_server_version", fail_redis_provenance)

    with pytest.raises(RuntimeError, match="Redis unavailable"):
        await read_load_bench.run(benchmark_args(tmp_path / "run"))

    assert container.stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["notes_per_size", "reads", "seed_concurrency"])
async def test_run_rejects_non_positive_counts_before_starting_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
) -> None:
    postgres_started = False

    def start_postgres() -> RecordingPostgresContainer:
        nonlocal postgres_started
        postgres_started = True
        return RecordingPostgresContainer()

    monkeypatch.setattr(read_load_bench, "start_postgres", start_postgres)

    with pytest.raises(ValueError, match=f"--{option.replace('_', '-')}"):
        await read_load_bench.run(benchmark_args(tmp_path / option, **{option: 0}))

    assert not postgres_started


@pytest.mark.asyncio
async def test_run_rejects_duplicate_sizes_before_starting_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_started = False

    def start_postgres() -> RecordingPostgresContainer:
        nonlocal postgres_started
        postgres_started = True
        return RecordingPostgresContainer()

    monkeypatch.setattr(read_load_bench, "start_postgres", start_postgres)

    with pytest.raises(ValueError, match="--sizes must not contain duplicates"):
        await read_load_bench.run(benchmark_args(tmp_path / "duplicate-sizes", sizes="1024,1024"))

    assert not postgres_started


@pytest.mark.asyncio
async def test_run_rejects_duplicate_concurrency_before_starting_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_started = False

    def start_postgres() -> RecordingPostgresContainer:
        nonlocal postgres_started
        postgres_started = True
        return RecordingPostgresContainer()

    monkeypatch.setattr(read_load_bench, "start_postgres", start_postgres)

    with pytest.raises(ValueError, match="--concurrency must not contain duplicates"):
        await read_load_bench.run(
            benchmark_args(tmp_path / "duplicate-concurrency", concurrency="1,8,8")
        )

    assert not postgres_started


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_directory", ["config", "project", "main-home"])
async def test_run_rejects_output_inside_runtime_directories_before_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_directory: str,
) -> None:
    postgres_started = False
    scratch = tmp_path / "runtime-output"
    output_dir = scratch / runtime_directory
    output_dir.mkdir(parents=True)
    sentinel = output_dir / "keep.txt"
    sentinel.write_text("preserved\n", encoding="utf-8")

    def start_postgres() -> RecordingPostgresContainer:
        nonlocal postgres_started
        postgres_started = True
        return RecordingPostgresContainer()

    monkeypatch.setattr(read_load_bench, "start_postgres", start_postgres)

    with pytest.raises(ValueError, match="outside benchmark runtime directories"):
        await read_load_bench.run(benchmark_args(scratch, output=str(output_dir / "results.jsonl")))

    assert sentinel.read_text(encoding="utf-8") == "preserved\n"
    assert not postgres_started


@pytest.mark.asyncio
async def test_run_rejects_manifest_as_output_before_starting_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_started = False

    def start_postgres() -> RecordingPostgresContainer:
        nonlocal postgres_started
        postgres_started = True
        return RecordingPostgresContainer()

    monkeypatch.setattr(read_load_bench, "start_postgres", start_postgres)

    with pytest.raises(ValueError, match="must not be manifest.json"):
        await read_load_bench.run(
            benchmark_args(
                tmp_path / "manifest-output",
                output=str(tmp_path / "published" / "manifest.json"),
            )
        )

    assert not postgres_started


@pytest.mark.asyncio
async def test_run_rejects_existing_output_without_truncate_before_starting_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_started = False
    output_path = tmp_path / "published" / "results.jsonl"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("existing run\n", encoding="utf-8")

    def start_postgres() -> RecordingPostgresContainer:
        nonlocal postgres_started
        postgres_started = True
        return RecordingPostgresContainer()

    monkeypatch.setattr(read_load_bench, "start_postgres", start_postgres)

    with pytest.raises(ValueError, match="--output already exists"):
        await read_load_bench.run(
            benchmark_args(tmp_path / "existing-output", output=str(output_path))
        )

    assert output_path.read_text(encoding="utf-8") == "existing run\n"
    assert not postgres_started


@pytest.mark.asyncio
async def test_run_rejects_second_output_in_manifest_directory_before_starting_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_started = False
    output_dir = tmp_path / "published"
    output_dir.mkdir(parents=True)
    first_output = output_dir / "first.jsonl"
    first_output.write_text("first run\n", encoding="utf-8")
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text('{"run_id": "first"}\n', encoding="utf-8")
    second_output = output_dir / "second.jsonl"

    def start_postgres() -> RecordingPostgresContainer:
        nonlocal postgres_started
        postgres_started = True
        return RecordingPostgresContainer()

    monkeypatch.setattr(read_load_bench, "start_postgres", start_postgres)

    with pytest.raises(ValueError, match="manifest.json for another run"):
        await read_load_bench.run(
            benchmark_args(tmp_path / "second-output", output=str(second_output))
        )

    assert first_output.read_text(encoding="utf-8") == "first run\n"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["run_id"] == "first"
    assert not second_output.exists()
    assert not postgres_started
