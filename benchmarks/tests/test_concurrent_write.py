"""Unit tests for the concurrent-writer driver (planning, stats, integrity)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from mcp.types import CallToolResult, TextContent

import basic_memory_benchmarks.bm_runtime as bm_runtime
import basic_memory_benchmarks.concurrent_write as concurrent_write
from basic_memory_benchmarks.bm_runtime import WarmMcpClient
from basic_memory_benchmarks.concurrent_write import (
    ConcurrentWriteConfig,
    ExpectedState,
    OpResult,
    build_hub_ops,
    build_summary_markdown,
    build_writer_plan,
    classify_error,
    run_integrity_checks,
    resolve_clean_checkout_sha,
    summarize_op_type,
    tool_result_error,
)


def _config(**overrides: object) -> ConcurrentWriteConfig:
    defaults: dict[str, object] = {
        "run_id": "test-run",
        "writers": 3,
        "notes_per_writer": 10,
        "edit_ratio": 0.5,
        "hub_notes": 2,
        "relation_pool": 4,
        "seed": 42,
        "bm_local_path": "/tmp/basic-memory",
    }
    defaults.update(overrides)
    return ConcurrentWriteConfig.model_validate(defaults)


# --- Workload planning ---


def test_writer_plan_is_deterministic() -> None:
    config = _config()
    first = build_writer_plan(1, config)
    second = build_writer_plan(1, config)
    assert first == second


def test_writer_plans_differ_between_writers() -> None:
    config = _config()
    assert build_writer_plan(0, config) != build_writer_plan(1, config)


def test_markers_are_unique_across_writers_and_hubs() -> None:
    config = _config()
    markers: list[str] = []
    for op in build_hub_ops(config):
        markers.extend(op.markers)
    for writer in range(config.writers):
        for op in build_writer_plan(writer, config):
            markers.extend(op.markers)
    assert len(markers) == len(set(markers))


def test_plan_contains_expected_creates_and_valid_edit_targets() -> None:
    config = _config()
    plan = build_writer_plan(2, config)
    creates = [op for op in plan if op.op_type == "create"]
    assert len(creates) == config.notes_per_writer

    created_identifiers = {op.identifier for op in creates}
    for op in plan:
        if op.op_type == "edit_hub":
            hub_index = int(op.identifier.rsplit("-", 1)[-1])
            assert 0 <= hub_index < config.hub_notes
        if op.op_type == "edit_own":
            # Writers only edit their own already-created notes.
            assert op.identifier in created_identifiers
            position = plan.index(op)
            assert op.identifier in {p.identifier for p in plan[:position] if p.op_type == "create"}


def test_zero_hub_notes_produces_no_hub_ops() -> None:
    config = _config(hub_notes=0)
    assert build_hub_ops(config) == []
    for writer in range(config.writers):
        assert all(op.op_type != "edit_hub" for op in build_writer_plan(writer, config))


# --- MCP process lifecycle ---


def test_timed_out_mcp_call_is_cancelled_before_stop_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_started = threading.Event()
    transport_exited = threading.Event()

    @asynccontextmanager
    async def fake_stdio_client(_params: object) -> AsyncIterator[tuple[object, object]]:
        try:
            yield object(), object()
        finally:
            transport_exited.set()

    class FakeClientSession:
        def __init__(self, _read_stream: object, _write_stream: object) -> None:
            pass

        async def __aenter__(self) -> FakeClientSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[SimpleNamespace(name="write_note")])

        async def call_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
            call_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(bm_runtime, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(bm_runtime, "ClientSession", FakeClientSession)

    client = WarmMcpClient(
        request_timeout_seconds=0.01,
        startup_timeout_seconds=1.0,
        required_tool="write_note",
    )
    client.start()
    with pytest.raises(FutureTimeoutError):
        client.call_tool("write_note", {})
    assert call_started.wait(timeout=1.0)

    client.stop()

    assert transport_exited.wait(timeout=1.0)
    with pytest.raises(RuntimeError, match="not running"):
        client.call_tool("write_note", {})


# --- Error classification and stats ---


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("deadlock detected on relation", "deadlock"),
        ("sqlite3.OperationalError: database is locked", "sqlite_locked"),
        ("tool call timed out after 120s", "timeout"),
        ("db_version conflict: stale write rejected", "write_conflict"),
        ("something else entirely", "other"),
    ],
)
def test_classify_error(text: str, kind: str) -> None:
    assert classify_error(text) == kind


def test_summarize_op_type_latency_stats() -> None:
    results = [
        OpResult(
            writer=0,
            op_index=i,
            op_type="create",
            identifier=f"notes/n{i}",
            started_at_utc="2026-01-01T00:00:00Z",
            latency_ms=float(latency),
            ok=(i != 3),
            error="boom" if i == 3 else None,
        )
        for i, latency in enumerate([10, 20, 30, 40, 100])
    ]
    stats = summarize_op_type(results)
    assert stats.count == 5
    assert stats.ok == 4
    assert stats.errors == 1
    assert stats.mean_ms == 40.0
    assert stats.p50_ms == 30.0
    assert stats.max_ms == 100.0


def test_tool_result_error_reads_structured_tool_failure() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text='{"error":"deadlock detected"}')],
        structuredContent={"result": {"error": "deadlock detected"}},
        isError=False,
    )
    assert tool_result_error(result) == "deadlock detected"


def test_tool_result_error_accepts_structured_success() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text='{"title":"note"}')],
        structuredContent={"result": {"title": "note"}},
        isError=False,
    )
    assert tool_result_error(result) is None


def test_concurrent_ops_request_json_tool_responses() -> None:
    op = build_writer_plan(0, _config(notes_per_writer=1))[0]
    _tool, arguments = concurrent_write._tool_call_for(op, "project")
    assert arguments["output_format"] == "json"


def test_unknown_status_schema_uses_fixed_settle_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = json.dumps({"total_files": 2, "observed_files": [{"path": "note.md"}]})
    monkeypatch.setattr(
        concurrent_write,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=status, stderr=""),
    )
    delays: list[float] = []
    monkeypatch.setattr(concurrent_write.time, "sleep", delays.append)

    _seconds, mode = concurrent_write._settle_index(
        prefix=["bm"],
        env={},
        project_name="project",
        timeout_seconds=1.0,
    )

    assert mode == "fixed-delay"
    assert delays == [concurrent_write.FALLBACK_SETTLE_SECONDS]


def test_resolve_clean_checkout_sha_rejects_dirty_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(concurrent_write, "git_sha", lambda _path: "abc123")
    monkeypatch.setattr(
        concurrent_write,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=" M src/basic_memory/app.py\n?? local.py\n"
        ),
    )

    with pytest.raises(ValueError, match="bm_resolved_sha.*dirty paths"):
        resolve_clean_checkout_sha(tmp_path)


def test_resolve_clean_checkout_sha_accepts_clean_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(concurrent_write, "git_sha", lambda _path: "abc123")
    monkeypatch.setattr(
        concurrent_write,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=""),
    )

    assert resolve_clean_checkout_sha(tmp_path) == "abc123"


# --- Integrity verification ---


SCHEMA = """
CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE entity (id INTEGER PRIMARY KEY, project_id INTEGER, permalink TEXT, file_path TEXT);
CREATE TABLE observation (id INTEGER PRIMARY KEY, entity_id INTEGER, category TEXT, content TEXT);
CREATE TABLE relation (
    id INTEGER PRIMARY KEY, from_id INTEGER, to_id INTEGER, to_name TEXT, relation_type TEXT
);
"""


def _make_db(path: Path, rows: dict[str, list[tuple]]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    for table, table_rows in rows.items():
        if not table_rows:
            continue
        placeholders = ",".join("?" for _ in table_rows[0])
        connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", table_rows)
    connection.commit()
    connection.close()


def _write_note_file(project_dir: Path, relative: str, markers: list[str]) -> None:
    path = project_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"- [fact] generated content {marker}" for marker in markers]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_integrity_clean_state_converges(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    _write_note_file(project_dir, "hubs/hub-0.md", ["bmk-setup-h00-l0"])
    _write_note_file(project_dir, "notes/w00-n0000.md", ["bmk-w00-o0000-l0"])
    db_path = tmp_path / "memory.db"
    _make_db(
        db_path,
        {
            "project": [(1, "bm-write-test")],
            "entity": [
                (1, 1, "hubs/hub-0", "hubs/hub-0.md"),
                (2, 1, "notes/w00-n0000", "notes/w00-n0000.md"),
            ],
            "observation": [
                (1, 1, "fact", "generated content bmk-setup-h00-l0"),
                (2, 2, "fact", "generated content bmk-w00-o0000-l0"),
            ],
            "relation": [(1, 2, None, "topic-1", "relates_to")],
        },
    )
    report = run_integrity_checks(
        db_path=db_path,
        project_name="bm-write-test",
        project_dir=project_dir,
        expected=ExpectedState(
            markers=frozenset({"bmk-setup-h00-l0", "bmk-w00-o0000-l0"}),
            ok_creates=1,
            hub_count=1,
        ),
    )
    assert report.converged
    assert report.markdown_files == 2
    assert report.entity_rows == 2
    assert report.observation_redundancy_pct == 0.0


def test_integrity_flags_duplicate_observation_tuples(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    _write_note_file(project_dir, "notes/w00-n0000.md", ["bmk-w00-o0000-l0"])
    db_path = tmp_path / "memory.db"
    _make_db(
        db_path,
        {
            "project": [(1, "bm-write-test")],
            "entity": [(1, 1, "notes/w00-n0000", "notes/w00-n0000.md")],
            # The #1214 shape: the same observation indexed twice.
            "observation": [
                (1, 1, "fact", "generated content bmk-w00-o0000-l0"),
                (2, 1, "fact", "generated content bmk-w00-o0000-l0"),
            ],
        },
    )
    report = run_integrity_checks(
        db_path=db_path,
        project_name="bm-write-test",
        project_dir=project_dir,
        expected=ExpectedState(markers=frozenset({"bmk-w00-o0000-l0"}), ok_creates=1, hub_count=0),
    )
    assert not report.converged
    assert report.duplicate_observation_tuples == 1
    assert report.observation_redundancy_pct == 50.0
    failed = {check.name for check in report.checks if not check.passed}
    assert failed == {"no_duplicate_observation_tuples"}


def test_integrity_flags_lost_write_and_duplicate_permalink(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    # Marker bmk-w00-o0001-l0 was reported ok but never landed on disk.
    _write_note_file(project_dir, "notes/w00-n0000.md", ["bmk-w00-o0000-l0"])
    db_path = tmp_path / "memory.db"
    _make_db(
        db_path,
        {
            "project": [(1, "bm-write-test")],
            "entity": [
                (1, 1, "notes/w00-n0000", "notes/w00-n0000.md"),
                # Duplicate permalink row.
                (2, 1, "notes/w00-n0000", "notes/w00-n0000 (copy).md"),
            ],
            "observation": [(1, 1, "fact", "generated content bmk-w00-o0000-l0")],
        },
    )
    report = run_integrity_checks(
        db_path=db_path,
        project_name="bm-write-test",
        project_dir=project_dir,
        expected=ExpectedState(
            markers=frozenset({"bmk-w00-o0000-l0", "bmk-w00-o0001-l0"}),
            ok_creates=2,
            hub_count=0,
        ),
    )
    assert not report.converged
    assert report.missing_markers == 1
    assert report.missing_marker_sample == ["bmk-w00-o0001-l0"]
    assert report.duplicate_permalinks == 1
    failed = {check.name for check in report.checks if not check.passed}
    assert "no_lost_writes" in failed
    assert "no_duplicate_permalinks" in failed
    assert "files_match_successful_creates" in failed


def test_integrity_missing_db_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Index database not found"):
        run_integrity_checks(
            db_path=tmp_path / "missing.db",
            project_name="bm-write-test",
            project_dir=tmp_path,
            expected=ExpectedState(markers=frozenset(), ok_creates=0, hub_count=0),
        )


def test_integrity_missing_project_fails_loudly(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    _make_db(db_path, {"project": [(1, "some-other-project")]})
    with pytest.raises(RuntimeError, match="not found"):
        run_integrity_checks(
            db_path=db_path,
            project_name="bm-write-test",
            project_dir=tmp_path,
            expected=ExpectedState(markers=frozenset(), ok_creates=0, hub_count=0),
        )


# --- Summary rendering ---


def test_summary_markdown_reports_convergence(tmp_path: Path) -> None:
    from basic_memory_benchmarks.concurrent_write import (
        ConcurrentWriteManifest,
        WriterOutcome,
        build_summary,
    )
    from basic_memory_benchmarks.models import RuntimeInfo

    config = _config()
    project_dir = tmp_path / "project"
    _write_note_file(project_dir, "notes/w00-n0000.md", ["bmk-w00-o0000-l0"])
    db_path = tmp_path / "memory.db"
    _make_db(
        db_path,
        {
            "project": [(1, config.project_name)],
            "entity": [(1, 1, "notes/w00-n0000", "notes/w00-n0000.md")],
            "observation": [(1, 1, "fact", "generated content bmk-w00-o0000-l0")],
        },
    )
    integrity = run_integrity_checks(
        db_path=db_path,
        project_name=config.project_name,
        project_dir=project_dir,
        expected=ExpectedState(markers=frozenset({"bmk-w00-o0000-l0"}), ok_creates=1, hub_count=0),
    )
    results = [
        OpResult(
            writer=0,
            op_index=0,
            op_type="create",
            identifier="notes/w00-n0000",
            started_at_utc="2026-01-01T00:00:00Z",
            latency_ms=12.0,
            ok=True,
            markers=["bmk-w00-o0000-l0"],
        )
    ]
    summary = build_summary(
        config=config,
        results=results,
        outcomes=[WriterOutcome(results=results)],
        concurrent_wall_seconds=1.5,
        settle_seconds=0.5,
        settle_mode="status-json",
        reindex_seconds=2.0,
        integrity=integrity,
    )
    manifest = ConcurrentWriteManifest(
        run_id=config.run_id,
        created_at_utc="2026-01-01T00:00:00Z",
        benchmark_git_sha="abc123",
        bm_source="local-checkout",
        bm_resolved_sha="def456",
        bm_local_path=config.bm_local_path,
        home_dir=str(tmp_path),
        project_dir=str(project_dir),
        project_name=config.project_name,
        runtime=RuntimeInfo(
            os="test", python_version="3.12", started_at_utc="2026-01-01T00:00:00Z"
        ),
        config=config,
    )
    markdown = build_summary_markdown(manifest, summary)
    assert "## Convergence: CONVERGED" in markdown
    assert "no_duplicate_observation_tuples" in markdown
    assert "bm-bench run concurrent-write" in markdown
    assert summary.converged
    assert summary.notes_created_ok == 1
    assert summary.ops_per_second == 0.67

    setup_result = OpResult(
        writer=-1,
        op_index=0,
        op_type="create_hub",
        identifier="hubs/hub-0",
        started_at_utc="2026-01-01T00:00:00Z",
        latency_ms=500.0,
        ok=True,
        markers=["bmk-setup-h00-l0"],
    )
    with_setup_summary = build_summary(
        config=config,
        results=[setup_result, *results],
        outcomes=[WriterOutcome(results=results)],
        concurrent_wall_seconds=1.5,
        settle_seconds=0.5,
        settle_mode="status-json",
        reindex_seconds=None,
        integrity=integrity,
    )
    assert with_setup_summary.ops_total == 2
    assert with_setup_summary.ops_per_second == 0.67

    failed_result = OpResult(
        writer=0,
        op_index=1,
        op_type="edit_own",
        identifier="notes/w00-n0000",
        started_at_utc="2026-01-01T00:00:01Z",
        latency_ms=15.0,
        ok=False,
        error="deadlock detected",
        error_kind="deadlock",
    )
    failed_summary = build_summary(
        config=config,
        results=[*results, failed_result],
        outcomes=[WriterOutcome(results=[*results, failed_result])],
        concurrent_wall_seconds=1.5,
        settle_seconds=0.5,
        settle_mode="status-json",
        reindex_seconds=None,
        integrity=integrity,
    )
    assert not failed_summary.converged
