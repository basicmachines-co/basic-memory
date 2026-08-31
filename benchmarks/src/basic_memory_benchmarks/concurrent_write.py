"""Concurrent-writer benchmark driver (basicmachines-co/basic-memory#1248, axes 1 and 4).

Measures correctness-under-concurrency: N independent MCP client sessions (one
`bm mcp` subprocess each — the basic-memory#1214 field-report shape of several
agents writing at once) create and edit notes in ONE shared Basic Memory
project, with overlapping relation targets and shared "hub" notes that every
writer appends to. The driver captures per-op latency and errors, waits for the
index to settle, then verifies the project converged:

- markdown file count == hub notes + successful creates
- entity rows in the index DB == markdown files on disk
- no duplicate permalinks
- no duplicate (entity, category, content) observation tuples (the #1214 metric)
- no duplicate (from, to, type) relation tuples
- every observation line written by a reported-success op is present exactly
  once on disk (each generated line carries a unique ``bmk-*`` marker, so lost
  appends and doubled appends are both detectable)

v0.22.1 is expected to fail under load (relation-table deadlocks #1213,
duplicate observations #1214); v0.23's generation fences must keep every check
green. Throughput and latency are secondary outputs, reported per op type.
Integrity checks read the run's isolated SQLite database directly; Postgres
integrity is a follow-up.
"""

from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from mcp.types import CallToolResult
from pydantic import BaseModel, Field
from rich.console import Console

from basic_memory_benchmarks.bm_runtime import (
    WarmMcpClient,
    isolated_bm_env,
    resolve_bm_command_prefix,
    settle_index,
)
from basic_memory_benchmarks.models import RuntimeInfo
from basic_memory_benchmarks.utils import git_sha, run_command, runtime_info, utc_now_iso

console = Console()

OpType = Literal["create_hub", "create", "edit_hub", "edit_own"]

MARKER_PATTERN = re.compile(r"bmk-[a-z0-9-]+")

# --- Configuration and artifact models ---


class ConcurrentWriteConfig(BaseModel):
    run_id: str
    writers: int = Field(default=4, ge=1)
    notes_per_writer: int = Field(default=25, ge=1)
    # Probability (per created note, evaluated twice) that a writer also
    # appends to a shared hub note / one of its own earlier notes.
    edit_ratio: float = Field(default=0.4, ge=0.0, le=1.0)
    hub_notes: int = Field(default=4, ge=0)
    relation_pool: int = Field(default=8, ge=1)
    seed: int = 42
    output_root: str = "benchmarks/runs"
    bm_source: str = "local-checkout"
    bm_local_path: str
    # Optional wall-clock cap: writers stop scheduling new ops once exceeded.
    max_seconds: float | None = Field(default=None, gt=0.0)
    op_timeout_seconds: float = Field(default=120.0, gt=0.0)
    settle_timeout_seconds: float = Field(default=180.0, gt=0.0)
    measure_reindex: bool = True

    @property
    def project_name(self) -> str:
        return f"bm-write-{self.run_id}"


class OpResult(BaseModel):
    writer: int
    op_index: int
    op_type: OpType
    identifier: str
    started_at_utc: str
    latency_ms: float
    ok: bool
    error: str | None = None
    error_kind: str | None = None
    markers: list[str] = Field(default_factory=list)


class OpTypeStats(BaseModel):
    count: int
    ok: int
    errors: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float


class IntegrityCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class IntegrityReport(BaseModel):
    checks: list[IntegrityCheck]
    markdown_files: int
    entity_rows: int
    observation_rows: int
    distinct_observation_tuples: int
    duplicate_observation_tuples: int
    observation_redundancy_pct: float
    duplicate_permalinks: int
    duplicate_relation_tuples: int
    expected_markers: int
    found_markers: int
    missing_markers: int
    duplicated_markers: int
    missing_marker_sample: list[str] = Field(default_factory=list)
    duplicate_permalink_sample: list[str] = Field(default_factory=list)
    duplicate_observation_sample: list[str] = Field(default_factory=list)
    converged: bool


