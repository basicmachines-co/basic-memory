"""Read-path load benchmark for Basic Memory.

Drives direct-permalink `read_note` calls over a warm `bm mcp` stdio session
and measures caller-perceived latency, throughput, response bandwidth, and
content correctness across note sizes and concurrency levels.

The benchmark compares installed Basic Memory builds without importing their
internals. Point `--bm-command` at a per-ref virtual environment; the harness,
corpus, request order, and MCP contract remain fixed.

Output is one JSONL record per size/concurrency pair in the generic benchmark
format: `{"benchmark", "metrics", "timestamp_utc"}`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

_CORPUS_SEARCH_TOKEN = "readloadbenchmarkmarker"


@dataclass(frozen=True, slots=True)
class ReadTarget:
    """One materialized note and the marker that proves a correct response."""

    identifier: str
    marker: str
    requested_content_bytes: int


class PostgresContainer(Protocol):
    """Narrow testcontainer lifecycle used by the optional Postgres backend."""

    def start(self) -> object: ...

    def stop(self) -> None: ...

    def get_connection_url(self) -> str: ...


# --- Synthetic corpus ------------------------------------------------------


def size_label(size_bytes: int) -> str:
    """Return a stable filesystem-safe label for one payload size."""
    if size_bytes % 1024 == 0:
        return f"{size_bytes // 1024}kib"
    return f"{size_bytes}b"


def synthetic_read_note(size_bytes: int, index: int) -> tuple[str, str, str]:
    """Build deterministic ASCII Markdown of exactly ``size_bytes`` bytes."""
    label = size_label(size_bytes)
    title = f"read-load-{label}-note-{index:05d}"
    marker = f"read-load-marker-{label}-{index:05d}"
    prefix = (
        f"# {title}\n\n"
        f"{_CORPUS_SEARCH_TOKEN} {marker}\n\n"
        "This note exercises direct content reads through the public MCP read_note tool.\n\n"
    )
    if len(prefix) > size_bytes:
        raise ValueError(f"size {size_bytes} is too small for benchmark metadata")
    filler = "database content read benchmark payload "
    repeats = ((size_bytes - len(prefix)) // len(filler)) + 1
    content = (prefix + filler * repeats)[:size_bytes]
    assert len(content.encode("utf-8")) == size_bytes
    return title, marker, content


# --- Metric and result helpers --------------------------------------------


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def emit(output_path: Path | None, record: dict[str, object]) -> None:
    line = json.dumps(record)
    print(line, flush=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def result_payload(result: CallToolResult) -> dict[str, object]:
    structured = result.structuredContent
    if isinstance(structured, dict):
        wrapped = structured.get("result")
        payload = wrapped if isinstance(wrapped, dict) else structured
        return {str(key): value for key, value in payload.items()}
    for item in result.content:
        text = getattr(item, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
    return {}


def result_text(result: CallToolResult) -> str | None:
    """Return text from a successful text-mode tool response."""
    if result.isError:
        return None
    text_parts = [
        text for item in result.content if isinstance((text := getattr(item, "text", None)), str)
    ]
    return "\n".join(text_parts) if text_parts else None


# --- Isolated runtime ------------------------------------------------------


def isolated_env(config_dir: Path, *, redis_url: str | None) -> dict[str, str]:
    """Create a local runtime environment without inherited BM configuration."""
    env = dict(os.environ)
    for key in (
        "BASIC_MEMORY_ENV",
        "BASIC_MEMORY_CLOUD_MODE",
        "BASIC_MEMORY_DATABASE_BACKEND",
        "BASIC_MEMORY_DATABASE_URL",
        "BASIC_MEMORY_HOME",
        "BASIC_MEMORY_REDIS_URL",
    ):
        env.pop(key, None)
    env["BASIC_MEMORY_CONFIG_DIR"] = str(config_dir)
    env["BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED"] = "false"
    env["BASIC_MEMORY_LOG_LEVEL"] = "WARNING"
    env["LOGFIRE_IGNORE_NO_CONFIG"] = "1"
    if redis_url is not None:
        env["BASIC_MEMORY_REDIS_URL"] = redis_url
    return env


def start_postgres() -> PostgresContainer:
    """Start a throwaway pgvector testcontainer for a Postgres run."""
    from testcontainers.postgres import PostgresContainer as TestPostgresContainer

    container = TestPostgresContainer("pgvector/pgvector:pg16")
    container.start()
    return container


def asyncpg_url(container: PostgresContainer) -> str:
    """Normalize a testcontainer URL to SQLAlchemy's asyncpg scheme."""
    url = container.get_connection_url()
    for sync_driver in ("postgresql+psycopg2", "postgresql+psycopg", "postgresql"):
        prefix = sync_driver + "://"
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def note_content_rows(db_path: Path) -> int:
    """Count SQLite note_content rows; older releases may not have the table."""
    if not db_path.exists():
        return 0
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'note_content'"
            ).fetchone()
            if table is None:
                return 0
            row = connection.execute("SELECT COUNT(*) FROM note_content").fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            connection.close()
    except sqlite3.Error:
        return 0


