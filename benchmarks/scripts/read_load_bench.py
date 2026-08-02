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
import hashlib
import json
import os
import platform
import random
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult
from sqlalchemy.ext.asyncio import create_async_engine

_CORPUS_SEARCH_TOKEN = "readloadbenchmarkmarker"


@dataclass(frozen=True, slots=True)
class ReadTarget:
    """One materialized note and the marker that proves a correct response."""

    identifier: str
    marker: str
    requested_content_bytes: int


@dataclass(frozen=True, slots=True)
class BasicMemoryRuntime:
    """Provenance from the Python interpreter that runs the target BM command."""

    module_path: Path
    python_version: str


class PostgresContainer(Protocol):
    """Narrow testcontainer lifecycle used by the optional Postgres backend."""

    def start(self) -> object: ...

    def stop(self) -> None: ...

    def get_connection_url(
        self,
        host: str | None = None,
        driver: str | None = None,
    ) -> str: ...


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


def checked_output(command: list[str], *, env: dict[str, str] | None = None) -> str:
    """Run a provenance command and require one non-empty output value."""
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"provenance command failed: {command[0]}") from error
    output = result.stdout.strip()
    if not output:
        raise RuntimeError(f"provenance command returned no output: {command[0]}")
    return output


def git_root(path: Path) -> Path:
    """Find the repository that owns a benchmark executable or source file."""
    start = path if path.is_dir() else path.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"could not resolve a Git repository for {path}")


def git_sha(repo_root: Path) -> str:
    return checked_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"])