class ConcurrentWriteSummary(BaseModel):
    run_id: str
    concurrent_wall_seconds: float
    settle_seconds: float
    settle_mode: Literal["status-json", "fixed-delay"]
    reindex_seconds: float | None = None
    ops_total: int
    ops_ok: int
    ops_error: int
    ops_not_attempted: int
    terminal_writer_failures: int
    error_kinds: dict[str, int] = Field(default_factory=dict)
    per_op_type: dict[str, OpTypeStats] = Field(default_factory=dict)
    notes_created_ok: int
    creates_per_minute: float
    ops_per_second: float
    integrity: IntegrityReport
    converged: bool


class ConcurrentWriteManifest(BaseModel):
    run_id: str
    created_at_utc: str
    benchmark_git_sha: str
    bm_source: str
    bm_resolved_sha: str
    bm_local_path: str
    bm_version: str | None = None
    home_dir: str
    project_dir: str
    project_name: str
    runtime: RuntimeInfo
    config: ConcurrentWriteConfig


# --- Workload planning (pure, deterministic) ---


@dataclass(frozen=True)
class PlannedOp:
    writer: int
    op_index: int
    op_type: OpType
    # For creates: the note title; edits target `identifier` instead.
    title: str
    identifier: str
    directory: str
    content: str
    markers: tuple[str, ...]


def _observation_line(category: str, text: str, marker: str, tag: str | None = None) -> str:
    suffix = f" #{tag}" if tag else ""
    return f"- [{category}] {text} {marker}{suffix}"


def build_hub_ops(config: ConcurrentWriteConfig) -> list[PlannedOp]:
    """Sequential setup ops: shared hub notes every writer will append to."""
    ops: list[PlannedOp] = []
    for hub in range(config.hub_notes):
        title = f"hub-{hub}"
        marker = f"bmk-setup-h{hub:02d}-l0"
        topic = hub % config.relation_pool
        content = "\n".join(
            [
                "## Observations",
                _observation_line(
                    "hub", f"shared hub {hub} seeded before concurrent phase", marker, "bench"
                ),
                "",
                "## Relations",
                f"- relates_to [[topic-{topic}]]",
            ]
        )
        ops.append(
            PlannedOp(
                writer=-1,
                op_index=hub,
                op_type="create_hub",
                title=title,
                identifier=f"hubs/{title}",
                directory="hubs",
                content=content,
                markers=(marker,),
            )
        )
    return ops


def build_writer_plan(writer: int, config: ConcurrentWriteConfig) -> list[PlannedOp]:
    """Deterministic op schedule for one writer.

    Every observation line carries a unique ``bmk-w<writer>-o<op>-l<line>``
    marker, so post-run file scans can prove each reported-success write
    survived exactly once. Relation targets are drawn from small shared pools
    (topics and hubs) to create the overlapping-entity contention that
    triggered #1213/#1214.
    """
    rng = random.Random(config.seed * 7919 + writer)
    ops: list[PlannedOp] = []
    op_index = 0
    for note_index in range(config.notes_per_writer):
        title = f"w{writer:02d}-n{note_index:04d}"
        markers = tuple(f"bmk-w{writer:02d}-o{op_index:04d}-l{line}" for line in range(3))
        topic_a = rng.randrange(config.relation_pool)
        topic_b = rng.randrange(config.relation_pool)
        lines = [
            "## Observations",
            _observation_line(
                "fact", f"writer {writer} note {note_index} primary fact", markers[0], "bench"
            ),
            _observation_line(
                "detail", f"writer {writer} note {note_index} supporting detail", markers[1]
            ),
            _observation_line(
                "status", f"writer {writer} note {note_index} status entry", markers[2]
            ),
            "",
            "## Relations",
            f"- relates_to [[topic-{topic_a}]]",
            f"- part_of [[topic-{topic_b}]]",
        ]
        if config.hub_notes > 0:
            hub = rng.randrange(config.hub_notes)
            lines.append(f"- references [[hub-{hub}]]")
        ops.append(
            PlannedOp(
                writer=writer,
                op_index=op_index,
                op_type="create",
                title=title,
                identifier=f"notes/{title}",
                directory="notes",
                content="\n".join(lines),
                markers=markers,
            )
        )
        op_index += 1

        if config.hub_notes > 0 and rng.random() < config.edit_ratio:
            hub = rng.randrange(config.hub_notes)
            marker = f"bmk-w{writer:02d}-o{op_index:04d}-l0"
            ops.append(
                PlannedOp(
                    writer=writer,
                    op_index=op_index,
                    op_type="edit_hub",
                    title="",
                    identifier=f"hubs/hub-{hub}",
                    directory="hubs",
                    content="\n"
                    + _observation_line("update", f"writer {writer} appended to hub {hub}", marker),
                    markers=(marker,),
                )
            )
            op_index += 1

        if rng.random() < config.edit_ratio:
            target = rng.randrange(note_index + 1)
            marker = f"bmk-w{writer:02d}-o{op_index:04d}-l0"
            ops.append(
                PlannedOp(
                    writer=writer,
                    op_index=op_index,
                    op_type="edit_own",
                    title="",
                    identifier=f"notes/w{writer:02d}-n{target:04d}",
                    directory="notes",
                    content="\n"
                    + _observation_line(
                        "update", f"writer {writer} revisited note {target}", marker
                    ),
                    markers=(marker,),
                )
            )
            op_index += 1
    return ops


