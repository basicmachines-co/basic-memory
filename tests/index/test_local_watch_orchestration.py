"""Tests for local watcher event-index orchestration."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from watchfiles import Change

from basic_memory.index.local_moves import (
    LocalWatchMoveProcessingResult,
    LocalWatchMoveProcessor,
)
from basic_memory.index.local_watch import (
    LocalWatchEventIndexRequest,
    LocalWatchProjectChangeBatch,
    LocalWatchStorageEventIndexRuntime,
    LocalWatchStorageEventSource,
    local_watch_filter_roots,
    local_watch_path_is_observable,
    local_watch_path_is_under_project,
    local_watch_project_change_batches,
    plan_local_watch_event_index_status_update,
    run_local_watch_event_indexing,
)
from basic_memory.runtime.storage import StorageEventPayload
from basic_memory.index.storage_events import (
    StorageEventIndexRuntime,
    StorageEventOperationProcessorFactory,
    StorageEventProjectResolver,
)
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import (
    RuntimeJobCounts,
    RuntimeStorageEventOperation,
)


def project_reference(project_path: str) -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=11,
        project_external_id="project-local",
        project_path=project_path,
        project_name="Local",
    )


@dataclass(slots=True)
class RecordingProjectResolver(StorageEventProjectResolver):
    project_path: str
    requested_paths: list[str] = field(default_factory=list)

    async def resolve_project(self, project_path: str) -> ProjectRuntimeReference | None:
        self.requested_paths.append(project_path)
        if project_path != self.project_path:
            return None
        return project_reference(project_path)


@dataclass(slots=True)
class RecordingProcessor:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        skip_reason = operation.skip_reason
        if skip_reason is None:
            raise AssertionError("skip operation missing reason")
        self.calls.append(("skip", skip_reason.value))

    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        self.calls.append(("index", operation.require_relative_path()))

    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        self.calls.append(("delete", operation.require_relative_path()))

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        self.calls.append(("failed", str(exc)))


@dataclass(slots=True)
class RecordingProcessorFactory(StorageEventOperationProcessorFactory):
    processor: RecordingProcessor

    def processor_for_project(
        self,
        project: ProjectRuntimeReference,
    ) -> RecordingProcessor:
        return self.processor


async def test_run_local_watch_event_indexing_normalizes_changes_and_dispatches(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "local-project"
    project_root.mkdir()
    note_path = project_root / "notes" / "a.md"
    note_path.parent.mkdir()
    note_path.write_text("# A\n", encoding="utf-8")

    resolver = RecordingProjectResolver(project_path="local-project")
    processor = RecordingProcessor()

    result = await run_local_watch_event_indexing(
        LocalWatchEventIndexRequest(
            project_root=project_root,
            project_prefix="local-project",
            changes=((Change.added, str(note_path)),),
            event_time="2026-06-20T16:00:00Z",
        ),
        runtime=StorageEventIndexRuntime(
            project_resolver=resolver,
            operation_processor_factory=RecordingProcessorFactory(processor),
        ),
    )

    assert result.as_dict() == {"processed": 1, "failed": 0, "skipped": 0}
    assert resolver.requested_paths == ["local-project"]
    assert processor.calls == [("index", "notes/a.md")]


@dataclass(slots=True)
class RaisingMoveProcessor:
    """Move processor that fails, standing in for a run_move_batches error."""

    async def process_moves(
        self,
        events: Sequence[StorageEventPayload],
    ) -> LocalWatchMoveProcessingResult:
        raise RuntimeError("move maintenance boom")


@pytest.mark.asyncio
async def test_run_local_watch_event_indexing_contains_move_processing_failure(
    tmp_path: Path,
) -> None:
    """A move-processing failure must not drop the rest of the batch's events."""
    project_root = tmp_path / "local-project"
    project_root.mkdir()
    note_path = project_root / "notes" / "a.md"
    note_path.parent.mkdir()
    note_path.write_text("# A\n", encoding="utf-8")

    resolver = RecordingProjectResolver(project_path="local-project")
    processor = RecordingProcessor()

    result = await run_local_watch_event_indexing(
        LocalWatchEventIndexRequest(
            project_root=project_root,
            project_prefix="local-project",
            changes=((Change.added, str(note_path)),),
            event_time="2026-06-20T16:00:00Z",
        ),
        runtime=LocalWatchStorageEventIndexRuntime(
            project_resolver=resolver,
            operation_processor_factory=RecordingProcessorFactory(processor),
            move_processor=cast(LocalWatchMoveProcessor, RaisingMoveProcessor()),
        ),
    )

    # Move detection failed, but the create event was still indexed as-is.
    assert result.as_dict() == {"processed": 1, "failed": 0, "skipped": 0}
    assert processor.calls == [("index", "notes/a.md")]


