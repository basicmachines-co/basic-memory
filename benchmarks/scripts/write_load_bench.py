#!/usr/bin/env python3
"""Write-path load benchmark for Basic Memory.

Drives `write_note` over a warm `bm mcp` stdio session at increasing
concurrency and measures caller-perceived accept latency, throughput, error
rate, and how long the background follow-up work takes to make the writes
visible (materialized on disk, then FTS-searchable).

The whole point is branch/SHA comparison: point `--bm-command` at a venv that
has a specific `basic-memory` ref installed; the harness is otherwise fixed.

Output: one JSONL record per concurrency level, in the generic compare format
(`{"benchmark", "metrics", "timestamp_utc"}`), so two runs diff with
`test-int/compare_search_benchmarks.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult


# --- Synthetic corpus ------------------------------------------------------

_TOPICS = [
    "coffee",
    "sync",
    "auth",
    "search",
    "indexing",
    "cloud",
    "markdown",
    "embeddings",
]


def synthetic_note(level: int, index: int) -> dict[str, str]:
    """A deterministic note with frontmatter, observations, and a relation.

    Relations reference sibling notes so the runtime's relation-resolution
    follow-up has real work to do (part of the async path we measure).
    """
    topic = _TOPICS[index % len(_TOPICS)]
    title = f"load-c{level}-note-{index:05d}"
    prev = f"load-c{level}-note-{(index - 1) % 1:05d}" if index else title
    content = (
        f"# {title}\n\n"
        f"Synthetic write-load note about {topic}. "
        f"It exists to exercise the accepted-note write path under concurrency.\n\n"
        "## Observations\n"
        f"- [fact] {topic} note number {index} in burst {level} #load\n"
        f"- [detail] repeated domain term {topic} {topic} {topic} for indexing #benchmark\n\n"
        "## Relations\n"
        f"- relates_to [[load-c{level}-note-{max(index - 1, 0):05d}]]\n"
    )
    _ = prev
    return {"title": title, "directory": f"load/c{level}", "content": content}


# --- Metric helpers --------------------------------------------------------


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def emit(output_path: Path | None, record: dict) -> None:
    line = json.dumps(record)
    print(line, flush=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


# --- MCP plumbing ----------------------------------------------------------


def _isolated_env(config_dir: Path) -> dict[str, str]:
    """Fresh runtime: scoped config/DB, and NOT test mode.

    BASIC_MEMORY_ENV must not be `test` (test mode disables the watcher and the
    background schedulers we are trying to measure). We pop it so an inherited
    value can't sneak the server into test mode.
    """
    env = dict(os.environ)
    for key in ("BASIC_MEMORY_ENV", "BASIC_MEMORY_CLOUD_MODE", "BASIC_MEMORY_HOME"):
        env.pop(key, None)
    env["BASIC_MEMORY_CONFIG_DIR"] = str(config_dir)
    return env


def _result_payload(result: CallToolResult) -> dict:
    structured = result.structuredContent
    if isinstance(structured, dict):
        wrapped = structured.get("result")
        return wrapped if isinstance(wrapped, dict) else structured
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


# --- Workload --------------------------------------------------------------


async def write_burst(
    session: ClientSession,
    *,
    project: str,
    level: int,
    count: int,
    concurrency: int,
) -> tuple[list[float], int]:
    """Fire `count` write_note calls with at most `concurrency` in flight."""
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0

    async def one(index: int) -> None:
        nonlocal errors
        note = synthetic_note(level, index)
        async with sem:
            start = time.perf_counter()
            try:
                result = await session.call_tool(
                    "write_note",
                    {
                        "project": project,
                        "title": note["title"],
                        "directory": note["directory"],
                        "content": note["content"],
                        "output_format": "json",
                    },
                )
                failed = bool(result.isError)
            except Exception:
                failed = True
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            latencies.append(elapsed_ms)
            if failed:
                errors += 1

    await asyncio.gather(*(one(index) for index in range(count)))
    return latencies, errors


async def poll_until(
    predicate, *, timeout_s: float, interval_s: float = 0.1
) -> float | None:
    """Return ms elapsed when predicate() is true, or None on timeout."""
    start = time.perf_counter()
    delay = interval_s
    while True:
        if await predicate():
            return (time.perf_counter() - start) * 1000.0
        if time.perf_counter() - start >= timeout_s:
            return None
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 1.0)


async def searchable_count(session: ClientSession, project: str, level: int) -> int:
    result = await session.call_tool(
        "search_notes",
        {
            "project": project,
            "query": f"burst {level}",
            "search_type": "text",
            "page": 1,
            "page_size": 1,
            "output_format": "json",
        },
    )
    payload = _result_payload(result)
    # FTS path reports an exact total; fall back to len(results).
    total = payload.get("total")
    if isinstance(total, int) and total > 0:
        return total
    results = payload.get("results")
    return len(results) if isinstance(results, list) else 0


async def run(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch).resolve()
    config_dir = scratch / "config"
    project_dir = scratch / "project"
    for path in (config_dir, project_dir):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

    env = _isolated_env(config_dir)
    params = StdioServerParameters(command=args.bm_command, args=["mcp"], env=env)
    output_path = Path(args.output).resolve() if args.output else None
    if output_path and output_path.exists() and args.truncate:
        output_path.unlink()

    levels = [int(c) for c in args.concurrency.split(",") if c.strip()]
    project = "writeload"

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            create = await session.call_tool(
                "create_memory_project",
                {"project_name": project, "project_path": str(project_dir)},
            )
            if create.isError:
                print("ERROR: could not create project", file=sys.stderr)
                return 1

            # Warmup: pay one-time costs (embedding-model download/load, index
            # init, cache warm) OUTSIDE measurement. Wait for the warmup writes
            # to become searchable so the model is fully loaded before level 1.
            await write_burst(
                session, project=project, level=0, count=args.warmup, concurrency=1
            )

            async def warmup_ready() -> bool:
                return await searchable_count(session, project, 0) >= args.warmup

            await poll_until(warmup_ready, timeout_s=args.drain_timeout)

            for level in levels:
                wall_start = time.perf_counter()
                latencies, errors = await write_burst(
                    session,
                    project=project,
                    level=level,
                    count=args.notes,
                    concurrency=level,
                )
                wall_s = time.perf_counter() - wall_start
                throughput = args.notes / wall_s if wall_s > 0 else 0.0

                level_dir = project_dir / "load" / f"c{level}"

                async def files_ready() -> bool:
                    return level_dir.exists() and len(list(level_dir.glob("*.md"))) >= args.notes

                async def search_ready() -> bool:
                    return await searchable_count(session, project, level) >= args.notes

                materialized_ms = await poll_until(files_ready, timeout_s=args.drain_timeout)
                searchable_ms = await poll_until(search_ready, timeout_s=args.drain_timeout)

                record = {
                    "benchmark": f"write-load c={level}",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "label": args.label,
                    "metrics": {
                        "concurrency": level,
                        "notes_written": args.notes,
                        "accept_latency_p50_ms": round(percentile(latencies, 50), 3),
                        "accept_latency_p95_ms": round(percentile(latencies, 95), 3),
                        "accept_latency_p99_ms": round(percentile(latencies, 99), 3),
                        "accept_latency_max_ms": round(max(latencies), 3) if latencies else 0.0,
                        "accept_throughput_per_sec": round(throughput, 3),
                        "accept_error_rate": round(errors / args.notes, 4),
                        "time_to_materialized_ms": round(materialized_ms, 1)
                        if materialized_ms is not None
                        else -1.0,
                        "time_to_searchable_ms": round(searchable_ms, 1)
                        if searchable_ms is not None
                        else -1.0,
                    },
                }
                emit(output_path, record)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bm-command",
        required=True,
        help="Path to the basic-memory executable in the per-ref venv",
    )
    parser.add_argument("--label", default="ref", help="Ref label recorded in each row")
    parser.add_argument("--notes", type=int, default=200, help="Writes per concurrency level")
    parser.add_argument("--warmup", type=int, default=5, help="Warmup writes (not measured)")
    parser.add_argument(
        "--concurrency", default="1,4,8,16,32", help="Comma-separated concurrency levels"
    )
    parser.add_argument(
        "--scratch", default=".scratch/write-load", help="Scratch dir for config + project"
    )
    parser.add_argument("--output", default=None, help="JSONL output path (also printed)")
    parser.add_argument("--truncate", action="store_true", help="Truncate output before writing")
    parser.add_argument(
        "--drain-timeout", type=float, default=120.0, help="Seconds to wait for visibility"
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