# --- Execution ---


def classify_error(text: str) -> str:
    lowered = text.lower()
    if "deadlock" in lowered:
        return "deadlock"
    if "database is locked" in lowered or "database table is locked" in lowered:
        return "sqlite_locked"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "conflict" in lowered or "version" in lowered or "stale" in lowered:
        return "write_conflict"
    return "other"


@dataclass
class WriterOutcome:
    results: list[OpResult] = field(default_factory=list)
    terminal_error: str | None = None
    not_attempted: int = 0


def _tool_call_for(op: PlannedOp, project_name: str) -> tuple[str, dict[str, Any]]:
    if op.op_type in ("create", "create_hub"):
        return "write_note", {
            "title": op.title,
            "directory": op.directory,
            "content": op.content,
            "project": project_name,
            "output_format": "json",
        }
    return "edit_note", {
        "identifier": op.identifier,
        "operation": "append",
        "content": op.content,
        "project": project_name,
        "output_format": "json",
    }


def _tool_result_payload(result: CallToolResult) -> dict[str, Any]:
    structured = result.structuredContent
    if isinstance(structured, dict):
        wrapped = structured.get("result")
        if isinstance(wrapped, dict):
            return wrapped
        return structured

    for item in result.content:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def tool_result_error(result: CallToolResult) -> str | None:
    """Return the MCP or tool-level error, including JSON responses with HTTP failures."""
    if result.isError:
        for item in result.content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return "Unknown MCP tool error"

    payload = _tool_result_payload(result)
    if not payload:
        return "MCP tool returned no JSON payload"
    error = payload.get("error")
    return str(error) if error else None