def test_local_watch_storage_event_source_groups_events_by_bucket(tmp_path: Path) -> None:
    project_root = tmp_path / "local-project"
    project_root.mkdir()
    note_path = project_root / "notes" / "a.md"
    note_path.parent.mkdir()
    note_path.write_text("# A\n", encoding="utf-8")

    source = LocalWatchStorageEventSource(
        LocalWatchEventIndexRequest(
            project_root=project_root,
            project_prefix="local-project",
            changes=((Change.added, str(note_path)),),
            event_time="2026-06-20T16:00:00Z",
            bucket_name="local-bucket",
        )
    )

    events_by_bucket = source.events_by_bucket()

    assert list(events_by_bucket) == ["local-bucket"]
    assert [event.object_key for event in events_by_bucket["local-bucket"]] == [
        "local-project/notes/a.md"
    ]


def test_local_watch_request_builds_storage_prefix_from_project(tmp_path: Path) -> None:
    project_root = tmp_path / "configured-project-root"
    project_root.mkdir()
    note_path = project_root / "notes" / "a.md"
    note_path.parent.mkdir()
    note_path.write_text("# A\n", encoding="utf-8")
    # No permalink/name attributes at all: a structural caller without the
    # optional identity shape falls back to the root directory name.
    project = SimpleNamespace(path=str(project_root))

    request = LocalWatchEventIndexRequest.from_project_changes(
        project=project,
        changes=((Change.added, str(note_path)),),
        event_time="2026-06-20T16:00:00Z",
        bucket_name="local-bucket",
    )
    source = LocalWatchStorageEventSource(request)

    assert request.project_root == project_root.resolve()
    assert request.project_prefix == "configured-project-root"
    assert [event.object_key for event in source.events()] == ["configured-project-root/notes/a.md"]

    # None-valued identity attributes also fall back to the directory name,
    # and a Path-typed project path is coerced rather than crashing.
    none_identity_request = LocalWatchEventIndexRequest.from_project_changes(
        project=SimpleNamespace(path=project_root, permalink=None, name=None),
        changes=((Change.added, str(note_path)),),
    )

    assert none_identity_request.project_root == project_root.resolve()
    assert none_identity_request.project_prefix == "configured-project-root"


def test_local_watch_request_uses_project_permalink_for_duplicate_leaf_roots(
    tmp_path: Path,
) -> None:
    alpha_root = tmp_path / "alpha" / "notes"
    beta_root = tmp_path / "beta" / "notes"
    alpha_note = alpha_root / "a.md"
    beta_note = beta_root / "b.md"
    alpha_note.parent.mkdir(parents=True)
    beta_note.parent.mkdir(parents=True)
    alpha_note.write_text("# A\n", encoding="utf-8")
    beta_note.write_text("# B\n", encoding="utf-8")
    alpha = SimpleNamespace(
        path=str(alpha_root),
        name="Alpha Notes",
        permalink="alpha-notes",
    )
    beta = SimpleNamespace(
        path=str(beta_root),
        name="Beta Notes",
        permalink="beta-notes",
    )

    alpha_request = LocalWatchEventIndexRequest.from_project_changes(
        project=alpha,
        changes=((Change.added, str(alpha_note)),),
    )
    beta_request = LocalWatchEventIndexRequest.from_project_changes(
        project=beta,
        changes=((Change.added, str(beta_note)),),
    )

    assert alpha_request.project_prefix == "alpha-notes"
    assert beta_request.project_prefix == "beta-notes"
    assert [event.object_key for event in LocalWatchStorageEventSource(alpha_request).events()] == [
        "alpha-notes/a.md"
    ]
    assert [event.object_key for event in LocalWatchStorageEventSource(beta_request).events()] == [
        "beta-notes/b.md"
    ]