async def poll_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    timeout_seconds: float,
    interval_seconds: float = 0.1,
) -> float | None:
    """Return elapsed milliseconds when an async predicate becomes true."""
    started = time.perf_counter()
    delay = interval_seconds
    while True:
        if await predicate():
            return (time.perf_counter() - started) * 1000.0
        if time.perf_counter() - started >= timeout_seconds:
            return None
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 1.0)


# --- Corpus preparation ----------------------------------------------------


async def write_target(
    session: ClientSession,
    *,
    project: str,
    size_bytes: int,
    index: int,
) -> ReadTarget:
    title, marker, content = synthetic_read_note(size_bytes, index)
    directory = f"read-load/{size_label(size_bytes)}"
    result = await session.call_tool(
        "write_note",
        {
            "project": project,
            "title": title,
            "directory": directory,
            "content": content,
            "output_format": "json",
        },
    )
    if result.isError:
        raise RuntimeError(f"write_note failed while seeding {title}")
    payload = result_payload(result)
    identifier = payload.get("permalink")
    if not isinstance(identifier, str) or not identifier:
        identifier = f"{directory}/{title}"
    return ReadTarget(
        identifier=identifier,
        marker=marker,
        requested_content_bytes=size_bytes,
    )


async def seed_corpus(
    session: ClientSession,
    *,
    project: str,
    sizes: list[int],
    notes_per_size: int,
    concurrency: int,
) -> list[ReadTarget]:
    """Create the deterministic corpus with bounded concurrent writes."""
    semaphore = asyncio.Semaphore(concurrency)

    async def write_one(size_bytes: int, index: int) -> ReadTarget:
        async with semaphore:
            return await write_target(
                session,
                project=project,
                size_bytes=size_bytes,
                index=index,
            )

    return list(
        await asyncio.gather(
            *(
                write_one(size_bytes, index)
                for size_bytes in sizes
                for index in range(notes_per_size)
            )
        )
    )


async def searchable_count(session: ClientSession, project: str) -> int:
    result = await session.call_tool(
        "search_notes",
        {
            "project": project,
            "query": _CORPUS_SEARCH_TOKEN,
            "search_type": "text",
            "page": 1,
            "page_size": 1,
            "output_format": "json",
        },
    )
    if result.isError:
        return 0
    payload = result_payload(result)
    total = payload.get("total")
    if isinstance(total, int):
        return total
    results = payload.get("results")
    return len(results) if isinstance(results, list) else 0


async def warm_targets(session: ClientSession, project: str, targets: list[ReadTarget]) -> None:
    """Validate each direct permalink once and warm database/filesystem caches."""
    for target in targets:
        result = await session.call_tool(
            "read_note",
            {"project": project, "identifier": target.identifier},
        )
        content = result_text(result)
        if content is None or target.marker not in content:
            raise RuntimeError(f"warm read failed validation for {target.identifier}")


# --- Measured workload -----------------------------------------------------