def _execute_op(client: WarmMcpClient, op: PlannedOp, project_name: str) -> tuple[OpResult, bool]:
    """Run one op; returns (result, terminal) — terminal means the session is unusable.

    A timed-out call leaves the request in flight on the single-slot session,
    so timeouts are terminal; MCP-level tool errors are recorded and the
    session keeps going (those errors ARE the measurement on v0.22.1).
    """
    tool, arguments = _tool_call_for(op, project_name)
    started_at = utc_now_iso()
    start = time.perf_counter()
    try:
        result = client.call_tool(tool, arguments)
    except TimeoutError:
        latency_ms = (time.perf_counter() - start) * 1000
        error = f"tool call timed out after {latency_ms / 1000:.0f}s"
        return (
            _op_result(op, started_at, latency_ms, ok=False, error=error),
            True,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        session_dead = isinstance(exc, RuntimeError) and "not running" in str(exc)
        return (
            _op_result(op, started_at, latency_ms, ok=False, error=f"{type(exc).__name__}: {exc}"),
            session_dead,
        )
    latency_ms = (time.perf_counter() - start) * 1000
    error = tool_result_error(result)
    if error is not None:
        return _op_result(op, started_at, latency_ms, ok=False, error=error), False
    return _op_result(op, started_at, latency_ms, ok=True, error=None), False


def _op_result(
    op: PlannedOp, started_at: str, latency_ms: float, *, ok: bool, error: str | None
) -> OpResult:
    return OpResult(
        writer=op.writer,
        op_index=op.op_index,
        op_type=op.op_type,
        identifier=op.identifier,
        started_at_utc=started_at,
        latency_ms=round(latency_ms, 2),
        ok=ok,
        error=error,
        error_kind=classify_error(error) if error else None,
        markers=list(op.markers),
    )


def _run_writer(
    *,
    client: WarmMcpClient,
    plan: list[PlannedOp],
    project_name: str,
    barrier: threading.Barrier,
    deadline: float | None,
    outcome: WriterOutcome,
) -> None:
    barrier.wait()
    for position, op in enumerate(plan):
        if deadline is not None and time.monotonic() >= deadline:
            outcome.not_attempted = len(plan) - position
            return
        result, terminal = _execute_op(client, op, project_name)
        outcome.results.append(result)
        if terminal:
            outcome.terminal_error = result.error
            outcome.not_attempted = len(plan) - position - 1
            return


# --- Integrity verification ---


@dataclass(frozen=True)
class ExpectedState:
    """What the project must contain if every reported-success op converged."""

    markers: frozenset[str]
    ok_creates: int
    hub_count: int


def _scan_markdown(project_dir: Path) -> tuple[int, Counter[str]]:
    files = sorted(project_dir.rglob("*.md"))
    markers: Counter[str] = Counter()
    for path in files:
        markers.update(MARKER_PATTERN.findall(path.read_text(encoding="utf-8")))
    return len(files), markers


def run_integrity_checks(
    *,
    db_path: Path,
    project_name: str,
    project_dir: Path,
    expected: ExpectedState,
) -> IntegrityReport:
    """Convergence verification against the on-disk files and the index DB.

    Fails loudly (raises) when the environment itself is broken — missing DB
    file or missing project row — because that is a driver bug, not a
    benchmark outcome. Product-level divergence is recorded in the report.
    """
    if not db_path.exists():
        raise RuntimeError(f"Index database not found: {db_path}")

    md_files, found_markers = _scan_markdown(project_dir)
    missing = sorted(expected.markers - set(found_markers))
    duplicated = sorted(marker for marker, count in found_markers.items() if count > 1)

    # Plain (not mode=ro) connection: the BM processes are stopped by now, and
    # a read-only open can fail on a WAL database that still has -wal pages.
    # This function issues SELECTs only.
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT id FROM project WHERE name = ?", (project_name,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Project '{project_name}' not found in {db_path}")
        project_id = int(row[0])

        entity_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM entity WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )
        duplicate_permalinks = [
            str(r[0])
            for r in connection.execute(
                "SELECT permalink FROM entity"
                " WHERE project_id = ? AND permalink IS NOT NULL"
                " GROUP BY permalink HAVING COUNT(*) > 1",
                (project_id,),
            )
        ]
        observation_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM observation o JOIN entity e ON o.entity_id = e.id"
                " WHERE e.project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )
        distinct_observation_tuples = int(
            connection.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT o.entity_id, o.category, o.content"
                " FROM observation o JOIN entity e ON o.entity_id = e.id"
                " WHERE e.project_id = ?)",
                (project_id,),
            ).fetchone()[0]
        )
        duplicate_observations = [
            f"entity={r[0]} [{r[1]}] x{r[3]}: {str(r[2])[:80]}"
            for r in connection.execute(
                "SELECT o.entity_id, o.category, o.content, COUNT(*)"
                " FROM observation o JOIN entity e ON o.entity_id = e.id"
                " WHERE e.project_id = ?"
                " GROUP BY o.entity_id, o.category, o.content HAVING COUNT(*) > 1",
                (project_id,),
            )
        ]
        duplicate_relation_tuples = int(
            connection.execute(
                "SELECT COUNT(*) FROM ("
                " SELECT r.from_id, r.to_name, r.relation_type"
                " FROM relation r JOIN entity e ON r.from_id = e.id"
                " WHERE e.project_id = ?"
                " GROUP BY r.from_id, r.to_name, r.relation_type HAVING COUNT(*) > 1)",
                (project_id,),
            ).fetchone()[0]
        )
    finally:
        connection.close()

    expected_files = expected.hub_count + expected.ok_creates
    checks = [
        IntegrityCheck(
            name="files_match_successful_creates",
            passed=md_files == expected_files,
            detail=f"{md_files} markdown files on disk, expected {expected_files}"
            f" ({expected.hub_count} hubs + {expected.ok_creates} successful creates)",
        ),
        IntegrityCheck(
            name="db_entities_match_files",
            passed=entity_rows == md_files,
            detail=f"{entity_rows} entity rows vs {md_files} markdown files",
        ),
        IntegrityCheck(
            name="no_duplicate_permalinks",
            passed=len(duplicate_permalinks) == 0,
            detail=f"{len(duplicate_permalinks)} duplicated permalinks",
        ),
        IntegrityCheck(
            name="no_duplicate_observation_tuples",
            passed=len(duplicate_observations) == 0,
            detail=f"{len(duplicate_observations)} duplicated (entity, category, content) tuples"
            f" across {observation_rows} observation rows",
        ),
        IntegrityCheck(
            name="no_duplicate_relation_tuples",
            passed=duplicate_relation_tuples == 0,
            detail=f"{duplicate_relation_tuples} duplicated (from, to, type) tuples",
        ),
        IntegrityCheck(
            name="no_lost_writes",
            passed=len(missing) == 0,
            detail=f"{len(missing)} markers from successful ops missing on disk",
        ),
        IntegrityCheck(
            name="no_doubled_writes",
            passed=len(duplicated) == 0,
            detail=f"{len(duplicated)} markers appear more than once on disk",
        ),
    ]
    redundancy_pct = (
        100.0 * (observation_rows - distinct_observation_tuples) / observation_rows
        if observation_rows
        else 0.0
    )
    return IntegrityReport(
        checks=checks,
        markdown_files=md_files,
        entity_rows=entity_rows,
        observation_rows=observation_rows,
        distinct_observation_tuples=distinct_observation_tuples,
        duplicate_observation_tuples=len(duplicate_observations),
        observation_redundancy_pct=round(redundancy_pct, 2),
        duplicate_permalinks=len(duplicate_permalinks),
        duplicate_relation_tuples=duplicate_relation_tuples,
        expected_markers=len(expected.markers),
        found_markers=len(found_markers),
        missing_markers=len(missing),
        duplicated_markers=len(duplicated),
        missing_marker_sample=missing[:10],
        duplicate_permalink_sample=duplicate_permalinks[:10],
        duplicate_observation_sample=duplicate_observations[:10],
        converged=all(check.passed for check in checks),
    )