def git_is_dirty(repo_root: Path) -> bool:
    """Record whether the resolved SHA has uncommitted differences."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("could not inspect benchmark Git state") from error
    return bool(result.stdout.strip())


def git_source(repo_root: Path) -> str:
    """Return a credential-free repository source identifier."""
    try:
        remote = checked_output(["git", "-C", str(repo_root), "remote", "get-url", "origin"])
    except RuntimeError:
        return f"local:{repo_root.name}"

    if remote.startswith("git@") and ":" in remote:
        host, path = remote.split("@", 1)[1].split(":", 1)
        return f"git:{host}/{path.removesuffix('.git')}"
    if "://" in remote:
        parsed = urlparse(remote)
        if parsed.hostname:
            return f"git:{parsed.hostname}{parsed.path.removesuffix('.git')}"
    return f"local:{repo_root.name}"


def basic_memory_runtime(command_path: Path, env: dict[str, str]) -> BasicMemoryRuntime:
    """Resolve module and Python provenance from the target BM environment."""
    python_command: list[str] | None = None
    for name in ("python", "python3", "python.exe"):
        interpreter = command_path.parent / name
        if interpreter.is_file():
            python_command = [str(interpreter)]
            break

    if python_command is None:
        try:
            with command_path.open(encoding="utf-8") as handle:
                first_line = handle.readline().strip()
        except (OSError, UnicodeDecodeError) as error:
            raise RuntimeError(
                f"could not resolve the Python environment for {command_path}"
            ) from error
        if not first_line.startswith("#!"):
            raise RuntimeError(f"Basic Memory command has no Python shebang: {command_path}")
        python_command = shlex.split(first_line.removeprefix("#!"))
        if not python_command:
            raise RuntimeError(f"Basic Memory command has an empty shebang: {command_path}")

    probe = (
        "import basic_memory, json, platform; "
        'print(json.dumps({"module_path": basic_memory.__file__, '
        '"python_version": platform.python_version()}))'
    )
    runtime_output = checked_output(
        [
            *python_command,
            "-c",
            probe,
        ],
        env=env,
    )
    try:
        runtime = json.loads(runtime_output)
    except json.JSONDecodeError as error:
        raise RuntimeError("Basic Memory returned invalid runtime provenance") from error
    if not isinstance(runtime, dict):
        raise TypeError("Basic Memory returned invalid runtime provenance")
    module_output = runtime.get("module_path")
    python_version = runtime.get("python_version")
    if not isinstance(module_output, str) or not isinstance(python_version, str):
        raise TypeError("Basic Memory returned incomplete runtime provenance")

    module_path = Path(module_output).resolve()
    if not module_path.is_file():
        raise RuntimeError(f"Basic Memory reported an invalid module path: {module_output}")
    return BasicMemoryRuntime(module_path=module_path, python_version=python_version)


def synthetic_corpus_checksum(*, sizes: list[int], notes_per_size: int) -> str:
    """Hash the exact deterministic corpus with unambiguous value boundaries."""
    digest = hashlib.sha256()
    for size_bytes in sizes:
        for index in range(notes_per_size):
            for value in synthetic_read_note(size_bytes, index):
                encoded = value.encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
    return digest.hexdigest()


async def redis_server_version(redis_url: str) -> str:
    """Read Redis server provenance without persisting its credential-bearing URL."""
    from redis.asyncio import Redis

    connection_url = redis_url if "://" in redis_url else f"redis://{redis_url}"
    client = Redis.from_url(connection_url, decode_responses=True)
    try:
        server = await client.info(section="server")
    finally:
        await client.aclose()
    version = server.get("redis_version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("Redis did not report its server version")
    return version


async def database_server_version(*, backend: str, database_url: str | None) -> str:
    """Read the selected database engine version without retaining its URL."""
    if backend == "sqlite":
        return sqlite3.sqlite_version
    if database_url is None:
        raise ValueError("Postgres provenance requires a database URL")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.exec_driver_sql("SHOW server_version")
            return str(result.scalar_one())
    finally:
        await engine.dispose()


def benchmark_manifest_path(*, scratch: Path, output_path: Path | None) -> Path:
    """Keep provenance beside retained results, or in scratch for stdout-only runs."""
    return (output_path.parent if output_path is not None else scratch) / "manifest.json"


def write_manifest(
    *,
    scratch: Path,
    output_path: Path | None,
    args: argparse.Namespace,
    env: dict[str, str],
    sizes: list[int],
    concurrency_levels: list[int],
    database_version: str,
    redis_version: str | None,
) -> None:
    """Write the provenance required to reproduce and interpret one run."""
    command_path = Path(shutil.which(args.bm_command) or "").resolve()
    if not command_path.is_file():
        raise RuntimeError(f"could not resolve Basic Memory command: {args.bm_command}")
    bm_runtime = basic_memory_runtime(command_path, env)
    bm_module_path = bm_runtime.module_path
    bm_repo_root = git_root(bm_module_path)
    package_root = (bm_repo_root / "src" / "basic_memory").resolve()
    if not bm_module_path.is_relative_to(package_root):
        raise RuntimeError(
            "installed Basic Memory is not linked to the resolved source checkout; "
            "use a per-ref editable environment"
        )
    benchmark_repo_root = git_root(Path(__file__).resolve())
    try:
        command_source = str(command_path.relative_to(bm_repo_root))
    except ValueError:
        command_source = command_path.name

    provider_versions = {
        "basic-memory": {
            "command": command_source,
            "module": str(bm_module_path.relative_to(bm_repo_root)),
            "python_version": bm_runtime.python_version,
            "version": checked_output([str(command_path), "--version"], env=env),
        }
    }
    if redis_version is not None:
        provider_versions["redis"] = {"version": redis_version}
    database_provider = "postgresql" if args.backend == "postgres" else "sqlite"
    provider_versions[database_provider] = {"version": database_version}

    manifest = {
        "schema_version": 1,
        "run_id": args.label,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "benchmark_source": git_source(benchmark_repo_root),
        "benchmark_git_sha": git_sha(benchmark_repo_root),
        "benchmark_git_dirty": git_is_dirty(benchmark_repo_root),
        "bm_source": git_source(bm_repo_root),
        "bm_resolved_sha": git_sha(bm_repo_root),
        "bm_git_dirty": git_is_dirty(bm_repo_root),
        "provider_versions": provider_versions,
        "dataset": {
            "source": "synthetic:read-load-v1",
            "checksum_sha256": synthetic_corpus_checksum(
                sizes=sizes,
                notes_per_size=args.notes_per_size,
            ),
            "sizes_bytes": sizes,
            "notes_per_size": args.notes_per_size,
        },
        "runtime": {
            "os": platform.platform(),
            "harness_python_version": sys.version.split()[0],
            "backend": args.backend,
            "redis_read_cache_enabled": redis_version is not None,
        },
        "config": {
            "reads_per_scenario": args.reads,
            "concurrency_levels": concurrency_levels,
            "seed_concurrency": args.seed_concurrency,
            "redis_max_connections": (
                int(env["BASIC_MEMORY_REDIS_MAX_CONNECTIONS"])
                if redis_version is not None
                else None
            ),
            "seed": args.seed,
            "ready_timeout_seconds": args.ready_timeout,
            "quiesce_seconds": args.quiesce_seconds,
        },
    }
    manifest_path = benchmark_manifest_path(scratch=scratch, output_path=output_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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


def isolated_env(
    config_dir: Path,
    *,
    redis_url: str | None,
    redis_max_connections: int | None,
) -> dict[str, str]:
    """Create a local runtime environment without inherited BM configuration."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("BASIC_MEMORY_")}
    env["BASIC_MEMORY_AUTO_UPDATE"] = "false"
    env["BASIC_MEMORY_CONFIG_DIR"] = str(config_dir)
    env["BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED"] = "false"
    env["BASIC_MEMORY_LOG_LEVEL"] = "WARNING"
    env["LOGFIRE_IGNORE_NO_CONFIG"] = "1"
    if redis_url is not None:
        if redis_max_connections is None or redis_max_connections <= 0:
            raise ValueError("Redis runs require a positive connection-pool size")
        env["BASIC_MEMORY_REDIS_URL"] = redis_url
        env["BASIC_MEMORY_REDIS_MAX_CONNECTIONS"] = str(redis_max_connections)
    return env