async def read_burst(
    session: ClientSession,
    *,
    project: str,
    targets: list[ReadTarget],
    count: int,
    concurrency: int,
    seed: int,
) -> tuple[list[float], int, int, int]:
    """Read deterministic targets with at most ``concurrency`` calls in flight."""
    request_targets = [targets[index % len(targets)] for index in range(count)]
    random.Random(seed).shuffle(request_targets)
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0
    validation_failures = 0
    response_bytes = 0

    async def read_one(target: ReadTarget) -> None:
        nonlocal errors, response_bytes, validation_failures
        async with semaphore:
            started = time.perf_counter()
            try:
                result = await session.call_tool(
                    "read_note",
                    {"project": project, "identifier": target.identifier},
                )
            # Each request is an observation. Transport/protocol failures count as benchmark
            # errors so the remaining deterministic burst can complete and report its rate.
            except Exception:  # noqa: BLE001
                errors += 1
                latencies.append((time.perf_counter() - started) * 1000.0)
                return
            latencies.append((time.perf_counter() - started) * 1000.0)
            content = result_text(result)
            if content is None:
                errors += 1
                return
            response_bytes += len(content.encode("utf-8"))
            if target.marker not in content:
                validation_failures += 1

    await asyncio.gather(*(read_one(target) for target in request_targets))
    return latencies, errors, validation_failures, response_bytes