def test_local_watch_project_change_batches_route_changes_to_projects(tmp_path: Path) -> None:
    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    outside_root = tmp_path / "outside"
    alpha_root.mkdir()
    beta_root.mkdir()
    outside_root.mkdir()
    alpha_note = alpha_root / "notes" / "a.md"
    beta_note = beta_root / "notes" / "b.md"
    outside_note = outside_root / "notes" / "ignored.md"
    for note in (alpha_note, beta_note, outside_note):
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Note\n", encoding="utf-8")
    alpha = SimpleNamespace(path=str(alpha_root))
    beta = SimpleNamespace(path=str(beta_root))

    batches = local_watch_project_change_batches(
        projects=(alpha, beta),
        changes=(
            (Change.added, str(alpha_note)),
            (Change.modified, str(beta_note)),
            (Change.added, str(outside_note)),
        ),
        ignore_patterns_by_project_root={
            alpha_root.resolve(): set(),
            beta_root.resolve(): set(),
        },
    )

    assert batches == (
        LocalWatchProjectChangeBatch(
            project=alpha,
            changes=((Change.added, str(alpha_note)),),
        ),
        LocalWatchProjectChangeBatch(
            project=beta,
            changes=((Change.modified, str(beta_note)),),
        ),
    )


def test_local_watch_project_change_batches_route_nested_to_deepest(tmp_path: Path) -> None:
    """A change under a nested child routes to the child even when the parent
    (which also contains the path) is listed first — repository order is arbitrary."""
    parent_root = tmp_path / "parent"
    child_root = parent_root / "child"
    child_root.mkdir(parents=True)
    child_note = child_root / "notes" / "c.md"
    child_note.parent.mkdir(parents=True, exist_ok=True)
    child_note.write_text("# Note\n", encoding="utf-8")
    parent = SimpleNamespace(path=str(parent_root))
    child = SimpleNamespace(path=str(child_root))

    batches = local_watch_project_change_batches(
        projects=(parent, child),  # parent first — the failing order before the fix
        changes=((Change.added, str(child_note)),),
        ignore_patterns_by_project_root={
            parent_root.resolve(): set(),
            child_root.resolve(): set(),
        },
    )

    assert batches == (
        LocalWatchProjectChangeBatch(
            project=child,
            changes=((Change.added, str(child_note)),),
        ),
    )


def test_local_watch_project_change_batches_drop_ignored_nested_child_file(
    tmp_path: Path,
) -> None:
    """When the deepest child project ignores a path, drop it instead of routing
    it up to the parent that also contains it."""
    parent_root = tmp_path / "parent"
    child_root = parent_root / "child"
    child_root.mkdir(parents=True)
    ignored_note = child_root / "ignored.md"
    ignored_note.write_text("# Ignored\n", encoding="utf-8")
    parent = SimpleNamespace(path=str(parent_root))
    child = SimpleNamespace(path=str(child_root))

    batches = local_watch_project_change_batches(
        projects=(parent, child),
        changes=((Change.added, str(ignored_note)),),
        ignore_patterns_by_project_root={
            # Parent would happily index it, but the deepest owner (child) ignores it.
            parent_root.resolve(): set(),
            child_root.resolve(): {"ignored.md"},
        },
    )

    assert batches == ()


