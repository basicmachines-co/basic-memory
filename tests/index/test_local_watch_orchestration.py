"""Tests for local watcher event-index orchestration."""

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from watchfiles import Change

from basic_memory.index import (
    LocalWatchProjectChangeBatch,
    LocalWatchEventIndexRequest,
    LocalWatchStorageEventSource,
    StorageEventIndexRuntime,
    StorageEventOperationProcessorFactory,
    StorageEventProjectResolver,
    local_watch_filter_roots,
    local_watch_path_is_observable,
    local_watch_project_change_batches,
    plan_local_watch_event_index_status_update,
    run_local_watch_event_indexing,
)
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeStorageEventOperation,
    RuntimeStorageEventProcessingResult,
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
        result=RuntimeStorageEventProcessingResult.empty().with_processed(2).with_skipped(1),
    )

    assert update.path == "configured-project-root"
    assert update.action == "index"
    assert update.status == "success"
    assert update.error is None
    assert update.synced_files_increment == 2
    assert update.error_count_increment == 0
    assert update.record_last_error is False


def test_local_watch_status_update_plans_failure_details() -> None:
    update = plan_local_watch_event_index_status_update(
        project_prefix="configured-project-root",
        result=RuntimeStorageEventProcessingResult.empty()
        .with_processed(1)
        .with_failed(2)
        .with_skipped(3),
    )

    assert update.path == "configured-project-root"
    assert update.action == "index"
    assert update.status == "error"
    assert update.error == "event-index processed=1 failed=2 skipped=3"
    assert update.synced_files_increment == 1
    assert update.error_count_increment == 2
    assert update.record_last_error is True