async def run(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch).resolve()
    config_dir = scratch / "config"
    project_dir = scratch / "project"
    main_home = scratch / "main-home"
    for path in (config_dir, project_dir, main_home):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

    pg_container = (
        start_postgres() if args.backend == "postgres" and not args.database_url else None
    )
    database_url = args.database_url or (asyncpg_url(pg_container) if pg_container else None)
    output_path = Path(args.output).resolve() if args.output else None
    if output_path is not None and output_path.exists() and args.truncate:
        output_path.unlink()

    sizes = [int(value) for value in args.sizes.split(",") if value.strip()]
    concurrency_levels = [int(value) for value in args.concurrency.split(",") if value.strip()]
    if not sizes or min(sizes) <= 0:
        raise ValueError("--sizes must contain positive byte counts")
    if not concurrency_levels or min(concurrency_levels) <= 0:
        raise ValueError("--concurrency must contain positive integers")
    redis_url = args.redis_url.strip() if args.redis_url else None

    project = "readload"
    env = isolated_env(config_dir, redis_url=redis_url)
    env["BASIC_MEMORY_HOME"] = str(main_home)
    if args.backend == "postgres":
        if database_url is None:
            raise ValueError("Postgres backend requires a database URL")
        env["BASIC_MEMORY_DATABASE_BACKEND"] = "postgres"
        env["BASIC_MEMORY_DATABASE_URL"] = database_url
    params = StdioServerParameters(command=args.bm_command, args=["mcp"], env=env)
    had_failures = False

    try:
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            created = await session.call_tool(
                "create_memory_project",
                {"project_name": project, "project_path": str(project_dir)},
            )
            if created.isError:
                raise RuntimeError("could not create benchmark project")

            targets = await seed_corpus(
                session,
                project=project,
                sizes=sizes,
                notes_per_size=args.notes_per_size,
                concurrency=args.seed_concurrency,
            )
            expected_notes = len(targets)

            async def files_ready() -> bool:
                return len(list(project_dir.rglob("*.md"))) >= expected_notes

            async def search_ready() -> bool:
                return await searchable_count(session, project) >= expected_notes

            materialized_ms = await poll_until(
                files_ready,
                timeout_seconds=args.ready_timeout,
            )
            searchable_ms = await poll_until(
                search_ready,
                timeout_seconds=args.ready_timeout,
            )
            if materialized_ms is None or searchable_ms is None:
                raise RuntimeError(
                    "corpus did not finish materializing and indexing before measurement"
                )

            await asyncio.sleep(args.quiesce_seconds)
            await warm_targets(session, project, targets)

            sqlite_note_content_rows = (
                note_content_rows(config_dir / "memory.db") if args.backend == "sqlite" else -1
            )

            for size_bytes in sizes:
                size_targets = [
                    target for target in targets if target.requested_content_bytes == size_bytes
                ]
                for concurrency in concurrency_levels:
                    wall_started = time.perf_counter()
                    latencies, errors, validation_failures, response_bytes = await read_burst(
                        session,
                        project=project,
                        targets=size_targets,
                        count=args.reads,
                        concurrency=concurrency,
                        seed=args.seed + size_bytes + concurrency,
                    )
                    wall_seconds = time.perf_counter() - wall_started
                    successful_reads = args.reads - errors
                    throughput = args.reads / wall_seconds if wall_seconds > 0 else 0.0
                    mib_per_second = (
                        response_bytes / (1024 * 1024) / wall_seconds if wall_seconds > 0 else 0.0
                    )
                    had_failures = had_failures or errors > 0 or validation_failures > 0
                    record: dict[str, object] = {
                        "benchmark": (f"read-load size={size_label(size_bytes)} c={concurrency}"),
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                        "label": args.label,
                        "metadata": {
                            "backend": args.backend,
                            "redis_read_cache_enabled": redis_url is not None,
                            "semantic_search_enabled": False,
                            "warm_direct_permalink_reads": True,
                            "corpus_notes": expected_notes,
                            "notes_per_size": args.notes_per_size,
                            "materialization_ready_ms": round(materialized_ms, 1),
                            "search_ready_ms": round(searchable_ms, 1),
                            "sqlite_note_content_rows": sqlite_note_content_rows,
                        },
                        "metrics": {
                            "concurrency": concurrency,
                            "requested_content_bytes": size_bytes,
                            "reads_requested": args.reads,
                            "successful_reads": successful_reads,
                            "read_latency_p50_ms": round(percentile(latencies, 50), 3),
                            "read_latency_p95_ms": round(percentile(latencies, 95), 3),
                            "read_latency_p99_ms": round(percentile(latencies, 99), 3),
                            "read_latency_max_ms": (round(max(latencies), 3) if latencies else 0.0),
                            "read_throughput_per_sec": round(throughput, 3),
                            "response_mib_per_sec": round(mib_per_second, 3),
                            "average_response_bytes": (
                                round(response_bytes / successful_reads, 1)
                                if successful_reads > 0
                                else 0.0
                            ),
                            "read_error_rate": round(errors / args.reads, 4),
                            "validation_failure_rate": round(validation_failures / args.reads, 4),
                        },
                    }
                    emit(output_path, record)
    finally:
        if pg_container is not None:
            pg_container.stop()

    return 2 if had_failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bm-command",
        required=True,
        help="Path to the basic-memory executable in the per-ref virtual environment",
    )
    parser.add_argument("--label", default="ref", help="Ref label recorded in each row")
    parser.add_argument(
        "--sizes",
        default="1024,16384,65536",
        help="Comma-separated generated Markdown sizes in bytes",
    )
    parser.add_argument(
        "--notes-per-size",
        type=int,
        default=32,
        help="Distinct corpus notes generated for each size",
    )
    parser.add_argument("--reads", type=int, default=128, help="Reads per size/concurrency row")
    parser.add_argument(
        "--concurrency",
        default="1,8,32,64",
        help="Comma-separated in-flight read limits",
    )
    parser.add_argument(
        "--seed-concurrency",
        type=int,
        default=8,
        help="Bounded write concurrency used only to prepare the corpus",
    )
    parser.add_argument("--seed", type=int, default=1021, help="Deterministic request-order seed")
    parser.add_argument(
        "--backend",
        choices=("sqlite", "postgres"),
        default="sqlite",
        help="Database backend used by the Basic Memory server",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Postgres URL; when omitted, a throwaway pgvector testcontainer is started",
    )
    parser.add_argument(
        "--redis-url",
        default=None,
        help="Enable standalone MCP read caching with this Redis URL or bare hostname",
    )
    parser.add_argument(
        "--scratch",
        default=".scratch/read-load",
        help="Scratch directory for isolated config and project files",
    )
    parser.add_argument("--output", default=None, help="JSONL output path (also printed)")
    parser.add_argument("--truncate", action="store_true", help="Truncate output before writing")
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for corpus materialization and FTS indexing",
    )
    parser.add_argument(
        "--quiesce-seconds",
        type=float,
        default=1.0,
        help="Quiet interval between readiness and unmeasured cache warmup",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
