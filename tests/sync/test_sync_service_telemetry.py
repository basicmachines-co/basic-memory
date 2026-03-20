"""Telemetry coverage for sync phase spans."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from basic_memory.sync.sync_service import SyncReport

sync_service_module = importlib.import_module("basic_memory.sync.sync_service")


def _capture_sync_telemetry():
    operations: list[tuple[str, dict]] = []
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_operation(name: str, **attrs):
        operations.append((name, attrs))
        yield

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return operations, spans, fake_operation, fake_span


@pytest.mark.asyncio
async def test_sync_emits_phase_spans(sync_service, project_config, monkeypatch) -> None:
    operations, spans, fake_operation, fake_span = _capture_sync_telemetry()
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

    async def fake_sync_file(path, new=True):
        return None, None

    async def fake_should_skip_file(path):
        return False

    async def fake_resolve_relations(entity_id=None):
        return None

    async def fake_quick_count_files(directory):
        return 3

    async def fake_find_by_id(project_id):
        return SimpleNamespace(id=project_id)

    async def fake_update(project_id, values):
        return None

    monkeypatch.setattr(sync_service_module.telemetry, "operation", fake_operation)
    monkeypatch.setattr(sync_service_module.telemetry, "span", fake_span)
    monkeypatch.setattr(sync_service, "scan", fake_scan)
    monkeypatch.setattr(sync_service, "handle_move", fake_handle_move)
    monkeypatch.setattr(sync_service, "handle_delete", fake_handle_delete)
    monkeypatch.setattr(sync_service, "sync_file", fake_sync_file)
    monkeypatch.setattr(sync_service, "_should_skip_file", fake_should_skip_file)
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
    assert operations == [
        (
            "sync.project.run",
            {"project_name": project_config.name, "force_full": True},
        )
    ]
    assert [name for name, _ in spans] == [
        "sync.project.scan",
        "sync.project.apply_changes",
        "sync.project.resolve_relations",
        "sync.project.update_watermark",
    ]