def start_postgres() -> PostgresContainer:
    """Start a throwaway pgvector testcontainer for a Postgres run."""
    from testcontainers.postgres import PostgresContainer as TestPostgresContainer

    container = TestPostgresContainer("pgvector/pgvector:pg16")
    try:
        container.start()
    except Exception:
        container.stop()
        raise
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
    sizes = [int(value) for value in args.sizes.split(",") if value.strip()]
    concurrency_levels = [int(value) for value in args.concurrency.split(",") if value.strip()]
    if not sizes or min(sizes) <= 0:
        raise ValueError("--sizes must contain positive byte counts")
    if len(sizes) != len(set(sizes)):
        raise ValueError("--sizes must not contain duplicates")
    if not concurrency_levels or min(concurrency_levels) <= 0:
        raise ValueError("--concurrency must contain positive integers")
    if len(concurrency_levels) != len(set(concurrency_levels)):
        raise ValueError("--concurrency must not contain duplicates")
    for option, value in (
        ("--notes-per-size", args.notes_per_size),
        ("--reads", args.reads),
        ("--seed-concurrency", args.seed_concurrency),
    ):
        if value <= 0:
            raise ValueError(f"{option} must be a positive integer")

    scratch = Path(args.scratch).resolve()
    output_path = Path(args.output).resolve() if args.output else None
    manifest_path = benchmark_manifest_path(scratch=scratch, output_path=output_path)
    config_dir = (scratch / "config").resolve()
    project_dir = (scratch / "project").resolve()
    main_home = (scratch / "main-home").resolve()
    runtime_dirs = (config_dir, project_dir, main_home)
    if output_path == manifest_path:
        raise ValueError("--output filename must not be manifest.json")
    if output_path is not None and any(
        artifact_path.is_relative_to(runtime_dir)
        for artifact_path in (output_path, manifest_path)
        for runtime_dir in runtime_dirs
    ):
        raise ValueError("--output and manifest must stay outside benchmark runtime directories")
    if output_path is not None and output_path.exists() and not args.truncate:
        raise ValueError("--output already exists; pass --truncate to replace its run artifacts")
    if output_path is not None and manifest_path.exists() and not output_path.exists():
        raise ValueError("--output directory already contains manifest.json for another run")
    if output_path is not None and args.truncate:
        output_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)

    for path in (config_dir, project_dir, main_home):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

    pg_container: PostgresContainer | None = None
    try:
        pg_container = (
            start_postgres() if args.backend == "postgres" and not args.database_url else None
        )
        database_url = args.database_url or (asyncpg_url(pg_container) if pg_container else None)

        redis_url = args.redis_url.strip() if args.redis_url else None
        redis_max_connections = (
            max(max(concurrency_levels), args.seed_concurrency) if redis_url is not None else None
        )
        project = "readload"
        env = isolated_env(
            config_dir,
            redis_url=redis_url,
            redis_max_connections=redis_max_connections,
        )
        env["BASIC_MEMORY_HOME"] = str(main_home)
        if args.backend == "postgres":
            if database_url is None:
                raise ValueError("Postgres backend requires a database URL")
            env["BASIC_MEMORY_DATABASE_BACKEND"] = "postgres"
            env["BASIC_MEMORY_DATABASE_URL"] = database_url

        database_version = await database_server_version(
            backend=args.backend,
            database_url=database_url,
        )
        redis_version = await redis_server_version(redis_url) if redis_url is not None else None
        write_manifest(
            scratch=scratch,
            output_path=output_path,
            args=args,
            env=env,
            sizes=sizes,
            concurrency_levels=concurrency_levels,
            database_version=database_version,
            redis_version=redis_version,
        )
        params = StdioServerParameters(command=args.bm_command, args=["mcp"], env=env)
        had_failures = False

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

            sqlite_note_content_rows = (
                note_content_rows(config_dir / "memory.db") if args.backend == "sqlite" else -1
            )

            for size_bytes in sizes:
                size_targets = [
                    target for target in targets if target.requested_content_bytes == size_bytes
                ]
                for concurrency in concurrency_levels:
                    # Response entries expire after 60 seconds and reads do not extend their TTL.
                    # Rewarming outside the timer keeps every row on the advertised cache state.
                    await warm_targets(session, project, size_targets)
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
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Replace an existing output and its manifest before writing",
    )
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