def test_local_watch_project_change_batches_apply_project_ignore_patterns(tmp_path: Path) -> None:
    project_root = tmp_path / "local-project"
    project_root.mkdir()
    kept_note = project_root / "notes" / "kept.md"
    ignored_note = project_root / "ignored.md"
    kept_note.parent.mkdir()
    kept_note.write_text("# Kept\n", encoding="utf-8")
    ignored_note.write_text("# Ignored\n", encoding="utf-8")
    project = SimpleNamespace(path=str(project_root))

    batches = local_watch_project_change_batches(
        projects=(project,),
        changes=(
            (Change.added, str(ignored_note)),
            (Change.added, str(kept_note)),
        ),
        ignore_patterns_by_project_root={project_root.resolve(): {"ignored.md"}},
    )

    assert batches == (
        LocalWatchProjectChangeBatch(
            project=project,
            changes=((Change.added, str(kept_note)),),
        ),
    )


def test_local_watch_path_under_project_excludes_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "local-project"
    project_root.mkdir()
    nested_file = project_root / "notes" / "a.md"
    nested_file.parent.mkdir()
    nested_file.write_text("# A\n", encoding="utf-8")
    outside_file = tmp_path / "outside" / "a.md"
    outside_file.parent.mkdir()
    outside_file.write_text("# Outside\n", encoding="utf-8")

    assert local_watch_path_is_under_project(
        project_root=project_root,
        path=nested_file,
    )
    assert not local_watch_path_is_under_project(
        project_root=project_root,
        path=project_root,
    )
    assert not local_watch_path_is_under_project(
        project_root=project_root,
        path=outside_file,
    )


def test_local_watch_filter_roots_sort_outermost_first(tmp_path: Path) -> None:
    outer_root = tmp_path / "outer"
    nested_root = outer_root / "nested"
    nested_root.mkdir(parents=True)

    roots = local_watch_filter_roots(
        (
            SimpleNamespace(path=str(nested_root)),
            SimpleNamespace(path=str(outer_root)),
        )
    )

    assert roots == (outer_root.resolve(), nested_root.resolve())


def test_local_watch_path_visibility_uses_project_relative_hidden_parts(tmp_path: Path) -> None:
    hidden_parent_project = tmp_path / ".claude" / "project"
    hidden_parent_project.mkdir(parents=True)
    visible_note = hidden_parent_project / "notes" / "visible.md"
    visible_note.parent.mkdir()
    visible_note.write_text("# Visible\n", encoding="utf-8")

    outer_project = tmp_path / "outer"
    nested_project = outer_project / ".private" / "subproject"
    nested_project.mkdir(parents=True)
    hidden_nested_note = nested_project / "notes" / "hidden.md"
    hidden_nested_note.parent.mkdir()
    hidden_nested_note.write_text("# Hidden\n", encoding="utf-8")

    assert local_watch_path_is_observable(
        project_roots=(hidden_parent_project.resolve(),),
        path=visible_note,
    )
    assert not local_watch_path_is_observable(
        project_roots=local_watch_filter_roots(
            (
                SimpleNamespace(path=str(outer_project)),
                SimpleNamespace(path=str(nested_project)),
            )
        ),
        path=hidden_nested_note,
    )
    assert not local_watch_path_is_observable(
        project_roots=(hidden_parent_project.resolve(),),
        path=hidden_parent_project / "notes" / "draft.tmp",
    )


def test_local_watch_status_update_plans_success() -> None:
    update = plan_local_watch_event_index_status_update(
        project_prefix="configured-project-root",
        result=RuntimeJobCounts(processed=2, skipped=1),
    )

    assert update.path == "configured-project-root"
    assert update.action == "index"
    assert update.status == "success"
    assert update.error is None
    assert update.indexed_files_increment == 2
    assert update.error_count_increment == 0
    assert update.record_last_error is False


def test_local_watch_status_update_plans_failure_details() -> None:
    update = plan_local_watch_event_index_status_update(
        project_prefix="configured-project-root",
        result=RuntimeJobCounts(processed=1, failed=2, skipped=3),
    )

    assert update.path == "configured-project-root"
    assert update.action == "index"
    assert update.status == "error"
    assert update.error == "event-index processed=1 failed=2 skipped=3"
    assert update.indexed_files_increment == 1
    assert update.error_count_increment == 2
    assert update.record_last_error is True