# --- Summaries and artifacts ---


def _percentile(sorted_values: list[float], quantile: float) -> float:
    # Same nearest-rank convention as scoring/retrieval.py's p95.
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, math.ceil(len(sorted_values) * quantile) - 1))
    return sorted_values[index]


def summarize_op_type(results: list[OpResult]) -> OpTypeStats:
    latencies = sorted(result.latency_ms for result in results)
    ok = sum(1 for result in results if result.ok)
    return OpTypeStats(
        count=len(results),
        ok=ok,
        errors=len(results) - ok,
        mean_ms=round(mean(latencies), 2) if latencies else 0.0,
        p50_ms=round(_percentile(latencies, 0.50), 2),
        p95_ms=round(_percentile(latencies, 0.95), 2),
        max_ms=round(max(latencies), 2) if latencies else 0.0,
    )


def resolve_clean_checkout_sha(checkout: Path) -> str:
    """Return the exact target SHA, rejecting bytes that the SHA cannot identify."""
    resolved_sha = git_sha(checkout)
    if resolved_sha is None:
        raise ValueError("--bm-local-path must point to a Basic Memory git checkout")

    status = run_command(
        ["git", "-C", str(checkout), "status", "--porcelain=v1", "--untracked-files=all"]
    )
    dirty_paths = [line for line in status.stdout.splitlines() if line.strip()]
    # Trigger: tracked or untracked bytes differ from the recorded commit.
    # Why: the run must be reproducible from bm_resolved_sha alone.
    # Outcome: abort before creating the isolated benchmark home or artifacts.
    if dirty_paths:
        sample = ", ".join(dirty_paths[:5])
        suffix = " ..." if len(dirty_paths) > 5 else ""
        raise ValueError(
            "--bm-local-path must be clean so bm_resolved_sha identifies the executed bytes; "
            f"dirty paths: {sample}{suffix}"
        )
    return resolved_sha


