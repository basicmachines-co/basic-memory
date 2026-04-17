"""Telemetry coverage for sync phase spans."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import AsyncMock

import logfire
import pytest

from basic_memory.sync.sync_service import SyncReport

batch_indexer_module = importlib.import_module("basic_memory.indexing.batch_indexer")
sync_service_module = importlib.import_module("basic_memory.sync.sync_service")


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


def _write_markdown(project_root: Path, relative_path: str, content: str) -> Path:
    """Create one markdown file under the test project."""
    file_path = project_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.mark.asyncio
async def test_sync_emits_phase_spans(sync_service, project_config, monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    report = SyncReport(
        new={"new.md"},
        modified={"modified.md"},
        deleted={"deleted.md"},
        moves={"old.md": "moved.md"},
    )

    async def fake_scan(directory, force_full=False):
        return report

    async def fake_handle_move(old_path, new_path):
        return None

    async def fake_handle_delete(path):
        return None

    async def fake_index_changed_files(changed_paths, checksums_by_path, progress_callback=None):
        return [], []

    async def fake_resolve_relations(entity_id=None):
        return set()

    async def fake_quick_count_files(directory):
        return 3

    async def fake_find_by_id(project_id):
        return SimpleNamespace(id=project_id)

    async def fake_update(project_id, values):
        return None

    monkeypatch.setattr(logfire, "span", fake_span)
    monkeypatch.setattr(sync_service, "scan", fake_scan)
    monkeypatch.setattr(sync_service, "handle_move", fake_handle_move)
    monkeypatch.setattr(sync_service, "handle_delete", fake_handle_delete)
    monkeypatch.setattr(sync_service, "_index_changed_files", fake_index_changed_files)
    monkeypatch.setattr(sync_service, "resolve_relations", fake_resolve_relations)
    monkeypatch.setattr(sync_service, "_quick_count_files", fake_quick_count_files)
    monkeypatch.setattr(sync_service.project_repository, "find_by_id", fake_find_by_id)
    monkeypatch.setattr(sync_service.project_repository, "update", fake_update)
    sync_service.app_config.semantic_search_enabled = False

    result = await sync_service.sync(
        project_config.home,
        project_name=project_config.name,
        force_full=True,
    )

    assert result is report
    span_names = [name for name, _ in spans]
    assert "sync.project.run" in span_names
    assert "sync.project.scan" in span_names
    assert "sync.project.apply_changes" in span_names
    assert "sync.project.resolve_relations" in span_names
    assert "sync.project.update_watermark" in span_names
    # The root span carries the project_name/force_full attrs previously on operation()
    root_span_attrs = next(attrs for name, attrs in spans if name == "sync.project.run")
    assert root_span_attrs["project_name"] == project_config.name
    assert root_span_attrs["force_full"] is True


@pytest.mark.asyncio
async def test_sync_one_markdown_file_emits_index_phase_spans(
    sync_service, test_project, monkeypatch
) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)
    monkeypatch.setattr(sync_service.search_service, "index_entity_data", AsyncMock())

    _write_markdown(
        Path(test_project.path),
        "notes/telemetry.md",
        dedent(
            f"""\
            ---
            title: Telemetry Note
            type: note
            permalink: {test_project.name}/notes/telemetry
            ---

            # Telemetry Note

            Body content.
            """
        ),
    )

    result = await sync_service.sync_one_markdown_file("notes/telemetry.md")

    index_spans = [(name, attrs) for name, attrs in spans if name.startswith("index.markdown_file")]
    assert [name for name, _ in index_spans] == [
        "index.markdown_file.prepare",
        "index.markdown_file.load_permalink_map",
        "index.markdown_file.normalize",
        "index.markdown_file.persist",
        "index.markdown_file.reload_entity",
    ]
    assert index_spans[0][1] == {"path": "notes/telemetry.md"}
    assert index_spans[3][1] == {"path": "notes/telemetry.md", "is_new": True}
    assert index_spans[4][1]["entity_id"] == result.entity.id


@pytest.mark.asyncio
async def test_sync_file_emits_failure_span(sync_service, monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    recorded_failures: list[tuple[str, str]] = []

    async def fake_record_failure(path, error):
        recorded_failures.append((path, error))

    async def fail_sync_markdown_file(path, new=True):
        raise ValueError("boom")

    monkeypatch.setattr(logfire, "span", fake_span)
    monkeypatch.setattr(sync_service, "_should_skip_file", lambda path: _false_async())
    monkeypatch.setattr(sync_service.file_service, "is_markdown", lambda path: True)
    monkeypatch.setattr(sync_service, "sync_markdown_file", fail_sync_markdown_file)
    monkeypatch.setattr(sync_service, "_record_failure", fake_record_failure)

    result = await sync_service.sync_file("notes/broken.md", new=True)

    assert result == (None, None)
    failure_spans = [span for span in spans if span[0] == "sync.file.failure"]
    assert failure_spans == [
        (
            "sync.file.failure",
            {
                "failure_type": "ValueError",
                "path": "notes/broken.md",
                "file_kind": "markdown",
                "is_new": True,
                "is_fatal": False,
            },
        )
    ]
    assert recorded_failures == [("notes/broken.md", "boom")]


@pytest.mark.asyncio
async def test_sync_file_logs_slow_operation(sync_service, monkeypatch) -> None:
    warning_messages: list[str] = []

    async def fake_sync_regular_file(path, new=True):
        return SimpleNamespace(id=1), "deadbeef"

    times = iter([10.0, 10.8])

    monkeypatch.setattr(sync_service, "_should_skip_file", lambda path: _false_async())
    monkeypatch.setattr(sync_service.file_service, "is_markdown", lambda path: False)
    monkeypatch.setattr(sync_service, "sync_regular_file", fake_sync_regular_file)
    monkeypatch.setattr(sync_service.search_service, "index_entity", _none_async)
    monkeypatch.setattr(sync_service, "_clear_failure", lambda path: None)
    monkeypatch.setattr(sync_service_module.time, "time", lambda: next(times))
    monkeypatch.setattr(sync_service_module.logger, "warning", warning_messages.append)

    entity, checksum = await sync_service.sync_file("assets/large.bin", new=False)

    assert entity.id == 1
    assert checksum == "deadbeef"
    assert warning_messages == [
        "Slow file sync detected: path=assets/large.bin, file_kind=regular, duration_ms=800"
    ]


async def _false_async() -> bool:
    return False


async def _none_async(*args, **kwargs) -> None:
    return None
