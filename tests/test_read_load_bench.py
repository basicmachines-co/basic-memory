from __future__ import annotations

import argparse
import importlib.util
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


@pytest.mark.asyncio
async def test_run_stops_postgres_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RecordingPostgresContainer()
    monkeypatch.setattr(read_load_bench, "start_postgres", lambda: container)

    async def fail_redis_provenance(redis_url: str) -> str:
        raise RuntimeError(f"Redis unavailable: {redis_url}")

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