def build_summary(
    *,
    config: ConcurrentWriteConfig,
    results: list[OpResult],
    outcomes: list[WriterOutcome],
    concurrent_wall_seconds: float,
    settle_seconds: float,
    settle_mode: Literal["status-json", "fixed-delay"],
    reindex_seconds: float | None,
    integrity: IntegrityReport,
) -> ConcurrentWriteSummary:
    ops_ok = sum(1 for result in results if result.ok)
    ops_error = len(results) - ops_ok
    ops_not_attempted = sum(outcome.not_attempted for outcome in outcomes)
    terminal_writer_failures = sum(1 for outcome in outcomes if outcome.terminal_error is not None)
    error_kinds = Counter(result.error_kind for result in results if result.error_kind is not None)
    per_op_type: dict[str, OpTypeStats] = {}
    for op_type in ("create_hub", "create", "edit_hub", "edit_own"):
        typed = [result for result in results if result.op_type == op_type]
        if typed:
            per_op_type[op_type] = summarize_op_type(typed)
    concurrent_results = [result for result in results if result.op_type != "create_hub"]
    notes_created_ok = sum(1 for r in results if r.op_type == "create" and r.ok)
    return ConcurrentWriteSummary(
        run_id=config.run_id,
        concurrent_wall_seconds=round(concurrent_wall_seconds, 2),
        settle_seconds=round(settle_seconds, 2),
        settle_mode=settle_mode,
        reindex_seconds=round(reindex_seconds, 2) if reindex_seconds is not None else None,
        ops_total=len(results),
        ops_ok=ops_ok,
        ops_error=ops_error,
        ops_not_attempted=ops_not_attempted,
        terminal_writer_failures=terminal_writer_failures,
        error_kinds=dict(error_kinds),
        per_op_type=per_op_type,
        notes_created_ok=notes_created_ok,
        creates_per_minute=round(notes_created_ok / (concurrent_wall_seconds / 60), 1)
        if concurrent_wall_seconds > 0
        else 0.0,
        ops_per_second=round(len(concurrent_results) / concurrent_wall_seconds, 2)
        if concurrent_wall_seconds > 0
        else 0.0,
        integrity=integrity,
        converged=(
            integrity.converged
            and ops_error == 0
            and ops_not_attempted == 0
            and terminal_writer_failures == 0
        ),
    )


def build_summary_markdown(
    manifest: ConcurrentWriteManifest, summary: ConcurrentWriteSummary
) -> str:
    config = manifest.config
    lines = [
        f"# Concurrent-Write Run `{manifest.run_id}`",
        "",
        "## Provenance",
        "",
        f"- Benchmark SHA: `{manifest.benchmark_git_sha}`",
        f"- BM source: `{manifest.bm_source}`",
        f"- BM resolved SHA: `{manifest.bm_resolved_sha or 'unknown'}`",
        f"- BM version: `{manifest.bm_version or 'unknown'}`",
        f"- Home: `{manifest.home_dir}`",
        "",
        "## Workload",
        "",
        f"- Writers: {config.writers} (one `bm mcp` session each)",
        f"- Notes per writer: {config.notes_per_writer}",
        f"- Edit ratio: {config.edit_ratio}, hub notes: {config.hub_notes},"
        f" relation pool: {config.relation_pool}, seed: {config.seed}",
        "",
        "## Throughput",
        "",
        f"- Concurrent phase: {summary.concurrent_wall_seconds}s wall",
        f"- Settle: {summary.settle_seconds}s ({summary.settle_mode})",
        f"- Reindex: {f'{summary.reindex_seconds}s' if summary.reindex_seconds is not None else 'not measured'}",
        f"- Ops: {summary.ops_total} total, {summary.ops_ok} ok, {summary.ops_error} errors,"
        f" {summary.ops_not_attempted} not attempted",
        f"- Terminal writer failures: {summary.terminal_writer_failures}",
        f"- Notes created: {summary.notes_created_ok}"
        f" ({summary.creates_per_minute} notes/min, {summary.ops_per_second} concurrent ops/s)",
        "",
        "## Latency (ms)",
        "",
        "| Op | Count | OK | Errors | Mean | P50 | P95 | Max |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for op_type, stats in summary.per_op_type.items():
        lines.append(
            f"| {op_type} | {stats.count} | {stats.ok} | {stats.errors} |"
            f" {stats.mean_ms} | {stats.p50_ms} | {stats.p95_ms} | {stats.max_ms} |"
        )
    if summary.error_kinds:
        lines += ["", "## Errors", ""]
        for kind, count in sorted(summary.error_kinds.items()):
            lines.append(f"- {kind}: {count}")
    integrity = summary.integrity
    lines += [
        "",
        f"## Convergence: {'CONVERGED' if summary.converged else 'DIVERGED'}",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for check in integrity.checks:
        lines.append(f"| {check.name} | {'pass' if check.passed else 'FAIL'} | {check.detail} |")
    lines += [
        "",
        f"- Observation redundancy: {integrity.observation_redundancy_pct}%"
        f" ({integrity.observation_rows} rows, {integrity.distinct_observation_tuples} distinct)",
        "",
        "## Reproduce",
        "",
        "```bash",
        f"uv run bm-bench run concurrent-write --run-id {manifest.run_id}"
        f" --writers {config.writers} --notes-per-writer {config.notes_per_writer}"
        f" --edit-ratio {config.edit_ratio} --hub-notes {config.hub_notes}"
        f" --seed {config.seed} --bm-local-path {config.bm_local_path}",
        "```",
    ]
    return "\n".join(lines).strip() + "\n"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True) + "\n")


def write_concurrent_artifacts(
    *,
    run_dir: Path,
    manifest: ConcurrentWriteManifest,
    results: list[OpResult],
    summary: ConcurrentWriteSummary,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "manifest.json", manifest.model_dump(mode="json"))
    _write_jsonl(run_dir / "per-op.jsonl", [row.model_dump(mode="json") for row in results])
    _write_json(run_dir / "concurrent-write-summary.json", summary.model_dump(mode="json"))
    (run_dir / "summary.md").write_text(build_summary_markdown(manifest, summary), encoding="utf-8")


# --- Orchestration ---


def _bm_version(prefix: list[str], env: dict[str, str]) -> str | None:
    try:
        result = run_command(prefix + ["--version"], env=env)
    except Exception:
        return None
    return result.stdout.strip() or None


def run_concurrent_write(config: ConcurrentWriteConfig) -> Path:
    """Execute the full driver: setup, concurrent phase, settle, verify, report."""
    bm_checkout = Path(config.bm_local_path).expanduser().resolve()
    prefix = resolve_bm_command_prefix(str(bm_checkout))
    bm_resolved_sha = resolve_clean_checkout_sha(bm_checkout)
    config = config.model_copy(update={"bm_local_path": str(bm_checkout)})

    home = Path("benchmarks/.bm-homes") / f"bm-write-{config.run_id}"
    if home.exists():
        raise RuntimeError(f"Home already exists (re-running a run_id is not supported): {home}")
    project_dir = home / "project"
    project_dir.mkdir(parents=True)
    (home / "default-home").mkdir()
    env = isolated_bm_env(home)

    console.print(f"[bold]concurrent-write[/bold] run_id={config.run_id} home={home}")
    run_command(prefix + ["project", "add", config.project_name, str(project_dir)], env=env)
    bm_version = _bm_version(prefix, env)

    manifest = ConcurrentWriteManifest(
        run_id=config.run_id,
        created_at_utc=utc_now_iso(),
        benchmark_git_sha=git_sha(Path(".")) or "unknown",
        bm_source=config.bm_source,
        bm_resolved_sha=bm_resolved_sha,
        bm_local_path=config.bm_local_path,
        bm_version=bm_version,
        home_dir=str(home),
        project_dir=str(project_dir),
        project_name=config.project_name,
        runtime=RuntimeInfo(
            os=runtime_info()[0],
            python_version=runtime_info()[1],
            started_at_utc=utc_now_iso(),
        ),
        config=config,
    )

    mcp_command = prefix[0]
    mcp_args = prefix[1:] + ["mcp"]
    clients = [
        WarmMcpClient(
            command=mcp_command,
            args=mcp_args,
            env=env,
            request_timeout_seconds=config.op_timeout_seconds,
            required_tool="write_note",
        )
        for _ in range(config.writers)
    ]

    results: list[OpResult] = []
    outcomes = [WriterOutcome() for _ in range(config.writers)]
    try:
        console.print(f"Starting {config.writers} warm `bm mcp` sessions...")
        for client in clients:
            client.start()

        # Setup phase: hubs are created sequentially through writer 0's session
        # so the concurrent phase starts from a known shared state.
        setup_results: list[OpResult] = []
        for op in build_hub_ops(config):
            result, terminal = _execute_op(clients[0], op, config.project_name)
            setup_results.append(result)
            if not result.ok:
                raise RuntimeError(f"Hub setup failed for {op.identifier}: {result.error}")
        results.extend(setup_results)

        plans = [build_writer_plan(writer, config) for writer in range(config.writers)]
        planned_ops = sum(len(plan) for plan in plans)
        console.print(
            f"Concurrent phase: {config.writers} writers, {planned_ops} planned ops"
            f" ({config.notes_per_writer} creates each + edits)..."
        )
        barrier = threading.Barrier(config.writers)
        started = time.monotonic()
        deadline = started + config.max_seconds if config.max_seconds is not None else None
        threads = [
            threading.Thread(
                target=_run_writer,
                kwargs={
                    "client": clients[writer],
                    "plan": plans[writer],
                    "project_name": config.project_name,
                    "barrier": barrier,
                    "deadline": deadline,
                    "outcome": outcomes[writer],
                },
                name=f"bm-writer-{writer}",
            )
            for writer in range(config.writers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        concurrent_wall_seconds = time.monotonic() - started
        for outcome in outcomes:
            results.extend(outcome.results)
    finally:
        for client in clients:
            client.stop()

    console.print("Waiting for index to settle...")
    settle_seconds, settle_mode = settle_index(
        prefix=prefix,
        env=env,
        project_name=config.project_name,
        timeout_seconds=config.settle_timeout_seconds,
    )

    ok_markers = frozenset(marker for result in results if result.ok for marker in result.markers)
    ok_creates = sum(1 for result in results if result.op_type == "create" and result.ok)
    ok_hubs = sum(1 for result in results if result.op_type == "create_hub" and result.ok)
    integrity = run_integrity_checks(
        db_path=home / "config" / "memory.db",
        project_name=config.project_name,
        project_dir=project_dir,
        expected=ExpectedState(markers=ok_markers, ok_creates=ok_creates, hub_count=ok_hubs),
    )

    # Trigger: reindex is enabled as a separate timing measurement.
    # Why: reindex mutates derived state and could hide or introduce the
    # concurrency outcome. Outcome: the convergence verdict above remains the
    # settled pre-reindex state; only wall time is measured afterward.
    reindex_seconds: float | None = None
    if config.measure_reindex:
        console.print("Measuring full reindex wall time...")
        reindex_start = time.monotonic()
        run_command(prefix + ["reindex", "--search", "-p", config.project_name], env=env)
        reindex_seconds = time.monotonic() - reindex_start

    summary = build_summary(
        config=config,
        results=results,
        outcomes=outcomes,
        concurrent_wall_seconds=concurrent_wall_seconds,
        settle_seconds=settle_seconds,
        settle_mode=settle_mode,
        reindex_seconds=reindex_seconds,
        integrity=integrity,
    )

    run_dir = Path(config.output_root) / config.run_id
    write_concurrent_artifacts(run_dir=run_dir, manifest=manifest, results=results, summary=summary)

    verdict = "[green]CONVERGED[/green]" if summary.converged else "[red]DIVERGED[/red]"
    console.print(
        f"{verdict} — {summary.ops_ok}/{summary.ops_total} ops ok,"
        f" {summary.notes_created_ok} notes at {summary.creates_per_minute} notes/min,"
        f" settle {summary.settle_seconds}s"
    )
    for check in integrity.checks:
        status = "[green]pass[/green]" if check.passed else "[red]FAIL[/red]"
        console.print(f"  {status} {check.name}: {check.detail}")
    return run_dir
